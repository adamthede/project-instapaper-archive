#!/usr/bin/env python3
"""Build the unread corpus: the listing, then the four-path fetch chain.

The meant-to-read pool is the whole live unread folder plus the folder items
that were organized but never opened - 492 items on 2026-09-15. This is the
only stage that touches the network.

    # What the listing sees, without fetching any text
    python3 scripts/core/fetch_unread_corpus.py --list-only

    # The full first pass (about 25 minutes, almost all of it waiting)
    python3 scripts/core/fetch_unread_corpus.py

    # A slice, to check the chain before committing to the whole pool
    python3 scripts/core/fetch_unread_corpus.py --limit 25

Idempotent and resumable. The queue at `data/unread_queue.jsonl` is keyed by
URL hash and written after every item, so a second run re-fetches nothing and
an interrupted run costs one item.

Exit codes: 0 done, 1 finished with items that resolved nowhere, 2 could not
start (no credentials).
"""
import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from unread import instapaper as ip  # noqa: E402
from unread import queue as queue_mod  # noqa: E402
from unread import resolve as rs  # noqa: E402

DATA = REPO_ROOT / "data"
QUEUE_PATH = DATA / "unread_queue.jsonl"
BODIES_DIR = DATA / "unread_bodies"
FAILURE_LOG = DATA / "unread_fetch_failures.log"


def report_line(index, total, row, result):
    mark = "ok " if result.ok else "-- "
    print(f"  {mark}{index:>4}/{total}  {result.path:<10} {row.get('url', '')[:72]}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--queue", default=str(QUEUE_PATH))
    ap.add_argument("--bodies", default=str(BODIES_DIR))
    ap.add_argument("--failure-log", default=str(FAILURE_LOG))
    ap.add_argument("--limit", type=int, default=None,
                    help="resolve at most this many pending items")
    ap.add_argument("--recheck-leg", default=None,
                    choices=["instapaper", "direct", "wayback", "metadata"],
                    help="put every row this leg settled back on the queue "
                         "before fetching, for when its acceptance rule changed")
    ap.add_argument("--list-only", action="store_true",
                    help="refresh the queue from the API and stop")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    try:
        client = ip.InstapaperClient.from_env()
    except ip.InstapaperError as exc:
        print(f"Cannot start: {exc}", file=sys.stderr)
        return 2

    rows, report = ip.fetch_pool(client)
    print(f"Listing: {report['unread']} unread, {report['folder_items']} folder items "
          f"({report['folder_never_opened']} never opened) -> pool {report['pool']}")
    for title, count in sorted(report["folders"].items()):
        print(f"    folder {title}: {count}")

    queue = queue_mod.Queue(args.queue)
    added = queue.upsert(rows)
    print(f"Queue: {len(queue)} rows ({added} new), {len(queue.pending())} pending "
          f"-> {args.queue}")

    if args.recheck_leg:
        reopened = rs.reopen_leg(queue, args.recheck_leg)
        print(f"Recheck: reopened {reopened} row(s) settled by the "
              f"{args.recheck_leg} leg; {len(queue.pending())} now pending.")

    if args.list_only:
        return 0

    counts = rs.run(queue, instapaper=client,
                    http=_session(), bodies_dir=args.bodies,
                    failure_log=args.failure_log, limit=args.limit,
                    progress=None if args.quiet else report_line)

    print("\nResolved this pass:")
    for leg in (rs.INSTAPAPER, rs.DIRECT, rs.WAYBACK, rs.METADATA):
        print(f"    {leg:<11} {counts.get(leg, 0)}")
    print(f"    {'total':<11} {counts['total']}")

    settled = [r for r in queue.rows() if r["outcome"] != queue_mod.PENDING]
    dead = [r for r in settled if r["resolve_path"] == rs.METADATA]
    print(f"\nCorpus: {len(settled)} settled of {len(queue)}, {len(dead)} on metadata alone.")
    return 1 if dead else 0


def _session():
    import requests
    return requests.Session()


if __name__ == "__main__":
    sys.exit(main())
