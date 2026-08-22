"""Controlled-vocabulary derivation (Phase A): the flock contract, the
resumable cache, deterministic clustering, set-based coverage, the naming
step's failure posture, and the gate page's escaping.

Nothing here touches LM Studio or the real index. Every LM Studio call is
driven by a fake session object, which is also how the flock assertions work:
the fake inspects the lock from inside the request, which is the only moment
at which "is it held?" is a meaningful question.
"""
import fcntl
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "site"))

from vocab import common, embed as embed_mod, cluster as cluster_mod  # noqa: E402
from vocab import gate as gate_mod, name_clusters  # noqa: E402


# --- fakes ----------------------------------------------------------------

class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def isolate_the_fleet_lock(monkeypatch, tmp_path):
    """Point the flock at a per-test path instead of the real fleet lock.

    Autouse and unconditional. Probing the real
    ~/.cache/tractor-silo/lmstudio-digest.lock made these tests fail whenever
    anything else on the machine was mid-inference — the nightly enrichment
    leg, or another stage of this very pipeline — which is a test that reports
    on the machine's mood rather than on the code. BSD flock semantics are
    identical on any path, so the assertions lose nothing.
    """
    monkeypatch.setattr(common, "LOCK_PATH", tmp_path / "fleet.lock")


def lock_is_held():
    """True if some OTHER open file description holds the fleet flock.

    BSD flock associates the lock with the open file description, not the
    process, so a second ``open()`` here genuinely contends with the one
    ``locked_post`` is holding — which is what makes this assertion real
    rather than a restatement of the code.
    """
    common.LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(common.LOCK_PATH, "w") as probe:
        try:
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(probe, fcntl.LOCK_UN)
        return False


class FakeEmbedSession:
    """Serves deterministic vectors and records what it was asked for."""

    def __init__(self, dim=8, fail_on_call=None, model=None, shuffle=False,
                 short_by=0, observer=None):
        self.dim = dim
        self.fail_on_call = fail_on_call
        self.model = model or common.EMBED_MODEL
        self.shuffle = shuffle
        self.short_by = short_by
        self.observer = observer
        self.batches = []
        self.calls = 0

    def _vector(self, text):
        rng = np.random.default_rng(abs(hash(text)) % (2 ** 32))
        return rng.normal(size=self.dim).tolist()

    def post(self, url, timeout=None, json=None):
        self.calls += 1
        if self.observer:
            self.observer(self)
        texts = json["input"]
        self.batches.append(list(texts))
        if self.fail_on_call and self.calls == self.fail_on_call:
            raise requests.ConnectionError("LM Studio went away")
        data = [{"index": i, "embedding": self._vector(t)}
                for i, t in enumerate(texts)]
        if self.short_by:
            data = data[:-self.short_by]
        if self.shuffle:
            data = list(reversed(data))
        return FakeResponse({"model": self.model, "data": data})


class FakeChatSession:
    def __init__(self, replies=None, fail_on=(), model=None, observer=None):
        self.replies = replies or {}
        self.fail_on = set(fail_on)
        self.model = model or common.CHAT_MODEL
        self.observer = observer
        self.calls = 0
        self.prompts = []

    def post(self, url, timeout=None, json=None):
        self.calls += 1
        prompt = json["messages"][0]["content"]
        self.prompts.append(prompt)
        if self.observer:
            self.observer(self)
        if self.calls in self.fail_on:
            return FakeResponse({"error": "boom"}, status_code=500)
        reply = self.replies.get(self.calls,
                                 '{"name": "Artificial Intelligence", '
                                 '"definition": "The engineering of systems '
                                 'that perform tasks requiring intelligence.", '
                                 '"axis": "topic"}')
        return FakeResponse({
            "model": self.model,
            "choices": [{"message": {"content": reply}}],
        })


