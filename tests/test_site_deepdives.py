"""Phase 5 deep dives: the corpus layer, year/orgs pages, the article payload.

Everything here is synthetic. The real Parquet index is never read - these
tests must pass on a machine that has never seen Adam's archive.
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

SITE = Path(__file__).resolve().parents[1] / "site"
sys.path.insert(0, str(SITE))

import corpus  # noqa: E402
import deepdives  # noqa: E402
import generate as gen  # noqa: E402


HOSTILE = 'Bad <script>alert(1)</script> "quote"'


def row(**over):
    base = {
        "instapaper_id": None, "matter_id": None, "source": "instapaper",
        "content_type": "article", "title": "A Title",
        "url": "https://www.example.com/a", "author": "Ada Lovelace",
        "date_saved": "2012-03-01", "date_archived": "2012-03-02",
        "word_count": 1000, "content_corrupted": False, "reading_time_min": 5.0,
        "topics": ["AI"], "people": ["Ada Lovelace"], "orgs": ["Google"],
        "locations": [], "concepts": [], "sentiment": "Neutral",
        "emotion": "Analytical", "summary": "s", "file_path": "/x.md",
        "content_snippet": "snip",
    }
    base.update(over)
    return base


def frame(rows):
    df = pd.DataFrame(rows)
    for c in ("date_saved", "date_archived"):
        df[c] = pd.to_datetime(df[c])
    return df


@pytest.fixture
def small():
    return corpus.prepare(frame([
        row(title="Alpha", orgs=["Google", "Apple"], date_archived="2012-01-10",
            word_count=1200),
        row(title="Beta", orgs=["Google"], date_archived="2012-06-10",
            word_count=800, url="https://sub.example.org/b"),
        row(title="Gamma", orgs=["Apple"], date_archived="2013-02-10",
            word_count=400, author="Unknown"),
        row(title="Corrupt", content_corrupted=True, date_archived="2012-01-11"),
        row(title="Ancient", source="legacy_pdf", url="", date_saved="1974-01-01",
            date_archived=None),
        row(title="Queued", source="matter", date_archived=None,
            date_saved="2026-01-01"),
    ]))


# ---------------------------------------------------------------------------
# corpus layer
# ---------------------------------------------------------------------------

def test_corrupted_rows_are_excluded_and_counted(small):
    assert "Corrupt" not in set(small.rows["title"])
    assert small.excluded_corrupted == 1


def test_pre_2005_rows_are_dropped_and_counted(small):
    assert "Ancient" not in set(small.rows["title"])
    assert small.excluded_pre_min_year == 1
    assert min(small.years) >= corpus.MIN_YEAR


def test_matter_row_without_an_archive_event_is_undated_not_dated_by_save(small):
    # A Matter save with no archive event is a QUEUED article, not a read one.
    # Falling back to date_saved would silently count the to-read pile as read.
    assert "Queued" not in set(small.rows["title"])
    assert small.excluded_undated == 1


def test_legacy_row_falls_back_to_date_saved():
    c = corpus.prepare(frame([
        row(title="Legacy", source="legacy_pdf", url="", date_archived=None,
            date_saved="2007-05-05"),
    ]))
    assert len(c) == 1
    assert c.rows.iloc[0]["date_read"].date().isoformat() == "2007-05-05"
    assert bool(c.rows.iloc[0]["proxy_dated"]) is True


def test_domain_strips_www_and_keeps_subdomains(small):
    domains = set(small.rows["domain"])
    assert "example.com" in domains and "sub.example.org" in domains


def test_top_entities_counts_each_article_once():
    c = corpus.prepare(frame([
        row(orgs=["Google", "Google", "Google"]),
        row(orgs=["Google"]),
        row(orgs=["Apple"]),
    ]))
    assert corpus.top_entities(c.rows, "orgs", 5) == [
        {"name": "Google", "count": 2}, {"name": "Apple", "count": 1}]


def test_top_entities_ties_break_alphabetically_and_respect_the_limit():
    c = corpus.prepare(frame([row(orgs=["Zeta", "Alpha", "Mid"])]))
    assert [o["name"] for o in corpus.top_entities(c.rows, "orgs", 2)] == ["Alpha", "Mid"]


def test_head_coverage_is_distinct_articles_not_a_sum_of_counts():
    # The bug this guards: summing the top-k counts double-counts every
    # article carrying two of them. Three articles, two orgs, top-2 coverage
    # is 100% - the sum-of-counts answer would be 133%.
    c = corpus.prepare(frame([
        row(orgs=["Google", "Apple"]), row(orgs=["Google"]), row(orgs=["Apple"]),
    ]))
    assert corpus.head_coverage(c.rows, "orgs", 2) == 100.0
    assert sum(o["count"] for o in corpus.top_entities(c.rows, "orgs", 2)) == 4


def test_head_coverage_ignores_articles_outside_the_head():
    c = corpus.prepare(frame([
        row(orgs=["Google"]), row(orgs=["Google"]),
        row(orgs=["Obscure"]), row(orgs=[]),
    ]))
    assert corpus.head_coverage(c.rows, "orgs", 1) == 50.0


def test_entity_coverage_counts_tagged_articles():
    c = corpus.prepare(frame([row(orgs=["Google"]), row(orgs=[]), row(orgs=None),
                              row(orgs=["Apple"])]))
    assert corpus.entity_coverage(c.rows, "orgs") == 50.0


def test_month_series_is_twelve_months_in_order(small):
    months = corpus.month_series(small.year(2012), 2012)
    assert [m["label"] for m in months][:3] == ["Jan", "Feb", "Mar"]
    assert len(months) == 12
    assert months[0]["count"] == 1 and months[0]["words"] == 1200
    assert months[5]["count"] == 1 and months[1]["count"] == 0


def test_year_stats_reconcile_with_the_rows(small):
    st = corpus.stats(small.year(2012))
    rows = small.year(2012)
    assert st["articles"] == len(rows) == 2
    assert st["words"] == int(rows["word_count"].sum()) == 2000
    assert st["domains"] == 2
    assert st["median_words"] == 1000


def test_as_list_survives_arrays_none_and_nan():
    import numpy as np
    assert corpus.as_list(np.array(["a", " b "])) == ["a", "b"]
    assert corpus.as_list(None) == []
    assert corpus.as_list(float("nan")) == []
    assert corpus.as_list(["", "  "]) == []


# ---------------------------------------------------------------------------
# the JSON payload
# ---------------------------------------------------------------------------

def test_payload_shape_is_a_field_header_plus_arrays(small):
    p = corpus.payload_rows(small)
    assert p["fields"] == ["title", "url", "source", "domain", "author",
                           "date_read", "date_saved", "words", "reading_time"]
    assert p["count"] == len(p["articles"]) == len(small)
    assert all(len(a) == len(p["fields"]) for a in p["articles"])


def test_payload_is_newest_first(small):
    p = corpus.payload_rows(small)
    dates = [a[p["fields"].index("date_read")] for a in p["articles"]]
    assert dates == sorted(dates, reverse=True)


def test_payload_excludes_corrupted_and_pre_2005_rows(small):
    titles = {a[0] for a in corpus.payload_rows(small)["articles"]}
    assert "Corrupt" not in titles and "Ancient" not in titles
    assert titles == {"Alpha", "Beta", "Gamma"}


def test_payload_blanks_the_unknown_author_placeholder(small):
    p = corpus.payload_rows(small)
    ai = p["fields"].index("author")
    by_title = {a[0]: a for a in p["articles"]}
    assert by_title["Gamma"][ai] == ""
    assert by_title["Alpha"][ai] == "Ada Lovelace"


def test_payload_carries_no_summary_or_body_fields(small):
    p = corpus.payload_rows(small)
    assert "summary" not in p["fields"] and "content_snippet" not in p["fields"]


def test_payload_json_enforces_the_size_cap(small, monkeypatch):
    monkeypatch.setattr(deepdives, "MAX_PAYLOAD_BYTES", 10)
    with pytest.raises(SystemExit, match="over the"):
        deepdives.payload_json(small)


def test_payload_json_under_the_cap_round_trips(small):
    blob = deepdives.payload_json(small)
    assert json.loads(blob)["count"] == len(small)
    assert len(blob) < deepdives.MAX_PAYLOAD_BYTES


def test_real_scale_payload_stays_within_an_absolute_budget():
    """A 20k-row corpus of realistic width must fit SIX MEGABYTES, stated
    absolutely. Asserting against MAX_PAYLOAD_BYTES would move with the
    constant, so the natural response to a bloated payload - raise the cap -
    would leave every test green. The budget is the point, not the constant."""
    rows = [row(title=f"Article number {i} about something or other",
                url=f"https://example.com/some/path/to/article-{i}",
                author="A Reasonably Long Author Name")
            for i in range(20000)]
    c = corpus.prepare(frame(rows))
    assert len(deepdives.payload_json(c)) < 6_000_000
    assert deepdives.MAX_PAYLOAD_BYTES <= 6 * 1024 * 1024


# ---------------------------------------------------------------------------
# rendering + escaping
# ---------------------------------------------------------------------------

@pytest.fixture
def hostile():
    return corpus.prepare(frame([
        row(title=HOSTILE, orgs=[HOSTILE], author=HOSTILE,
            url="javascript:alert(1)", date_archived="2012-04-04"),
    ]))


def test_year_page_escapes_hostile_org_names(hostile):
    out = deepdives.render_year(hostile, 2012)
    assert "<script>alert(1)</script>" not in out
    assert "&lt;script&gt;" in out


def test_year_page_tooltips_escape_their_data(hostile):
    out = deepdives.render_year(hostile, 2012)
    # The tooltip interpolates the org name into an attribute; an unescaped
    # double quote there breaks out of data-tip= into markup.
    assert 'data-tip="Bad &lt;script&gt;' in out
    assert '"quote"' not in out.split("<style")[0].replace("&quot;", "")


def test_orgs_page_escapes_hostile_org_names(hostile):
    out = deepdives.render_orgs(hostile)
    assert "<script>alert(1)</script>" not in out
    assert "&lt;script&gt;" in out


def test_year_page_carries_stats_rhythm_orgs_and_weeks(small):
    out = deepdives.render_year(
        small, 2012, weeks_in_year=[{"week": "2012-W02", "article_count": 3}])
    assert "Articles read" in out and "Reading time" in out
    assert 'class="months"' in out and "Jan" in out and "Dec" in out
    assert 'class="orow' in out and "Google" in out
    assert 'href="../../weeks/2012-W02/"' in out
    assert "2,000" in out  # words, tabular and formatted


def test_year_page_ranks_no_topics(small):
    """The audit's rule, enforced: topics are 73.3% singletons, so there is no
    topic river and no topic ranking here. The fixture's every article is
    tagged AI - if topics were being ranked, 'AI' would appear."""
    out = deepdives.render_year(small, 2012)
    assert ">AI<" not in out
    assert 'class="chip"' not in out
    assert "Recurring topics" not in out
    # The only mention of topics on the page is the note explaining why they
    # are absent.
    assert "Topics cannot be ranked this way" in out
    assert out.count("Topics") == 1


def test_orgs_page_ranks_no_topics(small):
    out = deepdives.render_orgs(small)
    assert ">AI<" not in out


def test_orgs_page_states_coverage_honestly(small):
    out = deepdives.render_orgs(small)
    assert "Articles tagged" in out and "Covered by the top 20" in out


def test_org_note_reports_the_same_head_coverage_as_the_stat_tile(small):
    """The note used to hardcode the audit's 42.9% and print it fourteen lines
    under a tile computing 45.2%. Both numbers now come from one measurement."""
    head = corpus.head_coverage(small.rows, "orgs", 20)
    out = deepdives.render_orgs(small)
    assert f"{head:,.1f}<em>%</em>" in out          # the tile
    assert f"top 20 cover {head:,.1f}% of the" in out  # the prose
    assert "42.9%" not in out


def test_org_note_measures_the_topic_vocabulary_rather_than_quoting_it(small):
    vocab, singles = corpus.topic_vocabulary(small.rows)
    assert (vocab, singles) == (1, 0.0)  # every fixture row is tagged "AI"
    note = deepdives.org_note(small.rows)
    assert "1 free-text strings, 0.0% of them used exactly once" in note
    assert "29,882" not in note and "73.3%" not in note


def test_topic_vocabulary_counts_distinct_strings_and_singletons():
    c = corpus.prepare(frame([
        row(topics=["AI", "AI"]), row(topics=["Rust"]), row(topics=["Solo"]),
    ]))
    # Every topic is used by exactly one ARTICLE, so the singleton share is
    # 100% - but only if the intra-article duplicate is collapsed first.
    # Without the dedupe "AI" counts 2 and the share drops to 66.7%, which is
    # what makes this fixture able to see the bug at all.
    assert corpus.topic_vocabulary(c.rows) == (3, 100.0)


def test_org_tooltips_name_the_denominator_they_actually_used(small):
    """A share of the whole archive labelled 'of the year' is a wrong number."""
    year = deepdives.render_year(small, 2012)
    facet = deepdives.render_orgs(small)
    assert "% of the year" in year and "% of the archive" not in year
    assert "% of the archive" in facet and "% of the year" not in facet


def test_year_provenance_note_scales_with_the_proxy_share():
    proxy = corpus.prepare(frame([
        row(title="L1", source="legacy_pdf", url="", date_archived=None,
            date_saved="2007-01-01"),
        row(title="L2", source="legacy_pdf", url="", date_archived=None,
            date_saved="2007-02-01"),
    ]))
    assert "pre-tracking-era year" in deepdives.render_year(proxy, 2007)

    mixed = corpus.prepare(frame([
        row(title="L1", source="legacy_pdf", url="", date_archived=None,
            date_saved="2012-01-01"),
        row(title="I1", date_archived="2012-01-02"),
    ]))
    out = deepdives.render_year(mixed, 2012)
    assert "1 of 2 articles" in out and "50%" in out


def test_year_page_without_proxy_dates_or_url_gaps_renders_no_provenance_note(small):
    assert "provenance" not in deepdives.render_year(small, 2013)


def test_a_year_with_no_urls_never_prints_a_bare_zero_under_sources():
    """Five year pages showed a large light 0 under 'Sources' because the
    legacy corpus carries no URLs at all. A zero with no explanation is a
    wrong answer to a question the reader did not know they were asking."""
    legacy = corpus.prepare(frame([
        row(title="L1", source="legacy_pdf", url="", date_archived=None,
            date_saved="2007-01-01"),
        row(title="L2", source="legacy_pdf", url="", date_archived=None,
            date_saved="2007-06-01"),
    ]))
    out = deepdives.render_year(legacy, 2007)
    assert corpus.stats(legacy.year(2007))["url_bearing"] == 0
    assert ">0</div><div class=\"l label\">Sources" not in out
    # The stat tile's sub-label and the provenance line are two separate
    # elements; assert each on its own wording so one cannot cover for the
    # other going missing.
    assert 'class="delta">no URLs in this era' in out              # the tile
    assert "there is no source to count" in out                    # the provenance line


def test_a_partially_urled_year_states_the_subset_it_counted():
    mixed = corpus.prepare(frame([
        row(title="L1", source="legacy_pdf", url="", date_archived=None,
            date_saved="2010-01-01"),
        row(title="L2", source="legacy_pdf", url="", date_archived=None,
            date_saved="2010-02-01"),
        row(title="I1", url="https://example.com/x", date_archived="2010-03-01"),
    ]))
    out = deepdives.render_year(mixed, 2010)
    assert "across 1 articles with a URL" in out
    assert "Sources are counted over the 1 of 3 articles that carry a URL" in out


def test_a_fully_urled_year_says_nothing_extra_about_sources(small):
    out = deepdives.render_year(small, 2012)
    assert "articles with a URL" not in out and "no URLs in this era" not in out.lower()


def test_year_page_nav_links_only_where_neighbours_exist(small):
    out = deepdives.render_year(small, 2012, prev_year=None, next_year="2013")
    assert 'href="../2013/"' in out and 'href="../2011/"' not in out


def test_year_page_with_no_weeks_says_so(small):
    assert "no weekly synthesis pages" in deepdives.render_year(small, 2013)


def test_articles_shell_interpolates_no_corpus_data(small):
    out = deepdives.render_articles_page(small)
    assert "Ada Lovelace" not in out and "example.com" not in out
    assert 'id="q"' in out and 'id="hits"' in out and 'id="detail"' in out


def test_articles_client_builds_rows_without_innerhtml():
    """Every cell is textContent and every href passes a scheme test in the
    browser too - the payload carries third-party scraped titles and URLs."""
    js = deepdives.ARTICLES_JS
    assert "innerHTML" not in js
    assert "textContent" in js
    assert "https?:" in js and "safeHref" in js


def test_shipped_safehref_rejects_hostile_schemes():
    """Run the actual shipped safeHref() in node against the schemes a
    scraped-URL corpus can contain. The source-level assertions above prove
    the gate is wired in; this proves the gate works."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    m = re.search(r"function safeHref[\s\S]*?\n  \}", deepdives.ARTICLES_JS)
    assert m, "safeHref not found in the shipped client code"
    probe = m.group(0) + """
var cases = ['javascript:alert(1)', 'JaVaScRiPt:alert(1)', 'data:text/html,<script>x',
             'vbscript:x', ' javascript:alert(1)', '', null,
             'https://example.com/a', 'http://example.com/b'];
console.log(JSON.stringify(cases.map(safeHref)));
"""
    out = subprocess.run([node, "-e", probe], capture_output=True, text=True,
                         timeout=30, check=True)
    assert json.loads(out.stdout) == [
        "", "", "", "", "", "", "", "https://example.com/a", "http://example.com/b"]


