#!/usr/bin/env python3
"""Phase A step 1 — embed every distinct concept/topic string, resumably.

    .venv/bin/python scripts/vocab/embed.py [--batch-size 512] [--limit N]

The cache this writes is the expensive artifact of the whole phase (~73k
strings, ~15 GPU-minutes) and everything downstream is cheap by comparison, so
the design priority is not throughput — it is that a killed run resumes rather
than restarts.

How that is achieved:

  * The cache is APPEND-ONLY SHARDS, one per batch, each written to a temp
    file and ``os.replace``d into place. A kill mid-write leaves a stray
    ``.tmp`` (ignored on load), never a half-written shard; a kill between
    writes loses at most one batch.
  * Resume is set subtraction, not a cursor: whatever keys the shards already
    hold are skipped. The work list is therefore correct even if shards were
    written out of order, partially deleted, or produced by an earlier run
    over a smaller corpus.
  * A manifest records the model, prefix and dimensionality. A cache built by
    a different embedding model is refused rather than silently extended — a
    half-nomic, half-gemma cache would cluster into garbage that looks fine.

The strings are the union of the two fields (concepts ∪ topics): 6,808 strings
appear in both, and embedding them once is both cheaper and necessary if the
gate is going to be able to see that the two axes overlap.

Fleet contract: the shared flock is taken around EACH batch request and
released before the next, so the nightly enrichment leg interleaves instead of
starving. See ``common.locked_post``.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vocab import common  # noqa: E402

SHARD_GLOB = "shard-*.npz"
MANIFEST_NAME = "manifest.json"


def shard_dir(data_dir):
    return Path(data_dir) / "embeddings"


def load_cache(data_dir):
    """Every embedded (key, vector) on disk. Returns ``(keys, vectors)``.

    Unreadable shards are reported and skipped rather than fatal: a corrupt
    shard costs its own batch on the next run, and refusing to start would
    cost the entire cache.
    """
    d = shard_dir(data_dir)
    keys, vecs = [], []
    if not d.is_dir():
        return np.array([], dtype=str), np.zeros((0, 0), dtype=np.float32)
    for path in sorted(d.glob(SHARD_GLOB)):
        try:
            # allow_pickle stays off — the shards hold a unicode array and a
            # float array, and a cache file should never be able to execute.
            with np.load(path, allow_pickle=False) as z:
                k, v = z["keys"], z["vectors"]
            if len(k) != len(v):
                raise ValueError(f"{len(k)} keys vs {len(v)} vectors")
        except Exception as exc:  # noqa: BLE001 - any unreadable shard
            print(f"  ! skipping unreadable shard {path.name}: {exc}",
                  file=sys.stderr)
            continue
        keys.append(np.asarray(k, dtype=str))
        vecs.append(np.asarray(v, dtype=np.float32))
    if not keys:
        return np.array([], dtype=str), np.zeros((0, 0), dtype=np.float32)
    all_keys = np.concatenate(keys)
    all_vecs = np.vstack(vecs)
    # Shards can overlap if a run was killed after the request but before the
    # write and then re-embedded the batch. First occurrence wins; the vectors
    # are identical anyway (the endpoint is deterministic for a fixed model).
    _, first = np.unique(all_keys, return_index=True)
    first.sort()
    return all_keys[first], all_vecs[first]


def _shard_index(path):
    try:
        return int(path.stem.rsplit("-", 1)[-1])
    except ValueError:
        return None


def _next_shard_index(d):
    seen = [i for i in (_shard_index(p) for p in d.glob(SHARD_GLOB))
            if i is not None]
    return max(seen) + 1 if seen else 0


def write_shard(data_dir, index, keys, vectors):
    """One checkpoint, atomically. Returns the shard path.

    The temp name is dot-prefixed rather than ``.npz.tmp`` suffixed for two
    reasons that both bit on first run: ``np.savez`` force-appends ``.npz`` to
    any name lacking it, and a ``shard-N.npz.tmp.npz`` matches SHARD_GLOB — so
    a half-written checkpoint would have been loaded as a real one.
    """
    d = shard_dir(data_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"shard-{index:05d}.npz"
    tmp = d / f".tmp-shard-{index:05d}.npz"
    np.savez(tmp, keys=np.asarray(keys, dtype=str),
             vectors=np.asarray(vectors, dtype=np.float32))
    os.replace(tmp, path)
    return path


def read_manifest(data_dir):
    path = Path(data_dir) / MANIFEST_NAME
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_manifest(data_dir, model, prefix, dim, n_cached):
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    (Path(data_dir) / MANIFEST_NAME).write_text(json.dumps({
        "model": model, "prefix": prefix, "dim": int(dim),
        "cached": int(n_cached), "updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }, indent=2), encoding="utf-8")


def check_manifest(data_dir, model, prefix):
    """Refuse to extend a cache that a different model or prefix produced."""
    m = read_manifest(data_dir)
    if not m:
        return
    if m.get("model") != model or m.get("prefix", "") != prefix:
        raise SystemExit(
            f"cache at {data_dir} was built with model={m.get('model')!r} "
            f"prefix={m.get('prefix','')!r}, this run wants model={model!r} "
            f"prefix={prefix!r}.\nMixing embedding spaces produces clusters "
            "that look plausible and mean nothing. Point --data-dir somewhere "
            "else, or delete the cache to rebuild it.")


def pending(inventory_strings, cached_keys):
    """Strings still to embed, in the inventory's (sorted) order."""
    have = set(cached_keys.tolist())
    return [s for s in inventory_strings if s not in have]


