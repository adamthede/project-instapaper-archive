"""Phase 5b: the trends layer, the facets, and the people cleanup.

Everything here is synthetic. The real Parquet index is never read - these
tests must pass on a machine that has never seen Adam's archive.
"""
import json
import re
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "site"))
sys.path.insert(0, str(ROOT / "scripts" / "core"))

import corpus  # noqa: E402
import deepdives  # noqa: E402
import entity_hygiene  # noqa: E402
import generate as gen  # noqa: E402
import trends  # noqa: E402


HOSTILE = 'Bad <script>alert(1)</script> "quote" & <b>'


def row(**over):
    base = {
        "instapaper_id": None, "matter_id": None, "source": "instapaper",
        "content_type": "article", "title": "A Title",
        "url": "https://www.example.com/a", "author": "Ada Lovelace",
        "date_saved": "2012-03-01", "date_archived": "2012-03-02",
        "word_count": 1000, "content_corrupted": False, "reading_time_min": 5.0,
        "grade_level": 11.0, "topics": ["AI"], "people": ["Ada Lovelace"],
        "orgs": ["Google"], "locations": ["London"], "concepts": ["Compute"],
        "sentiment": "Neutral", "emotion": "Analytical", "summary": "s",
        "file_path": "/x.md", "content_snippet": "snip",
    }
    base.update(over)
    return base


def build(rows):
    df = pd.DataFrame(rows)
    df["date_saved"] = pd.to_datetime(df["date_saved"])
    df["date_archived"] = pd.to_datetime(df["date_archived"])
    return corpus.prepare(df)


# ---------------------------------------------------------------------------
# hero stats + era split
# ---------------------------------------------------------------------------

def test_era_split_folds_five_legacy_scanners_into_one_era():
    c = build([row(source=s, date_archived="2008-01-0%d" % (i + 1))
               for i, s in enumerate(["legacy_pdf", "legacy_txt", "legacy_doc",
                                      "legacy_htm", "legacy_rtf"])]
              + [row(source="instapaper"), row(source="instapaper"),
                 row(source="matter", date_archived="2023-05-05")])
    split = corpus.era_split(c.rows)
    by_era = {s["era"]: s for s in split}
    assert by_era["legacy"]["articles"] == 5
    assert by_era["instapaper"]["articles"] == 2
    assert by_era["matter"]["articles"] == 1
    # Chronological, not by size: legacy first even when instapaper is bigger.
    assert [s["era"] for s in split] == ["legacy", "instapaper", "matter"]


def test_era_shares_are_of_the_whole_archive_and_sum_to_100():
    c = build([row(source="legacy_pdf")] * 3 + [row(source="instapaper")]
              + [row(source="matter", date_archived="2023-05-05")] * 4)
    split = corpus.era_split(c.rows)
    assert sum(s["articles"] for s in split) == len(c.rows) == 8
    assert abs(sum(s["share"] for s in split) - 100.0) < 0.2
    assert {s["era"]: s["share"] for s in split}["legacy"] == 37.5


def test_unrecognised_source_becomes_unattributed_not_dropped():
    c = build([row(source="instapaper"), row(source="wat"), row(source=None)])
    split = corpus.era_split(c.rows)
    assert sum(s["articles"] for s in split) == 3
    assert {s["era"] for s in split} == {"instapaper", "unknown"}


def test_hero_totals_come_from_the_index_not_the_week_files():
    c = build([row(word_count=1000, reading_time_min=600.0),
               row(word_count=3000, reading_time_min=1200.0)])
    html = gen.render_hero(c)
    assert "4,000" in html                       # words, summed from the index
    assert "30<em> hrs</em>" in html             # 1,800 minutes
    assert '2</div><div class="l label">Articles' in html


def test_hero_escapes_and_never_lets_an_era_bar_exceed_the_row():
    c = build([row(source="legacy_pdf")] * 7 + [row(source="instapaper")])
    html = gen.render_hero(c)
    widths = [float(w) for w in re.findall(r'width:([\d.]+)%', html)]
    assert widths and sum(widths) <= 100.5
    assert all(0 <= w <= 100 for w in widths)


def week(article_count=4, **over):
    base = {"week": "2012-W10", "week_start": "2012-03-05",
            "week_end": "2012-03-11", "article_count": article_count,
            "total_words": 900, "reading_time_hours": 1.5, "prose": "p",
            "articles": [], "top_topics": []}
    base.update(over)
    return base