def test_articles_page_reconciles_the_rows_it_dropped(small):
    """The page says 3 where the index holds 6; the difference has to be
    visible or the site is quietly hiding rows."""
    out = deepdives.render_articles_page(small)
    assert "3 of 6 indexed rows" in out
    assert "1 corrupted" in out and "1 undated" in out and "1 dated before 2005" in out


def test_articles_page_omits_the_reconciliation_when_nothing_was_dropped():
    clean = corpus.prepare(frame([row(title="Only", date_archived="2012-01-01")]))
    assert "indexed rows" not in deepdives.render_articles_page(clean)


def test_articles_client_clears_a_stale_detail_panel_on_every_search():
    js = deepdives.ARTICLES_JS
    body = js[js.index("function render(term)"):js.index("q.addEventListener")]
    assert "detail.hidden = true" in body and "detail.textContent = ''" in body


def test_articles_client_has_an_empty_state():
    assert "matched === 0" in deepdives.ARTICLES_JS
    assert "nothing matches" in deepdives.ARTICLES_JS


def test_articles_page_says_search_does_not_cover_bodies(small):
    assert "not article" in deepdives.render_articles_page(small)


def test_extra_style_extends_rather_than_forks(small):
    # The deep-dive rules must ride on top of the week-page variables, not
    # redeclare them - one :root, one palette.
    assert ":root" not in deepdives.EXTRA_STYLE
    assert "var(--amber)" in deepdives.EXTRA_STYLE
    assert "@media (max-width:560px)" in deepdives.EXTRA_STYLE


