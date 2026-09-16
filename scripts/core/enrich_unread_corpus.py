#!/usr/bin/env python3
"""Enrich the unread corpus: the base schema plus the unread template.

Runs on Gemini 2.5 Flash-Lite through the project's existing plumbing, on whole
articles rather than their first 10,000 characters. Resumable: every completed
article is appended to `data/unread_enriched.jsonl` before the next one starts.

    # What it would spend, without spending it
    python3 scripts/core/enrich_unread_corpus.py --dry-run

    # The gate: measure the cost per article on a slice first
    python3 scripts/core/enrich_unread_corpus.py --limit 25

    # The rest
    python3 scripts/core/enrich_unread_corpus.py

Exit codes: 0 done, 1 finished with records that failed validation, 2 could not
start (no API key, or nothing fetched yet).
"""
import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from unread import enrich  # noqa: E402
from unread import queue as queue_mod  # noqa: E402

DATA = REPO_ROOT / "data"


def line(index, total, row, record, ledger):
    why = "-" if not record["why_saved"] else record["why_saved_confidence"]
    print(f"  {index:>4}/{total}  {record['resolve_path']:<10} "
          f"aged_out={str(record['aged_out']):<5} why={why:<6} "
          f"${ledger.usd:.4f}  {row.get('url', '')[:56]}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--queue", default=str(DATA / "unread_queue.jsonl"))
    ap.add_argument("--bodies", default=str(DATA / "unread_bodies"))
    ap.add_argument("--out", default=str(DATA / "unread_enriched.jsonl"))
    ap.add_argument("--failure-log", default=str(DATA / "unread_enrich_failures.log"))
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    if not Path(args.queue).exists():
        print(f"No queue at {args.queue}. Run fetch_unread_corpus.py first.",
              file=sys.stderr)
        return 2

    queue = queue_mod.Queue(args.queue)

    model = None
    if not args.dry_run:
        try:
            model = enrich.GeminiModel()
        except Exception as exc:  # noqa: BLE001
            print(f"Cannot start: {exc}", file=sys.stderr)
            return 2

    summary = enrich.run(queue, bodies_dir=args.bodies, out_path=args.out,
                         model=model, limit=args.limit, dry_run=args.dry_run,
                         failure_log=args.failure_log,
                         progress=None if (args.quiet or args.dry_run) else line)

    if args.dry_run:
        print(f"Dry run: {summary['candidates']} articles would be enriched on "
              f"{summary['model']}. Nothing sent, nothing spent.")
        return 0

    print(f"\nEnriched {summary['enriched']} of {summary['candidates']} on "
          f"{summary['model']}.")
    print(f"  input tokens   {summary['input_tokens']:,}")
    print(f"  output tokens  {summary['output_tokens']:,}")
    print(f"  cost           ${summary['usd']:.4f} "
          f"(${summary['usd_per_article']:.5f} per article)")
    if summary.get("invalid"):
        print(f"  invalid        {summary['invalid']} (see {args.failure_log})")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