def test_index_reports_synthesis_coverage_not_a_second_copy_of_the_totals():
    """The week files sum to exactly the archive totals after a full backfill,
    so echoing them was a redundant row. Coverage is the thing the hero cannot
    already tell you."""
    c = build([row() for _ in range(4)])
    html = gen.render_index([week(article_count=4)], corpus_data=c)
    assert "Every one of these articles sits in one of the 1 weekly syntheses" in html
    assert "17,259,758" not in html


def test_a_partial_backfill_reports_the_gap_rather_than_claiming_coverage():
    c = build([row() for _ in range(10)])
    html = gen.render_index([week(article_count=4)], corpus_data=c)
    assert "cover 4 of these 10 articles" in html


def test_index_without_a_corpus_falls_back_to_week_derived_stats():
    html = gen.render_index([week()], corpus_data=None)
    assert "Weeks" in html and "erabar" not in html


# ---------------------------------------------------------------------------
# complexity
# ---------------------------------------------------------------------------

def test_grade_level_is_clipped_before_it_is_averaged():
    c = build([row(grade_level=10.0), row(grade_level=12.0),
               row(grade_level=857.0), row(grade_level=-3.0)])
    st = corpus.complexity_stats(c.rows)
    # clipped: (10 + 12 + 20 + 0) / 4 = 10.5. Unclipped would be 219.0.
    assert st["avg"] == 10.5
    assert st["raw_avg"] == 219.0
    assert st["clipped"] == 2
    assert st["graded"] == 4


def test_a_clipped_row_can_never_be_the_densest_read():
    """The 857 row clips to 20.0, the top of the band. If the densest read were
    picked off the clipped series it would win every time."""
    c = build([row(title="Real", grade_level=17.0, word_count=1200),
               row(title="Parser noise", grade_level=857.0, word_count=5000)])
    st = corpus.complexity_stats(c.rows)
    assert st["densest"]["title"] == "Real"
    assert st["densest"]["grade"] == 17.0


def test_densest_read_ignores_stubs_below_the_word_floor():
    c = build([row(title="Long", grade_level=14.0, word_count=2000),
               row(title="Stub", grade_level=19.9, word_count=120)])
    st = corpus.complexity_stats(c.rows)
    assert st["densest"]["title"] == "Long"


def test_densest_read_is_none_when_nothing_clears_the_floor():
    c = build([row(grade_level=14.0, word_count=100)])
    assert corpus.complexity_stats(c.rows)["densest"] is None


def test_densest_read_drops_a_non_http_url_rather_than_linking_it():
    c = build([row(title="X", grade_level=14.0, word_count=2000,
                   url="javascript:alert(1)")])
    st = corpus.complexity_stats(c.rows)
    assert st["densest"]["url"] == ""


def test_complexity_delta_is_against_the_previous_year_with_data():
    c = build([row(date_archived="2012-06-01", grade_level=10.0),
               row(date_archived="2013-06-01", grade_level=12.5),
               row(date_archived="2015-06-01", grade_level=11.0)])
    series = {r["year"]: r for r in corpus.complexity_by_year(c)}
    assert series[2012]["delta"] is None
    assert series[2013]["delta"] == 2.5
    # 2014 is empty, so 2015 compares against 2013 - the last year that had one.
    assert series[2014]["avg"] is None and series[2014]["delta"] is None
    assert series[2015]["delta"] == -1.5


def test_a_three_article_year_cannot_be_crowned_the_densest():
    """The real 2021: three articles averaging grade 14.00, the highest figure
    in a twenty-two year series. It must be drawn, marked, and ineligible."""
    fat = [row(date_archived="2012-%02d-01" % (m + 1), grade_level=11.0)
           for m in range(12)] * 3
    thin = [row(date_archived="2013-01-01", grade_level=19.0)] * 3
    c = build(fat + thin)
    series = {r["year"]: r for r in corpus.complexity_by_year(c)}
    assert series[2013]["low"] is True and series[2013]["graded"] == 3
    assert series[2012]["low"] is False
    html, top, bottom = trends.complexity_band(corpus.complexity_by_year(c))
    assert top["year"] == 2012 and bottom["year"] == 2012
    assert "thin" in html                       # drawn and marked, not hidden
    assert "read as noise" in html