# ---------------------------------------------------------------------------
# end to end through generate()
# ---------------------------------------------------------------------------

WEEK_MD = """---
week: 2012-W02
week_start: '2012-01-09'
week_end: '2012-01-15'
generated: '2026-08-20'
model: qwen3.6-35b-a3b-mtp
article_count: 1
total_words: 1200
reading_time_hours: 0.5
top_topics: []
top_people: []
articles:
- title: Alpha
  url: https://www.example.com/a
  words: 1200
  date_read: '2012-01-10'
---

Only paragraph of the digest.
"""


@pytest.fixture
def synth_dir(tmp_path):
    d = tmp_path / "synthesis"
    d.mkdir()
    (d / "2012-W02.md").write_text(WEEK_MD, encoding="utf-8")
    return d


@pytest.fixture
def index_file(tmp_path, small):
    p = tmp_path / "index.parquet"
    # Round-trip the raw frame, not the prepared one: generate() must do its
    # own corruption/date filtering, exactly as it will in production.
    frame([
        row(title="Alpha", orgs=["Google", "Apple"], date_archived="2012-01-10",
            word_count=1200),
        row(title="Beta", orgs=["Google"], date_archived="2012-06-10", word_count=800),
        row(title="Gamma", orgs=["Apple"], date_archived="2013-02-10", word_count=400),
        row(title="Corrupt", content_corrupted=True, date_archived="2012-01-11"),
    ]).to_parquet(p)
    return p