def make_rows(spec):
    """A rows frame shaped like the site corpus, from {id: (concepts, topics)}."""
    records, index = [], []
    for row_id, (concepts, topics) in spec.items():
        index.append(row_id)
        records.append({
            "title": f"Article {row_id}",
            "url": f"https://example.com/{row_id}",
            "word_count": 1000 + row_id,
            "year": 2020,
            "concepts": list(concepts),
            "topics": list(topics),
        })
    return pd.DataFrame(records, index=index)


# --- the flock contract ---------------------------------------------------

def test_flock_is_held_during_the_request(tmp_path):
    held = []
    session = FakeEmbedSession(observer=lambda s: held.append(lock_is_held()))
    embed_mod.run(tmp_path, ["alpha", "beta"], batch_size=2, session=session,
                  progress=lambda *_: None)
    assert held == [True], "the fleet lock must be held while the request is in flight"


def test_flock_is_released_after_the_request(tmp_path):
    session = FakeEmbedSession()
    embed_mod.run(tmp_path, ["alpha"], batch_size=1, session=session,
                  progress=lambda *_: None)
    assert not lock_is_held(), "the lock must be released when the batch returns"


def test_flock_is_not_held_across_batches(tmp_path):
    """The contract is per-batch, not per-run: the nightly leg has to interleave."""
    between = []
    session = FakeEmbedSession()
    embed_mod.run(tmp_path, ["a", "b", "c", "d"], batch_size=1, session=session,
                  progress=lambda *_: between.append(lock_is_held()))
    assert session.calls == 4
    # progress fires after every checkpointed batch; the lock must be free at
    # each of those moments.
    assert between and not any(between), between


def test_naming_holds_and_releases_the_lock_per_call(tmp_path):
    during, after = [], []
    session = FakeChatSession(observer=lambda s: during.append(lock_is_held()))
    clusters = [{"id": 0, "members": ["ai"], "size": 1, "articles": 1},
                {"id": 1, "members": ["vc"], "size": 1, "articles": 1}]
    rows = make_rows({1: (["ai"], []), 2: (["vc"], [])})
    inv = common.Inventory(rows)
    name_clusters.run(clusters, inv, tmp_path / "names.jsonl",
                      progress=lambda *_: after.append(lock_is_held()),
                      session=session)
    assert during == [True, True]
    assert not any(after)


# --- the resumable cache --------------------------------------------------

def test_a_killed_run_resumes_instead_of_restarting(tmp_path):
    strings = [f"s{i}" for i in range(6)]
    dying = FakeEmbedSession(fail_on_call=3)
    with pytest.raises(SystemExit):
        embed_mod.run(tmp_path, strings, batch_size=2, session=dying,
                      progress=lambda *_: None)

    keys, vectors = embed_mod.load_cache(tmp_path)
    assert sorted(keys.tolist()) == ["s0", "s1", "s2", "s3"], \
        "batches that completed before the failure must be on disk"
    assert len(vectors) == 4

    resumed = FakeEmbedSession()
    embed_mod.run(tmp_path, strings, batch_size=2, session=resumed,
                  progress=lambda *_: None)
    assert resumed.batches == [["s4", "s5"]], \
        "resume must re-request only what is missing"
    keys, vectors = embed_mod.load_cache(tmp_path)
    assert sorted(keys.tolist()) == strings
    assert len(vectors) == 6


def test_resume_survives_a_half_written_checkpoint(tmp_path):
    """A kill mid-write leaves a temp file, which must never load as a shard."""
    embed_mod.run(tmp_path, ["a", "b"], batch_size=2, session=FakeEmbedSession(),
                  progress=lambda *_: None)
    shards = embed_mod.shard_dir(tmp_path)
    (shards / ".tmp-shard-00099.npz").write_bytes(b"not a real npz")
    keys, _ = embed_mod.load_cache(tmp_path)
    assert sorted(keys.tolist()) == ["a", "b"]


