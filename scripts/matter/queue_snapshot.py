#!/usr/bin/env python3
"""A nightly snapshot of the Matter reading queue: how big is the backlog?

The Matter sync (export_matter_to_archive.py) is a record of what Adam has
READ. This is the other half: a dated ledger of what he has SAVED and not yet
read, so the backlog's size, inflow and outflow can be measured over time
instead of only ever glimpsed once, live, in the Matter app.

Two files, deliberately different shapes:

    data/matter/queue_snapshots.jsonl  the ledger -- one JSON line per run,
                                        item-level detail (id, word_count,
                                        reading_progress, site, updated_at),
                                        NEVER a title or a URL. Those identify
                                        a specific saved-not-read article, which
                                        is a different kind of private than a
                                        read one: Reading's public record can
                                        show titles because a read article is a
                                        finished act (see docs/MATTER_SYNC.md
                                        and the unread-corpus plan's public-
                                        shape rules for the fuller argument).
                                        Titles and URLs stay in the archive
                                        vault, where they already live.

    data/matter/queue_daily.csv        one row per CALENDAR DATE (UTC), always
                                        the most recently observed state for
                                        that date. A second run on the same
                                        date REPLACES that date's row; the
                                        ledger keeps every run and marks the
                                        replacing one `superseding: true`, so
                                        the full history of same-day reruns is
                                        still recoverable from the JSONL even
                                        though the CSV only ever shows one row
                                        per day.

Inflow/outflow (`new_ids_since_previous` / `gone_ids_since_previous`) are a set
diff against the last snapshot from an EARLIER date -- never against another
run from today, so rerunning the snapshot twice in one day reports the same
numbers both times rather than drifting. On the very first run ever, both are
`None` (written as an empty CSV cell), not 0: there is nothing to diff against,
and 0 would falsely claim "no change" rather than "unknown."

Which statuses count as "queue"
--------------------------------
Per docs/MATTER_SYNC.md and scripts/matter/sync.py's own docstring, Matter's
full documented status enum is exactly three values:

    archive  -> read.  (What the nightly sync pulls. Not this ledger's job.)
    queue    -> saved, not read. THE unread backlog. This ledger's job.
    inbox    -> not even saved yet -- Matter's unsaved discovery feed. Not
                intent, so not backlog.

So `queue` is the only documented "saved but unread" status, and
EXTRA_UNREAD_STATUSES below is deliberately empty rather than guessed at.
`fetch_queue_items` still takes an `extra_statuses` parameter so a genuinely
new unread-like status (if Matter's API ever grows one) is one constant away
from being included, not a rewrite.

date_read backfill (--backfill-reads)
--------------------------------------
Outflow needs a "reads per week" series to compare the queue's drain against,
and the ledger itself has no history before tonight. `--backfill-reads` builds
that history from `data/archive_index.parquet` -- the SAME `date_archived`-
falling-back-to-`date_saved` derivation `build_index.py` bakes into that index
and the dashboard calls `date_read` (see docs/MATTER_SYNC.md, "How date_read
works"). Reading the index rather than re-deriving that fallback chain by
re-parsing 18,000+ files' frontmatter by hand keeps this script from carrying a
second, driftable copy of logic that already exists, tested, in one place; it
also avoids the vault scan's own documented cost (up to ~50 minutes cold over
SMB -- see "Which interpreter to run" in that same doc). This is why
`--backfill-reads` needs pandas/pyarrow (the venv interpreter), while the
nightly snapshot leg itself only needs the standard library plus `requests`,
matching the fleet's homebrew-python3 constraint for the nightly job.

Never logs or prints the Matter token. The client only ever sees it internally.
"""