def test_the_page_names_the_thin_years_it_refused_to_crown():
    fat = [row(date_archived="2012-%02d-01" % (m + 1), grade_level=11.0)
           for m in range(12)] * 3
    thin = [row(date_archived="2013-01-01", grade_level=19.0)] * 3
    html = trends.render_trends(build(fat + thin))
    assert "not eligible to be named the densest" in html
    assert "2013" in html


def test_every_year_is_eligible_when_none_of_them_are_thin():
    thin = [row(date_archived="2012-01-01", grade_level=11.0)] * 3
    series = corpus.complexity_by_year(build(thin))
    # all thin: the callout falls back to the whole series rather than vanishing
    _, top, bottom = trends.complexity_band(series)
    assert top["year"] == 2012 and bottom["year"] == 2012


def test_complexity_by_year_keeps_empty_years_in_the_series():
    c = build([row(date_archived="2010-06-01"), row(date_archived="2014-06-01")])
    years = [r["year"] for r in corpus.complexity_by_year(c)]
    assert years == [2010, 2011, 2012, 2013, 2014]


def test_missing_grade_column_degrades_instead_of_killing_the_build(capsys):
    rows = [row() for _ in range(2)]
    for r in rows:
        del r["grade_level"]
    c = build(rows)
    assert corpus.complexity_stats(c.rows)["avg"] is None
    assert corpus.grade_series(c.rows).isna().all()
    assert "grade_level" in capsys.readouterr().err


def test_retyped_grade_column_still_fails_loud():
    """Absence degrades; a retype must not. Strings here would otherwise
    average to nothing and report a confident, wrong number."""
    c = build([row(grade_level="11.0"), row(grade_level="12.0")])
    with pytest.raises(ValueError, match="schema drift"):
        corpus.grade_series(c.rows)


def test_year_page_shows_the_reading_level_and_its_delta():
    c = build([row(date_archived="2012-06-01", grade_level=10.0, word_count=1500),
               row(date_archived="2013-06-01", grade_level=13.0, word_count=1500,
                   title="Harder")])
    html = deepdives.render_year(c, 2013, prev_year=2012)
    assert "Average reading level" in html
    assert "13.0" in html
    assert "▲ 3.00 vs 2012" in html
    assert "Densest read of the year" in html and "Harder" in html


def test_year_page_omits_the_reading_level_when_there_is_none():
    rows = [row(date_archived="2013-06-01")]
    del rows[0]["grade_level"]
    c = build(rows)
    html = deepdives.render_year(c, 2013)
    assert "Average reading level" not in html
    assert "Densest read" not in html


def test_densest_read_title_is_escaped_on_the_year_page():
    c = build([row(date_archived="2013-06-01", title=HOSTILE, grade_level=15.0,
                   word_count=2000)])
    html = deepdives.render_year(c, 2013)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


# ---------------------------------------------------------------------------
# sentiment
# ---------------------------------------------------------------------------

def test_sentiment_shares_sum_to_exactly_100_for_every_rated_year():
    """Three independently rounded shares can miss 100 by a tenth, and the
    strip would render a sliver of nothing. 1/3-1/3-1/3 is the classic case."""
    c = build([row(date_archived="2012-01-0%d" % i, sentiment=s)
               for i, s in enumerate(["Positive", "Neutral", "Negative"], start=1)])
    for year in corpus.sentiment_by_year(c):
        if year["rated"]:
            assert sum(year["shares"].values()) == 100.0


def test_unrated_and_unknown_sentiment_stay_out_of_the_denominator():
    c = build([row(sentiment="Positive"), row(sentiment="Neutral"),
               row(sentiment=None), row(sentiment="Sarcastic")])
    year = corpus.sentiment_by_year(c)[0]
    assert year["rated"] == 2
    assert year["other"] == 1
    assert year["shares"]["Positive"] == 50.0
    assert sum(year["shares"].values()) == 100.0


def test_a_year_with_no_rated_articles_reports_zeros_not_a_division_error():
    c = build([row(sentiment=None), row(sentiment=None)])
    year = corpus.sentiment_by_year(c)[0]
    assert year["rated"] == 0
    assert year["shares"] == {"Positive": 0.0, "Neutral": 0.0, "Negative": 0.0}