def test_an_unreadable_shard_costs_its_batch_not_the_cache(tmp_path):
    embed_mod.run(tmp_path, ["a", "b", "c", "d"], batch_size=2,
                  session=FakeEmbedSession(), progress=lambda *_: None)
    shards = sorted(embed_mod.shard_dir(tmp_path).glob("shard-*.npz"))
    shards[0].write_bytes(b"corrupt")
    keys, vectors = embed_mod.load_cache(tmp_path)
    assert sorted(keys.tolist()) == ["c", "d"]
    assert len(vectors) == 2
    # ...and the next run re-embeds exactly the lost batch.
    again = FakeEmbedSession()
    embed_mod.run(tmp_path, ["a", "b", "c", "d"], batch_size=2, session=again,
                  progress=lambda *_: None)
    assert again.batches == [["a", "b"]]


def test_cache_refuses_a_different_embedding_model(tmp_path):
    embed_mod.run(tmp_path, ["a"], batch_size=1, session=FakeEmbedSession(),
                  progress=lambda *_: None)
    embed_mod.write_manifest(tmp_path, "some-other-embedder", "", 8, 1)
    with pytest.raises(SystemExit, match="Mixing embedding spaces"):
        embed_mod.run(tmp_path, ["b"], batch_size=1, session=FakeEmbedSession(),
                      progress=lambda *_: None)


def test_vectors_are_matched_to_strings_by_index_not_arrival_order(tmp_path):
    """A reordered response would attach every vector to the wrong string."""
    ordered = FakeEmbedSession()
    embed_mod.run(tmp_path / "a", ["alpha", "beta", "gamma"], batch_size=3,
                  session=ordered, progress=lambda *_: None)
    shuffled = FakeEmbedSession(shuffle=True)
    embed_mod.run(tmp_path / "b", ["alpha", "beta", "gamma"], batch_size=3,
                  session=shuffled, progress=lambda *_: None)

    ka, va = embed_mod.load_cache(tmp_path / "a")
    kb, vb = embed_mod.load_cache(tmp_path / "b")
    assert ka.tolist() == kb.tolist()
    assert np.allclose(va, vb)


def test_a_short_batch_is_fatal_rather_than_misaligned(tmp_path):
    with pytest.raises(RuntimeError, match="asked for 3 embeddings, got 2"):
        embed_mod.run(tmp_path, ["a", "b", "c"], batch_size=3,
                      session=FakeEmbedSession(short_by=1),
                      progress=lambda *_: None)


def test_a_swapped_embedding_model_is_refused(tmp_path):
    with pytest.raises(common.ModelMismatch):
        embed_mod.run(tmp_path, ["a"], batch_size=1,
                      session=FakeEmbedSession(model="gemma-4-12b-it-mlx"),
                      progress=lambda *_: None)


# --- coverage is a set question ------------------------------------------

def test_coverage_is_a_union_not_a_sum_of_mentions():
    """Two strings on the same articles cover those articles once, not twice."""
    rows = make_rows({
        1: (["AI", "Artificial Intelligence"], []),
        2: (["AI", "Artificial Intelligence"], []),
        3: (["AI"], []),
        4: ([], ["Cooking"]),
    })
    inv = common.Inventory(rows)
    assert inv.count("AI") == 3
    assert inv.count("Artificial Intelligence") == 2
    # The sum would be 5/4 = 125%. The union is 3 of 4 articles.
    assert inv.coverage_of(["AI", "Artificial Intelligence"]) == 75.0


def test_an_article_naming_a_string_twice_counts_once():
    rows = make_rows({1: (["AI", "AI"], ["AI"])})
    inv = common.Inventory(rows)
    assert inv.count("AI") == 1
    assert inv.coverage_of(["AI"]) == 100.0


def test_a_string_in_both_fields_is_one_inventory_entry():
    rows = make_rows({1: (["AI"], ["AI"]), 2: ([], ["AI"])})
    inv = common.Inventory(rows)
    assert inv.strings == ["AI"]
    assert inv.field_of["AI"] == {"concepts", "topics"}
    assert inv.count("AI") == 2


