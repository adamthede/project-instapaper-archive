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
                                        per day. `previous_date` and
                                        `days_since_previous` say what the
                                        inflow/outflow columns were actually
                                        measured across, so a 3-night gap
                                        never reads as a single day's flow.

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

Pagination is fetched via a hand-rolled loop over `client.get()` rather than
`MatterClient.iter_items()`/`_paginate()`. `_paginate()` treats a page that
reports `has_more: true` with no `next_cursor` as "nothing sane to do except
stop" and returns silently -- the right call for the incremental read sync,
which self-heals on the next nightly run. It is the wrong call for a snapshot:
a silently truncated queue listing would get written into the ledger as if it
were the complete queue, and every id missing from it would misreport as an
article that "left the queue overnight." This module raises instead (see
`_iter_status_pages` and PR #27 review, finding 1), which fails the run --
and the heartbeat -- rather than fabricating a snapshot.

date_read backfill (--backfill-reads)
--------------------------------------
Outflow needs a "reads per week" series to compare the queue's drain against,
and the ledger itself has no history before tonight. `--backfill-reads` builds
that history from `data/archive_index.parquet`, deriving `date_read` the SAME
era-aware way `dashboard/app.py` and `scripts/core/weekly_synthesis.py` already
do (see `derive_date_read` below): `date_archived` falling back to
`date_saved` for everything EXCEPT Matter rows, which are dated only by a real
archive event and never by when they were saved (see docs/MATTER_SYNC.md, "How
date_read works"). Reading the index rather than re-deriving this by hand from
18,000+ files' frontmatter keeps this script from carrying a second copy of
logic that already exists, tested, in two other places; it also avoids the
vault scan's own documented cost (up to ~50 minutes cold over SMB -- see
"Which interpreter to run" in that same doc). This is why `--backfill-reads`
needs pandas/pyarrow (the venv interpreter), while the nightly snapshot leg
itself only needs the standard library plus `requests`, matching the fleet's
homebrew-python3 constraint for the nightly job.

Every week in the backfill's [now - years, now] window is emitted, including
weeks with zero reads -- an absent week is indistinguishable from "nobody
measured this week" and silently inflates any average computed from the file
(PR #27 review, finding 4). The LAST row in that range covers whatever ISO
week `now` falls in, which is almost always a partial week (`now` is rarely a
Sunday) -- treat it as provisional when eyeballing a trend.

Known, accepted limitations (see PR #27 review for the fuller discussion):
  * A queued item can sit at `reading_progress == 1.0` (finished but never
    re-filed out of the queue -- docs/MATTER_SYNC.md measures 29 of 521 such
    items). It counts toward `count`/`total_words` like any other queued
    item, correctly sits outside `started_not_finished_count`, and is not
    filtered out: "still in the queue" is the definition this ledger uses,
    not "still worth finishing."
  * `taken_at`/`date` are UTC. A hand-run in the evening in a UTC-behind zone
    can date itself tomorrow relative to local wall-clock time. This
    self-heals exactly like a same-day rerun does (the next nightly run
    supersedes it), so it is not guarded against further.

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
    from .errors import MatterAPIError
    from .state import atomic_write_text, to_iso, utcnow
except ImportError:
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
    from matter.api import MatterClient  # noqa: E402
    from matter.credentials import load_token, token_path  # noqa: E402
    from matter.errors import MatterAPIError  # noqa: E402
    from matter.state import atomic_write_text, to_iso, utcnow  # noqa: E402

import argparse
import csv
import io
import json
import logging
import os
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
    "new_ids_since_previous", "gone_ids_since_previous",
    "previous_date", "days_since_previous", "source",
]
WEEKLY_CSV_FIELDS = ["week", "reads", "words_read"]

ITEM_FIELDS = ("id", "word_count", "reading_progress", "site", "updated_at")

MAX_PAGE_SIZE = 100


# ---- fetch ------------------------------------------------------------------

def _iter_status_pages(client, status: str, *, page_size: int = MAX_PAGE_SIZE):
    """GET /items for one status, paginating by hand via client.get().

    Deliberately does not use MatterClient.iter_items()/_paginate(): see the
    module docstring for why silent truncation there is fine for the
    incremental read sync and not fine here. Raises MatterAPIError on:
      * a non-list `results` field,
      * `has_more: true` with no `next_cursor` (an incomplete page Matter
        cannot resume, per _paginate's own comment -- upgraded here from
        "stop quietly" to "fail loudly", since there is no next run that
        self-heals a snapshot the way there is for a delta sync),
      * a repeated cursor (the same infinite-loop guard _paginate uses).
    """
    params: dict = {"limit": min(page_size, MAX_PAGE_SIZE), "order": "updated", "status": status}
    seen_cursors: set[str] = set()
    while True:
        payload = client.get("/items", params)
        results = payload.get("results") or []
        if not isinstance(results, list):
            raise MatterAPIError(f"GET /items returned a non-list 'results' field for status={status!r}")
        yield from results

        if not payload.get("has_more"):
            return
        cursor = payload.get("next_cursor")
        if not cursor:
            raise MatterAPIError(
                f"Matter reported more queue items (has_more=true) but returned no "
                f"next_cursor for status={status!r}. Refusing to write a truncated "
                f"queue snapshot as if it were the complete queue -- see PR #27 "
                f"review, finding 1."
            )
        if cursor in seen_cursors:
            raise MatterAPIError(f"GET /items repeated pagination cursor {cursor!r} for status={status!r}")
        seen_cursors.add(cursor)
        params["cursor"] = cursor


def fetch_queue_items(client, extra_statuses=EXTRA_UNREAD_STATUSES, *,
                       page_size: int = MAX_PAGE_SIZE) -> list[dict]:
    """The full current queue, plus any extra unread-like statuses.

    A full listing every run, not an incremental delta -- unlike the read
    sync, this ledger has no watermark, because it wants to be a snapshot of
    the queue's current shape, not a stream of individual changes to it.
    Deduped by id: an item Matter happens to return under more than one
    status pass is counted, and its word count summed, exactly once.
    """
    seen: dict[str, dict] = {}
    for status in (QUEUE_STATUS, *extra_statuses):
        for item in _iter_status_pages(client, status, page_size=page_size):
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
        previous_date = None
        days_since_previous = None
    else:
        prev_ids = {i["id"] for i in previous["items"]}
        new_ids = len(ids - prev_ids)
        gone_ids = len(prev_ids - ids)
        previous_date = previous["date"]
        days_since_previous = (
            date.fromisoformat(entry["date"]) - date.fromisoformat(previous["date"])
        ).days

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
        "previous_date": previous_date,
        "days_since_previous": days_since_previous,
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
                  csv_path: Path = DEFAULT_DAILY_CSV, now: datetime | None = None,
                  page_size: int = MAX_PAGE_SIZE) -> dict:
    now = now or utcnow()
    today = now.astimezone(timezone.utc).date().isoformat()

    prior_entries = read_ledger_entries(ledger_path)
    superseding = any(e.get("date") == today for e in prior_entries)
    previous = find_previous_snapshot(prior_entries, today)

    # Fetched (and can raise, e.g. on a truncated page) BEFORE anything is
    # written, so a failure here leaves the ledger and CSV exactly as they
    # were -- no partial or fabricated snapshot.
    items = fetch_queue_items(client, page_size=page_size)
    entry = build_snapshot_entry(items, now, superseding=superseding)
    append_ledger_entry(ledger_path, entry)

    daily_row = compute_daily_row(entry, previous)
    upsert_daily_row(csv_path, daily_row)

    return {"entry": entry, "daily_row": daily_row}


def run_snapshot_with_heartbeat(client, *, ledger_path: Path, csv_path: Path,
                                 heartbeat_path: Path | None = None, now: datetime | None = None,
                                 page_size: int = MAX_PAGE_SIZE) -> tuple[int, dict | None]:
    """run_snapshot, wrapped so the fleet-standard heartbeat is written on
    EITHER outcome. Factored out of main() so the failure path -- in
    particular, run_snapshot raising because fetch_queue_items detected a
    truncated page (finding 1) -- is unit-testable without argparse or a
    real credential, and so a broken night can never produce a silent
    outcome:"ok" heartbeat (finding 6). Returns (exit_code, result_or_None).
    """
    started_at = utcnow()
    try:
        result = run_snapshot(client, ledger_path=ledger_path, csv_path=csv_path,
                               now=now, page_size=page_size)
    except Exception as exc:  # noqa: BLE001 - the nightly job must still heartbeat on failure
        log.error("%s", exc)
        if heartbeat_path:
            write_snapshot_heartbeat(heartbeat_path, started_at=started_at, finished_at=utcnow(),
                                      outcome="fail", extra={"error": str(exc)})
        return 2, None

    if heartbeat_path:
        write_snapshot_heartbeat(heartbeat_path, started_at=started_at, finished_at=utcnow(),
                                  outcome="ok", extra={"count": result["daily_row"]["count"]})
    return 0, result


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

def _iso_week_key(d: date) -> str:
    iso_year, iso_week, _ = d.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def compute_weekly_reads(records: list[dict], *, now: date, years: int = 2) -> list[dict]:
    """Group (date_read, word_count) rows into an ISO-week reads/words series.

    `records` items are {"date_read": date | None, "word_count": int | None}.
    A row with no `date_read` is excluded, never counted as a zero-word read --
    the archive's own convention for "no read evidence" (see docs/MATTER_SYNC.md,
    "How date_read works").

    Every ISO week between `now - years` and `now` is emitted, including weeks
    with zero reads: an absent week is indistinguishable from an unmeasured
    one and silently inflates any average taken from the file (PR #27 review,
    finding 4). Keyed by ISO year, not calendar year (`d.isocalendar()`, not
    `d.year`), so a date like 2024-12-30 -- ISO week 1 of 2025 -- sorts and
    groups after 2024's weeks rather than colliding with January 2024 (finding
    8 / the mutation that survived on this exact boundary).
    """
    since = now - timedelta(days=365 * years)
    counts: dict[str, dict] = {}
    for record in records:
        read_on = record.get("date_read")
        if read_on is None:
            continue
        if not (since <= read_on <= now):
            continue
        key = _iso_week_key(read_on)
        bucket = counts.setdefault(key, {"reads": 0, "words_read": 0})
        bucket["reads"] += 1
        bucket["words_read"] += record.get("word_count") or 0

    # Walk every day in the window (not every 7th day from `since`, which
    # could miss a week if `since` doesn't land on the same weekday as the
    # ISO week boundary) collecting each week's key once, in order.
    ordered_keys: list[str] = []
    seen: set[str] = set()
    cursor = since
    while cursor <= now:
        key = _iso_week_key(cursor)
        if key not in seen:
            seen.add(key)
            ordered_keys.append(key)
        cursor += timedelta(days=1)

    return [
        {"week": key, "reads": counts.get(key, {}).get("reads", 0),
         "words_read": counts.get(key, {}).get("words_read", 0)}
        for key in ordered_keys
    ]


def write_weekly_reads_csv(csv_path: Path, rows: list[dict]) -> None:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=WEEKLY_CSV_FIELDS)
    writer.writeheader()
    for row in rows:
        writer.writerow({field: row.get(field) for field in WEEKLY_CSV_FIELDS})
    csv_path = Path(csv_path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(csv_path, buffer.getvalue(), create_parents=True)


def derive_date_read(df):
    """Era-aware date_read, mirroring dashboard/app.py's and
    scripts/core/weekly_synthesis.py's derive_date_read exactly (see PR #27
    review, finding 3).

    Matter rows (`source == "matter"`) are dated ONLY by a real archive event
    (`date_archived`) and never fall back to `date_saved`: Matter states
    explicitly whether something was read, so a Matter row with no
    `date_archived` is positive evidence it was NOT read, and dating it by
    when it was saved would assert a read that never happened. Every other
    row falls back to `date_saved`, because ~11,300 legacy/Instapaper rows
    have no archive date at all to read from.

    Currently latent rather than active: the read sync pulls `status=archive`
    only, so no unread Matter row reaches the index today, and a flat fillna
    (what this function replaces) agrees with this one on the live data.
    dashboard/app.py calls its own copy of this rule "the second lock on that
    door" precisely because `--status queue` still exists for deliberate use
    -- this module needs the same lock, not a third copy that omits it.
    """
    fallback = df["date_archived"].fillna(df["date_saved"])
    is_matter = df["source"].fillna("").astype(str).eq("matter")
    return fallback.mask(is_matter, df["date_archived"])


def backfill_reads(parquet_path: Path, csv_path: Path, *, now: date | None = None,
                    years: int = 2) -> list[dict]:
    """Build data/matter/reads_weekly.csv from the archive's own date_read.

    Needs pandas/pyarrow -- imported here, not at module scope, so the nightly
    snapshot leg (which runs on homebrew python3, no pandas) never pays for it.
    """
    import pandas as pd  # local import: see the module docstring

    now = now or utcnow().date()
    df = pd.read_parquet(parquet_path, columns=["date_saved", "date_archived", "word_count", "source"])
    date_read = derive_date_read(df)

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

    Read-only, by design, per the dispatch spec verbatim ("Read column False
    = unread") and matching the 487/1,017,211 figure Adam's own plan doc
    (docs/plans-to-do/2026-09-15-unread-corpus-what-i-meant-to-read.md,
    Follow-on tasks item 2) already anchors for this exact historical point.

    Caveat (PR #27 review, finding 2, not acted on here without Adam's
    sign-off): the export also carries a `Saved` column. Of the 487,
    431 are Saved=True (arguably "queue"-equivalent, matching every API-driven
    row in this same ledger) and 56 are Saved=False (arguably closer to
    Matter's "inbox" -- unsaved, not even intent, per this module's own
    EXTRA_UNREAD_STATUSES docstring). That would put this one historical
    anchor about 13% above the population definition every other row in
    queue_daily.csv uses. Left as specified rather than silently
    reinterpreted, because the spec gave an explicit column and an explicit
    target value; flagged on the PR for Adam to decide.
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
        # Not derivable from this export (no reading_progress column, and no
        # relationship to any other snapshot in the ledger): left blank, not
        # guessed at.
        "started_not_finished_count": None,
        "new_ids_since_previous": None,
        "gone_ids_since_previous": None,
        "previous_date": None,
        "days_since_previous": None,
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

    heartbeat_path = None if args.no_heartbeat else Path(args.heartbeat)
    try:
        token_file = Path(args.token_file).expanduser() if args.token_file else token_path()
        token = load_token(token_file)
        client = MatterClient(token)
    except Exception as exc:  # noqa: BLE001 - credential/auth failures must heartbeat too
        log.error("%s", exc)
        if heartbeat_path:
            write_snapshot_heartbeat(heartbeat_path, started_at=utcnow(), finished_at=utcnow(),
                                      outcome="fail", extra={"error": str(exc)})
        return 2

    exit_code, result = run_snapshot_with_heartbeat(
        client, ledger_path=Path(args.ledger), csv_path=Path(args.csv), heartbeat_path=heartbeat_path)
    if exit_code != 0:
        return exit_code

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
