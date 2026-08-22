"""Shared plumbing for the controlled-vocabulary derivation (Phase A).

Three things live here because embed / cluster / name / gate all need them and
none of them should own them:

  * the fleet flock contract for LM Studio (BSD ``fcntl.flock`` on the
    byte-identical path every other holder uses, acquired around ONE request
    and released before the next — never held across a run);
  * the string inventory, which is the unit of work for every later stage: a
    distinct free-text string, the set of ARTICLES carrying it, and which
    field(s) it came from;
  * article coverage, which is a set question everywhere in this codebase and
    is defined exactly once, here.

The corpus definition is imported from ``site/corpus.py`` rather than
re-derived. That is deliberate: the number this whole phase is trying to move
is ``corpus.RANKABLE_HEAD_COVERAGE`` measured over ``corpus.prepare``'s rows,
and a derivation run over a differently-filtered population would produce a
coverage curve that cannot be compared to the bar it is meant to clear.
"""
import fcntl
import os
import sys
from pathlib import Path

import numpy as np
import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "site"))

import corpus  # noqa: E402

# --- LM Studio ------------------------------------------------------------

LOCK_PATH = Path.home() / ".cache" / "tractor-silo" / "lmstudio-digest.lock"
LMSTUDIO_BASE = os.getenv("LMSTUDIO_BASE", "http://localhost:1234/v1")
EMBED_URL = os.getenv("LMSTUDIO_EMBED_URL", f"{LMSTUDIO_BASE}/embeddings")
CHAT_URL = os.getenv("LMSTUDIO_URL", f"{LMSTUDIO_BASE}/chat/completions")

# Pinned per the routing contract, EXACT catalog ids. A partial name 400s when
# nothing is loaded — JIT-load only matches exact ids. Same discipline (and
# same failure story) as enrich_archive_local.py.
EMBED_MODEL = os.getenv("LMSTUDIO_EMBED_MODEL", "text-embedding-nomic-embed-text-v1.5")
CHAT_MODEL = os.getenv("LMSTUDIO_MODEL", "qwen3.6-35b-a3b-mtp")

# A 2048-string embedding batch takes ~16s and a 35B naming call can take
# ~60s; the ceiling is for a busy machine, not the happy path.
REQUEST_TIMEOUT = 600

DEFAULT_DATA_DIR = Path(os.getenv("VOCAB_DATA_DIR", REPO_ROOT / "data" / "vocab"))
INDEX_PATH = Path(os.getenv("ARCHIVE_INDEX_PATH", REPO_ROOT / "data" / "archive_index.parquet"))

# Measured 2026-08-21 against the live endpoint, and the reason no task prefix
# is applied to the strings. nomic-embed-text-v1.5 documents `clustering: `,
# but the shared prefix tokens compress the whole similarity range: with it,
# AI/Artificial-Intelligence scored .934 against an AI/banana-bread floor of
# .682 (gap .25); bare, .836 against .366 (gap .47). Wider gap, better
# threshold behaviour, so the strings go in bare. Do not "fix" this by adding
# the prefix without re-measuring the gap.
EMBED_PREFIX = os.getenv("VOCAB_EMBED_PREFIX", "")


class ModelMismatch(RuntimeError):
    """LM Studio served a model other than the pinned one."""


def _check_model(served, pinned):
    if pinned.lower() not in str(served or "").lower():
        raise ModelMismatch(
            f"asked for {pinned!r}, LM Studio served {served!r} — refusing to "
            "derive a vocabulary with a swapped model. Load the pinned model "
            "or set the env var to what you actually intend.")