def test_clusters_rank_by_article_coverage_not_by_member_count():
    """A ten-alias cluster on three articles must rank below a one-alias
    cluster on ten. Ranking on vocabulary size is the failure mode this
    whole phase is correcting."""
    spec = {}
    for i in range(1, 11):
        spec[i] = (["mega"], [])
    for i in range(11, 14):
        spec[i] = ([f"tiny{i}" for _ in range(1)], [])
    rows = make_rows(spec)
    inv = common.Inventory(rows)
    labels = np.array([0] + [1] * 3)
    keys = ["mega", "tiny11", "tiny12", "tiny13"]
    clusters = cluster_mod.assemble(labels, keys, inv)
    assert clusters[0]["members"] == ["mega"]
    assert clusters[0]["articles"] == 10
    assert clusters[1]["size"] == 3
    assert clusters[1]["articles"] == 3


def test_coverage_curve_is_cumulative_and_never_exceeds_one_hundred():
    spec = {i: ([f"c{i % 5}"], []) for i in range(1, 21)}
    rows = make_rows(spec)
    inv = common.Inventory(rows)
    keys = [f"c{i}" for i in range(5)]
    clusters = cluster_mod.assemble(np.arange(5), keys, inv)
    curve = cluster_mod.coverage_curve(clusters, inv.n_articles, (1, 2, 5, 10))
    values = [p["coverage"] for p in curve]
    assert values == sorted(values), "cumulative coverage cannot go down"
    assert values[-1] <= 100.0
    assert values[-1] == 100.0
    assert curve[-1]["available"] == 5, "asking for more clusters than exist is honest"


def test_curve_counts_an_article_once_across_overlapping_clusters():
    rows = make_rows({1: (["a", "b"], []), 2: (["a"], []), 3: (["b"], [])})
    inv = common.Inventory(rows)
    clusters = cluster_mod.assemble(np.array([0, 1]), ["a", "b"], inv)
    curve = cluster_mod.coverage_curve(clusters, inv.n_articles, (1, 2))
    assert curve[0]["coverage"] == pytest.approx(66.7, abs=0.1)
    # a covers {1,2}, b covers {1,3}; the union is all three, not four.
    assert curve[1]["coverage"] == 100.0


# --- deterministic clustering --------------------------------------------

def synthetic_vectors(n_groups=6, per_group=25, dim=32, seed=7):  # noqa: E302
    rng = np.random.default_rng(seed)
    centres = rng.normal(size=(n_groups, dim))
    centres /= np.linalg.norm(centres, axis=1, keepdims=True)
    vectors = np.repeat(centres, per_group, axis=0)
    vectors = vectors + rng.normal(scale=0.06, size=vectors.shape)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    truth = np.repeat(np.arange(n_groups), per_group)
    return vectors.astype(np.float32), truth


def test_clustering_is_deterministic_across_runs():
    vectors, _ = synthetic_vectors()
    threshold = cluster_mod.similarity_to_distance(0.8)
    first = cluster_mod.cluster_once(vectors, dims=16, neighbors=10,
                                     threshold=threshold)
    second = cluster_mod.cluster_once(vectors, dims=16, neighbors=10,
                                      threshold=threshold)
    assert np.array_equal(first, second)


def test_clustering_is_deterministic_under_a_shuffled_cache_order():
    """Same strings, same vectors, different shard order — same grouping.

    Not the same LABELS (labels follow leaf order by construction), but the
    same partition. Shards land in whatever order batches finished, so a
    partition that depended on it would make the cache non-reproducible.
    """
    vectors, _ = synthetic_vectors()
    threshold = cluster_mod.similarity_to_distance(0.8)
    base = cluster_mod.cluster_once(vectors, 16, 10, threshold)

    order = np.random.default_rng(3).permutation(len(vectors))
    shuffled = cluster_mod.cluster_once(vectors[order], 16, 10, threshold)

    def partition(labels, ids):
        return {frozenset(ids[labels == lab]) for lab in np.unique(labels)}

    assert partition(base, np.arange(len(vectors))) == partition(shuffled, order)


@pytest.mark.parametrize("method,neighbors", [("components", 15),
                                              ("average", 10)])
