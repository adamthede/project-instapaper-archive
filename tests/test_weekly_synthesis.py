"""Weekly synthesis: window selection, stats, idempotence, failure posture."""
import datetime as dt
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "core"))
import weekly_synthesis as ws


def make_df(rows):
    base = {
        "source": "matter", "title": "t", "url": "", "word_count": 1000,
        "content_corrupted": False, "topics": [], "people": [], "orgs": [],
        "summary": "s", "file_path": "/nonexistent.md",
        "date_archived": pd.NaT, "date_saved": pd.NaT,
    }
    return pd.DataFrame([{**base, **r} for r in rows]).astype(
        {"date_archived": "datetime64[ns]", "date_saved": "datetime64[ns]"})


# ---- week arithmetic --------------------------------------------------------

def test_last_closed_week_on_a_wednesday():
    assert ws.last_closed_week(dt.date(2026, 8, 19)) == "2026-W33"


def test_last_closed_week_on_a_monday_is_the_week_just_ended():
    assert ws.last_closed_week(dt.date(2026, 8, 17)) == "2026-W33"


def test_last_closed_week_on_a_sunday_is_still_the_prior_week():
    # Sunday's week is not closed until midnight - the 20:00 job must
    # therefore target the week that ENDS that day only via --week; the
    # default is the previous one.
    assert ws.last_closed_week(dt.date(2026, 8, 23)) == "2026-W33"


def test_week_bounds_iso_year_boundary():
    # 2026-W53 does not exist; 2027-W01 starts Monday 2027-01-04,
    # and 2026-W53's absence means 2026-12-31 (Thursday) is 2026-W53? No:
    # ISO gives 2026 exactly 53 weeks only if it starts/ends Thursday.
    # 2026-01-01 is a Thursday, so 2026 HAS a W53 spanning the new year.
    start, end = ws.week_bounds("2026-W53")
    assert start == dt.date(2026, 12, 28)
    assert end == dt.date(2027, 1, 3)


# ---- selection --------------------------------------------------------------

def test_selection_is_inclusive_of_monday_and_sunday():
    df = make_df([
        {"title": "mon", "date_archived": "2026-08-10"},
        {"title": "sun", "date_archived": "2026-08-16"},
        {"title": "before", "date_archived": "2026-08-09"},
        {"title": "after", "date_archived": "2026-08-17"},
    ])
    got = set(ws.select_week(df, "2026-W33")["title"])
    assert got == {"mon", "sun"}


def test_matter_rows_never_date_by_date_saved():
    # A saved-not-read Matter article must not enter the week it was saved.
    df = make_df([{"title": "unread", "date_saved": "2026-08-12"}])
    assert ws.select_week(df, "2026-W33").empty


def test_legacy_rows_fall_back_to_date_saved():
    df = make_df([{"title": "old", "source": "instapaper",
                   "date_saved": "2026-08-12"}])
    assert list(ws.select_week(df, "2026-W33")["title"]) == ["old"]


def test_corrupted_rows_are_excluded():
    df = make_df([{"title": "junk", "date_archived": "2026-08-12",
                   "content_corrupted": True}])
    assert ws.select_week(df, "2026-W33").empty


# ---- stats ------------------------------------------------------------------

def test_top_values_ranks_and_caps():
    s = pd.Series([["a", "b"], ["a"], ["a", "c"], ["b"], ["d"], ["e"], ["f"]])
    top = ws.top_values(s, n=3)
    assert top[0] == "a" and len(top) == 3


def test_week_rereads_counts_only_recorded_in_window(tmp_path):
    manifest = {"items": {
        "in": {"reread_date": "2026-08-12", "reread_recorded": True},
        "out": {"reread_date": "2026-08-01", "reread_recorded": True},
        "unrecorded": {"reread_date": "2026-08-13", "reread_recorded": False},
    }}
    (tmp_path / ".matter_manifest.json").write_text(json.dumps(manifest))
    assert ws.week_rereads(tmp_path, dt.date(2026, 8, 10), dt.date(2026, 8, 16)) == 1


def test_week_rereads_missing_manifest_is_zero(tmp_path):
    assert ws.week_rereads(tmp_path, dt.date(2026, 8, 10), dt.date(2026, 8, 16)) == 0


# ---- end-to-end with a fake model ------------------------------------------

@pytest.fixture
def fake_run(tmp_path, monkeypatch):
    df = make_df([
        {"title": "One", "date_archived": "2026-08-11", "word_count": 2380,
         "topics": ["ai"], "summary": "About AI."},
        {"title": "Two", "date_archived": "2026-08-14", "word_count": 1190,
         "topics": ["ai", "climate"], "summary": "About climate."},
    ])
    idx = tmp_path / "archive_index.parquet"
    df.to_parquet(idx)
    monkeypatch.setattr(ws, "INDEX_PATH", idx)
    monkeypatch.setattr(ws, "HEARTBEAT", tmp_path / "hb.json")
    monkeypatch.setattr(ws, "synthesize", lambda prompt: "A woven digest.")
    out_dir = tmp_path / "synthesis"
    return out_dir


def run_main(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["weekly_synthesis.py"] + argv)
    return ws.main()


def test_writes_frontmatter_stats_and_prose(fake_run, monkeypatch):
    assert run_main(monkeypatch, ["--week", "2026-W33",
                                  "--out-dir", str(fake_run), "--no-heartbeat"]) == 0
    text = (fake_run / "2026-W33.md").read_text()
    assert "article_count: 2" in text
    assert "total_words: 3570" in text
    assert "reading_time_hours: 0.2" in text
    assert text.rstrip().endswith("A woven digest.")


def test_regeneration_overwrites_idempotently(fake_run, monkeypatch):
    for _ in range(2):
        run_main(monkeypatch, ["--week", "2026-W33",
                               "--out-dir", str(fake_run), "--no-heartbeat"])
    files = list(fake_run.glob("*.md"))
    assert len(files) == 1


def test_dry_run_writes_nothing(fake_run, monkeypatch, capsys):
    assert run_main(monkeypatch, ["--week", "2026-W33", "--dry-run",
                                  "--out-dir", str(fake_run)]) == 0
    assert not fake_run.exists()
    assert "article_count: 2" in capsys.readouterr().out


def test_empty_week_is_ok_not_fail(fake_run, monkeypatch):
    assert run_main(monkeypatch, ["--week", "2026-W20",
                                  "--out-dir", str(fake_run), "--no-heartbeat"]) == 0
    assert not fake_run.exists()


def test_model_failure_is_nonzero_and_heartbeats_fail(fake_run, monkeypatch, tmp_path):
    def boom(prompt):
        raise RuntimeError("LM Studio unreachable")
    monkeypatch.setattr(ws, "synthesize", boom)
    rc = run_main(monkeypatch, ["--week", "2026-W33", "--out-dir", str(fake_run)])
    assert rc == 1
    assert not fake_run.exists()
    hb = json.loads((tmp_path / "hb.json").read_text())
    assert hb["outcome"] == "fail"
    assert "unreachable" in hb["error"]


def test_prev_week_delta_is_carried(fake_run, monkeypatch):
    fake_run.mkdir(parents=True)
    (fake_run / "2026-W32.md").write_text(
        "---\narticle_count: 7\ntotal_words: 999\n---\nold\n")
    run_main(monkeypatch, ["--week", "2026-W33",
                           "--out-dir", str(fake_run), "--no-heartbeat"])
    text = (fake_run / "2026-W33.md").read_text()
    assert "prev_week:" in text and "articles: 7" in text