def test_generate_renders_deep_dives_alongside_week_pages(synth_dir, index_file, tmp_path):
    out = tmp_path / "_site"
    count = gen.generate(synth_dir, out, index_path=index_file)
    assert count == 1
    assert (out / "weeks" / "2012-W02" / "index.html").exists()
    assert (out / "years" / "2012" / "index.html").exists()
    assert (out / "years" / "2013" / "index.html").exists()
    assert (out / "orgs" / "index.html").exists()
    assert (out / "articles" / "index.html").exists()
    payload = json.loads((out / "articles.json").read_text())
    assert payload["count"] == 3  # the corrupted row never ships


def test_generate_is_idempotent_with_deep_dives(synth_dir, index_file, tmp_path):
    out = tmp_path / "_site"
    gen.generate(synth_dir, out, index_path=index_file)
    first = (out / "years" / "2012" / "index.html").read_text()
    gen.generate(synth_dir, out, index_path=index_file)
    assert (out / "years" / "2012" / "index.html").read_text() == first


def test_index_year_heads_link_to_the_rollups(synth_dir, index_file, tmp_path):
    out = tmp_path / "_site"
    gen.generate(synth_dir, out, index_path=index_file)
    home = (out / "index.html").read_text()
    assert 'href="years/2012/"' in home
    assert 'href="orgs/"' in home and 'href="articles/"' in home