# Dual-mode import: this file lives INSIDE the `matter` package, like its
# siblings (api.py, state.py, ...), which normally only ever get imported by a
# CLI entry point elsewhere (see scripts/core/export_matter_to_archive.py).
# This one is also meant to be launchd's ProgramArguments[1] directly -- run as
# a plain script, not a module -- so relative imports fail there
# ("attempted relative import with no known parent package"). Fall back to the
# same sys.path trick export_matter_to_archive.py uses.
try:
    from .api import MatterClient
    from .credentials import load_token, token_path
    from .state import atomic_write_text, to_iso, utcnow
except ImportError:
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
    from matter.api import MatterClient  # noqa: E402
    from matter.credentials import load_token, token_path  # noqa: E402
    from matter.state import atomic_write_text, to_iso, utcnow  # noqa: E402

import argparse
import csv
import io
import json
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("matter.queue_snapshot")

REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_LEDGER = REPO_ROOT / "data" / "matter" / "queue_snapshots.jsonl"
DEFAULT_DAILY_CSV = REPO_ROOT / "data" / "matter" / "queue_daily.csv"
DEFAULT_WEEKLY_READS_CSV = REPO_ROOT / "data" / "matter" / "reads_weekly.csv"
DEFAULT_PARQUET = REPO_ROOT / "data" / "archive_index.parquet"
DEFAULT_HEARTBEAT = Path("~/Library/Logs/MatterSync/queue-snapshot-heartbeat.json")
DEFAULT_2023_EXPORT = Path(
    "~/Downloads/Data Exports/2023 05-12 DATA - Matter Export/_matter_history.csv"
)
SEED_2023_DATE = "2023-05-12"

QUEUE_STATUS = "queue"
# See the module docstring: the documented enum is archive/queue/inbox, and
# only `queue` means "saved, not read." Empty on purpose -- not a guess.
EXTRA_UNREAD_STATUSES: tuple = ()

SOURCE_MATTER_API = "matter-api"
SOURCE_EXPORT_2023 = "export-2023"

DAILY_CSV_FIELDS = [
    "date", "count", "total_words", "started_not_finished_count",
    "new_ids_since_previous", "gone_ids_since_previous", "source",
]
WEEKLY_CSV_FIELDS = ["week", "reads", "words_read"]

ITEM_FIELDS = ("id", "word_count", "reading_progress", "site", "updated_at")


# ---- fetch ------------------------------------------------------------------

def fetch_queue_items(client, extra_statuses=EXTRA_UNREAD_STATUSES) -> list[dict]:
    """The full current queue, plus any extra unread-like statuses.

    A full listing every run, not an incremental delta -- unlike the read
    sync, this ledger has no watermark, because it wants to be a snapshot of
    the queue's current shape, not a stream of individual changes to it.
    """
    seen: dict[str, dict] = {}
    for status in (QUEUE_STATUS, *extra_statuses):
        for item in client.iter_items(status=status, page_size=100):
            item_id = item.get("id")
            if item_id and item_id not in seen:
                seen[item_id] = item
    return list(seen.values())


def compact_item(item: dict) -> dict:
    """The allowlisted subset that is safe to put in the ledger.

    Deliberately an allowlist, not a denylist that strips `title`/`url`: an
    allowlist can never leak a field nobody thought to strip (see
    command-center's own pattern_allowlist_over_denylist memory -- the same
    lesson, applied here before it needed to be relearned).
    """
    return {
        "id": item.get("id"),
        "word_count": item.get("word_count"),
        "reading_progress": item.get("reading_progress"),
        "site": item.get("site_name"),
        "updated_at": item.get("updated_at"),
    }


# ---- the ledger (JSONL) ------------------------------------------------------

def read_ledger_entries(ledger_path: Path) -> list[dict]:
    ledger_path = Path(ledger_path)
    if not ledger_path.exists():
        return []
    entries = []
    for line in ledger_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except ValueError:
            log.warning("Skipping unparseable ledger line in %s", ledger_path)
    return entries


