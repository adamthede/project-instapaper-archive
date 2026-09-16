"""The nightly Matter queue snapshot ledger.

No network. `FakeQueueClient` stands in for `MatterClient` and only implements
`iter_items(status=..., page_size=...)`, which is all `queue_snapshot.py` calls.
Every test is hermetic: temp files for the ledger/CSV, no real vault, no real
parquet index, no real credential.
"""

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from matter import queue_snapshot as qs  # noqa: E402


def make_item(item_id, *, word_count=1000, reading_progress=0.0,
              site_name="example.com", updated_at="2026-09-14T10:00:00Z",
              status="queue", title="Some Article", url=None, **overrides):
    """A Matter item shaped like the real API -- title/url included on purpose,
    so tests can prove the ledger writer strips them."""
    item = {
        "object": "item",
        "id": item_id,
        "title": title,
        "url": url or f"https://{site_name}/{item_id}",
        "site_name": site_name,
        "status": status,
        "word_count": word_count,
        "reading_progress": reading_progress,
        "updated_at": updated_at,
    }
    item.update(overrides)
    return item


class FakeQueueClient:
    """Records every iter_items call; serves canned items regardless of status
    (tests assert on what was ASKED for via `.calls`)."""

    def __init__(self, items):
        self._items = list(items)
        self.calls = []

    def iter_items(self, *, status=None, updated_since=None, order="updated", page_size=100):
        self.calls.append({"status": status, "page_size": page_size})
        for item in self._items:
            if status is None or item.get("status") == status:
                yield dict(item)


