#!/usr/bin/env python3
"""The public shape of the unread corpus, for data.adamthede.com.

Writes the record's data layer and refuses to publish anything the leak scan
can find a title or a URL path in. The record's own page design is a later lane
and does not start before Adam has approved a mockup.

    python3 scripts/core/publish_unread_record.py --out records/meant-to-read
    python3 scripts/core/publish_unread_record.py --out /tmp/check --needles data/unread_needles.txt

The needle file is this corpus's contribution to data.adamthede.com's own
`data/private_strings.txt`, which is gitignored and hand-assembled. It is never
written inside the published record, and the build refuses if you ask it to be.

Exit codes: 0 published, 1 the scan found something (nothing published), 2
could not start.
"""
import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from unread import analysis  # noqa: E402
from unread import enrich  # noqa: E402
from unread import public  # noqa: E402

DATA = REPO_ROOT / "data"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--enriched", default=str(DATA / "unread_enriched.jsonl"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--needles", default=None,
                    help="write this corpus's leak needles here, beside the build")
    ap.add_argument("--shortlist", type=int, default=25,
                    help="the shortlist length; only its COUNT is published")
    args = ap.parse_args(argv)

    records = enrich.load_records(args.enriched)
    if not records:
        print(f"Nothing enriched at {args.enriched}.", file=sys.stderr)
        return 2

    count = len(analysis.shortlist(records, {}, limit=args.shortlist))

    try:
        result = public.build(records, args.out, needle_file=args.needles,
                              shortlist_count=count)
    except public.LeakTestError as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        print("Nothing was published.", file=sys.stderr)
        return 1

    print(f"Public record: {result['items']} items -> {result['out']}/")
    print(f"Leak scan clean over {result['titles']:,} titles and "
          f"{result['paths']:,} URL paths.")
    if args.needles:
        print(f"Needles for data.adamthede.com -> {args.needles} (never publish this)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
