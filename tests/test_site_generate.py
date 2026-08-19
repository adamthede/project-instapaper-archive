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


WEEK2_MD = WEEK_MD.replace("2026-W33", "2025-W50").replace("'2026-08-10'", "'2025-12-08'") \
                  .replace("'2026-08-16'", "'2025-12-14'") \
                  .replace("'2026-08-11'", "'2025-12-09'").replace("'2026-08-14'", "'2025-12-12'")


@pytest.fixture
def two_year_dir(tmp_path):
    d = tmp_path / "synthesis"
    d.mkdir()
    (d / "2026-W33.md").write_text(WEEK_MD, encoding="utf-8")
    (d / "2025-W50.md").write_text(WEEK2_MD, encoding="utf-8")
    return d


def test_index_lists_every_week_with_year_grouping(two_year_dir):
    # Round-1 minor 6: a surviving mutant dropped a week from the row list
    # and nothing failed. Every week gets a row AND a trend bar; years head.
    weeks = gen.load_weeks(two_year_dir)
    html_out = gen.render_index(weeks)
    for w in ("2026-W33", "2025-W50"):
        assert html_out.count(f'href="weeks/{w}/"') == 2, w  # trend + row
    assert '>2026</div>' in html_out and '>2025</div>' in html_out


def test_bad_week_is_skipped_and_prior_site_survives(two_year_dir, tmp_path, capsys):
    out = tmp_path / "_site"
    gen.generate(two_year_dir, out)
    # Poison one week: null hours used to kill the whole build AFTER the old
    # site had already been deleted (round-1 blocker 1).
    bad = (two_year_dir / "2025-W50.md").read_text().replace(
        "reading_time_hours: 0.3", "reading_time_hours: null")
    (two_year_dir / "2025-W50.md").write_text(bad)
    count = gen.generate(two_year_dir, out)
    assert count == 1
    assert (out / "weeks" / "2026-W33" / "index.html").exists()
    assert "2025-W50" in capsys.readouterr().err


def test_out_dir_guard_refuses_foreign_directories(two_year_dir, tmp_path):
    precious = tmp_path / "precious"
    precious.mkdir()
    (precious / "irreplaceable.md").write_text("do not delete")
    with pytest.raises(SystemExit, match="Refusing to overwrite"):
        gen.generate(two_year_dir, precious)
    assert (precious / "irreplaceable.md").exists()


def test_regeneration_over_own_output_is_allowed(two_year_dir, tmp_path):
    out = tmp_path / "_site"
    gen.generate(two_year_dir, out)
    gen.generate(two_year_dir, out)  # marker present -> no refusal
    assert (out / gen.MARKER).exists()


def test_javascript_scheme_and_missing_urls_render_nonlinks(synth_dir):
    m = gen.load_weeks(synth_dir)[0]
    m["articles"][0]["url"] = "javascript:alert(1)"
    m["articles"][1]["url"] = ""
    html_out = gen.render_week(m)
    assert "javascript:" not in html_out
    assert 'href="#"' not in html_out
    assert html_out.count('<span class="row') == 2  # both are non-links


def test_hostile_topic_count_is_escaped(synth_dir):
    m = gen.load_weeks(synth_dir)[0]
    m["top_topics"] = [{"name": "AI", "count": '1"><script>x</script>'}]
    html_out = gen.render_week(m)
    assert "<script>x</script>" not in html_out


def test_index_hours_keep_their_decimal(two_year_dir):
    weeks = gen.load_weeks(two_year_dir)
    html_out = gen.render_index(weeks)
    assert "0.6<em> hrs</em>" in html_out  # 0.3 + 0.3, not int-truncated


def test_plain_string_topic_fallback_is_whole_word(synth_dir):
    m = gen.load_weeks(synth_dir)[0]
    m["top_topics"] = ["Artificial Intelligence"]
    html_out = gen.render_index([m])
    assert "Artificial Intelligence" in html_out
