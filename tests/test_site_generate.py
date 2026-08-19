"""Site generator: loading, prose splitting, rendering, escaping, end-to-end."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "site"))
import generate as gen


WEEK_MD = """---
week: 2026-W33
week_start: '2026-08-10'
week_end: '2026-08-16'
generated: '2026-08-19'
model: qwen3.6-35b-a3b-mtp
article_count: 2
total_words: 3570
reading_time_hours: 0.3
top_topics:
- name: AI
  count: 2
top_people: []
top_orgs: []
rereads_recorded: 0
articles:
- title: One <script>alert(1)</script>
  url: https://www.example.com/one
  words: 2380
  date_read: '2026-08-11'
- title: Two
  url: https://sub.example.org/two
  words: 1190
  date_read: '2026-08-14'
---

First paragraph of the digest.

Second paragraph. The thread of the week is agency amid acceleration.
"""


@pytest.fixture
def synth_dir(tmp_path):
    d = tmp_path / "synthesis"
    d.mkdir()
    (d / "2026-W33.md").write_text(WEEK_MD, encoding="utf-8")
    return d


def test_load_weeks_skips_malformed_files_loudly(synth_dir, capsys):
    (synth_dir / "2026-W34.md").write_text("no frontmatter at all")
    weeks = gen.load_weeks(synth_dir)
    assert [w["week"] for w in weeks] == ["2026-W33"]
    assert "2026-W34" in capsys.readouterr().err


def test_load_weeks_derives_missing_sources(synth_dir):
    weeks = gen.load_weeks(synth_dir)
    assert weeks[0]["articles"][0]["source"] == "example.com"
    assert weeks[0]["articles"][1]["source"] == "sub.example.org"


def test_split_prose_lifts_the_thread_sentence():
    paras, thread = gen.split_prose(
        "Alpha.\n\nBeta. The thread of the week is agency.")
    assert paras == ["Alpha.", "Beta."]
    assert thread == "The thread of the week is agency."


def test_split_prose_without_thread_is_untouched():
    paras, thread = gen.split_prose("Alpha.\n\nBeta ends plainly.")
    assert thread is None
    assert paras == ["Alpha.", "Beta ends plainly."]


def test_day_series_covers_monday_to_sunday(synth_dir):
    m = gen.load_weeks(synth_dir)[0]
    days = gen.day_series(m)
    assert [d["label"] for d in days] == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    assert days[1]["words"] == 2380 and days[4]["words"] == 1190
    assert sum(d["count"] for d in days) == 2


def test_week_page_escapes_hostile_titles(synth_dir):
    m = gen.load_weeks(synth_dir)[0]
    html = gen.render_week(m)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_week_page_carries_stats_prose_and_nav(synth_dir):
    m = gen.load_weeks(synth_dir)[0]
    html = gen.render_week(m, prev_wk="2026-W32", next_wk="2026-W34")
    for needle in ("3,570", "First paragraph", "Thread of the week",
                   'href="../2026-W32/"', 'href="../2026-W34/"',
                   "2,380", "example.com"):
        assert needle in html, needle


def test_index_totals_and_trend(synth_dir):
    weeks = gen.load_weeks(synth_dir)
    html = gen.render_index(weeks)
    assert "3,570" in html and 'href="weeks/2026-W33/"' in html


def test_generate_end_to_end_and_idempotent(synth_dir, tmp_path):
    out = tmp_path / "_site"
    for _ in range(2):
        count = gen.generate(synth_dir, out)
    assert count == 1
    assert (out / "index.html").exists()
    assert (out / "style.css").exists()
    assert (out / "weeks" / "2026-W33" / "index.html").exists()


def test_generate_refuses_an_empty_dir(tmp_path):
    empty = tmp_path / "none"
    empty.mkdir()
    with pytest.raises(SystemExit):
        gen.generate(empty, tmp_path / "_site")
