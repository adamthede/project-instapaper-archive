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
  3. A k-nearest-neighbour graph (exact, brute-force — it is a few BLAS matrix
     products at this size) used as a CONNECTIVITY CONSTRAINT. Agglomerative
     clustering with connectivity only ever considers merges along graph
     edges, which is what turns the quadratic problem into a tractable one.
  4. Build the merge tree ONCE, then cut it at whatever threshold is asked
     for. The threshold is the only tuning knob, the tree is not re-computed
     per threshold, and ``--sweep`` therefore costs almost nothing — which is
     the point, because the threshold should be chosen from the coverage
     curve rather than from taste.

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
    print(f"  {len(clusters):,} clusters at similarity {args.similarity}")
    print("\n  coverage curve (cumulative article coverage of the top N):")
    for point in curve:
        print(f"    top-{point['n']:<4} {point['coverage']:>6.1f}%  "
              f"({point['articles']:,} articles)")

    out = Path(args.out or Path(args.data_dir) / "clusters.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "field_scope": args.field,
        "n_articles": inv.n_articles,
        "n_strings": len(sel_keys),
        "params": {"similarity": args.similarity, "dims": args.dims,
                   "neighbors": args.neighbors, "linkage": "average",
                   "embed_model": common.EMBED_MODEL,
                   "embed_prefix": common.EMBED_PREFIX},
        "coverage_curve": curve,
        "clusters": [{k: v for k, v in c.items() if not k.startswith("_")}
                     for c in clusters],
    }
    out.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