def test_stylesheet_carries_both_week_and_deep_dive_rules(synth_dir, index_file, tmp_path):
    out = tmp_path / "_site"
    gen.generate(synth_dir, out, index_path=index_file)
    css = (out / "style.css").read_text()
    assert ".days {" in css and ".months {" in css
    assert css.count(":root {") == 1


def test_missing_index_degrades_to_a_weeks_only_site(synth_dir, tmp_path, capsys):
    out = tmp_path / "_site"
    gen.generate(synth_dir, out, index_path=tmp_path / "absent.parquet")
    assert (out / "weeks" / "2012-W02" / "index.html").exists()
    assert not (out / "years").exists()
    assert not (out / "articles.json").exists()
    assert "skipping year/orgs/article pages" in capsys.readouterr().err
    # and the year head stays plain text rather than a dead link
    assert 'href="years/2012/"' not in (out / "index.html").read_text()


def test_unreadable_index_degrades_instead_of_killing_the_build(synth_dir, tmp_path, capsys):
    bad = tmp_path / "bad.parquet"
    bad.write_text("not a parquet file")
    out = tmp_path / "_site"
    gen.generate(synth_dir, out, index_path=bad)
    assert (out / "index.html").exists()
    assert "skipping year/orgs/article pages" in capsys.readouterr().err