def dt(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def read_csv_rows(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


# ---- fetch: only the documented unread-like status ------------------------

def test_fetch_queue_items_asks_for_queue_status_only_by_default():
    """Mutation: fetch_queue_items() forgetting the status filter, or asking
    for 'archive'/'inbox' instead of 'queue', would pull read history or the
    unsaved discovery feed into a ledger meant to measure the unread backlog.
    """
    client = FakeQueueClient([make_item("itm_1", status="queue")])
    items = qs.fetch_queue_items(client)
    assert [i["id"] for i in items] == ["itm_1"]
    assert client.calls[0]["status"] == "queue"
    # No other status was asked for: EXTRA_UNREAD_STATUSES is documented empty
    # (archive/queue/inbox are the whole enum; only queue is "saved, unread").
    assert len(client.calls) == 1


# ---- first run: nulls, not zeros -------------------------------------------

def test_first_run_new_and_gone_ids_are_null_not_zero(tmp_path):
    """Mutation: defaulting the diff counts to 0 on a first run (no previous
    snapshot exists to diff against) would silently claim zero inflow/outflow
    instead of admitting there is nothing to compare against yet.
    """
    client = FakeQueueClient([make_item("itm_1"), make_item("itm_2")])
    ledger = tmp_path / "queue_snapshots.jsonl"
    csv_path = tmp_path / "queue_daily.csv"

    result = qs.run_snapshot(client, ledger_path=ledger, csv_path=csv_path,
                              now=dt("2026-09-14T07:40:00+00:00"))

    assert result["daily_row"]["new_ids_since_previous"] is None
    assert result["daily_row"]["gone_ids_since_previous"] is None
    assert result["daily_row"]["count"] == 2

    rows = read_csv_rows(csv_path)
    assert len(rows) == 1
    assert rows[0]["new_ids_since_previous"] == ""
    assert rows[0]["gone_ids_since_previous"] == ""


# ---- the id diff ------------------------------------------------------------

def test_id_diff_counts_inflow_and_outflow_against_previous_snapshot(tmp_path):
    """Mutation: swapping the set-difference operands (new vs gone), or using
    intersection/union instead of difference, would misreport which ids are
    newly saved vs newly resolved.
    """
    ledger = tmp_path / "queue_snapshots.jsonl"
    csv_path = tmp_path / "queue_daily.csv"

    day1 = FakeQueueClient([make_item("A"), make_item("B"), make_item("C")])
    qs.run_snapshot(day1, ledger_path=ledger, csv_path=csv_path,
                     now=dt("2026-09-14T07:40:00+00:00"))

    # B, C carry over; A is gone (read/removed); D is newly saved.
    day2 = FakeQueueClient([make_item("B"), make_item("C"), make_item("D")])
    result = qs.run_snapshot(day2, ledger_path=ledger, csv_path=csv_path,
                              now=dt("2026-09-15T07:40:00+00:00"))

    assert result["daily_row"]["new_ids_since_previous"] == 1   # D
    assert result["daily_row"]["gone_ids_since_previous"] == 1  # A
    assert result["daily_row"]["count"] == 3


# ---- same-day idempotency ---------------------------------------------------

def test_same_day_rerun_is_idempotent_and_marks_superseding(tmp_path):
    """Mutation: appending a second CSV row for the same date (instead of
    replacing it) would double-count that day in every downstream chart.
    Mutation: diffing the second same-day run against the FIRST same-day run
    (instead of against the last prior-day snapshot) would make the reported
    inflow/outflow drift between identical reruns on the same day.
    """
    ledger = tmp_path / "queue_snapshots.jsonl"
    csv_path = tmp_path / "queue_daily.csv"

    day1 = FakeQueueClient([make_item("A"), make_item("B")])
    qs.run_snapshot(day1, ledger_path=ledger, csv_path=csv_path,
                     now=dt("2026-09-14T07:40:00+00:00"))

    same_items = [make_item("A"), make_item("B"), make_item("C")]

    run1 = FakeQueueClient(list(same_items))
    result1 = qs.run_snapshot(run1, ledger_path=ledger, csv_path=csv_path,
                               now=dt("2026-09-15T07:40:00+00:00"))

    run2 = FakeQueueClient(list(same_items))
    result2 = qs.run_snapshot(run2, ledger_path=ledger, csv_path=csv_path,
                               now=dt("2026-09-15T19:40:00+00:00"))

    # Same content, same day: the diff against the day-1 baseline must not move.
    assert result1["daily_row"]["new_ids_since_previous"] == 1   # C
    assert result1["daily_row"]["gone_ids_since_previous"] == 0
    assert result2["daily_row"] == result1["daily_row"]

    entries = read_jsonl(ledger)
    assert len(entries) == 3
    assert entries[0].get("superseding", False) is False
    assert entries[1].get("superseding", False) is False
    assert entries[2]["superseding"] is True

    rows = read_csv_rows(csv_path)
    assert len(rows) == 2  # one row per DATE, not per run
    today_row = next(r for r in rows if r["date"] == "2026-09-15")
    assert today_row["count"] == "3"
    assert today_row["new_ids_since_previous"] == "1"
    assert today_row["gone_ids_since_previous"] == "0"


# ---- no titles or URLs in the ledger ---------------------------------------

def test_ledger_never_contains_titles_or_urls(tmp_path):
    """Mutation: passing the raw Matter item dict into the ledger instead of
    the compact allowlisted subset would leak titles and URLs into a file that
    is explicitly documented as never carrying them.
    """
    client = FakeQueueClient([
        make_item("itm_1", title="A Very Private Title",
                  url="https://example.com/private-path", site_name="example.com"),
    ])
    ledger = tmp_path / "queue_snapshots.jsonl"
    csv_path = tmp_path / "queue_daily.csv"

    qs.run_snapshot(client, ledger_path=ledger, csv_path=csv_path,
                     now=dt("2026-09-14T07:40:00+00:00"))

    raw_text = ledger.read_text()
    assert "Very Private Title" not in raw_text
    assert "private-path" not in raw_text

    entries = read_jsonl(ledger)
    item = entries[0]["items"][0]
    assert set(item.keys()) == {"id", "word_count", "reading_progress", "site", "updated_at"}
    assert item["id"] == "itm_1"
    assert item["site"] == "example.com"


# ---- started_not_finished boundary -----------------------------------------

def test_started_not_finished_excludes_never_opened_and_fully_read(tmp_path):
    """Mutation: an off-by-one on the 0/1 boundary (e.g. `<=` instead of `<`)
    would count never-opened or fully-read items as 'in progress'.
    """
    client = FakeQueueClient([
        make_item("never", reading_progress=0.0),
        make_item("started", reading_progress=0.4),
        make_item("almost_done", reading_progress=0.99),
        make_item("finished_but_still_queued", reading_progress=1.0),
        make_item("unknown", reading_progress=None),
    ])
    ledger = tmp_path / "queue_snapshots.jsonl"
    csv_path = tmp_path / "queue_daily.csv"

    result = qs.run_snapshot(client, ledger_path=ledger, csv_path=csv_path,
                              now=dt("2026-09-14T07:40:00+00:00"))

    assert result["daily_row"]["started_not_finished_count"] == 2  # started, almost_done


# ---- weekly reads backfill aggregation -------------------------------------

def test_backfill_weekly_reads_aggregation():
    """Mutation: summing word_count without the two-year window, or grouping
    by calendar week instead of ISO week, would produce a weekly series that
    disagrees with what a person means by 'this week'.
    """
    from datetime import date
    now = date(2026, 9, 16)

    records = [
        # ISO week 2026-W37: Mon 2026-09-07 .. Sun 2026-09-13
        {"date_read": date(2026, 9, 8), "word_count": 1000},
        {"date_read": date(2026, 9, 10), "word_count": 500},
        # ISO week 2026-W38: Mon 2026-09-14 .. Sun 2026-09-20
        {"date_read": date(2026, 9, 15), "word_count": 2000},
        # Outside the 2-year window -- must be excluded.
        {"date_read": date(2023, 1, 1), "word_count": 999999},
        # No read date at all -- must be excluded, never counted as zero.
        {"date_read": None, "word_count": 100},
    ]

    weeks = qs.compute_weekly_reads(records, now=now, years=2)

    by_week = {w["week"]: w for w in weeks}
    assert by_week["2026-W37"]["reads"] == 2
    assert by_week["2026-W37"]["words_read"] == 1500
    assert by_week["2026-W38"]["reads"] == 1
    assert by_week["2026-W38"]["words_read"] == 2000
    assert "2023-W01" not in by_week
    # Sorted ascending by week.
    assert [w["week"] for w in weeks] == sorted(w["week"] for w in weeks)


# ---- the 2023 seed row -------------------------------------------------------

def test_seed_2023_records_487_unread_as_one_historical_row(tmp_path):
    """Mutation: filtering on the wrong column/value (e.g. `Saved` instead of
    `Read`, or the string 'false' instead of 'False') would seed the wrong
    count, silently, since nothing else checks this row against reality.
    """
    export_csv = tmp_path / "_matter_history.csv"
    with open(export_csv, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Title", "Author", "Publisher", "URL", "Word Count",
                          "Saved", "Read", "Highlight Count", "Last Interaction Date", "File Id"])
        writer.writerow(["A", "", "", "https://a", "100", "False", "False", "0", "2022-01-01", "c1"])
        writer.writerow(["B", "", "", "https://b", "200", "False", "False", "0", "2022-01-01", "c2"])
        writer.writerow(["C", "", "", "https://c", "300", "False", "False", "0", "2022-01-01", "c3"])
        writer.writerow(["D", "", "", "https://d", "9999", "False", "True", "0", "2022-01-01", "c4"])
        writer.writerow(["E", "", "", "https://e", "9999", "False", "True", "0", "2022-01-01", "c5"])

    daily_csv = tmp_path / "queue_daily.csv"
    row = qs.seed_2023(export_csv, daily_csv)

    assert row["count"] == 3
    assert row["total_words"] == 600
    assert row["source"] == "export-2023"
    assert row["date"] == "2023-05-12"

    rows = read_csv_rows(daily_csv)
    assert len(rows) == 1
    assert rows[0]["source"] == "export-2023"
    assert rows[0]["started_not_finished_count"] == ""
    assert rows[0]["new_ids_since_previous"] == ""


def test_seed_2023_does_not_clobber_existing_daily_rows(tmp_path):
    """Mutation: an upsert that rewrites the whole file from scratch instead of
    merging by date would silently delete every real nightly snapshot row the
    first time the seed is (re-)run.
    """
    daily_csv = tmp_path / "queue_daily.csv"
    qs.upsert_daily_row(daily_csv, {
        "date": "2026-09-14", "count": 5, "total_words": 1000,
        "started_not_finished_count": 1, "new_ids_since_previous": None,
        "gone_ids_since_previous": None, "source": "matter-api",
    })

    export_csv = daily_csv.parent / "_matter_history.csv"
    with open(export_csv, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Title", "Author", "Publisher", "URL", "Word Count",
                          "Saved", "Read", "Highlight Count", "Last Interaction Date", "File Id"])
        writer.writerow(["A", "", "", "https://a", "50", "False", "False", "0", "2022-01-01", "c1"])

    qs.seed_2023(export_csv, daily_csv)

    rows = read_csv_rows(daily_csv)
    dates = {r["date"] for r in rows}
    assert dates == {"2026-09-14", "2023-05-12"}


# ---- heartbeat --------------------------------------------------------------

def test_run_snapshot_heartbeat_carries_fleet_standard_keys(tmp_path):
    """Mutation: omitting started_at/finished_at/outcome, or naming them
    differently, would make this job invisible to the cockpit's heartbeat
    reader the same way every other fleet job is read.
    """
    client = FakeQueueClient([make_item("itm_1")])
    ledger = tmp_path / "queue_snapshots.jsonl"
    csv_path = tmp_path / "queue_daily.csv"
    heartbeat = tmp_path / "heartbeat.json"

    qs.write_snapshot_heartbeat(
        heartbeat,
        started_at=dt("2026-09-14T07:40:00+00:00"),
        finished_at=dt("2026-09-14T07:40:05+00:00"),
        outcome="ok",
        extra={"count": 1},
    )

    payload = json.loads(heartbeat.read_text())
    assert payload["outcome"] == "ok"
    assert "started_at" in payload and "finished_at" in payload
    assert payload["count"] == 1