def test_thin_years_are_flagged_low_and_marked_in_the_strip():
    c = build([row(date_archived="2012-01-01", sentiment="Negative"),
               row(date_archived="2013-01-01", sentiment="Positive")]
              + [row(date_archived="2013-02-%02d" % (i + 1), sentiment="Neutral")
                 for i in range(28)])
    series = {y["year"]: y for y in corpus.sentiment_by_year(c)}
    assert series[2012]["low"] is True and series[2012]["rated"] == 1
    assert series[2013]["low"] is False and series[2013]["rated"] == 29
    html = trends.sentiment_strip(corpus.sentiment_by_year(c), [2012, 2013])
    assert "lown" in html
    # Named in text, never by colour alone.
    for label in corpus.SENTIMENTS:
        assert f">{label}</th>" in html


def test_sentiment_strip_scales_within_a_row_not_across_rows():
    """Neutral dominates every year of the real corpus. A shared scale would
    render Negative uniformly dark and hide the spike that is the whole point."""
    c = build([row(date_archived="2012-01-%02d" % (i + 1),
                   sentiment="Neutral" if i else "Negative") for i in range(30)]
              + [row(date_archived="2013-01-%02d" % (i + 1),
                     sentiment="Negative" if i < 15 else "Neutral")
                 for i in range(30)])
    html = trends.sentiment_strip(corpus.sentiment_by_year(c), [2012, 2013])
    neg_row = html.split('>Negative</th>')[1].split("</tr>")[0]
    intensities = [float(v) for v in re.findall(r'--i:([\d.]+)', neg_row)]
    # 2013 is Negative's own peak, so it reaches full intensity even though
    # Neutral is the bigger number in both years.
    assert intensities[-1] == 1.0
    assert intensities[0] < intensities[-1]


# ---------------------------------------------------------------------------
# heatmaps
# ---------------------------------------------------------------------------

def test_entity_matrix_counts_an_article_once_per_year_not_once_per_mention():
    c = build([row(date_archived="2012-06-01", orgs=["Google", "Google", "Apple"]),
               row(date_archived="2012-07-01", orgs=["Google"])])
    m = corpus.entity_year_matrix(c, "orgs", 10)
    assert m["cells"]["Google"][2012] == 2
    assert m["cells"]["Apple"][2012] == 1


def test_matrix_keeps_years_with_no_hits_as_real_zero_columns():
    c = build([row(date_archived="2012-06-01", orgs=["Google"]),
               row(date_archived="2014-06-01", orgs=["Apple"])])
    m = corpus.entity_year_matrix(c, "orgs", 10)
    assert m["years"] == [2012, 2013, 2014]
    assert m["cells"]["Google"] == {2012: 1, 2013: 0, 2014: 0}
    assert m["year_totals"][2013] == 0


def test_domain_matrix_covers_only_url_bearing_rows():
    c = build([row(url="https://nytimes.com/a"), row(url="https://nytimes.com/b"),
               row(url="")])
    m = corpus.domain_year_matrix(c, 10)
    assert m["names"] == ["nytimes.com"]
    assert m["row_totals"]["nytimes.com"] == 2
    assert m["year_totals"][2012] == 3          # denominator is ALL articles


def test_cell_intensity_is_sqrt_scaled_zero_at_zero_and_capped_at_one():
    assert trends._intensity(0, 100) == 0.0
    assert trends._intensity(100, 100) == 1.0
    assert trends._intensity(25, 100) == 0.5    # sqrt(0.25), not 0.25
    assert trends._intensity(5, 0) == 0.0       # empty matrix, no ZeroDivision
    assert trends._intensity(150, 100) == 1.0   # never overflows the ramp


def test_heatmap_escapes_hostile_entity_names_in_cells_and_tooltips():
    c = build([row(date_archived="2012-06-01", orgs=[HOSTILE]),
               row(date_archived="2013-06-01", orgs=[HOSTILE])])
    html = trends.heatmap(corpus.entity_year_matrix(c, "orgs", 10),
                          "cap", "Organization")
    assert "<script>" not in html
    assert "alert(1)" in html          # present, but neutered
    assert html.count("&lt;script&gt;") >= 2   # row header AND tooltips
    assert 'data-tip="Bad &lt;' in html or "&quot;quote&quot;" in html


