"""The nightly Matter queue snapshot ledger.

No network. `FakeQueueClient` stands in for `MatterClient` and only implements
`get(path, params)`, shaped like the real API's paginated /items response
(`results` / `has_more` / `next_cursor`) -- deliberately the same low-level
call `queue_snapshot.py` itself makes (see its `_iter_status_pages`), not the
higher-level `iter_items()` convenience wrapper, so tests can simulate Matter
sending an incomplete page. Every test is hermetic: temp files for the
ledger/CSV, no real vault, no real parquet index, no real credential.
"""

import csv
import json
import sys
from datetime import date, datetime, timezone

import pytest
from pathlib import Path

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
    """Serves GET /items shaped like the real, documented response.

    `truncate_after` simulates Matter's own documented edge case: a page that
    reports `has_more: true` but no `next_cursor` (see api.py's `_paginate`
    comment). `truncate_after=0` means "truncate on the very first page";
    `truncate_after=N` truncates once N full pages have already been served.
    """

    def __init__(self, items, *, truncate_after=None):
        self._items = list(items)
        self._truncate_after = truncate_after
        self.calls = []

    def get(self, path, params=None):
        params = dict(params or {})
        self.calls.append({"path": path, "params": params})
        status = params.get("status")
        matching = [i for i in self._items if status is None or i.get("status") == status]

        limit = params.get("limit") or 100
        cursor = params.get("cursor")
        start = int(cursor) if cursor else 0
        page = matching[start:start + limit]
        end = start + len(page)
        has_more = end < len(matching)
        next_cursor = str(end) if has_more else None

        page_index = start // limit if limit else 0
        if self._truncate_after is not None and has_more and page_index >= self._truncate_after:
            next_cursor = None  # Matter's malformed has_more=true / no cursor case

        return {"object": "list", "results": [dict(i) for i in page],
                "has_more": has_more, "next_cursor": next_cursor}


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
    assert client.calls[0]["params"]["status"] == "queue"
    # No other status was asked for: EXTRA_UNREAD_STATUSES is documented empty
    # (archive/queue/inbox are the whole enum; only queue is "saved, unread").
    assert len(client.calls) == 1


def test_fetch_queue_items_dedupes_by_id_across_status_passes():
    """Mutation: dropping the id dedupe would double-count (and double-sum
    word_count for) any item Matter happens to return under more than one
    status pass.
    """
    shared = make_item("dup", word_count=500, status="queue")
    client = FakeQueueClient([shared])
    items = qs.fetch_queue_items(client, extra_statuses=("queue",))
    assert len(items) == 1
    assert items[0]["id"] == "dup"


def test_incomplete_pagination_raises_instead_of_writing_a_truncated_snapshot(tmp_path):
    """Mutation: silently accepting a truncated page (has_more=true, no
    next_cursor) instead of raising would write a short queue as if it were
    complete, and every id missing from it would misreport as 'gone' on the
    next diff (PR #27 review, finding 1).
    """
    items = [make_item(f"itm_{i}") for i in range(5)]
    client = FakeQueueClient(items, truncate_after=0)  # truncates on page 1 of 3
    ledger = tmp_path / "queue_snapshots.jsonl"
    csv_path = tmp_path / "queue_daily.csv"

    with pytest.raises(qs.MatterAPIError):
        qs.run_snapshot(client, ledger_path=ledger, csv_path=csv_path,
                         now=dt("2026-09-14T07:40:00+00:00"), page_size=2)

    # No partial or fabricated snapshot left behind.
    assert not ledger.exists()
    assert not csv_path.exists()