def append_ledger_entry(ledger_path: Path, entry: dict) -> None:
    ledger_path = Path(ledger_path)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with open(ledger_path, "a", encoding="utf-8") as stream:
        stream.write(json.dumps(entry, ensure_ascii=False))
        stream.write("\n")
        stream.flush()
        import os
        os.fsync(stream.fileno())


def find_previous_snapshot(entries: list[dict], today: str) -> dict | None:
    """The most recent entry from a date earlier than `today`.

    Walking backward and skipping every entry that shares today's date is what
    makes same-day reruns idempotent: run 1 and run 2 both diff against the
    same prior-day baseline, never against each other.
    """
    for entry in reversed(entries):
        if entry.get("date") != today:
            return entry
    return None


def build_snapshot_entry(items: list[dict], now: datetime, *, superseding: bool) -> dict:
    compacted = [compact_item(i) for i in items]
    total_words = sum(i["word_count"] for i in compacted if i.get("word_count"))
    return {
        "taken_at": to_iso(now),
        "date": now.astimezone(timezone.utc).date().isoformat(),
        "count": len(compacted),
        "total_words": total_words,
        "items": compacted,
        "superseding": superseding,
    }


# ---- the daily summary (CSV) -------------------------------------------------

def compute_daily_row(entry: dict, previous: dict | None, *, source: str = SOURCE_MATTER_API) -> dict:
    ids = {i["id"] for i in entry["items"]}
    if previous is None:
        new_ids = None
        gone_ids = None
    else:
        prev_ids = {i["id"] for i in previous["items"]}
        new_ids = len(ids - prev_ids)
        gone_ids = len(prev_ids - ids)

    started_not_finished = sum(
        1 for i in entry["items"]
        if isinstance(i.get("reading_progress"), (int, float)) and 0 < i["reading_progress"] < 1
    )

    return {
        "date": entry["date"],
        "count": entry["count"],
        "total_words": entry["total_words"],
        "started_not_finished_count": started_not_finished,
        "new_ids_since_previous": new_ids,
        "gone_ids_since_previous": gone_ids,
        "source": source,
    }


def _csv_cell(value) -> str:
    return "" if value is None else str(value)


def read_daily_rows(csv_path: Path) -> dict[str, dict]:
    csv_path = Path(csv_path)
    if not csv_path.exists():
        return {}
    with open(csv_path, newline="", encoding="utf-8") as f:
        return {row["date"]: row for row in csv.DictReader(f)}