def test_heatmap_tooltip_carries_the_count_and_the_share_of_that_year():
    c = build([row(date_archived="2012-06-01", orgs=["Google"]),
               row(date_archived="2012-07-01", orgs=["Google"]),
               row(date_archived="2012-08-01", orgs=["Apple"]),
               row(date_archived="2012-09-01", orgs=["Apple"])])
    html = trends.heatmap(corpus.domain_year_matrix(c, 10), "cap", "Source")
    assert "100.0% of that year" in html
    html2 = trends.heatmap(corpus.entity_year_matrix(c, "orgs", 10), "cap", "Org")
    assert "50.0% of that year" in html2


def test_heatmap_renders_an_empty_note_rather_than_a_headless_table():
    c = build([row(orgs=[], locations=[])])
    html = trends.heatmap(corpus.entity_year_matrix(c, "orgs", 10), "cap",
                          "Org", empty="no organizations tagged")
    assert "<table" not in html
    assert "no organizations tagged" in html


def test_wide_matrices_scroll_inside_their_own_container():
    c = build([row(date_archived="2012-06-01", orgs=["Google"])])
    html = trends.heatmap(corpus.entity_year_matrix(c, "orgs", 10), "cap", "Org")
    assert 'class="hmwrap"' in html
    assert "overflow-x:auto" in trends.TRENDS_STYLE
    # and the page itself must never be the thing that scrolls
    assert "overflow-x:clip" in gen.STYLE


# ---------------------------------------------------------------------------
# the rankability measurement
# ---------------------------------------------------------------------------

def test_head_coverage_is_a_set_question_not_a_sum():
    """An article tagged with two head values is ONE covered article.

    Head is {A, B}, each counted twice. Summing the head counts gives
    (2 + 2) / 3 = 133%; the honest set answer is 2 covered articles of 3.
    This is the exact error that reported 83% where the audit measured 42.9%.
    """
    c = build([row(concepts=["A", "B"]), row(concepts=["A", "B"]),
               row(concepts=["C"])])
    report = corpus.vocabulary_report(c.rows, "concepts", k=2)
    assert report["head_coverage"] == 66.7


def test_vocabulary_report_measures_singletons_and_tagged_share():
    c = build([row(concepts=["Shared", "Once"]), row(concepts=["Shared"]),
               row(concepts=[])])
    report = corpus.vocabulary_report(c.rows, "concepts", k=1)
    assert report["vocabulary"] == 2
    assert report["tagged_share"] == 66.7
    assert report["singleton_share"] == 50.0
    assert report["head_coverage"] == 66.7      # "Shared" covers 2 of 3


def test_rankable_is_decided_by_the_stated_bar():
    thin = build([row(concepts=[f"c{i}"]) for i in range(10)])
    assert corpus.vocabulary_report(thin.rows, "concepts", k=2)["rankable"] is False
    dense = build([row(concepts=["Same"]) for _ in range(10)])
    report = corpus.vocabulary_report(dense.rows, "concepts", k=2)
    assert report["head_coverage"] == 100.0
    assert report["rankable"] is True
    assert corpus.RANKABLE_HEAD_COVERAGE == 40.0


def test_vocabulary_report_on_an_absent_column_is_empty_not_an_error():
    c = build([row()])
    assert corpus.vocabulary_report(c.rows, "nope")["vocabulary"] == 0


def test_concepts_verdict_gates_the_page_and_reports_its_numbers():
    """A long tail relative to k is the whole point: 60 concepts used once
    each means the top 20 reach a third of the archive and no further."""
    thin = build([row(concepts=[f"c{i}"]) for i in range(60)])
    report, rankable, bar = deepdives.concepts_verdict(thin)
    assert report["vocabulary"] == 60
    assert report["head_coverage"] == 33.3
    assert report["singleton_share"] == 100.0
    assert rankable is False and bar == 40.0


# ---------------------------------------------------------------------------
# people cleanup
# ---------------------------------------------------------------------------

FABRICATED = ["Josh Earnest", "Antonia Iamartino", "Deb Haaland",
              "Todd Sherman", "Todd Kaplan", "Jony Ive"]


def codesign_rows(n=12, corrupted=True, **over):
    """The measured shape of the defect: one host, one exact word count, one
    identical extracted cast."""
    return [row(title=f"Co.Design {i}", url=f"https://www.fastcodesign.com/{i}",
                word_count=642, people=list(FABRICATED),
                content_corrupted=corrupted, **over) for i in range(n)]