def test_missing_or_null_results_field_raises_but_empty_list_does_not():
    """Mutation: `payload.get("results") or []` collapses three response
    shapes into one -- a missing `results` key, `{"results": null}`, and a
    legitimately empty queue (`{"results": []}`) all produced `count=0` with
    no raise. Only the third is a real shape Matter can send; the other two
    must raise instead of silently reporting a fabricated maximal outflow
    the next time this snapshot is diffed (PR #27 review, residual R1).
    """
    class MalformedClient:
        def __init__(self, payload):
            self._payload = payload

        def get(self, path, params=None):
            return self._payload

    with pytest.raises(qs.MatterAPIError):
        qs.fetch_queue_items(MalformedClient({"object": "list", "has_more": False}))  # no results key

    with pytest.raises(qs.MatterAPIError):
        qs.fetch_queue_items(MalformedClient(
            {"object": "list", "results": None, "has_more": False}))

    # A legitimately empty queue must NOT raise.
    items = qs.fetch_queue_items(MalformedClient(
        {"object": "list", "results": [], "has_more": False, "next_cursor": None}))
    assert items == []


def test_pagination_stops_at_a_page_ceiling_instead_of_looping_forever():
    """Mutation: dropping the page-count ceiling (keeping only the
    repeat-cursor guard) would let a pathological API that always returns a
    FRESH cursor with has_more=true loop this nightly job forever -- launchd
    will not start the next run while this one is still going, so the
    fleet's only symptom would be a job that silently stopped producing
    snapshots (PR #27 review, residual R2).

    The fake client is bounded at a call count well past `max_pages`
    (finite, not truly infinite) on purpose: if the ceiling under test ever
    regresses, this test must fail cleanly -- no exception raised -- rather
    than hang the suite by chasing an unboundedly patient fake.
    """
    class LongButFiniteClient:
        def __init__(self, real_end=500):
            self.calls = 0
            self._real_end = real_end  # far past max_pages below

        def get(self, path, params=None):
            self.calls += 1
            has_more = self.calls < self._real_end
            return {"object": "list", "results": [], "has_more": has_more,
                     "next_cursor": f"cursor-{self.calls}" if has_more else None}

    client = LongButFiniteClient()
    with pytest.raises(qs.MatterAPIError):
        qs.fetch_queue_items(client, page_size=1, max_pages=5)

    # The ceiling actually bit well before the fake's own natural end.
    assert client.calls <= 6


def test_heartbeat_reports_fail_when_run_snapshot_raises(tmp_path):
    """Mutation: hardcoding outcome='ok' regardless of whether run_snapshot
    raised would make this job's only fleet-visible failure signal silently
    lie on the exact night the truncation guard (finding 1) fires (finding 6).
    """
    items = [make_item(f"itm_{i}") for i in range(5)]
    client = FakeQueueClient(items, truncate_after=0)
    ledger = tmp_path / "queue_snapshots.jsonl"
    csv_path = tmp_path / "queue_daily.csv"
    heartbeat = tmp_path / "heartbeat.json"

    exit_code, result = qs.run_snapshot_with_heartbeat(
        client, ledger_path=ledger, csv_path=csv_path, heartbeat_path=heartbeat,
        now=dt("2026-09-14T07:40:00+00:00"), page_size=2,
    )

    assert exit_code != 0
    assert result is None
    payload = json.loads(heartbeat.read_text())
    assert payload["outcome"] == "fail"
    assert "error" in payload


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
    assert result["daily_row"]["previous_date"] is None
    assert result["daily_row"]["days_since_previous"] is None
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
    assert result["daily_row"]["previous_date"] == "2026-09-14"
    assert result["daily_row"]["days_since_previous"] == 1