def test_clustering_recovers_planted_groups(method, neighbors):
    """The threshold has to actually separate — a determinism test alone
    would pass just as well if everything collapsed into one cluster."""
    vectors, truth = synthetic_vectors()
    labels = cluster_mod.cluster_once(
        vectors, dims=16, neighbors=neighbors, method=method,
        threshold=cluster_mod.similarity_to_distance(0.8))
    assert len(np.unique(labels)) == len(np.unique(truth))
    for group in np.unique(truth):
        assert len(np.unique(labels[truth == group])) == 1


def test_components_fragments_a_group_when_k_is_too_small():
    """The known failure direction of the mutual-kNN method, pinned on purpose.

    Reciprocity is what buys immunity from chaining, and the price is that a
    genuine group larger than its neighbour budget can split: 25 planted
    points at k=5 come apart into a dozen pieces. Fragmentation is the SAFE
    direction — the gate lets Adam merge two halves of a concept, whereas a
    chained blob has to be split by hand — but `--neighbors` has to be chosen
    with the expected entry size in mind, not left at a default.
    """
    vectors, truth = synthetic_vectors(per_group=25)
    tight = cluster_mod.cluster_once(
        vectors, dims=16, neighbors=5, method="components",
        threshold=cluster_mod.similarity_to_distance(0.8))
    assert len(np.unique(tight)) > len(np.unique(truth))
    # ...and every fragment is still PURE: fragmentation never mixes groups.
    for label in np.unique(tight):
        assert len(np.unique(truth[tight == label])) == 1


def test_mutual_knn_resists_the_chaining_that_one_sided_edges_allow():
    """Two planted groups joined by a bridge point.

    One-sided edges let the bridge glue both groups into one cluster;
    requiring reciprocity refuses it. This is the 41,980-string blob that
    similarity 0.78 produced on the real corpus, in miniature.
    """
    rng = np.random.default_rng(11)
    a = rng.normal(size=(1, 24))
    a /= np.linalg.norm(a)
    b = -a
    left = a + rng.normal(scale=0.05, size=(40, 24))
    right = b + rng.normal(scale=0.05, size=(40, 24))
    bridge = np.linspace(0, 1, 12)[:, None] * (b - a) + a
    vectors = np.vstack([left, right, bridge])
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    vectors = vectors.astype(np.float32)

    threshold = cluster_mod.similarity_to_distance(0.55)
    X = cluster_mod.reduce_dims(vectors, 12)
    chained = cluster_mod.components_labels(X, 12, threshold, mutual=False)
    guarded = cluster_mod.components_labels(X, 12, threshold, mutual=True)
    assert len(np.unique(guarded)) > len(np.unique(chained))


def test_chaining_report_flags_a_blob_that_coverage_alone_would_praise():
    """High coverage from one giant cluster must not read as success."""
    rows = make_rows({i: (["everything"], []) for i in range(1, 21)})
    inv = common.Inventory(rows)
    clusters = cluster_mod.assemble(np.array([0]), ["everything"], inv)
    curve = cluster_mod.coverage_curve(clusters, inv.n_articles, (20,))
    assert curve[0]["coverage"] == 100.0, "coverage looks perfect..."
    report = cluster_mod.chaining_report(clusters, inv.n_articles)
    assert report["chained"], "...but the chaining check has to catch it"
    assert report["top_share"] == 100.0


def test_chaining_report_is_quiet_on_a_healthy_head():
    rows = make_rows({i: ([f"c{i % 10}"], []) for i in range(1, 101)})
    inv = common.Inventory(rows)
    clusters = cluster_mod.assemble(np.arange(10), [f"c{i}" for i in range(10)],
                                    inv)
    report = cluster_mod.chaining_report(clusters, inv.n_articles)
    assert not report["chained"]
    assert report["top_share"] == 10.0


def test_a_lower_similarity_threshold_never_splits_what_a_higher_one_merged():
    vectors, _ = synthetic_vectors()
    loose = cluster_mod.cluster_once(
        vectors, 16, 10, cluster_mod.similarity_to_distance(0.5))
    tight = cluster_mod.cluster_once(
        vectors, 16, 10, cluster_mod.similarity_to_distance(0.95))
    assert len(np.unique(loose)) <= len(np.unique(tight))
    for label in np.unique(tight):
        members = np.where(tight == label)[0]
        assert len(np.unique(loose[members])) == 1


