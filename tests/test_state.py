"""Watermark and manifest behaviour."""

import json
import os
from datetime import datetime, timedelta, timezone

from matter.state import WATERMARK_OVERLAP, SyncState, atomic_write_text, to_iso


def test_a_fresh_state_has_no_watermark(tmp_path):
    state = SyncState.load(tmp_path / ".matter_manifest.json")
    assert state.watermark is None
    assert state.updated_since() is None  # first run walks the whole library


def test_watermark_survives_a_save_and_reload(tmp_path):
    path = tmp_path / ".matter_manifest.json"
    state = SyncState.load(path)
    state.advance_watermark(datetime(2026, 8, 11, 4, 45, tzinfo=timezone.utc))
    state.save()

    assert SyncState.load(path).watermark == "2026-08-11T04:45:00Z"


def test_updated_since_rewinds_by_the_overlap_to_absorb_clock_skew(tmp_path):
    state = SyncState.load(tmp_path / "m.json")
    state.advance_watermark(datetime(2026, 8, 11, 4, 45, tzinfo=timezone.utc))

    expected = datetime(2026, 8, 11, 4, 45, tzinfo=timezone.utc) - WATERMARK_OVERLAP
    assert state.updated_since() == to_iso(expected)


def test_watermark_is_stored_in_utc_with_a_z_suffix(tmp_path):
    """`updated_since` is compared server-side; a naive local timestamp would be wrong by hours."""
    state = SyncState.load(tmp_path / "m.json")
    eastern_noon = datetime(2026, 8, 11, 12, 0, tzinfo=timezone(timedelta(hours=-4)))
    state.advance_watermark(eastern_noon)
    assert state.watermark == "2026-08-11T16:00:00Z"


def test_a_corrupt_watermark_falls_back_to_a_full_walk(tmp_path):
    state = SyncState.load(tmp_path / "m.json")
    state.watermark = "not a timestamp"
    assert state.updated_since() is None


def test_unchanged_requires_both_a_matching_timestamp_and_a_recorded_file(tmp_path):
    state = SyncState.load(tmp_path / "m.json")
    state.record_item("itm_a", updated_at="2026-03-30T19:15:00Z", path="matter/a.md")

    assert state.is_unchanged("itm_a", "2026-03-30T19:15:00Z")
    assert not state.is_unchanged("itm_a", "2026-04-01T00:00:00Z")
    assert not state.is_unchanged("itm_unknown", "2026-03-30T19:15:00Z")


def test_an_item_skipped_as_a_duplicate_is_never_treated_as_synced(tmp_path):
    """No path recorded means no file was written, whatever the timestamp says."""
    state = SyncState.load(tmp_path / "m.json")
    state.record_item("itm_dup", updated_at="2026-03-30T19:15:00Z",
                      skipped_reason="duplicate_url", duplicate_of="2019-01-01 – Same.md")
    assert not state.is_unchanged("itm_dup", "2026-03-30T19:15:00Z")


def test_known_urls_only_includes_items_that_produced_a_file(tmp_path):
    state = SyncState.load(tmp_path / "m.json")
    state.record_item("itm_a", normalized_url="https://e.com/a", path="matter/a.md")
    state.record_item("itm_b", normalized_url="https://e.com/b", skipped_reason="duplicate_url")

    assert state.known_urls() == {"https://e.com/a": "matter/a.md"}


def test_record_item_merges_rather_than_replaces(tmp_path):
    state = SyncState.load(tmp_path / "m.json")
    state.record_item("itm_a", path="matter/a.md", date_saved="2026-01-01")
    state.record_item("itm_a", updated_at="2026-08-01T00:00:00Z")

    record = state.get_item("itm_a")
    assert record["date_saved"] == "2026-01-01"
    assert record["updated_at"] == "2026-08-01T00:00:00Z"


def test_a_corrupt_manifest_is_set_aside_rather_than_wedging_the_job(tmp_path):
    path = tmp_path / ".matter_manifest.json"
    path.write_text("{ this is not json")

    state = SyncState.load(path)

    assert state.items == {}
    assert path.with_suffix(".json.corrupt").exists(), "the damaged file is kept for inspection"


def test_atomic_write_replaces_content_wholesale(tmp_path):
    path = tmp_path / "out.json"
    atomic_write_text(path, "first")
    atomic_write_text(path, "second")
    assert path.read_text() == "second"


def test_a_failed_write_leaves_the_previous_content_intact(tmp_path, monkeypatch):
    """The manifest is the record of what has been pulled; a torn write would corrupt it."""
    path = tmp_path / "out.json"
    atomic_write_text(path, "original")

    def explode(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", explode)
    try:
        atomic_write_text(path, "replacement")
    except OSError:
        pass

    assert path.read_text() == "original"
    leftovers = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == [], "the temp file is cleaned up on failure"


def test_saved_manifest_is_readable_json(tmp_path):
    path = tmp_path / ".matter_manifest.json"
    state = SyncState.load(path)
    state.record_item("itm_a", path="matter/a.md", title="Ünïcodé tïtle")
    state.advance_watermark(datetime(2026, 8, 11, tzinfo=timezone.utc))
    state.save()

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["items"]["itm_a"]["title"] == "Ünïcodé tïtle"