def test_days_since_previous_reflects_a_multi_day_gap(tmp_path):
    """Mutation: omitting previous_date/days_since_previous (or hardcoding
    days_since_previous to 1) would make a flow measured across a multi-night
    gap indistinguishable from a single day's flow (PR #27 review, finding 5).
    """
    ledger = tmp_path / "queue_snapshots.jsonl"
    csv_path = tmp_path / "queue_daily.csv"

    day1 = FakeQueueClient([make_item("A"), make_item("B")])
    qs.run_snapshot(day1, ledger_path=ledger, csv_path=csv_path,
                     now=dt("2026-09-10T07:40:00+00:00"))

    # Three nights missed; the next snapshot is on 09-14.
    day2 = FakeQueueClient([make_item("B"), make_item("C")])
    result = qs.run_snapshot(day2, ledger_path=ledger, csv_path=csv_path,
                              now=dt("2026-09-14T07:40:00+00:00"))

    assert result["daily_row"]["previous_date"] == "2026-09-10"
    assert result["daily_row"]["days_since_previous"] == 4

    rows = read_csv_rows(csv_path)
    today_row = next(r for r in rows if r["date"] == "2026-09-14")
    assert today_row["days_since_previous"] == "4"


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


# ---- started_not_finished boundary, total_words, source --------------------

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
    # A finished-but-still-queued item still counts toward the backlog itself
    # (PR #27 review's known limitation, documented -- not filtered out).
    assert result["daily_row"]["count"] == 5


def test_daily_row_total_words_and_source(tmp_path):
    """Mutation: hardcoding total_words to 0 (M13), or defaulting source to
    anything other than 'matter-api' (M19), would go unnoticed without a
    direct assertion on these two fields -- source is the only thing that
    keeps the 2023 seed row out of velocity math computed over this file.
    """
    client = FakeQueueClient([make_item("a", word_count=100), make_item("b", word_count=250)])
    ledger = tmp_path / "queue_snapshots.jsonl"
    csv_path = tmp_path / "queue_daily.csv"

    result = qs.run_snapshot(client, ledger_path=ledger, csv_path=csv_path,
                              now=dt("2026-09-14T07:40:00+00:00"))

    assert result["daily_row"]["total_words"] == 350
    assert result["daily_row"]["source"] == qs.SOURCE_MATTER_API == "matter-api"


# ---- weekly reads backfill aggregation -------------------------------------

def test_backfill_weekly_reads_aggregation():
    """Mutation: summing word_count without the two-year window, or grouping
    by calendar week instead of ISO week, would produce a weekly series that
    disagrees with what a person means by 'this week'. Mutation: dropping
    zero-read weeks would bias any average computed from the file (finding 4).
    """
    now = date(2026, 9, 16)  # a Wednesday, ISO week 2026-W38

    records = [
        # ISO week 2026-W37: Mon 2026-09-07 .. Sun 2026-09-13
        {"date_read": date(2026, 9, 8), "word_count": 1000},
        {"date_read": date(2026, 9, 10), "word_count": 500},
        # ISO week 2026-W38: Mon 2026-09-14 .. Sun 2026-09-20 (in progress)
        {"date_read": date(2026, 9, 15), "word_count": 2000},
        # Outside the 2-year window -- its word count must not appear anywhere.
        {"date_read": date(2023, 1, 1), "word_count": 999999},
        # No read date at all -- excluded, never counted as zero.
        {"date_read": None, "word_count": 100},
    ]

    weeks = qs.compute_weekly_reads(records, now=now, years=2)
    by_week = {w["week"]: w for w in weeks}

    assert by_week["2026-W37"] == {"week": "2026-W37", "reads": 2, "words_read": 1500}
    assert by_week["2026-W38"] == {"week": "2026-W38", "reads": 1, "words_read": 2000}

    # A week with no reads is still emitted, at zero -- not silently dropped.
    assert by_week["2026-W36"] == {"week": "2026-W36", "reads": 0, "words_read": 0}

    # The 999999-word out-of-window record must not have leaked into any week.
    assert sum(w["words_read"] for w in weeks) == 3500
    assert sum(w["reads"] for w in weeks) == 3

    # The window is exactly [now - 2 years, now] -- every ISO week in it,
    # nothing older.
    since = now.replace(year=now.year - 2)
    expected_keys = []
    seen = set()
    cursor = since
    from datetime import timedelta as _td
    while cursor <= now:
        y, w, _ = cursor.isocalendar()
        key = f"{y}-W{w:02d}"
        if key not in seen:
            seen.add(key)
            expected_keys.append(key)
        cursor += _td(days=1)
    assert [w["week"] for w in weeks] == expected_keys