@pytest.mark.parametrize("break_it", [
    pytest.param(lambda df: df.drop(columns=["word_count"]), id="missing-word_count"),
    pytest.param(lambda df: df.drop(columns=["reading_time_min"]), id="missing-reading_time"),
    pytest.param(lambda df: df.assign(word_count=["a", "b", "c", "d"]), id="word_count-as-strings"),
    pytest.param(lambda df: df.assign(reading_time_min=["a", "b", "c", "d"]),
                 id="reading_time-as-strings"),
])
def test_index_schema_drift_costs_the_deep_dives_not_the_week_pages(
        synth_dir, index_file, tmp_path, capsys, break_it):
    """A backfill that renames or retypes a column must not take down 123 week
    pages that never read the index. Before this guard, a KeyError inside
    render_deep_dives aborted the whole build."""
    drifted = tmp_path / "drifted.parquet"
    break_it(pd.read_parquet(index_file)).to_parquet(drifted)
    out = tmp_path / "_site"
    gen.generate(synth_dir, out, index_path=drifted)
    # The week pages never depend on the index, so they must always survive.
    assert (out / "weeks" / "2012-W02" / "index.html").exists()
    assert (out / "index.html").exists()

    home = (out / "index.html").read_text()
    if (out / "years").exists():
        # The drift was absorbed - then the deep dives must be COMPLETE, not
        # half-built: no year links pointing at pages that were rolled back.
        assert (out / "articles.json").exists() and (out / "orgs").exists()
        for year_dir in (out / "years").glob("*"):
            assert (year_dir / "index.html").exists()
    else:
        # The drift was fatal to the deep-dive leg - then it must be loud, and
        # no partial output may survive into the deployed site.
        err = capsys.readouterr().err
        assert "rendering weeks only" in err or "skipping year/orgs/article pages" in err
        assert not (out / "articles.json").exists() and not (out / "orgs").exists()
        assert 'href="years/' not in home


def test_payload_cap_failure_leaves_the_previous_site_standing(
        synth_dir, index_file, tmp_path, monkeypatch):
    out = tmp_path / "_site"
    gen.generate(synth_dir, out, index_path=index_file)
    before = (out / "index.html").read_text()
    monkeypatch.setattr(deepdives, "MAX_PAYLOAD_BYTES", 10)
    with pytest.raises(SystemExit):
        gen.generate(synth_dir, out, index_path=index_file)
    assert (out / "index.html").read_text() == before
    assert not (tmp_path / "_site.building").exists()


