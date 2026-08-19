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

def test_default_week_midweek_is_last_closed_week():
    assert ws.default_week(dt.date(2026, 8, 19)) == "2026-W33"


def test_default_week_monday_is_the_week_just_ended():
    assert ws.default_week(dt.date(2026, 8, 17)) == "2026-W33"


def test_default_week_SUNDAY_is_that_days_own_week():
    # The 20:00 Sunday job must digest the week ending that evening, not
    # serve a digest seven days stale (round-1 review blocker 1).
    assert ws.default_week(dt.date(2026, 8, 23)) == "2026-W34"
    assert ws.default_week(dt.date(2026, 8, 30)) == "2026-W35"


def test_parse_week_normalizes_and_rejects():
    assert ws.parse_week("2026-W5") == "2026-W05"
    assert ws.parse_week("2026-w33") == "2026-W33"
    import pytest as _pt
    with _pt.raises(SystemExit, match="2027-W53"):
        ws.parse_week("2027-W53")
    with _pt.raises(SystemExit, match="garbage"):
        ws.parse_week("garbage")


def test_week_bounds_iso_year_boundary():
    # 2026 begins on a Thursday, so it has a W53 spanning the new year.
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

def test_top_values_emits_count_pairs_and_drops_singletons():
    s = pd.Series([["a", "b"], ["a"], ["a", "c"], ["b"], ["d"], ["e"], ["f"]])
    top = ws.top_values(s, n=3)
    # Singletons are alphabetical noise (round-1 minor 7): only repeats,
    # as [value, count] pairs.
    assert top == [{"name": "a", "count": 3}, {"name": "b", "count": 2}]


def test_source_host_strips_www_keeps_subdomains_and_survives_empties():
    assert ws.source_host("https://www.theatlantic.com/magazine/x") == "theatlantic.com"
    assert ws.source_host("https://simonsarris.substack.com/p/x") == "simonsarris.substack.com"
    assert ws.source_host("") == ""
    assert ws.source_host(None) == ""


def test_top_sources_counts_distinct_and_repeats():
    rows = pd.DataFrame({"url": ["https://a.com/1", "https://a.com/2",
                                 "https://b.org/1", ""]})
    out = ws.top_sources(rows)
    assert out == {"distinct": 2, "repeats": [{"name": "a.com", "count": 2}]}


def test_heartbeat_timestamps_carry_the_z_suffix(tmp_path, monkeypatch):
    # The Z is the contract with launchd_stats._read_heartbeat; a naive
    # stamp would display a 20:00 CDT run as 01:00 the next day.
    monkeypatch.setattr(ws, "HEARTBEAT", tmp_path / "hb.json")
    ws.write_heartbeat("ok", "2026-W33", 5)
    hb = json.loads((tmp_path / "hb.json").read_text())
    assert hb["started_at"].endswith("Z")


def test_gather_highlights_matter_only_next_heading_and_oversize_skip(tmp_path):
    def article(name, body):
        f = tmp_path / name
        f.write_text(body, encoding="utf-8")
        return str(f)
    rows = pd.DataFrame([
        {"source": "matter", "title": "H", "file_path": article(
            "h.md", "body\n## Highlights\n> quote one\n## NotHighlights\nswallowed?")},
        {"source": "matter", "title": "BIG", "file_path": article(
            "big.md", "## Highlights\n" + "x" * 10000)},
        {"source": "matter", "title": "H2", "file_path": article(
            "h2.md", "## Highlights\n> quote two")},
        {"source": "instapaper", "title": "LEGACY", "file_path": article(
            "l.md", "## Highlights\n> never read - wrong era")},
    ])
    out = ws.gather_highlights(rows)
    assert "quote one" in out and "quote two" in out
    assert "swallowed" not in out          # stops at the next heading
    assert "xxxx" not in out               # oversized skipped, not fatal
    assert "wrong era" not in out          # matter-only


def test_prompt_carries_highlights_and_caps_the_roster():
    rows = pd.DataFrame([{"title": f"T{i}", "word_count": 100, "topics": ["t"],
                          "summary": "s"} for i in range(50)])
    prompt = ws.build_weekly_prompt("2026-W33", rows, "> my highlight")
    assert "my highlight" in prompt
    assert "T39" in prompt and "T40" not in prompt
    assert "10 more articles" in prompt


def test_synthesize_uses_prose_temperature(monkeypatch):
    seen = {}
    def fake(prompt, temperature=None, max_tokens=None):
        seen.update(t=temperature, m=max_tokens)
        return "ok"
    monkeypatch.setattr(ws, "_locked_completion", fake)
    ws.synthesize("p")
    assert seen == {"t": 0.7, "m": 1200}


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


def test_unmounted_vault_heartbeats_fail_not_silence(fake_run, monkeypatch, tmp_path):
    # Round-1 blocker 2: the realistic Sunday failure (NAS asleep) must not
    # leave the cockpit reading last week's stale ok.
    real_mkdir = Path.mkdir
    def deny_out_dir(self, *a, **k):
        if self.name == "synthesis":
            raise PermissionError("Read-only file system")
        return real_mkdir(self, *a, **k)
    monkeypatch.setattr(Path, "mkdir", deny_out_dir)
    rc = run_main(monkeypatch, ["--week", "2026-W33", "--out-dir", str(fake_run)])
    assert rc == 1
    hb = json.loads((tmp_path / "hb.json").read_text())
    assert hb["outcome"] == "fail" and "Read-only" in hb["error"]


def test_bad_week_config_error_still_heartbeats_fail(fake_run, monkeypatch, tmp_path):
    # The SystemExit branch of the blocker-2 fix, unpinned in round 2 (N3).
    with pytest.raises(SystemExit):
        run_main(monkeypatch, ["--week", "garbage", "--out-dir", str(fake_run)])
    hb = json.loads((tmp_path / "hb.json").read_text())
    assert hb["outcome"] == "fail"


def test_dry_run_failure_writes_no_heartbeat(fake_run, monkeypatch, tmp_path):
    def boom(prompt):
        raise RuntimeError("down")
    monkeypatch.setattr(ws, "synthesize", boom)
    rc = run_main(monkeypatch, ["--week", "2026-W33", "--dry-run",
                                "--out-dir", str(fake_run)])
    assert rc == 1
    assert not (tmp_path / "hb.json").exists()


def test_safe_int_and_summary_guard_nan():
    import numpy as np
    assert ws.safe_int(np.float64("nan")) == 0
    rows = pd.DataFrame([{"title": "T", "word_count": np.float64("nan"),
                          "topics": ["t"], "summary": np.float64("nan")}])
    prompt = ws.build_weekly_prompt("2026-W33", rows, "")
    assert "nan" not in prompt


def test_as_list_handles_arrays_none_and_scalars():
    import numpy as np
    assert ws.as_list(np.array(["a", "b"])) == ["a", "b"]
    assert ws.as_list(None) == []
    assert ws.as_list(3) == []


def test_prev_week_delta_is_carried(fake_run, monkeypatch):
    fake_run.mkdir(parents=True)
    (fake_run / "2026-W32.md").write_text(
        "---\narticle_count: 7\ntotal_words: 999\n---\nold\n")
    run_main(monkeypatch, ["--week", "2026-W33",
                           "--out-dir", str(fake_run), "--no-heartbeat"])
    text = (fake_run / "2026-W33.md").read_text()
    assert "prev_week:" in text and "articles: 7" in text