def test_the_fabricated_cluster_is_found_and_named():
    df = pd.DataFrame(codesign_rows(12) + [row(people=["Tim Cook"])])
    out, clusters = entity_hygiene.scrub(df, log=lambda m: None)
    assert len(clusters) == 1
    assert clusters[0]["host"] == "fastcodesign.com"
    assert clusters[0]["word_count"] == 642
    assert set(clusters[0]["names"]) == set(FABRICATED)
    assert len(clusters[0]["row_ids"]) == 12


def test_scrub_blanks_only_the_cluster_and_leaves_real_people_alone():
    df = pd.DataFrame(codesign_rows(12) + [row(people=["Tim Cook"])])
    out, _ = entity_hygiene.scrub(df, log=lambda m: None)
    assert list(out["people"])[:12] == [[]] * 12
    assert list(out["people"])[12] == ["Tim Cook"]
    # Nothing but the one column moves: the articles still count everywhere.
    assert len(out) == len(df)
    assert list(out["word_count"]) == list(df["word_count"])
    assert list(out["orgs"]) == list(df["orgs"])


def test_scrub_never_drops_the_names_from_articles_that_really_are_about_them():
    """Jony Ive is in the fabricated cast AND a real subject. A blocklist would
    erase him from the archive; a cluster rule must not."""
    df = pd.DataFrame(codesign_rows(12) + [
        row(title="Real Ive profile", url="https://newyorker.com/ive",
            word_count=4200, people=["Jony Ive"])])
    out, _ = entity_hygiene.scrub(df, log=lambda m: None)
    assert list(out["people"])[12] == ["Jony Ive"]


def test_a_recurring_single_name_is_a_person_not_boilerplate():
    df = pd.DataFrame([row(url="https://businessinsider.com/%d" % i,
                           word_count=500, people=["Steve Jobs"])
                       for i in range(40)])
    out, clusters = entity_hygiene.scrub(df, log=lambda m: None)
    assert clusters == []
    assert all(p == ["Steve Jobs"] for p in out["people"])


def test_the_same_cast_at_different_lengths_is_a_byline_not_furniture():
    df = pd.DataFrame([row(url="https://adventurecycling.org/%d" % i,
                           word_count=900 + i * 40,
                           people=["Alissa Bell", "Brielle Wacker"])
                       for i in range(30)])
    _, clusters = entity_hygiene.scrub(df, log=lambda m: None)
    assert clusters == []


def test_the_same_cast_across_different_hosts_is_not_one_cluster():
    df = pd.DataFrame([row(url="https://host%d.com/a" % i, word_count=642,
                           people=["A Name", "B Name"]) for i in range(30)])
    _, clusters = entity_hygiene.scrub(df, log=lambda m: None)
    assert clusters == []


def test_a_cluster_under_the_threshold_is_left_alone():
    df = pd.DataFrame(codesign_rows(entity_hygiene.MIN_CLUSTER - 1))
    _, clusters = entity_hygiene.scrub(df, log=lambda m: None)
    assert clusters == []


def test_cluster_membership_ignores_order_and_duplicates():
    rows = codesign_rows(6)
    rows += [row(title="reordered", url="https://www.fastcodesign.com/x",
                 word_count=642, people=list(reversed(FABRICATED)) + ["Jony Ive"])
             for _ in range(6)]
    df = pd.DataFrame(rows)
    _, clusters = entity_hygiene.scrub(df, log=lambda m: None)
    assert len(clusters) == 1 and len(clusters[0]["row_ids"]) == 12


def test_the_scrub_reports_every_row_it_touches():
    """No silent drops - the count, the host, the word count and the names all
    have to reach the build log."""
    df = pd.DataFrame(codesign_rows(12))
    lines = []
    entity_hygiene.scrub(df, log=lines.append)
    blob = "\n".join(lines)
    assert "12" in blob and "fastcodesign.com" in blob and "642" in blob
    for name in FABRICATED:
        assert name in blob


def test_misaligned_hosts_raise_rather_than_scrub_the_wrong_articles():
    df = pd.DataFrame(codesign_rows(12))
    with pytest.raises(ValueError, match="misaligned"):
        entity_hygiene.scrub(df, hosts=["fastcodesign.com"], log=lambda m: None)