def locked_post(url, payload, timeout=REQUEST_TIMEOUT, session=None):
    """POST one request while holding the shared fleet flock.

    The lock is taken immediately before the request and released in a
    ``finally`` immediately after it, so a long run yields the GPU between
    units of work instead of starving the nightly enrichment leg. BSD flock on
    the byte-identical fleet path — POSIX lockf would not interoperate with
    the other holders.

    The explicit ``LOCK_UN`` is belt-and-braces, not the mechanism: closing
    the fd at the end of the ``with`` releases a BSD flock on its own, and a
    mutant that deletes the unlock still passes the release tests for that
    reason. What the tests actually pin down is that the fd is opened and
    closed INSIDE this function — hoisting it to module scope to save a few
    microseconds would hold the lock for the whole run, and
    ``test_flock_is_not_held_across_batches`` fails when it is.

    Returns ``(response, lock_wait_seconds)``.
    """
    import time
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    post = (session or requests).post
    with open(LOCK_PATH, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        waited = time.time() - started
        try:
            resp = post(url, timeout=timeout, json=payload)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
    return resp, waited


def embed_batch(texts, model=None, session=None):
    """Embed one batch under the flock. Returns ``(float32 [n, d], lock_wait)``.

    Order is restored from each datum's ``index`` rather than trusted from the
    response array — a reordered batch would silently attach every vector to
    the wrong string, and nothing downstream could detect it.
    """
    model = model or EMBED_MODEL
    payload = {"model": model, "input": [EMBED_PREFIX + t for t in texts]}
    resp, waited = locked_post(EMBED_URL, payload, session=session)
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
    body = resp.json()
    _check_model(body.get("model"), model)
    data = body.get("data") or []
    if len(data) != len(texts):
        raise RuntimeError(
            f"asked for {len(texts)} embeddings, got {len(data)} back")
    ordered = sorted(data, key=lambda d: d.get("index", 0))
    vectors = np.asarray([d["embedding"] for d in ordered], dtype=np.float32)
    if vectors.ndim != 2 or not vectors.shape[1]:
        raise RuntimeError(f"malformed embedding payload, shape {vectors.shape}")
    return vectors, waited


def chat(prompt, model=None, temperature=0.2, max_tokens=400, session=None):
    """One chat completion under the flock, pinned model verified."""
    model = model or CHAT_MODEL
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    resp, waited = locked_post(CHAT_URL, payload, session=session)
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
    body = resp.json()
    _check_model(body.get("model"), model)
    content = (body.get("choices") or [{}])[0].get("message", {}).get("content")
    if not content or not content.strip():
        raise RuntimeError("empty response (check thinking-off in the model's "
                           "LM Studio load config)")
    return content, waited


# --- the corpus, and its strings ------------------------------------------

FIELDS = ("concepts", "topics")


def load_rows(index_path=None):
    """The site's corpus rows — same filters, same population, same index."""
    return corpus.load_corpus(Path(index_path or INDEX_PATH)).rows


class Inventory:
    """Every distinct free-text string, and the ARTICLES that carry it.

    ``articles[s]`` is a set of DataFrame row ids, never a mention count: an
    article that names "AI" five times is one article's worth of attention.
    Every coverage number in this phase is computed from these sets, which is
    why the class hands out sets rather than integers.
    """

    def __init__(self, rows, fields=FIELDS):
        self.n_articles = int(len(rows))
        self.fields = tuple(f for f in fields if f in rows.columns)
        self.articles = {}
        self.field_of = {}
        # Kept per field as well as pooled. A vocabulary derived over
        # concepts ∪ topics is scored one way; the two index columns Phase C
        # actually builds are scored another, and the 40% rankability bar is
        # defined per column. Reporting only the pooled number flatters the
        # result — measured here, pooled top-20 is 43.6% while the same
        # clusters score 32.2% and 34.9% against the individual columns.
        self.by_field = {field: {} for field in self.fields}
        for field in self.fields:
            per_field = self.by_field[field]
            for row_id, value in zip(rows.index, rows[field]):
                for name in set(corpus.as_list(value)):
                    self.articles.setdefault(name, set()).add(row_id)
                    per_field.setdefault(name, set()).add(row_id)
                    self.field_of.setdefault(name, set()).add(field)
        # Sorted so every downstream stage sees the same order for the same
        # corpus — the clustering's determinism guarantee starts here.
        self.strings = sorted(self.articles)
        # Article display metadata, pulled once by column rather than a
        # `.loc` per article. The gate asks for examples on ~250 clusters
        # covering thousands of articles each; row-at-a-time `.loc` made that
        # tens of seconds of pure pandas overhead.
        self.meta = {}
        for row_id, title, url, words, year in zip(
                rows.index,
                self._column(rows, "title"), self._column(rows, "url"),
                self._column(rows, "word_count"), self._column(rows, "year")):
            self.meta[row_id] = {
                "title": str(title or "").strip(),
                "url": str(url or ""),
                # NaN is the common case for word_count, and NaN != NaN.
                "words": int(words) if words is not None and words == words else 0,
                "year": int(year) if year is not None and year == year else 0,
            }

    @staticmethod
    def _column(rows, name):
        return rows[name] if name in rows.columns else [None] * len(rows)

    def __len__(self):
        return len(self.strings)

    def count(self, name):
        return len(self.articles.get(name, ()))

    def coverage_of(self, names):
        """Share of articles carrying at least one of ``names``, as a %.

        A set union, not a sum. Summing per-string counts double-counts every
        article tagged with two members and turns a real 25% into a reported
        80%; that inflation is the exact bug ``corpus.head_coverage`` exists
        to avoid, and it is avoided here the same way.
        """
        if not self.n_articles:
            return 0.0
        hit = set()
        for name in names:
            hit |= self.articles.get(name, set())
        return round(len(hit) / self.n_articles * 100, 1)

    def article_set(self, names, field=None):
        """Articles carrying any of ``names``; restricted to one field if given."""
        source = self.by_field[field] if field else self.articles
        hit = set()
        for name in names:
            hit |= source.get(name, set())
        return hit


def example_articles(inventory, members, k=3):
    """The ``k`` most on-topic articles for a cluster, deterministically.

    "Most on-topic" is how many of the cluster's OWN member strings an article
    carries — an article tagged with four of them is a better illustration of
    what the cluster means than one tagged with a single fringe alias. Ties
    break on length then title, so the same cluster always shows the same
    examples and the naming prompt is reproducible.
    """
    members = set(members)
    per_article = {}
    for name in members:
        for row_id in inventory.articles.get(name, ()):
            per_article[row_id] = per_article.get(row_id, 0) + 1

    scored = []
    for row_id, carried in per_article.items():
        meta = inventory.meta.get(row_id)
        if not meta or not meta["title"]:
            continue
        scored.append((-carried, -meta["words"], meta["title"], meta["url"],
                       meta["year"]))
    scored.sort()
    return [{"title": t, "url": u, "year": y} for _, _, t, u, y in scored[:k]]
