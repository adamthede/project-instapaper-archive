#!/usr/bin/env python3
"""Local-inference enrichment via LM Studio (Qwen) — the nightly-trickle variant.

Same prompt, parser, and file-writer as enrich_archive_gemini.py (imported, so
the two backends cannot drift). What differs is everything the fleet contract
demands of a local pipeline:

  * The shared flock (~/.cache/tractor-silo/lmstudio-digest.lock) is acquired
    around EACH inference call and released between articles — never held
    across the whole batch. (Duplicated inline by design, like the
    inbox-observatory copy, so this repo has no cross-repo import.)
  * The model is PINNED. The request names it, and the response's `model`
    field is checked against it; a silent Qwen→Gemma swap aborts the run
    rather than enriching the corpus with a different model's judgment.
  * Sequential, one request in flight — bounded-concurrency rule.

Division of labor: Gemini remains the bulk-backfill tool (fast, parallel,
pennies). This is for the nightly trickle — a handful of new Matter articles a
day, zero cloud dependency. Run it under the repo venv (pandas + frontmatter):

    .venv/bin/python scripts/core/enrich_archive_local.py [limit] [dry-run]

Gotcha (fleet memory 2026-06-28): Qwen thinking-off is a LOAD-TIME setting in
LM Studio; with thinking ON the content channel can come back empty. If every
article fails with "empty response", fix the model's load config, not this
script.
"""
import fcntl
import json
import os
import sys
import time
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from enrich_archive_gemini import (  # noqa: E402
    FAILURE_LOG, INDEX_PATH, build_prompt, log_failure,
    parse_llm_response, update_markdown_file,
)

LMSTUDIO_URL = os.getenv("LMSTUDIO_URL", "http://localhost:1234/v1/chat/completions")
# Pinned per the routing contract: never "whatever's loaded".
PINNED_MODEL = os.getenv("LMSTUDIO_MODEL", "qwen3.6-35b-a3b")
LOCK_PATH = Path.home() / ".cache" / "tractor-silo" / "lmstudio-digest.lock"
REQUEST_TIMEOUT = 300  # a 10k-char article on a busy machine can be slow


class ModelMismatch(RuntimeError):
    pass


def _locked_completion(prompt):
    """One inference under the shared flock. BSD flock on the byte-identical
    fleet path — POSIX lockf would not interoperate with the other holders."""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK_PATH, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            resp = requests.post(LMSTUDIO_URL, timeout=REQUEST_TIMEOUT, json={
                "model": PINNED_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
                "max_tokens": 800,
                "stream": False,
            })
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
    resp.raise_for_status()
    body = resp.json()
    served = body.get("model", "")
    if PINNED_MODEL.lower() not in served.lower():
        raise ModelMismatch(
            f"asked for {PINNED_MODEL!r}, LM Studio served {served!r} — "
            "refusing to enrich with a swapped model. Load the pinned model "
            "or set LMSTUDIO_MODEL to what you actually intend.")
    return body["choices"][0]["message"]["content"]


def needs_enrichment(row):
    def empty(v):
        if v is None:
            return True
        if isinstance(v, (list, tuple)) or hasattr(v, "__len__") and not isinstance(v, str):
            return len(v) == 0
        return not str(v).strip() or str(v) == "nan"
    if bool(row.get("content_corrupted")):
        return False
    return empty(row.get("topics")) and empty(row.get("summary"))


def main():
    limit = None
    dry_run = False
    min_words = 0
    for arg in sys.argv[1:]:
        if arg.isdigit():
            limit = int(arg)
        elif arg == "dry-run":
            dry_run = True
        elif arg.startswith("min-words="):
            min_words = int(arg.split("=", 1)[1])

    if not INDEX_PATH.exists():
        sys.exit(f"Index not found: {INDEX_PATH} — run build_index.py first.")
    df = pd.read_parquet(INDEX_PATH)
    candidates = df[df.apply(needs_enrichment, axis=1)]
    if min_words:
        # Skip the known-empty legacy rows; only bodies worth reading.
        candidates = candidates[candidates["word_count"].fillna(0) >= min_words]
    if limit:
        candidates = candidates.head(limit)
    print(f"{len(candidates)} article(s) need enrichment "
          f"(model={PINNED_MODEL}, url={LMSTUDIO_URL})")
    if dry_run or candidates.empty:
        return

    ok = failed = 0
    started = time.time()
    for _, row in candidates.iterrows():
        title = row.get("title") or Path(str(row["file_path"])).stem
        try:
            with open(row["file_path"], "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            if not content.strip():
                raise ValueError("Empty content")
            answer = _locked_completion(build_prompt(content))
            if not answer or not answer.strip():
                raise ValueError("empty response (check thinking-off in the "
                                 "model's LM Studio load config)")
            data = parse_llm_response(answer)
            if not data or not update_markdown_file(row["file_path"], data):
                raise ValueError("parse or file-write failure")
            ok += 1
            print(f"  ok    {title}")
        except ModelMismatch:
            raise  # abort the whole run — every subsequent call would mismatch too
        except (requests.ConnectionError, requests.Timeout) as e:
            sys.exit(f"LM Studio unreachable at {LMSTUDIO_URL}: {e}\n"
                     f"({ok} enriched before this; re-run to continue.)")
        except Exception as e:
            failed += 1
            log_failure(str(title), str(row["file_path"]), f"local: {e}", FAILURE_LOG)
            print(f"  FAIL  {title}: {e}")

    mins = (time.time() - started) / 60
    print(f"\nDone: {ok} enriched, {failed} failed in {mins:.1f} min.")
    if ok:
        print("Re-run build_index.py so the dashboard sees the new fields.")


if __name__ == "__main__":
    main()