def test_hygiene_runs_before_the_corrupted_filter_so_the_leak_is_caught():
    """The ordering trap. 10 of these 12 rows are flagged corrupted and would
    be filtered out anyway; the 2 that are not are exactly the rows that carry
    the fabricated names onto a ranked page. Scrubbing after the filter leaves
    a 2-row cluster no threshold can see."""
    rows = codesign_rows(10, corrupted=True) + codesign_rows(2, corrupted=False)
    c = build(rows + [row(people=["Tim Cook"])])
    assert len(c.rows) == 3                     # 10 corrupted rows gone
    names = {name for v in c.rows["people"] for name in corpus.as_list(v)}
    assert names == {"Tim Cook"}
    for fabricated in FABRICATED:
        assert fabricated not in names


def test_corpus_carries_the_cleanup_evidence_for_the_page_to_state():
    c = build(codesign_rows(12) + [row(people=["Tim Cook"])])
    assert c.scrubbed_people == 12
    assert len(c.people_clusters) == 1


def test_scrubbed_rows_already_dropped_as_corrupted_are_not_claimed_as_a_save():
    """The real index's shape: every fabricated row is ALSO flagged corrupted,
    so the ranking was clean before this rule existed. The page must not take
    credit for that, so the two counts are kept apart."""
    c = build(codesign_rows(12, corrupted=True) + [row(people=["Tim Cook"])])
    assert c.scrubbed_people == 12
    assert c.scrubbed_people_in_corpus == 0
    disclosure = deepdives.render_people(c).split("What was taken out of this list")[1]
    assert "already clean" in disclosure
    assert "would be sitting in the ranking" not in disclosure


def test_a_scrubbed_row_that_survives_the_other_filters_is_counted_as_one():
    c = build(codesign_rows(12, corrupted=False) + [row(people=["Tim Cook"])])
    assert c.scrubbed_people == c.scrubbed_people_in_corpus == 12
    disclosure = deepdives.render_people(c).split("What was taken out of this list")[1]
    assert "would be sitting in the ranking" in disclosure


def test_a_lone_row_with_a_mixed_cast_is_left_alone_and_that_is_deliberate():
    """The residual leak in the real index, pinned rather than papered over.

    One uncorrupted fastcodesign row carries a cast that mixes the furniture
    names with the article's real subjects. Its fingerprint is unique, so it
    forms no cluster and keeps its people - "Todd Kaplan" survives on /people/
    with a count of 1 out of 41,514 names. Scrubbing it would take a name
    blocklist, which would also erase Jony Ive from the articles genuinely
    about him. This test exists so that trade-off stays a decision.
    """
    mixed = row(title="mixed", url="https://www.fastcodesign.com/odd",
                word_count=642, people=["Todd Kaplan", "Jony Ive", "Pope Leo XIV"])
    c = build(codesign_rows(12) + [mixed])
    names = {name for v in c.rows["people"] for name in corpus.as_list(v)}
    assert names == {"Todd Kaplan", "Jony Ive", "Pope Leo XIV"}
    assert c.scrubbed_people == 12


def test_people_page_ranks_the_cleaned_data_and_says_what_it_removed():
    c = build(codesign_rows(12) + [row(people=["Tim Cook"]) for _ in range(3)])
    html = deepdives.render_people(c)
    ranking, disclosure = html.split("What was taken out of this list")
    assert "Tim Cook" in ranking
    for fabricated in FABRICATED:
        # gone from the ranking, named in the disclosure - the fix is not
        # allowed to be invisible
        assert fabricated not in ranking
        assert fabricated in disclosure
    assert "12 articles in the index carry a cast that was never in them" in disclosure
    assert "Fabricated casts dropped" in ranking


def test_people_page_escapes_hostile_names():
    c = build([row(people=[HOSTILE]) for _ in range(3)])
    html = deepdives.render_people(c)
    assert "<script>" not in html and "&lt;script&gt;" in html


# ---------------------------------------------------------------------------
# facets + payload
# ---------------------------------------------------------------------------

def test_locations_page_ranks_and_states_its_coverage():
    c = build([row(locations=["London", "Paris"]), row(locations=["London"]),
               row(locations=[])])
    html = deepdives.render_locations(c)
    assert "London" in html and "Paris" in html
    assert "Distinct places" in html
    assert "66.7" in html          # tagged share, measured not quoted