def write_daily_rows(csv_path: Path, rows_by_date: dict[str, dict]) -> None:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=DAILY_CSV_FIELDS)
    writer.writeheader()
    for d in sorted(rows_by_date):
        row = rows_by_date[d]
        writer.writerow({field: _csv_cell(row.get(field)) for field in DAILY_CSV_FIELDS})
    csv_path = Path(csv_path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(csv_path, buffer.getvalue(), create_parents=True)


def upsert_daily_row(csv_path: Path, row: dict) -> None:
    """Replace the row for `row['date']`, leaving every other date untouched."""
    rows = read_daily_rows(csv_path)
    rows[row["date"]] = {field: row.get(field) for field in DAILY_CSV_FIELDS}
    write_daily_rows(csv_path, rows)


# ---- orchestration ------------------------------------------------------------

def run_snapshot(client, *, ledger_path: Path = DEFAULT_LEDGER,
                  csv_path: Path = DEFAULT_DAILY_CSV, now: datetime | None = None) -> dict:
    now = now or utcnow()
    today = now.astimezone(timezone.utc).date().isoformat()

    prior_entries = read_ledger_entries(ledger_path)
    superseding = any(e.get("date") == today for e in prior_entries)
    previous = find_previous_snapshot(prior_entries, today)

    items = fetch_queue_items(client)
    entry = build_snapshot_entry(items, now, superseding=superseding)
    append_ledger_entry(ledger_path, entry)

    daily_row = compute_daily_row(entry, previous)
    upsert_daily_row(csv_path, daily_row)

    return {"entry": entry, "daily_row": daily_row}


# ---- heartbeat ----------------------------------------------------------------

def write_snapshot_heartbeat(path: Path, *, started_at: datetime, finished_at: datetime,
                              outcome: str, extra: dict | None = None) -> None:
    """The fleet-standard heartbeat shape (started_at/finished_at/outcome),
    matching what command-center's launchd_stats.py reads -- see
    scripts/matter/sync.py's write_heartbeat for the sibling job's version.
    """
    payload = {
        "started_at": to_iso(started_at),
        "finished_at": to_iso(finished_at),
        "outcome": outcome,
    }
    if extra:
        payload.update(extra)
    try:
        atomic_write_text(Path(path).expanduser(), json.dumps(payload, indent=2),
                           create_parents=True)
    except OSError as exc:
        log.warning("Could not write heartbeat to %s: %s", path, exc)


# ---- backfill: reads per week (outflow history) -------------------------------

def compute_weekly_reads(records: list[dict], *, now: date, years: int = 2) -> list[dict]:
    """Group (date_read, word_count) rows into an ISO-week reads/words series.

    `records` items are {"date_read": date | None, "word_count": int | None}.
    A row with no `date_read` is excluded, never counted as a zero-word read --
    the archive's own convention for "no read evidence" (see docs/MATTER_SYNC.md,
    "How date_read works").
    """
    since = now - timedelta(days=365 * years)
    buckets: dict[str, dict] = {}
    for record in records:
        read_on = record.get("date_read")
        if read_on is None:
            continue
        if not (since <= read_on <= now):
            continue
        iso_year, iso_week, _ = read_on.isocalendar()
        key = f"{iso_year}-W{iso_week:02d}"
        bucket = buckets.setdefault(key, {"week": key, "reads": 0, "words_read": 0})
        bucket["reads"] += 1
        bucket["words_read"] += record.get("word_count") or 0
    return [buckets[k] for k in sorted(buckets)]


def write_weekly_reads_csv(csv_path: Path, rows: list[dict]) -> None:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=WEEKLY_CSV_FIELDS)
    writer.writeheader()
    for row in rows:
        writer.writerow({field: row.get(field) for field in WEEKLY_CSV_FIELDS})
    csv_path = Path(csv_path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(csv_path, buffer.getvalue(), create_parents=True)


def backfill_reads(parquet_path: Path, csv_path: Path, *, now: date | None = None,
                    years: int = 2) -> list[dict]:
    """Build data/matter/reads_weekly.csv from the archive's own date_read.

    Needs pandas/pyarrow -- imported here, not at module scope, so the nightly
    snapshot leg (which runs on homebrew python3, no pandas) never pays for it.
    """
    import pandas as pd  # local import: see the module docstring

    now = now or utcnow().date()
    df = pd.read_parquet(parquet_path, columns=["date_saved", "date_archived", "word_count"])
    date_read = df["date_archived"].fillna(df["date_saved"])

    records = []
    for read_on, word_count in zip(date_read, df["word_count"]):
        if pd.isna(read_on):
            records.append({"date_read": None, "word_count": None})
            continue
        records.append({
            "date_read": read_on.date() if hasattr(read_on, "date") else read_on,
            "word_count": None if pd.isna(word_count) else int(word_count),
        })

    rows = compute_weekly_reads(records, now=now, years=years)
    write_weekly_reads_csv(csv_path, rows)
    return rows


# ---- seed: the May 2023 export's unread snapshot -------------------------------

def load_export_unread_totals(export_csv_path: Path) -> tuple[int, int]:
    """(count, total_words) of rows where Read == "False" in a Matter export CSV.

    Reads the export in place; never copies it (see the dispatch spec -- the
    export lives under ~/Downloads and stays there).
    """
    count = 0
    total_words = 0
    with open(export_csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if (row.get("Read") or "").strip() != "False":
                continue
            count += 1
            try:
                total_words += int((row.get("Word Count") or "0").strip() or 0)
            except ValueError:
                pass
    return count, total_words


def seed_2023(export_csv_path: Path, csv_path: Path, *, seed_date: str = SEED_2023_DATE) -> dict:
    count, total_words = load_export_unread_totals(export_csv_path)
    row = {
        "date": seed_date,
        "count": count,
        "total_words": total_words,
        # Not derivable from this export (no reading_progress column, no
        # relationship to any other snapshot): left blank, not guessed at.
        "started_not_finished_count": None,
        "new_ids_since_previous": None,
        "gone_ids_since_previous": None,
        "source": SOURCE_EXPORT_2023,
    }
    upsert_daily_row(csv_path, row)
    return row


# ---- CLI ------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="queue_snapshot.py",
        description="Nightly snapshot of the Matter reading queue backlog.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--backfill-reads", action="store_true",
                       help="Build data/matter/reads_weekly.csv from the archive's own "
                            "date_read history (last --years years). No API call.")
    mode.add_argument("--seed-2023", action="store_true",
                       help="Record the May 2023 export's unread count as one historical "
                            "row in queue_daily.csv. No API call.")

    parser.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    parser.add_argument("--csv", default=str(DEFAULT_DAILY_CSV))
    parser.add_argument("--reads-csv", default=str(DEFAULT_WEEKLY_READS_CSV))
    parser.add_argument("--parquet", default=str(DEFAULT_PARQUET))
    parser.add_argument("--export-csv", default=str(DEFAULT_2023_EXPORT))
    parser.add_argument("--years", type=int, default=2)
    parser.add_argument("--token-file", help="Override the credential path.")
    parser.add_argument("--heartbeat", default=str(DEFAULT_HEARTBEAT))
    parser.add_argument("--no-heartbeat", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-q", "--quiet", action="store_true")
    return parser


def _configure_logging(args) -> None:
    level = logging.DEBUG if args.verbose else logging.WARNING if args.quiet else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)-7s %(message)s",
                         datefmt="%Y-%m-%d %H:%M:%S")


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    _configure_logging(args)

    if args.seed_2023:
        row = seed_2023(Path(args.export_csv).expanduser(), Path(args.csv))
        print(json.dumps(row, indent=2) if args.json else
              f"Seeded {row['date']}: {row['count']} unread, {row['total_words']} words "
              f"(source={row['source']})")
        return 0

    if args.backfill_reads:
        rows = backfill_reads(Path(args.parquet), Path(args.reads_csv), years=args.years)
        print(json.dumps(rows, indent=2) if args.json else
              f"Wrote {len(rows)} weekly rows to {args.reads_csv}")
        return 0

    started_at = utcnow()
    heartbeat_path = None if args.no_heartbeat else Path(args.heartbeat)
    try:
        token_file = Path(args.token_file).expanduser() if args.token_file else token_path()
        token = load_token(token_file)
        client = MatterClient(token)
        result = run_snapshot(client, ledger_path=Path(args.ledger), csv_path=Path(args.csv))
    except Exception as exc:  # noqa: BLE001 - the nightly job must still heartbeat on failure
        log.error("%s", exc)
        if heartbeat_path:
            write_snapshot_heartbeat(heartbeat_path, started_at=started_at, finished_at=utcnow(),
                                      outcome="fail", extra={"error": str(exc)})
        return 2

    finished_at = utcnow()
    if heartbeat_path:
        write_snapshot_heartbeat(heartbeat_path, started_at=started_at, finished_at=finished_at,
                                  outcome="ok", extra={"count": result["daily_row"]["count"]})

    if args.json:
        print(json.dumps(result["daily_row"], indent=2))
    else:
        row = result["daily_row"]
        print(f"{row['date']}: {row['count']} in queue, {row['total_words']} words, "
              f"{row['started_not_finished_count']} started-not-finished, "
              f"new={row['new_ids_since_previous']}, gone={row['gone_ids_since_previous']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