def test_generated_pages_never_scroll_horizontally(synth_dir, index_file, tmp_path):
    out = tmp_path / "_site"
    gen.generate(synth_dir, out, index_path=index_file)
    css = (out / "style.css").read_text()
    assert "overflow-x:hidden" in css and "overflow-x:clip" in css
    for html_file in out.rglob("index.html"):
        text = html_file.read_text()
        assert "width=device-width" in text


# ---------------------------------------------------------------------------
# round-3: the fixture-blind gaps the round-2 mutation run found
# ---------------------------------------------------------------------------

@pytest.fixture
def wide():
    """A corpus with more than 20 distinct orgs, so head-20 and head-100 differ.

    The `small` fixture has two orgs, which makes top-20 and top-100 the same
    number - a mutant swapping k=20 for k=100 in org_note survived against it.
    """
    rows = []
    for i in range(30):
        # org i appears on (30 - i) articles, so rank order is stable and the
        # tail past rank 20 is non-empty.
        for j in range(30 - i):
            rows.append(row(title=f"A{i}-{j}", orgs=[f"Org{i:02d}"],
                            date_archived="2012-04-01"))
    return corpus.prepare(frame(rows))


def test_head_coverage_at_20_differs_from_the_whole_vocabulary(wide):
    assert corpus.head_coverage(wide.rows, "orgs", 20) < 99.0
    assert corpus.head_coverage(wide.rows, "orgs", 100) == pytest.approx(100.0)


def test_org_note_uses_the_top_20_not_the_whole_vocabulary(wide):
    head = corpus.head_coverage(wide.rows, "orgs", 20)
    assert f"top 20 cover {head:,.1f}%" in deepdives.org_note(wide.rows)


def test_year_org_note_describes_the_year_not_the_whole_archive():
    """The round-1 fix measured the note on the whole Corpus, so all 22 year
    pages printed the archive's coverage under twenty year-scoped rows."""
    c = corpus.prepare(frame(
        [row(title=f"y12-{i}", orgs=[f"Org{i:02d}"], date_archived="2012-04-01")
         for i in range(30)]
        + [row(title=f"y13-{i}", orgs=["Solo"], date_archived="2013-04-01")
           for i in range(40)]))
    year_rows = c.year(2013)
    out = deepdives.render_year(c, 2013)
    assert f"of the {len(year_rows):,} articles counted here" in out
    assert f"of the {len(c.rows):,} articles counted here" not in out
    # 2013 is one org over 40 articles: fully covered by its own top 20.
    assert "top 20 cover 100.0%" in out


def test_a_median_over_no_numeric_words_reports_zero_not_a_crash():
    c = corpus.prepare(frame([row(word_count=None), row(word_count=None)]))
    assert corpus.stats(c.rows)["median_words"] == 0


def test_a_numeric_column_of_strings_is_drift_not_a_zero(small):
    """errors="coerce" alone turned a retyped column into a complete, swapped
    site reporting 0 words. The likelier drift must not be the quieter one."""
    lying = small.rows.assign(word_count=["x"] * len(small.rows))
    with pytest.raises(ValueError, match="schema drift"):
        corpus.stats(lying)


def test_a_half_retyped_column_is_drift_too():
    """A backfill retypes rows one at a time. A share-of-values threshold only
    moves the cliff: at the midpoint of a live retype the old rule passed and
    the site deployed half the archive's words, undetectably - payload_rows'
    safe_int drops exactly the same rows, so the two surfaces agreed."""
    c = corpus.prepare(frame([row(title=f"A{i}", word_count=100)
                              for i in range(10)]))
    for retyped in (1, 3, 5, 9):
        mixed = list(c.rows["word_count"].astype(object))
        for i in range(retyped):
            mixed[i] = str(mixed[i])
        with pytest.raises(ValueError, match="schema drift"):
            corpus.stats(c.rows.assign(word_count=mixed))


def test_clean_numeric_strings_are_drift_even_though_they_would_coerce():
    """A column arriving as "100"/"200" currently works by coincidence.
    Coincidence is not a contract - the dtype is still wrong."""
    c = corpus.prepare(frame([row(word_count=100), row(word_count=200)]))
    with pytest.raises(ValueError, match="schema drift"):
        corpus.stats(c.rows.assign(word_count=["100", "200"]))


