#!/usr/bin/env python3
"""The public shape of the unread corpus, for data.adamthede.com.

Writes the record's data layer and refuses to publish anything the leak scan
can find a title or a URL path in. The record's own page design is a later lane
and does not start before Adam has approved a mockup.

    python3 scripts/core/publish_unread_record.py --out records/meant-to-read
    python3 scripts/core/publish_unread_record.py --out /tmp/check --needles data/unread_needles.txt

This record is a PAIRED one: almost every plate on it draws the unread queue
against what was actually read, so the build reads `data/archive_index.parquet`
as well as the enriched queue. That file is required by default and the build
refuses without it, because a payload missing the read series produces a page
with two plates that cannot be drawn and nothing saying why. `--no-comparison`
is the deliberate way to build the unpaired half.

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


def read_corpus(index_path):
    """The read side of every paired plate: counts by year, topics, titles.

    pandas is imported here rather than at module scope so `--no-comparison`
    runs in an environment that does not have it. The titles are not leak
    needles; they are what the topic redaction checks the READ column against.
    """
    import pandas as pd

    frame = pd.read_parquet(index_path)
    return (analysis.read_corpus_by_year(frame),
            analysis.read_corpus_topics(frame),
            analysis.read_corpus_titles(frame))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--enriched", default=str(DATA / "unread_enriched.jsonl"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--needles", default=None,
                    help="write this corpus's leak needles here, beside the build")
    ap.add_argument("--shortlist", type=int, default=25,
                    help="private shortlist page size; the PUBLIC count is the "
                         "number of pieces that qualified, not this")
    ap.add_argument("--index", default=str(DATA / "archive_index.parquet"),
                    help="the read corpus, for the paired plates")
    ap.add_argument("--no-comparison", action="store_true",
                    help="build without the read series. The record's first "
                         "two plates cannot be drawn from the result.")
    args = ap.parse_args(argv)

    records = enrich.load_records(args.enriched)
    if not records:
        print(f"Nothing enriched at {args.enriched}.", file=sys.stderr)
        return 2

    by_year = topics = None
    titles = ()
    if not args.no_comparison:
        # Refuse rather than warn. A payload silently missing the read series
        # publishes a record whose first two plates are empty, and the build
        # that made it reported success.
        if not Path(args.index).exists():
            print(f"No read corpus at {args.index}. Two of this record's six "
                  f"plates draw the unread queue against what was read, and "
                  f"they cannot be built without it. Pass --index, or "
                  f"--no-comparison to build the unpaired half on purpose.",
                  file=sys.stderr)
            return 2
        by_year, topics, titles = read_corpus(args.index)

    # What qualified, not what one page of it holds: publishing
    # len(shortlist) would publish the --shortlist default rather than a
    # measurement.
    count = analysis.shortlist_eligible_count(records)

    try:
        result = public.build(records, args.out, needle_file=args.needles,
                              shortlist_count=count, read_by_year=by_year,
                              read_topics=topics, read_titles=titles)
    except public.LeakTestError as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        print("Nothing was published.", file=sys.stderr)
        return 1

    print(f"Public record: {result['items']} items -> {result['out']}/")
    print(f"Leak scan clean over {result['titles']:,} titles and "
          f"{result['paths']:,} URL paths.")
    if by_year:
        print(f"Paired against {sum(by_year.values()):,} read-it-later "
              f"articles across {len(by_year)} years.")
    else:
        print("Built WITHOUT the read series: plates 01 and 02 cannot be drawn.")
    if args.needles:
        print(f"Needles for data.adamthede.com -> {args.needles} (never publish this)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