def test_locations_page_escapes_hostile_place_names():
    c = build([row(locations=[HOSTILE])])
    html = deepdives.render_locations(c)
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_payload_carries_a_clipped_grade_or_null():
    c = build([row(grade_level=11.4), row(grade_level=857.0), row(grade_level=None)])
    payload = corpus.payload_rows(c)
    assert payload["fields"][-1] == "grade"
    grades = sorted(a[-1] for a in payload["articles"] if a[-1] is not None)
    assert grades == [11.4, 20.0]
    assert sum(1 for a in payload["articles"] if a[-1] is None) == 1


def test_payload_rows_stay_aligned_with_their_grades():
    """zip() over a sorted frame and a series is only correct if the series is
    the sorted one. A mismatch here would hand every article someone else's
    reading level, silently."""
    c = build([row(title="old", date_archived="2012-01-01", grade_level=5.0),
               row(title="new", date_archived="2013-01-01", grade_level=15.0)])
    payload = corpus.payload_rows(c)
    by_title = {a[0]: a[-1] for a in payload["articles"]}
    assert by_title == {"new": 15.0, "old": 5.0}


def test_payload_cap_is_still_enforced(monkeypatch):
    c = build([row() for _ in range(3)])
    monkeypatch.setattr(deepdives, "MAX_PAYLOAD_BYTES", 10)
    with pytest.raises(SystemExit, match="over the"):
        deepdives.payload_json(c)


def test_payload_json_round_trips_with_the_new_field():
    c = build([row(grade_level=9.9)])
    payload = json.loads(deepdives.payload_json(c).decode("utf-8"))
    idx = payload["fields"].index("grade")
    assert payload["articles"][0][idx] == 9.9


# ---------------------------------------------------------------------------
# the page, end to end
# ---------------------------------------------------------------------------

def test_trends_page_renders_every_band():
    c = build([row(date_archived=f"201{y}-06-01", orgs=["Google"],
                   locations=["London"], sentiment="Positive",
                   grade_level=10.0 + y, url="https://nytimes.com/%d" % y)
               for y in range(1, 6)])
    html = trends.render_trends(c, domain="reading.example.com")
    assert "<h1>Trends</h1>" in html
    for heading in ("average grade level per year", "sentiment mix by year",
                    "top 15 sources by year", "top 15 organizations by year",
                    "top 15 places by year"):
        assert heading in html
    assert html.count('class="hmwrap"') == 4        # sentiment + three heatmaps
    assert "<!DOCTYPE html>" in html


def test_trends_page_survives_a_corpus_with_no_entities_at_all():
    c = build([row(orgs=[], locations=[], url="", sentiment=None,
                   grade_level=None)])
    html = trends.render_trends(c)
    assert "<h1>Trends</h1>" in html
    assert "no organizations tagged" in html


def test_generate_writes_the_new_pages_and_links_them(tmp_path):
    synth = tmp_path / "synthesis"
    synth.mkdir()
    (synth / "2012-W10.md").write_text(
        "---\nweek: 2012-W10\nweek_start: 2012-03-05\nweek_end: 2012-03-11\n"
        "article_count: 1\ntotal_words: 900\nreading_time_hours: 1.5\n"
        "articles:\n  - title: A\n    url: https://example.com/a\n"
        "    words: 900\n    date_read: 2012-03-06\n---\nProse.\n",
        encoding="utf-8")
    index = tmp_path / "index.parquet"
    df = pd.DataFrame([row(date_archived="2012-03-0%d" % (i + 1)) for i in range(5)])
    df["date_saved"] = pd.to_datetime(df["date_saved"])
    df["date_archived"] = pd.to_datetime(df["date_archived"])
    df.to_parquet(index)

    out = tmp_path / "site"
    gen.generate(str(synth), str(out), index_path=str(index))
    for page_path in ("trends", "orgs", "people", "locations", "articles"):
        assert (out / page_path / "index.html").exists()
    assert not (out / "concepts").exists()
    home = (out / "index.html").read_text()
    for href in ('href="trends/"', 'href="people/"', 'href="locations/"'):
        assert href in home
    assert "erabar" in home
    css = (out / "style.css").read_text()
    assert ".hm td.hc" in css and ".erabar" in css