def test_an_all_null_numeric_column_is_not_drift():
    c = corpus.prepare(frame([row(word_count=None), row(word_count=None)]))
    assert corpus.stats(c.rows.assign(word_count=[None, None]))["words"] == 0


def test_a_missing_numeric_column_raises_rather_than_reading_as_absent():
    """rows[name], not rows.get(name): .get() reads like a graceful-absence
    guard, but pd.to_numeric(None) returns a float64 scalar and the failure
    surfaces later as a confusing AttributeError."""
    c = corpus.prepare(frame([row()]))
    with pytest.raises(KeyError):
        corpus.stats(c.rows.drop(columns=["word_count"]))


def test_a_sparse_numeric_column_is_not_mistaken_for_drift():
    c = corpus.prepare(frame([row(word_count=1000), row(word_count=None),
                              row(word_count=None)]))
    assert corpus.stats(c.rows)["words"] == 1000


def test_string_typed_word_count_costs_the_deep_dives(synth_dir, index_file,
                                                      tmp_path, capsys):
    drifted = tmp_path / "drifted.parquet"
    df = pd.read_parquet(index_file)
    df.assign(word_count=["a"] * len(df)).to_parquet(drifted)
    out = tmp_path / "_site"
    gen.generate(synth_dir, out, index_path=drifted)
    assert (out / "weeks").exists() and (out / "index.html").exists()
    assert not (out / "years").exists()
    assert not (out / "articles.json").exists()
    assert "rendering weeks only" in capsys.readouterr().err


def test_article_links_open_in_a_new_tab(small):
    out = deepdives.render_articles_page(small)
    assert "target = '_blank'" in out and "rel = 'noreferrer'" in out


def test_year_note_keeps_head_coverage_local_and_the_vocabulary_archive_wide():
    """Scoping BOTH halves to the year made the sentence contradict itself:
    the singleton share is monotone in sample size, so a thin year printed
    "top 20 cover 100.0%" and "100.0% used exactly once" in one breath,
    collapsing the very contrast the sentence exists to draw."""
    c = corpus.prepare(frame(
        [row(title="thin", orgs=["Solo"], topics=["Only"], date_archived="2021-04-01")]
        + [row(title=f"y13-{i}", orgs=[f"Org{i:02d}"], topics=[f"T{i:02d}", "Shared"],
               date_archived="2013-04-01") for i in range(40)]))
    thin_vocab, thin_singles = corpus.topic_vocabulary(c.year(2021))
    all_vocab, all_singles = corpus.topic_vocabulary(c.rows)
    assert (thin_vocab, thin_singles) == (1, 100.0)   # the artifact, if year-scoped
    assert all_singles < 100.0                        # the real claim

    out = deepdives.render_year(c, 2021)
    assert f"top 20 cover 100.0% of the 1 articles counted here" in out
    assert f"across the whole archive that vocabulary is {all_vocab:,} free-text" in out
    assert f"{all_singles:,.1f}% of them used exactly once" in out
    assert "100.0% of them used exactly once" not in out


def test_long_entity_names_can_ellipsize_instead_of_widening_their_row():
    """Found in the browser at 390px: "Department of Homeland Security" grew
    the 1fr grid track (auto min-size beats nowrap+ellipsis), pushing the
    count column - the thing the row exists to report - off screen. The row
    did not scroll the page only because the body clips overflow, so it read
    as a silently truncated name with no ellipsis and no count."""
    for selector in (".orow .on", ".arow .at"):
        block = deepdives.EXTRA_STYLE.split(selector, 1)[1].split("}", 1)[0]
        assert "min-width:0" in block, f"{selector} can still widen its track"
        assert "text-overflow:ellipsis" in block


@pytest.mark.parametrize("column", ["word_count", "reading_time_min"])
def test_every_numeric_stat_column_goes_through_the_drift_guard(small, column):
    """The guard was wired for both columns but pinned for only one, so
    bypassing it for reading_time_min survived mutation. Every numeric column
    stats() reads must fail the same way on the same drift."""
    lying = small.rows.assign(**{column: ["x"] * len(small.rows)})
    with pytest.raises(ValueError, match="schema drift"):
        corpus.stats(lying)
