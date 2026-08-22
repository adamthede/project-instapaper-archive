#!/usr/bin/env python3
"""Phase A step 2 — cluster the cached embeddings into candidate vocabulary entries.

    .venv/bin/python scripts/vocab/cluster.py [--similarity 0.78] [--sweep]

This is the DETERMINISTIC half of the phase and it is kept strictly separate
from the generation half: nothing here calls an LLM, and two runs over the
same cache must produce byte-identical clusters. ``--verify-determinism``
proves it in-process by running the whole pipeline twice and comparing the
labelling.

Scale forced the shape of it. 73k vectors rules out a plain O(n²)
agglomerative — the pairwise matrix alone is 21 GB — so:

  1. L2-normalise, so euclidean distance is a monotone function of cosine
     similarity and the two notions of "close" cannot disagree.
  2. PCA to a few dozen dimensions with a deterministic solver. This is what
     makes step 3 affordable, and short noun phrases do not need 768
     dimensions to be separable.
  3. A MUTUAL k-nearest-neighbour graph (exact, brute-force — a few BLAS
     matrix products at this size), keeping only edges shorter than the
     threshold, and take its connected components. Requiring both endpoints
     to agree is the guard against single-linkage chaining; without it one
     cluster absorbed 41,980 of the 73,099 strings and reported 99.7%
     coverage while doing it.
  4. Report the coverage curve BOTH pooled and per source column, and check
     the head for chaining, because coverage alone cannot tell a working
     vocabulary from one blob that ate the corpus.

Structured average linkage (``--method average``) is kept for small inputs
and for comparison, but it is not the default: on the real 73k strings it ran
past ten minutes single-threaded at 5 GB resident without finishing, because
each merge unions its members' adjacency lists. The components method does
the same job in about three seconds.

The threshold is expressed as a cosine SIMILARITY (0.78 = "merge things at
least this alike") rather than a raw euclidean distance, because that is the
number a human can sanity-check: measured on this endpoint, AI/Artificial
Intelligence sit at .836 and AI/supply-chains at .426.

Ranking is by ARTICLE coverage — how many distinct articles a cluster's member
strings touch, a set union — never by summed mentions. See
``common.Inventory.coverage_of``.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vocab import common, embed as embed_mod  # noqa: E402

CURVE_POINTS = (20, 50, 100, 150, 200, 250, 300, 400, 500)
SWEEP_DEFAULT = (0.78, 0.84, 0.88, 0.89, 0.90, 0.91, 0.92, 0.94)


def similarity_to_distance(similarity):
    """Cosine similarity -> euclidean distance between unit vectors.

    ``|a - b|² = 2 - 2·cos(a, b)`` when both are unit length.
    """
    return float(np.sqrt(max(0.0, 2.0 - 2.0 * float(similarity))))


def reduce_dims(vectors, dims, seed=0):
    """L2-normalise, PCA down to ``dims``, re-normalise. Deterministic.

    The solver is pinned to an exact one (never ``randomized``) so the result
    depends on the input alone; sklearn's ``svd_flip`` already fixes the
    component sign ambiguity that would otherwise flip axes between runs.
    Re-normalising afterwards restores the unit sphere, so the caller's
    similarity threshold means the same thing before and after reduction.
    """
    from sklearn.decomposition import PCA

    X = np.asarray(vectors, dtype=np.float64)
    X /= np.maximum(np.linalg.norm(X, axis=1, keepdims=True), 1e-12)
    dims = int(min(dims, X.shape[0], X.shape[1]))
    if dims < X.shape[1]:
        try:
            pca = PCA(n_components=dims, svd_solver="covariance_eigh",
                      random_state=seed)
            X = pca.fit_transform(X)
        except (ValueError, TypeError):  # older sklearn: no covariance_eigh
            X = PCA(n_components=dims, svd_solver="full",
                    random_state=seed).fit_transform(X)
    X /= np.maximum(np.linalg.norm(X, axis=1, keepdims=True), 1e-12)
    return np.ascontiguousarray(X, dtype=np.float64)


def knn_graph(X, neighbors, mode="distance"):
    """Exact k-nearest-neighbour graph. Brute force on purpose.

    An approximate index (HNSW and friends) would be the single
    non-deterministic component in an otherwise reproducible pipeline, and at
    73k x 64 the exact version is a few BLAS matrix products.
    """
    from sklearn.neighbors import kneighbors_graph
    return kneighbors_graph(X, n_neighbors=int(neighbors), mode=mode,
                            metric="euclidean", include_self=False, n_jobs=-1)


def components_labels(X, neighbors, threshold, mutual=True):
    """Connected components of the kNN graph, keeping only close-enough edges.

    This is single-linkage restricted to the kNN graph, and it is the method
    that actually scales here: structured average linkage on 73k strings ran
    past ten minutes single-threaded at 5 GB resident, because each merge
    unions its members' adjacency lists and the heap grows with them. This
    runs in seconds and is exactly as deterministic.

    ``mutual`` keeps only edges both endpoints agree on (i is in j's k nearest
    AND j is in i's). That is the cheap guard against single-linkage chaining:
    a popular string sits in the neighbour list of hundreds of rare ones, and
    accepting those one-sided edges is how "Technology" swallows half the
    vocabulary through a chain of individually-plausible hops.
    """
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import connected_components

    graph = knn_graph(X, neighbors, mode="distance").tocoo()
    keep = graph.data <= threshold
    adjacency = csr_matrix(
        (np.ones(int(keep.sum()), dtype=np.int8),
         (graph.row[keep], graph.col[keep])), shape=graph.shape)
    adjacency = (adjacency.multiply(adjacency.T) if mutual
                 else adjacency.maximum(adjacency.T))
    _, labels = connected_components(adjacency, directed=False)
    # Relabel by first appearance so labels depend on leaf order alone.
    order, out = {}, np.empty(len(labels), dtype=np.int64)
    for i, lab in enumerate(labels):
        lab = int(lab)
        if lab not in order:
            order[lab] = len(order)
        out[i] = order[lab]
    return out


def build_tree(X, neighbors):
    """Structured average-linkage merge tree. Returns ``(children, distances)``.

    The kNN graph is exact (brute force) rather than approximate, because an
    approximate index would be the one non-deterministic component in an
    otherwise reproducible pipeline.
    """
    from sklearn.cluster import AgglomerativeClustering
    from sklearn.neighbors import kneighbors_graph

    knn = kneighbors_graph(X, n_neighbors=int(neighbors), mode="connectivity",
                           metric="euclidean", include_self=False,
                           n_jobs=-1)
    model = AgglomerativeClustering(
        n_clusters=None, distance_threshold=0.0, metric="euclidean",
        linkage="average", connectivity=knn, compute_full_tree=True,
        compute_distances=True)
    model.fit(X)
    return model.children_, model.distances_


def cut_tree(children, distances, n_leaves, threshold):
    """Cut the merge tree: union every merge made at distance <= threshold.

    Union-find over the merge sequence rather than sklearn's "keep the last k
    merges" cut, because the connectivity constraint can make ``distances_``
    non-monotonic and a count-based cut would then slice at an arbitrary
    distance. This cut is defined by the threshold alone, which is what makes
    ``--sweep`` comparable across thresholds and the result reproducible.

    Returns an int array of cluster labels, one per leaf, relabelled to be
    stable: 0..k-1 in order of each cluster's smallest leaf index.
    """
    parent = np.arange(n_leaves + len(children), dtype=np.int64)

    def find(i):
        root = i
        while parent[root] != root:
            root = parent[root]
        while parent[i] != root:  # path compression
            parent[i], i = root, parent[i]
        return root

    for step, (a, b) in enumerate(children):
        if distances[step] > threshold:
            continue
        node = n_leaves + step
        ra, rb = find(int(a)), find(int(b))
        parent[ra] = node
        parent[rb] = node
        parent[node] = node

    roots = np.fromiter((find(i) for i in range(n_leaves)), dtype=np.int64,
                        count=n_leaves)
    # Relabel by first appearance so labels depend on leaf order only.
    order, labels = {}, np.empty(n_leaves, dtype=np.int64)
    for i, r in enumerate(roots):
        r = int(r)
        if r not in order:
            order[r] = len(order)
        labels[i] = order[r]
    return labels


def assemble(labels, keys, inventory):
    """Group strings by label and attach article coverage. Ranked, deterministic.

    Sorted by article count descending, then by canonical-ish member name, so
    two clusters that happen to tie do not swap places between runs.
    """
    groups = {}
    for key, label in zip(keys, labels):
        groups.setdefault(int(label), []).append(str(key))

    clusters = []
    for label, members in groups.items():
        members = sorted(members, key=lambda s: (-inventory.count(s), s))
        articles = inventory.article_set(members)
        clusters.append({
            # The pre-ranking label, kept so centroid lookups can get back to
            # the row this cluster came from after the sort reorders things.
            "label": int(label),
            "members": members,
            "articles": len(articles),
            "coverage": round(len(articles) / inventory.n_articles * 100, 3)
            if inventory.n_articles else 0.0,
            "size": len(members),
            "fields": sorted({f for m in members
                              for f in inventory.field_of.get(m, ())}),
            "_articles": articles,
        })
    clusters.sort(key=lambda c: (-c["articles"], c["members"][0]))
    for i, c in enumerate(clusters):
        c["id"] = i
    return clusters


def coverage_curve(clusters, n_articles, points=CURVE_POINTS):
    """Cumulative ARTICLE coverage of the top-N clusters, for several N.

    A running set union down the ranked list, so an article tagged by three
    different top-N clusters is counted once. Summing per-cluster coverages
    would report well over 100% and is exactly the mistake this archive's
    ``head_coverage`` was written to avoid.
    """
    curve, seen = [], set()
    wanted = sorted(set(points))
    at = 0
    for n in wanted:
        while at < min(n, len(clusters)):
            seen |= clusters[at]["_articles"]
            at += 1
        curve.append({
            "n": n,
            "articles": len(seen),
            "coverage": round(len(seen) / n_articles * 100, 1) if n_articles else 0.0,
            "available": min(n, len(clusters)),
        })
    return curve


def naive_baseline(inventory, points=(20, 50, 100, 150, 250)):
    """What case-folding and de-pluralising alone would have achieved.

    The control group for the whole phase. Roughly 40% of the multi-member
    clusters this pipeline produces are explained entirely by lowercasing and
    dropping a trailing 's' — no embeddings, no GPU. Reporting the derived
    coverage against raw free-text credits the embeddings with that share
    too, so the gate shows this line as well and the honest claim becomes the
    gap between the two, not the gap from zero.
    """
    groups = {}
    for name in inventory.strings:
        key = name.casefold().strip()
        for suffix in ("ies", "es", "s"):
            if len(key) > 4 and key.endswith(suffix):
                key = key[:-len(suffix)] + ("y" if suffix == "ies" else "")
                break
        groups.setdefault(key, []).append(name)

    scored = sorted((inventory.article_set(members) for members in groups.values()),
                    key=len, reverse=True)
    curve, seen = [], set()
    at = 0
    for n in sorted(set(points)):
        while at < min(n, len(scored)):
            seen |= scored[at]
            at += 1
        curve.append({"n": n, "articles": len(seen),
                      "coverage": round(len(seen) / inventory.n_articles * 100, 1)
                      if inventory.n_articles else 0.0,
                      "available": min(n, len(scored))})
    return {"groups": len(groups), "curve": curve}


def free_text_baseline(inventory, points=(20, 50, 100, 150, 250)):
    """The pooled free-text curve — the like-for-like starting point.

    ``corpus.vocabulary_report`` measures each column separately (22.0% and
    25.3% at top-20). Pooling the two fields the way this derivation does
    scores 34.1% before any clustering at all, and THAT is the number a
    pooled result has to beat. Quoting the per-column figures against a
    pooled result overstates the gain by more than double.
    """
    scored = sorted((inventory.articles[s] for s in inventory.strings),
                    key=len, reverse=True)
    curve, seen = [], set()
    at = 0
    for n in sorted(set(points)):
        while at < min(n, len(scored)):
            seen |= scored[at]
            at += 1
        curve.append({"n": n, "articles": len(seen),
                      "coverage": round(len(seen) / inventory.n_articles * 100, 1)
                      if inventory.n_articles else 0.0,
                      "available": min(n, len(scored))})
    return curve


def column_curve(clusters, inventory, field, points=CURVE_POINTS):
    """The coverage curve measured against ONE source column.

    Phase C builds `canonical_concepts` and `canonical_topics` as separate
    index columns, and `corpus.RANKABLE_HEAD_COVERAGE` is defined per column.
    A vocabulary derived over both fields pooled scores well above either
    column alone, so reporting only the pooled figure would tell Adam he had
    cleared a bar that neither real column clears. Both go in the artifact.

    Clusters are re-ranked within the field, because a cluster's rank in the
    pooled list is not its rank among the articles this column tagged.
    """
    scored = []
    for cluster in clusters:
        hit = inventory.article_set(cluster["members"], field=field)
        if hit:
            scored.append(hit)
    scored.sort(key=len, reverse=True)
    curve, seen = [], set()
    at = 0
    for n in sorted(set(points)):
        while at < min(n, len(scored)):
            seen |= scored[at]
            at += 1
        curve.append({
            "n": n, "articles": len(seen),
            "coverage": round(len(seen) / inventory.n_articles * 100, 1)
            if inventory.n_articles else 0.0,
            "available": min(n, len(scored)),
        })
    return curve


def sibling_clusters(X, labels, clusters, limit, similarity, per_entry=8):
    """For each ranked entry, the OFF-PAGE clusters that look like it.

    The method's accepted failure direction is fragmentation, and on this
    corpus it is severe: "privacy" survives as dozens of separate clusters of
    which only a handful reach a 250-row page. A reviewer cannot merge what
    the page does not show, so each on-page entry carries its nearest
    off-page relatives — by centroid cosine similarity, at a looser threshold
    than the one that built the clusters — and the gate lets them be folded in.

    Without this the gate can only curate the head it happens to display,
    which on the measured corpus leaves ~15% of articles reachable only
    through clusters no one ever sees.
    """
    n_clusters = int(labels.max()) + 1 if len(labels) else 0
    if not n_clusters:
        return {}
    sums = np.zeros((n_clusters, X.shape[1]), dtype=np.float64)
    np.add.at(sums, labels, X)
    counts = np.bincount(labels, minlength=n_clusters).reshape(-1, 1)
    centroids = sums / np.maximum(counts, 1)
    # Re-normalise or the dot product below is not a cosine at all: a mean of
    # unit vectors is shorter than unit, by an amount that varies with how
    # tight the cluster is, so an unnormalised "similarity" would
    # systematically favour singleton clusters.
    centroids /= np.maximum(np.linalg.norm(centroids, axis=1, keepdims=True), 1e-12)

    # clusters[] is the ranked order; map rank -> the label it came from.
    label_of_rank = [c["label"] for c in clusters]
    head = centroids[label_of_rank[:limit]]
    rest_ranks = list(range(limit, len(clusters)))
    if not rest_ranks:
        return {}
    rest = centroids[[label_of_rank[r] for r in rest_ranks]]

    out = {}
    for i, sims in enumerate(head @ rest.T):
        close = np.where(sims >= similarity)[0]
        if not len(close):
            continue
        ranked = sorted(close, key=lambda j: (-clusters[rest_ranks[j]]["articles"],
                                              rest_ranks[j]))[:per_entry]
        out[i] = [{
            "rank": rest_ranks[j],
            "articles": clusters[rest_ranks[j]]["articles"],
            "similarity": round(float(sims[j]), 3),
            "members": clusters[rest_ranks[j]]["members"],
        } for j in ranked]
    return out


def max_fold_curves(clusters, inventory, limit, points=(20, 50, 250)):
    """The CEILING: per-column coverage if every offered sibling were folded in.

    The gate can only reassemble what it shows, so "does curating this page
    clear the 40% bar?" has an upper bound, and it is worth knowing before
    spending an afternoon on 250 decisions rather than after. Measured on this
    corpus the answer is no — maximal folding reaches ~31% and ~37% at top-20
    against the two columns — which turns "merge the axes" from one option
    into the only route to a per-column pass at k=20.
    """
    folded = []
    for cluster in clusters[:limit]:
        members = list(cluster["members"])
        for sib in cluster.get("siblings") or []:
            members.extend(sib["members"])
        folded.append(members)

    out = {}
    for field in inventory.fields:
        scored = sorted((inventory.article_set(m, field=field) for m in folded),
                        key=len, reverse=True)
        curve, seen = [], set()
        at = 0
        for n in sorted(set(points)):
            while at < min(n, len(scored)):
                seen |= scored[at]
                at += 1
            curve.append({"n": n, "articles": len(seen),
                          "coverage": round(len(seen) / inventory.n_articles * 100, 1)
                          if inventory.n_articles else 0.0})
        out[field] = curve
    return out


def chaining_report(clusters, n_articles):
    """Diagnostics for the failure mode that silently destroys a run.

    Single-linkage chains. Measured on this corpus at similarity 0.78, one
    cluster absorbed 41,980 of the 73,099 strings and touched 16,293 of 16,346
    articles — and reported 99.7% top-20 coverage while doing it. Coverage
    alone cannot distinguish "the vocabulary works" from "one blob ate the
    corpus", so the top cluster's share is checked separately and loudly.
    """
    if not clusters:
        return {"top_share": 0.0, "top_size": 0, "chained": False}
    top = clusters[0]
    share = round(top["articles"] / n_articles * 100, 1) if n_articles else 0.0
    return {
        "top_share": share,
        "top_size": top["size"],
        # A single legitimate entry does not touch a third of 22 years of
        # reading. Above this, the head is a chain, not a subject heading.
        "chained": share > 33.0,
    }


def select_scope(inventory, keys, vectors, field):
    """The (keys, vectors) subset in scope, in the inventory's sorted order."""
    index = {str(k): i for i, k in enumerate(keys)}
    if field == "both":
        wanted = inventory.strings
    else:
        wanted = [s for s in inventory.strings
                  if field in inventory.field_of.get(s, ())]
    picked = [(s, index[s]) for s in wanted if s in index]
    missing = len(wanted) - len(picked)
    if missing:
        print(f"  ! {missing:,} in-scope strings have no cached embedding — "
              "run embed.py to completion for a full derivation",
              file=sys.stderr)
    sel_keys = [s for s, _ in picked]
    sel_vecs = vectors[[i for _, i in picked]]
    return sel_keys, sel_vecs


def cluster_once(vectors, dims, neighbors, threshold, seed=0, method="components",
                 mutual=True):
    """The whole deterministic pipeline, end to end. Returns leaf labels."""
    X = reduce_dims(vectors, dims, seed=seed)
    if method == "components":
        return components_labels(X, neighbors, threshold, mutual=mutual)
    children, distances = build_tree(X, neighbors)
    return cut_tree(children, distances, len(X), threshold)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data-dir", default=str(common.DEFAULT_DATA_DIR))
    ap.add_argument("--index", default=str(common.INDEX_PATH))
    ap.add_argument("--field", choices=("both", "concepts", "topics"),
                    default="both")
    ap.add_argument("--similarity", type=float, default=0.78,
                    help="merge strings at least this cosine-similar")
    ap.add_argument("--dims", type=int, default=64)
    ap.add_argument("--neighbors", type=int, default=20)
    ap.add_argument("--method", choices=("components", "average"),
                    default="components",
                    help="components: mutual-kNN connected components, seconds. "
                         "average: structured average linkage, higher quality "
                         "in principle but did not finish on 73k strings.")
    ap.add_argument("--siblings-for", type=int, default=common.GATE_LIMIT,
                    help="attach off-page look-alike clusters to this many "
                         "ranked entries, so the gate can repair fragmentation. "
                         "Must match gate.py --limit; both default to "
                         "common.GATE_LIMIT for that reason.")
    ap.add_argument("--sibling-similarity", type=float, default=0.80,
                    help="centroid similarity for calling two clusters "
                         "relatives; deliberately looser than --similarity")
    ap.add_argument("--chaining-ok", action="store_true",
                    help="accept one-sided kNN edges (components method). "
                         "Faster to merge, much easier to chain two unrelated "
                         "clusters through a popular string.")
    ap.add_argument("--sweep", action="store_true",
                    help="report cluster counts + coverage across thresholds "
                         "and exit without writing")
    ap.add_argument("--verify-determinism", action="store_true",
                    help="run the pipeline twice and compare the labelling")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    rows = common.load_rows(args.index)
    inv = common.Inventory(rows)
    keys, vectors = embed_mod.load_cache(args.data_dir)
    if not len(keys):
        raise SystemExit(f"no embedding cache at {args.data_dir} — run embed.py")
    sel_keys, sel_vecs = select_scope(inv, keys, vectors, args.field)
    print(f"clustering {len(sel_keys):,} strings ({args.field}) over "
          f"{inv.n_articles:,} articles")

    t0 = time.time()
    X = reduce_dims(sel_vecs, args.dims)
    t_pca = time.time() - t0
    print(f"  reduce {t_pca:.1f}s -> {X.shape[1]}d", flush=True)

    tree = None
    if args.method == "average":
        t0 = time.time()
        tree = build_tree(X, args.neighbors)
        print(f"  tree {time.time() - t0:.1f}s", flush=True)

    def label_at(similarity):
        threshold = similarity_to_distance(similarity)
        if args.method == "components":
            return components_labels(X, args.neighbors, threshold,
                                     mutual=not args.chaining_ok)
        return cut_tree(tree[0], tree[1], len(X), threshold)

    if args.sweep:
        points = (20, 50, 100, 150, 200)
        print(f"\n{'sim':>6} {'clusters':>9} {'top1 str':>9} {'top1 art%':>10} " +
              " ".join(f"top{n}".rjust(7) for n in points), flush=True)
        for sim in SWEEP_DEFAULT:
            clusters = assemble(label_at(sim), sel_keys, inv)
            curve = {c["n"]: c["coverage"]
                     for c in coverage_curve(clusters, inv.n_articles, points)}
            chain = chaining_report(clusters, inv.n_articles)
            flag = "  CHAINED" if chain["chained"] else ""
            print(f"{sim:>6.2f} {len(clusters):>9,} {chain['top_size']:>9,} "
                  f"{chain['top_share']:>9.1f}% " +
                  " ".join(f"{curve[n]:>6.1f}%" for n in points) + flag,
                  flush=True)
        return

    labels = label_at(args.similarity)
    if args.verify_determinism:
        # A second pass from the raw cached vectors, not a re-cut of anything
        # already in memory — the point is to prove PCA and the kNN graph are
        # reproducible too, not just the final grouping step.
        again = cluster_once(sel_vecs, args.dims, args.neighbors,
                             similarity_to_distance(args.similarity),
                             method=args.method, mutual=not args.chaining_ok)
        same = bool(np.array_equal(labels, again))
        print(f"  determinism: two full runs {'MATCH' if same else 'DIFFER'}")
        if not same:
            raise SystemExit("clustering is not deterministic — refusing to write")

    clusters = assemble(labels, sel_keys, inv)
    curve = coverage_curve(clusters, inv.n_articles)
    columns = {f: column_curve(clusters, inv, f) for f in inv.fields}
    print(f"  {len(clusters):,} clusters at similarity {args.similarity}")

    # The chaining check runs HERE, on the path that writes the artifact —
    # not only under --sweep, where it used to live and where nobody making a
    # decision would ever see it.
    chain = chaining_report(clusters, inv.n_articles)
    print(f"  largest cluster: {chain['top_size']:,} strings, "
          f"{chain['top_share']}% of articles")
    if chain["chained"]:
        print("  !! CHAINED: one cluster covers a third of the corpus. The "
              "coverage numbers below are an artifact of that blob, not a "
              "working vocabulary. Raise --similarity.", file=sys.stderr)

    def show(label, points):
        print(f"\n  {label}")
        for point in points:
            if point["n"] in (20, 50, 100, 150, 250):
                print(f"    top-{point['n']:<4} {point['coverage']:>6.1f}%  "
                      f"({point['articles']:,} articles)")
    show("pooled concepts+topics (cumulative article coverage):", curve)
    for field, points in columns.items():
        show(f"against the `{field}` column alone:", points)

    siblings = sibling_clusters(X, labels, clusters, args.siblings_for,
                                args.sibling_similarity)
    for rank, found in siblings.items():
        clusters[rank]["siblings"] = found
    print(f"\n  {len(siblings):,} of the top {args.siblings_for} entries have "
          f"off-page relatives at similarity >= {args.sibling_similarity}")

    ceiling = max_fold_curves(clusters, inv, args.siblings_for)
    print("  ceiling if EVERY offered sibling were folded in (top-20): " +
          ", ".join(f"{f} {c[0]['coverage']}%" for f, c in ceiling.items()))

    out = Path(args.out or Path(args.data_dir) / "clusters.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "field_scope": args.field,
        "n_articles": inv.n_articles,
        "n_strings": len(sel_keys),
        # Everything needed to reproduce this file from the cache. It used to
        # claim `linkage: average` no matter what actually ran, which named
        # the one method that was abandoned.
        "params": {"similarity": args.similarity, "dims": args.dims,
                   "neighbors": args.neighbors, "method": args.method,
                   "mutual_knn": not args.chaining_ok,
                   "sibling_similarity": args.sibling_similarity,
                   "embed_model": common.EMBED_MODEL,
                   "embed_prefix": common.EMBED_PREFIX},
        "coverage_curve": curve,
        "column_curves": columns,
        "chaining": chain,
        # The two control groups. Without them "43.6%" has nothing to be
        # better than, and the obvious comparison (the per-column free-text
        # figures) is the wrong one.
        "free_text_curve": free_text_baseline(inv),
        "naive_baseline": naive_baseline(inv),
        "max_fold_curves": ceiling,
        # `label` is the pre-ranking row id, needed while computing centroids
        # and meaningless afterwards; it does not belong in 54,226 records.
        "clusters": [{k: v for k, v in c.items()
                      if not k.startswith("_") and k != "label"}
                     for c in clusters],
    }
    out.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