def test_similarity_to_distance_matches_the_unit_sphere_identity():
    assert cluster_mod.similarity_to_distance(1.0) == 0.0
    assert cluster_mod.similarity_to_distance(0.5) == pytest.approx(1.0)
    assert cluster_mod.similarity_to_distance(-1.0) == pytest.approx(2.0)


def test_assemble_ranking_is_stable_under_input_order():
    rows = make_rows({1: (["a"], []), 2: (["b"], [])})
    inv = common.Inventory(rows)
    one = cluster_mod.assemble(np.array([0, 1]), ["a", "b"], inv)
    two = cluster_mod.assemble(np.array([1, 0]), ["b", "a"], inv)
    assert [c["members"] for c in one] == [c["members"] for c in two]


# --- the naming step's failure posture ------------------------------------

def make_clusters(n):
    return [{"id": i, "members": [f"m{i}"], "size": 1, "articles": n - i}
            for i in range(n)]


def test_a_failed_naming_call_does_not_lose_the_cluster(tmp_path):
    clusters = make_clusters(3)
    rows = make_rows({i: ([f"m{i}"], []) for i in range(3)})
    inv = common.Inventory(rows)
    out = tmp_path / "names.jsonl"
    ok, failed = name_clusters.run(clusters, inv, out,
                                   progress=lambda *_: None,
                                   session=FakeChatSession(fail_on={2}))
    assert (ok, failed) == (2, 1)
    named = name_clusters.load_named(out)
    assert len(named) == 3, "every cluster must have a record, named or not"
    fallbacks = [r for r in named.values() if r["named_by"] == "fallback"]
    assert len(fallbacks) == 1
    assert fallbacks[0]["name"] == "m1", "falls back to the cluster's own best member"
    assert fallbacks[0]["error"]


def test_unparseable_json_falls_back_rather_than_raising(tmp_path):
    clusters = make_clusters(1)
    rows = make_rows({0: (["m0"], [])})
    out = tmp_path / "names.jsonl"
    ok, failed = name_clusters.run(
        clusters, common.Inventory(rows), out, progress=lambda *_: None,
        session=FakeChatSession(replies={1: "I'm afraid I can't do that."}))
    assert (ok, failed) == (0, 1)
    assert name_clusters.load_named(out)[list(name_clusters.load_named(out))[0]]["name"] == "m0"


def test_naming_resumes_by_member_hash_not_cluster_id(tmp_path):
    clusters = make_clusters(2)
    rows = make_rows({i: ([f"m{i}"], []) for i in range(2)})
    inv = common.Inventory(rows)
    out = tmp_path / "names.jsonl"
    name_clusters.run(clusters, inv, out, progress=lambda *_: None,
                      session=FakeChatSession())

    # Re-cluster: same members, different ids and order.
    reordered = [{"id": 9, "members": ["m1"], "size": 1, "articles": 1},
                 {"id": 8, "members": ["m0"], "size": 1, "articles": 2}]
    second = FakeChatSession()
    name_clusters.run(reordered, inv, out, progress=lambda *_: None,
                      session=second)
    assert second.calls == 0, "unchanged member lists must not be re-named"

    # A cluster whose membership actually changed IS re-named.
    changed = [{"id": 0, "members": ["m0", "m1"], "size": 2, "articles": 2}]
    third = FakeChatSession()
    name_clusters.run(changed, inv, out, progress=lambda *_: None,
                      session=third)
    assert third.calls == 1


def test_a_torn_last_line_does_not_break_resume(tmp_path):
    out = tmp_path / "names.jsonl"
    out.write_text('{"key": "abc", "name": "Fine"}\n{"key": "def", "na',
                   encoding="utf-8")
    named = name_clusters.load_named(out)
    assert list(named) == ["abc"]