def run(data_dir, strings, batch_size, session=None, progress=print):
    """Embed everything in ``strings`` that is not already cached."""
    check_manifest(data_dir, common.EMBED_MODEL, common.EMBED_PREFIX)
    cached_keys, cached_vecs = load_cache(data_dir)
    todo = pending(strings, cached_keys)
    progress(f"{len(strings):,} distinct strings; {len(cached_keys):,} cached; "
             f"{len(todo):,} to embed (batch={batch_size})")
    if not todo:
        if len(cached_vecs):
            write_manifest(data_dir, common.EMBED_MODEL, common.EMBED_PREFIX,
                           cached_vecs.shape[1], len(cached_keys))
        return 0

    shard_i = _next_shard_index(shard_dir(data_dir))
    done = 0
    lock_wait = 0.0
    started = time.time()
    dim = cached_vecs.shape[1] if len(cached_vecs) else 0
    for offset in range(0, len(todo), batch_size):
        batch = todo[offset:offset + batch_size]
        try:
            vectors, waited = common.embed_batch(batch, session=session)
        except (requests.ConnectionError, requests.Timeout) as exc:
            raise SystemExit(
                f"LM Studio unreachable at {common.EMBED_URL}: {exc}\n"
                f"{done:,} embedded this run and CHECKPOINTED — re-run to "
                "resume from the cache.") from exc
        if dim and vectors.shape[1] != dim:
            raise SystemExit(
                f"dimensionality changed mid-run: cache is {dim}-d, this "
                f"batch came back {vectors.shape[1]}-d. Cache left intact.")
        dim = vectors.shape[1]
        write_shard(data_dir, shard_i, batch, vectors)
        write_manifest(data_dir, common.EMBED_MODEL, common.EMBED_PREFIX, dim,
                       len(cached_keys) + done + len(batch))
        shard_i += 1
        done += len(batch)
        lock_wait += waited
        elapsed = time.time() - started
        rate = done / elapsed if elapsed else 0
        eta = (len(todo) - done) / rate / 60 if rate else 0
        progress(f"  {done:,}/{len(todo):,}  {rate:.0f} str/s  "
                 f"lock-wait {lock_wait / 60:.1f}m  eta {eta:.1f}m")
    progress(f"embedded {done:,} in {(time.time() - started) / 60:.1f} min "
             f"({lock_wait / 60:.1f} min of that waiting for the fleet lock)")
    return done


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data-dir", default=str(common.DEFAULT_DATA_DIR))
    ap.add_argument("--index", default=str(common.INDEX_PATH))
    ap.add_argument("--batch-size", type=int, default=512,
                    help="512 holds the fleet lock ~5s per batch at ~110 "
                         "str/s; 2048 is ~15%% faster and holds it ~16s.")
    ap.add_argument("--limit", type=int, default=None,
                    help="embed only the first N pending strings (smoke test)")
    args = ap.parse_args(argv)

    rows = common.load_rows(args.index)
    inv = common.Inventory(rows)
    print(f"corpus: {inv.n_articles:,} articles, fields {inv.fields}")
    strings = inv.strings
    if args.limit:
        cached, _ = load_cache(args.data_dir)
        strings = pending(strings, cached)[:args.limit]
    run(args.data_dir, strings, args.batch_size)
    keys, vecs = load_cache(args.data_dir)
    print(f"cache: {len(keys):,} vectors x {vecs.shape[1] if len(vecs) else 0}d "
          f"at {shard_dir(args.data_dir)}")


if __name__ == "__main__":
    main()