def test_iso_week_key_crosses_calendar_year_boundary_correctly():
    """Mutation: keying weeks by calendar year (`d.year`) instead of ISO year
    (`d.isocalendar()`) would put 2024-12-30 in a '2024-W01'-shaped bucket
    that collides with January 2024 and sorts to the front of the series,
    instead of its real ISO week, 2025-W01, which belongs at the end
    (PR #27 review, finding 8 -- the mutation that survived on this boundary).
    """
    now = date(2025, 1, 5)
    records = [
        {"date_read": date(2024, 12, 30), "word_count": 111},  # ISO 2025-W01
        {"date_read": date(2024, 12, 29), "word_count": 222},  # ISO 2024-W52
    ]
    weeks = qs.compute_weekly_reads(records, now=now, years=2)
    by_week = {w["week"]: w for w in weeks}

    assert by_week["2025-W01"]["words_read"] == 111
    assert by_week["2024-W52"]["words_read"] == 222
    assert "2024-W01" not in by_week or by_week["2024-W01"]["words_read"] == 0

    week_keys = [w["week"] for w in weeks]
    assert week_keys.index("2024-W52") < week_keys.index("2025-W01")


def test_derive_date_read_does_not_fall_back_for_matter_rows():
    """Mutation: a flat `date_archived.fillna(date_saved)` across every row
    (no era distinction) would date an unread-but-indexed Matter row by when
    it was saved, asserting a read that never happened -- the exact defect
    dashboard/app.py and weekly_synthesis.py both guard against
    (PR #27 review, finding 3).
    """
    import pandas as pd

    df = pd.DataFrame({
        "date_archived": pd.to_datetime([None, "2026-01-05", None]),
        "date_saved": pd.to_datetime(["2025-01-01", "2025-06-01", "2025-12-01"]),
        "source": ["matter", "matter", "legacy_pdf"],
    })

    result = qs.derive_date_read(df)

    # Matter row 0: no date_archived -> stays unread (NaT), never falls back.
    assert pd.isna(result.iloc[0])
    # Matter row 1: has date_archived -> uses it.
    assert result.iloc[1] == pd.Timestamp("2026-01-05")
    # Legacy row 2: no date_archived -> falls back to date_saved.
    assert result.iloc[2] == pd.Timestamp("2025-12-01")


def test_backfill_reads_writes_the_weekly_csv_end_to_end(tmp_path):
    """Mutation: skipping the write_weekly_reads_csv call (or any refactor
    that computes the rows but never persists them) would leave
    reads_weekly.csv stale or missing while backfill_reads still returns
    successfully and the nightly caller reports success (M16 in PR #27's
    mutation table -- the previous test suite never called backfill_reads()
    at all, only its pure helpers). Mutation: dating an undated row (no
    date_archived, no date_saved) by `now` instead of leaving it None would
    silently count a row with zero read evidence as read this week (M12).
    """
    import pandas as pd

    df = pd.DataFrame({
        "date_saved": pd.to_datetime(["2026-09-01", "2026-09-08", None]),
        "date_archived": pd.to_datetime(["2026-09-02", None, None]),
        "word_count": [500, 700, 900],
        "source": ["instapaper", "legacy_pdf", "legacy_pdf"],
    })
    parquet_path = tmp_path / "archive_index.parquet"
    df.to_parquet(parquet_path)

    csv_path = tmp_path / "reads_weekly.csv"
    assert not csv_path.exists()

    rows = qs.backfill_reads(parquet_path, csv_path, now=date(2026, 9, 16), years=1)

    assert csv_path.exists()
    written = read_csv_rows(csv_path)
    assert [r["week"] for r in written] == [r["week"] for r in rows]
    assert len(written) == len(rows)
    # Rows 1 and 2 have an effective date_read (row 2 is non-Matter, so it
    # falls back to date_saved) -- proves the parquet -> derive -> group ->
    # write chain ran end to end. Row 3 has neither date and must be excluded
    # entirely, not dated by `now`.
    assert sum(int(r["reads"]) for r in written) == 2


