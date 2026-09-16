#!/usr/bin/env python3
"""The unread analysis: the aggregates, the drift, and the private shortlist.

Reads the enriched corpus and the read corpus's index and writes one private
report. Everything here carries titles and URLs; it is for
reading.adamthede.com, behind Cloudflare Access, and never for the public
record.

    python3 scripts/core/analyze_unread_corpus.py
    python3 scripts/core/analyze_unread_corpus.py --shortlist 40

Exit codes: 0 done, 2 could not start (nothing enriched yet).
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from unread import analysis  # noqa: E402
from unread import enrich  # noqa: E402

DATA = REPO_ROOT / "data"


def live_index(explicit=None):
    """The read corpus's Parquet, wherever this checkout can see one.

    `data/` is gitignored, so the index exists in the main checkout and not in
    a worktree - and this work was built in a worktree. Same fallback
    `tests/test_public_shape.py` uses.
    """
    if explicit:
        return Path(explicit)
    here = DATA / "archive_index.parquet"
    if here.exists():
        return here
    try:
        common = subprocess.run(["git", "rev-parse", "--git-common-dir"],
                                capture_output=True, text=True,
                                cwd=str(REPO_ROOT)).stdout.strip()
        if common:
            candidate = Path(common).resolve().parent / "data" / "archive_index.parquet"
            if candidate.exists():
                return candidate
    except OSError:
        pass
    return here


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--enriched", default=str(DATA / "unread_enriched.jsonl"))
    ap.add_argument("--index", default=None)
    ap.add_argument("--out", default=str(DATA / "unread_analysis.json"))
    ap.add_argument("--shortlist", type=int, default=25)
    args = ap.parse_args(argv)

    records = enrich.load_records(args.enriched)
    if not records:
        print(f"Nothing enriched at {args.enriched}. Run enrich_unread_corpus.py first.",
              file=sys.stderr)
        return 2

    index = live_index(args.index)
    read_topics, read_frame = {}, None
    if index.exists():
        import pandas as pd
        read_frame = pd.read_parquet(
            index, columns=["source", "date_saved", "topics", "content_corrupted", "url"])
        read_topics = analysis.read_corpus_topics(read_frame)
        print(f"Read corpus: {len(read_frame):,} rows from {index}, "
              f"{sum(sum(c.values()) for c in read_topics.values()):,} topic mentions "
              f"across {len(read_topics)} years.")
    else:
        print(f"No read corpus index at {index}; drift and the source comparison "
              f"will be empty.", file=sys.stderr)

    report = analysis.build(records, read_topics=read_topics, read_frame=read_frame,
                            shortlist_limit=args.shortlist)

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, indent=2, ensure_ascii=False),
                              encoding="utf-8")

    print(f"\n{report['items']} items enriched.")
    print(f"  survival     live web {report['survival']['live_web']}, "
          f"archived only {report['survival']['archived_only']}, "
          f"dead {report['survival']['dead']}")
    why = report["why_saved"]
    print(f"  why_saved    {why['counted']} counted, "
          f"{why['excluded_low_confidence']} excluded (low confidence), "
          f"{why['no_inference']} no inference")
    print(f"  peak drift   {report['peak_drift_year']}")
    print(f"  shortlist    {len(report['shortlist'])} (private)")
    print(f"\nPrivate report -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