def test_a_swapped_naming_model_aborts_the_run(tmp_path):
    clusters = make_clusters(2)
    rows = make_rows({i: ([f"m{i}"], []) for i in range(2)})
    with pytest.raises(common.ModelMismatch):
        name_clusters.run(clusters, common.Inventory(rows),
                          tmp_path / "names.jsonl", progress=lambda *_: None,
                          session=FakeChatSession(model="gemma-4-12b-it-mlx"))


def test_the_naming_prompt_carries_members_frequencies_and_examples(tmp_path):
    rows = make_rows({1: (["ai", "artificial intelligence"], []),
                      2: (["ai"], [])})
    inv = common.Inventory(rows)
    cluster = {"id": 0, "members": ["ai", "artificial intelligence"],
               "size": 2, "articles": 2}
    session = FakeChatSession()
    name_clusters.run([cluster], inv, tmp_path / "n.jsonl",
                      progress=lambda *_: None, session=session)
    prompt = session.prompts[0]
    assert "artificial intelligence" in prompt
    assert "Article 1" in prompt, "example titles must reach the prompt"
    assert "2 articles" in prompt
    assert "grouping is FINAL" in prompt, \
        "the model must be told it is not deciding membership"


@pytest.mark.parametrize("reply", [
    '{"name": "AI", "definition": "d", "axis": "topic"}',
    '```json\n{"name": "AI", "definition": "d", "axis": "topic"}\n```',
    'Sure!\n```\n{"name": "AI", "definition": "d", "axis": "topic"}\n```\nHope that helps.',
    'Here you go: {"name": "AI", "definition": "d", "axis": "topic"} - done',
])
def test_parse_reply_survives_the_usual_chat_wrappers(reply):
    assert name_clusters.parse_reply(reply)["name"] == "AI"


def test_parse_reply_drops_an_axis_it_does_not_recognise():
    parsed = name_clusters.parse_reply('{"name": "AI", "axis": "vibes"}')
    assert parsed["axis"] == ""


def test_parse_reply_rejects_a_nameless_object():
    with pytest.raises(ValueError):
        name_clusters.parse_reply('{"definition": "no name here"}')


# --- the gate page --------------------------------------------------------

HOSTILE = '</script><img src=x onerror="alert(1)">'
HOSTILE_ATTR = 'evil" onmouseover="alert(2)'


def build_gate(tmp_path, members, url="https://example.com/1", name=None,
               definition="A definition."):
    rows = make_rows({1: (members, []), 2: (members[:1], [])})
    rows.loc[1, "url"] = url
    inv = common.Inventory(rows)
    clusters = [{"id": 0, "members": members, "size": len(members),
                 "articles": 2, "coverage": 100.0, "fields": ["concepts"]}]
    key = name_clusters.cluster_key(members)
    names = {key: {"key": key, "name": name or members[0],
                   "definition": definition, "axis": "concept",
                   "named_by": "test"}}
    payload = {"generated": "2026-08-21T00:00:00", "params": {},
               "coverage_curve": [{"n": 20, "coverage": 44.0, "articles": 8,
                                   "available": 1}]}
    entries = gate_mod.build_entries(clusters, names, inv, 50)
    return gate_mod.render(entries, payload, inv, payload["coverage_curve"])


def test_gate_escapes_hostile_member_strings(tmp_path):
    html = build_gate(tmp_path, [HOSTILE, "benign"])
    assert HOSTILE not in html
    assert "<img src=x" not in html
    assert "&lt;/script&gt;&lt;img src=x onerror=" in html


def test_gate_escapes_a_hostile_proposed_name(tmp_path):
    html = build_gate(tmp_path, ["ai"], name=HOSTILE_ATTR)
    assert 'onmouseover="alert(2)"' not in html
    assert "&quot;" in html


def test_gate_escapes_a_hostile_definition(tmp_path):
    html = build_gate(tmp_path, ["ai"], definition=HOSTILE)
    assert HOSTILE not in html
    assert "onerror=" not in html.replace("&quot;", "").replace("&#x27;", "") \
        or "&lt;img" in html