# ---- the 2023 seed row -------------------------------------------------------

def test_seed_2023_records_queue_equivalent_unread_as_one_historical_row(tmp_path):
    """Mutation: filtering on the wrong column/value (e.g. the string 'false'
    instead of 'False', or dropping the Saved condition entirely) would
    silently seed the wrong count.

    The population is Saved=="True" AND Read=="False" -- Matter's present-day
    `status=="queue"` equivalent, matching every API-driven row in this same
    file. Adam confirmed this reading (via the team lead, 2026-09-16) after
    PR #27 review, finding 2, showed that filtering on Read alone (487) pulls
    in 56 Saved=="False" rows that are closer to Matter's "inbox" (unsaved,
    not even intent) than to "queue". The fixture deliberately includes a
    Saved=="False"/Read=="False" row (B) to prove it is now excluded, not
    just a Read=="True" row (D) to prove Read still matters.
    """
    export_csv = tmp_path / "_matter_history.csv"
    with open(export_csv, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Title", "Author", "Publisher", "URL", "Word Count",
                          "Saved", "Read", "Highlight Count", "Last Interaction Date", "File Id"])
        writer.writerow(["A", "", "", "https://a", "100", "True", "False", "0", "2022-01-01", "c1"])
        writer.writerow(["B", "", "", "https://b", "200", "False", "False", "0", "2022-01-01", "c2"])
        writer.writerow(["C", "", "", "https://c", "300", "True", "False", "0", "2022-01-01", "c3"])
        writer.writerow(["D", "", "", "https://d", "9999", "True", "True", "0", "2022-01-01", "c4"])
        writer.writerow(["E", "", "", "https://e", "9999", "False", "True", "0", "2022-01-01", "c5"])

    daily_csv = tmp_path / "queue_daily.csv"
    row = qs.seed_2023(export_csv, daily_csv)

    assert row["count"] == 2          # A, C only -- B excluded (Saved=="False")
    assert row["total_words"] == 400
    assert row["source"] == "export-2023"
    assert row["date"] == "2023-05-12"

    rows = read_csv_rows(daily_csv)
    assert len(rows) == 1
    assert rows[0]["source"] == "export-2023"
    assert rows[0]["started_not_finished_count"] == ""
    assert rows[0]["new_ids_since_previous"] == ""
    assert rows[0]["previous_date"] == ""
    assert rows[0]["days_since_previous"] == ""


def test_seed_2023_does_not_clobber_existing_daily_rows(tmp_path):
    """Mutation: an upsert that rewrites the whole file from scratch instead of
    merging by date would silently delete every real nightly snapshot row the
    first time the seed is (re-)run.
    """
    daily_csv = tmp_path / "queue_daily.csv"
    qs.upsert_daily_row(daily_csv, {
        "date": "2026-09-14", "count": 5, "total_words": 1000,
        "started_not_finished_count": 1, "new_ids_since_previous": None,
        "gone_ids_since_previous": None, "previous_date": None,
        "days_since_previous": None, "source": "matter-api",
    })

    export_csv = daily_csv.parent / "_matter_history.csv"
    with open(export_csv, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Title", "Author", "Publisher", "URL", "Word Count",
                          "Saved", "Read", "Highlight Count", "Last Interaction Date", "File Id"])
        writer.writerow(["A", "", "", "https://a", "50", "True", "False", "0", "2022-01-01", "c1"])

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