def test_gate_refuses_to_link_a_javascript_url(tmp_path):
    html = build_gate(tmp_path, ["ai"], url="javascript:alert(1)")
    assert "javascript:alert" not in html
    assert 'class="eg"' in html, "the example is still shown, just not linked"
    assert '<a class="eg" href=""' not in html


def test_gate_links_a_real_http_url(tmp_path):
    html = build_gate(tmp_path, ["ai"], url="https://example.com/piece")
    assert '<a class="eg" href="https://example.com/piece"' in html
    assert 'rel="noopener"' in html


def test_gate_shows_cumulative_coverage_and_the_rankability_bar(tmp_path):
    rows = make_rows({i: ([f"c{i % 4}"], []) for i in range(1, 21)})
    inv = common.Inventory(rows)
    clusters = cluster_mod.assemble(np.arange(4), [f"c{i}" for i in range(4)], inv)
    entries = gate_mod.build_entries(clusters, {}, inv, 10)
    cumulative = [x["cumulative"] for x in entries]
    assert cumulative == sorted(cumulative)
    assert cumulative[-1] == 100.0
    html = gate_mod.render(entries, {"params": {}}, inv, [])
    assert "cumulative" in html
    assert "40%" in html, "the rankability bar has to be on the page"


def test_gate_flags_an_entry_whose_naming_call_failed(tmp_path):
    rows = make_rows({1: (["m0"], [])})
    inv = common.Inventory(rows)
    clusters = [{"id": 0, "members": ["m0"], "size": 1, "articles": 1,
                 "coverage": 100.0}]
    key = name_clusters.cluster_key(["m0"])
    names = {key: {"key": key, "name": "m0", "definition": "", "axis": "",
                   "error": "HTTP 500", "named_by": "fallback"}}
    entries = gate_mod.build_entries(clusters, names, inv, 10)
    html = gate_mod.render(entries, {"params": {}}, inv, [])
    assert "the naming call failed" in html
    assert "HTTP 500" in html


def test_gate_renders_without_any_names_at_all(tmp_path):
    """The gate must survive a naming stage that never ran."""
    rows = make_rows({1: (["m0"], [])})
    inv = common.Inventory(rows)
    clusters = [{"id": 0, "members": ["m0"], "size": 1, "articles": 1,
                 "coverage": 100.0}]
    entries = gate_mod.build_entries(clusters, {}, inv, 10)
    html = gate_mod.render(entries, {"params": {}}, inv, [])
    assert "m0" in html


def test_gate_is_self_contained():
    rows = make_rows({1: (["m0"], [])})
    inv = common.Inventory(rows)
    clusters = [{"id": 0, "members": ["m0"], "size": 1, "articles": 1,
                 "coverage": 100.0}]
    entries = gate_mod.build_entries(clusters, {}, inv, 10)
    html = gate_mod.render(entries, {"params": {}}, inv, [])
    for forbidden in ("<link ", "src=\"http", "@import", "fonts.googleapis"):
        assert forbidden not in html, f"gate must not fetch {forbidden}"


# --- example selection ----------------------------------------------------

def test_examples_prefer_articles_carrying_more_of_the_cluster():
    rows = make_rows({
        1: (["a"], []),
        2: (["a", "b", "c"], []),
        3: (["a", "b"], []),
    })
    inv = common.Inventory(rows)
    examples = common.example_articles(inv, ["a", "b", "c"], k=3)
    assert [x["title"] for x in examples] == ["Article 2", "Article 3", "Article 1"]


def test_examples_are_stable_across_calls():
    rows = make_rows({i: (["a"], []) for i in range(1, 30)})
    inv = common.Inventory(rows)
    first = common.example_articles(inv, ["a"])
    second = common.example_articles(inv, ["a"])
    assert first == second


def test_examples_skip_untitled_articles():
    rows = make_rows({1: (["a"], []), 2: (["a"], [])})
    rows.loc[1, "title"] = ""
    examples = common.example_articles(common.Inventory(rows), ["a"])
    assert [x["title"] for x in examples] == ["Article 2"]
