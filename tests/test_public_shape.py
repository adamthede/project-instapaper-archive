"""The public shape of this site: the cover alone, at data.adamthede.com/reading/.

`site/generate.py --public --out <dir>` emits the Reading record for the public
tier described in command-center's `docs/planning/2026-09-09-data-adamthede-com.md`.
The private nightly build does not change; this is a second build shape of the
same generator.

Every test here names, in its docstring, the mutation it catches.

Two things these tests deliberately do NOT do.

They do not drive a browser. This suite has none and the repo has no CI to run
one in, so the self-contained claim, the viewport claims and the fidelity pair
are asserted at the markup level here and measured for real in
`docs/qa/2026-09-10-public-shape/`, the same split the cover suite settled.

They do not decide the leak question on a fixture alone. The fixture corpus
carries realistic titles and URLs so the scan has something to find, and
`test_the_live_archive_public_build_leaks_nothing` runs the same scan over the
whole real index - every title, longest first - and skips where it is absent.
"""
import datetime as dt
import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
SITE = REPO / "site"
sys.path.insert(0, str(SITE))

import cover  # noqa: E402
import generate as gen  # noqa: E402
import htmlkit  # noqa: E402
import public_shape  # noqa: E402

# Read at import: conftest's autouse fixture deletes INSTAPAPER_VAULT_PATH from
# every test's environment, so the one test that is about the real archive has
# to capture it before that runs.
LIVE_VAULT = os.environ.get("INSTAPAPER_VAULT_PATH")


def _live_index():
    """The Parquet index, wherever this checkout can see one.

    `data/archive_index.parquet` is gitignored, so it exists in the main
    checkout and not in a worktree - and this work was built in a worktree.
    """
    here = REPO / "data" / "archive_index.parquet"
    if here.exists():
        return here
    try:
        common = subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return here
    return Path(common).resolve().parent / "data" / "archive_index.parquet"


LIVE_INDEX = _live_index()

# A fixed date, so two builds in the same test session cannot differ by a
# midnight, and so the fidelity pair below compares two renders of one day.
TODAY = dt.date(2026, 9, 10)


# ---------------------------------------------------------------------------
# a corpus shaped like the real one, with titles and URLs worth leaking
# ---------------------------------------------------------------------------

# (day read, source, url, minutes, canonical entries, title)
#
# The titles are real-shaped sentences rather than "Title 3": the leak scan
# only considers strings of twelve characters or more, so a fixture of short
# labels would let every leak test pass without testing anything.
ROWS = [
    ("2008-03-01", "legacy_pdf", None, 500, ["Social Media"],
     "The Cathedral and the Bazaar, Reconsidered"),
    ("2011-02-01", "legacy_doc", None, 1000, ["Mobile Devices"],
     "What the Smartphone Did to the Commute"),
    ("2011-05-01", "legacy_txt", None, 750, ["Social Media"],
     "Timelines, Feeds, and the End of the Archive"),
    ("2011-09-01", "legacy_htm", None, 250, ["Innovation"],
     "A Short History of the Skunk Works"),
    ("2012-01-10", "instapaper", "https://www.example.com/2012/01/feeds-and-walls",
     600, ["Social Media", "Mobile Devices"],
     "Feeds and Walls: Two Ways to Read the Web"),
    ("2016-05-05", "instapaper", "https://old.example.net/g/innovation-theatre",
     200, ["Innovation"], "Innovation Theatre and Its Discontents"),
    ("2018-04-02", "instapaper", "https://sub.example.org/b/attention-is-all",
     400, ["Artificial Intelligence"],
     "Attention Is All You Need, For Now"),
    ("2019-06-03", "instapaper", "https://news.example.net/c/the-long-tail-again",
     1500, ["Artificial Intelligence", "Innovation"],
     "The Long Tail, Twenty Years On"),
    ("2020-08-04", "instapaper", "https://www.example.com/d/remote-work-forever",
     300, ["Innovation"], "Remote Work Forever, and Other Predictions"),
    ("2024-02-05", "matter", "https://matter.example.com/e/small-models-win",
     1100, ["Artificial Intelligence"],
     "Small Models Win the Long Game"),
    ("2026-01-06", "matter", "https://www.example.com/f/the-quiet-phone",
     450, ["Mobile Devices"], "The Quiet Phone and What It Costs"),
]

WORDS_PER_MINUTE = 238

WEEK_TEMPLATE = """---
week: {week}
week_start: '{start}'
week_end: '{end}'
generated: '2026-09-08'
model: qwen3.6-35b-a3b-mtp
article_count: {count}
total_words: {words}
reading_time_hours: {hours}
top_topics: []
top_people: []
articles:
- title: {title}
  url: https://www.example.com/2012/01/feeds-and-walls
  words: {words}
  date_read: '{start}'
---

Only paragraph of the digest for {week}.
"""

# Three consecutive ISO weeks and one on its own, so the longest streak is a
# measured 3. The titles are the roster the public build must never receive.
WEEKS = [("2012-W02", 1, 1200, "Feeds and Walls: Two Ways to Read the Web"),
         ("2012-W03", 2, 2400, "Innovation Theatre and Its Discontents"),
         ("2012-W04", 3, 3600, "The Long Tail, Twenty Years On"),
         ("2013-W06", 1, 1000, "Small Models Win the Long Game")]


@pytest.fixture
def synth_dir(tmp_path):
    d = tmp_path / "synthesis"
    d.mkdir()
    for week, count, words, title in WEEKS:
        y, w = week.split("-W")
        start = dt.date.fromisocalendar(int(y), int(w), 1)
        d.joinpath(f"{week}.md").write_text(
            WEEK_TEMPLATE.format(week=week, start=start.isoformat(),
                                 end=(start + dt.timedelta(days=6)).isoformat(),
                                 count=count, words=words, title=title,
                                 hours=round(words / WORDS_PER_MINUTE / 60, 2)),
            encoding="utf-8")
    return d


@pytest.fixture
def index_file(tmp_path):
    records = []
    for i, (day, source, url, minutes, entries, title) in enumerate(ROWS):
        archived = None if source.startswith("legacy") else day
        records.append({
            "instapaper_id": None, "matter_id": None, "source": source,
            "content_type": "article", "title": title, "url": url,
            "author": "Ada Lovelace", "date_saved": day,
            "date_archived": archived,
            "word_count": minutes * WORDS_PER_MINUTE,
            "content_corrupted": False, "reading_time_min": float(minutes),
            "topics": ["AI"], "people": ["Ada Lovelace"], "orgs": ["Google"],
            "locations": ["Berlin"], "concepts": list(entries),
            "canonical_entries": list(entries),
            "sentiment": "Neutral", "emotion": "Analytical",
            "summary": "A summary sentence nobody should ever read in public.",
            "file_path": f"/{i}.md", "content_snippet": "snip",
        })
    df = pd.DataFrame(records)
    for c in ("date_saved", "date_archived"):
        df[c] = pd.to_datetime(df[c])
    p = tmp_path / "index.parquet"
    df.to_parquet(p)
    return p


@pytest.fixture
def public_out(synth_dir, index_file, tmp_path):
    out = tmp_path / "public"
    public_shape.build(synth_dir, out, index_path=index_file, today=TODAY,
                       thumbnail=False)
    return out


@pytest.fixture
def public_html(public_out):
    return (public_out / "index.html").read_text(encoding="utf-8")


@pytest.fixture
def private_out(synth_dir, index_file, tmp_path):
    out = tmp_path / "_site"
    gen.generate(synth_dir, out, index_path=index_file)
    return out


def files_under(root):
    return sorted(p.relative_to(root).as_posix()
                  for p in root.rglob("*") if p.is_file())


def anchors(html):
    """(href, whole tag) for every anchor in the document."""
    return [(m.group(1), m.group(0))
            for m in re.finditer(r'<a\b[^>]*href="([^"]*)"[^>]*>', html)]


# ---------------------------------------------------------------------------
# what the build emits
# ---------------------------------------------------------------------------

def test_the_public_build_emits_exactly_the_record_files(public_out):
    """Catches a public build that ships a page the record does not have.

    The record is the cover, its thumbnail and its provenance note. A build
    that also writes `style.css`, a `weeks/` tree, `articles.json` or the
    generator's own marker is shipping the private site under a public URL.
    Point `build()` at `generate.generate` and this fails on the file list.
    """
    # thumbnail=False in the fixture, so the capture is not asserted here -
    # test_the_thumbnail_is_a_capture_of_the_page_beside_it does that.
    assert files_under(public_out) == ["PROVENANCE.md", "index.html"]


def test_the_public_page_is_the_cover_and_no_part_of_the_weeks_index(public_html):
    """Catches the weeks index, its rows or a week roster reaching the public
    build.

    The cover's four columns must be there; the index's week rows (`wrow`), the
    week page's article roster (`row`) and its trend strip must not. Render
    `generate.render_index` into the public output instead and this fails.
    """
    assert public_html.count('class="fcol"') == 4
    assert 'class="fhero accent"' in public_html
    for forbidden in ('class="wrow"', 'class="row"', 'class="trend"',
                      'class="ystrip"', 'class="roster"', 'class="atitle"'):
        assert forbidden not in public_html, forbidden


def test_two_public_builds_of_the_same_data_are_byte_identical(
        synth_dir, index_file, tmp_path):
    """Catches a clock, a set iteration or a temp path leaking into the page.

    The provenance note records a sha256 of `index.html`, so a page that
    differs run to run makes that hash a lie the moment it is written. Stamp
    `dt.datetime.now()` anywhere in the cover and this fails.
    """
    a, b = tmp_path / "a", tmp_path / "b"
    public_shape.build(synth_dir, a, index_path=index_file, today=TODAY,
                       thumbnail=False)
    public_shape.build(synth_dir, b, index_path=index_file, today=TODAY,
                       thumbnail=False)
    assert ((a / "index.html").read_bytes() == (b / "index.html").read_bytes())


# ---------------------------------------------------------------------------
# redaction at the data layer
# ---------------------------------------------------------------------------

def test_the_renderer_is_handed_no_article_title_and_no_url(index_file):
    """Catches the reduction being skipped, or widened to carry a title.

    The press's wording is that the build receives a view of the data with the
    private strings already removed, so no template can leak what it never saw.
    Add "title" or "url" to `public_shape.ROW_COLUMNS` and this fails.
    """
    import corpus as corpus_mod
    full = corpus_mod.load_corpus(index_file)
    reduced = public_shape.reduce_corpus(full)
    assert "title" not in reduced.rows.columns
    assert "url" not in reduced.rows.columns
    for banned in ("author", "summary", "content_snippet", "file_path"):
        assert banned not in reduced.rows.columns, banned
    assert set(reduced.rows.columns) == set(public_shape.ROW_COLUMNS)


def test_the_week_records_the_renderer_sees_carry_no_article_roster(synth_dir):
    """Catches the week reduction being skipped.

    A week record's `articles` list is every title and every URL of that week -
    the feed the plan of record says to drop. Return `weeks` unchanged from
    `reduce_weeks` and this fails.
    """
    weeks = gen.load_weeks(synth_dir)
    assert any(m.get("articles") for m in weeks), "fixture carries no roster"
    reduced = public_shape.reduce_weeks(weeks)
    assert reduced, "the reduction dropped every week"
    for m in reduced:
        assert set(m) == set(public_shape.WEEK_KEYS), set(m)


def test_the_reduced_view_still_draws_the_cover_the_full_one_draws(
        synth_dir, index_file):
    """Catches a column dropped from the reduced view that the cover reads.

    The reduction is only safe if it is lossless for this page: the cover
    rendered from the reduced corpus and reduced weeks must be the same
    document as the cover rendered from the whole index. Remove `domain`,
    `proxy_dated` or `reading_time_min` from ROW_COLUMNS and this fails - which
    is the point, because the alternative failure is a public cover quietly
    printing zeros.
    """
    import corpus as corpus_mod
    full = corpus_mod.load_corpus(index_file)
    weeks = gen.load_weeks(synth_dir)
    dd = {"years": 3, "facets": 5, "total": 8}
    from_full = cover.render_cover(full, weeks, deep_dives=dd, today=TODAY)
    from_reduced = cover.render_cover(
        public_shape.reduce_corpus(full), public_shape.reduce_weeks(weeks),
        deep_dives=dd, today=TODAY)
    assert from_full == from_reduced


# ---------------------------------------------------------------------------
# the leak scan
# ---------------------------------------------------------------------------

def test_no_article_title_from_the_index_appears_in_the_public_build(
        public_out, index_file):
    """Catches a title reaching any emitted file.

    Every title in the index of twelve characters or more, longest first,
    against every file the build wrote. Widen ROW_COLUMNS to carry `title` and
    the cover would not print it today, but this is the test that would catch
    the day something did.
    """
    titles, _ = public_shape.private_strings(index_file)
    assert len(titles) >= 10, "the fixture lost its long titles"
    assert public_shape.leak_scan(public_out, titles, []) == []


def test_no_source_url_path_appears_in_the_public_build(public_out, index_file):
    """Catches a URL path reaching any emitted file.

    Hosts are not paths, and the cover prints hosts on purpose - the Sources
    column ranks publications by name. What must never appear is the path that
    identifies an individual article.
    """
    _, paths = public_shape.private_strings(index_file)
    assert len(paths) >= 5, "the fixture lost its long URL paths"
    assert public_shape.leak_scan(public_out, [], paths) == []


def test_the_leak_scan_is_red_when_a_title_is_injected(public_out, index_file):
    """Catches a scan that walks the wrong files, or reports nothing whatever
    it finds.

    A leak test that cannot go red is decoration. One title is written into the
    emitted page and the same call must name it. Make `leak_scan` return `[]`
    unconditionally and this fails while every other leak test stays green,
    which is exactly the pair a mutation audit exists to separate.
    """
    titles, _ = public_shape.private_strings(index_file)
    victim = titles[0]
    page_path = public_out / "index.html"
    page_path.write_text(page_path.read_text(encoding="utf-8")
                         .replace("</footer>", f"<!-- {victim} --></footer>"),
                         encoding="utf-8")
    found = public_shape.leak_scan(public_out, titles, [])
    assert [f["needle"] for f in found] == [victim], found
    assert found[0]["file"] == "index.html"


def test_the_leak_scan_reads_every_file_the_build_wrote(public_out, index_file):
    """Catches a scan that only ever looks at index.html.

    The press says walk every emitted file. A title pasted into the provenance
    note is a title on a public URL, so the note is in scope like anything else.
    Narrow the walk to `index.html` and this fails.
    """
    titles, _ = public_shape.private_strings(index_file)
    note = public_out / "PROVENANCE.md"
    note.write_text(note.read_text(encoding="utf-8") + f"\n{titles[0]}\n",
                    encoding="utf-8")
    found = public_shape.leak_scan(public_out, titles, [])
    assert [f["file"] for f in found] == ["PROVENANCE.md"], found


def test_the_build_refuses_to_finish_when_the_scan_finds_something(
        synth_dir, index_file, tmp_path, monkeypatch):
    """Catches a scan that runs and is then ignored.

    The press says the leak test runs before every upload and fails closed. A
    build that reports a finding on stderr and writes the directory anyway has
    published it. Turn the `SystemExit` into a `print` and this fails.
    """
    real = public_shape.render_public_cover

    def leaky(*a, **kw):
        html = real(*a, **kw)
        return html.replace("</footer>",
                            "<!-- The Long Tail, Twenty Years On --></footer>")

    monkeypatch.setattr(public_shape, "render_public_cover", leaky)
    out = tmp_path / "leaky"
    with pytest.raises(SystemExit) as err:
        public_shape.build(synth_dir, out, index_path=index_file, today=TODAY,
                           thumbnail=False)
    assert "The Long Tail" in str(err.value)
    assert not (out / "index.html").exists(), "the leaking build was published"


def test_the_scan_ignores_strings_too_short_to_identify_an_article(index_file):
    """Catches a minimum length of zero, which matches everything.

    A four-character title ("Bees") would match inside an unrelated word on any
    page and turn the scan into a permanent red. Twelve is the floor, and the
    fixture and the live archive are both scanned at it. Set MIN_NEEDLE to 1
    and the corpus's short strings start matching the cover's own prose.
    """
    titles, paths = public_shape.private_strings(index_file)
    assert all(len(t) >= public_shape.MIN_NEEDLE for t in titles)
    assert all(len(p) >= public_shape.MIN_NEEDLE for p in paths)
    assert public_shape.MIN_NEEDLE == 12


def test_the_scan_takes_the_longest_strings_first(index_file):
    """Catches an unordered needle list.

    Longest first is what the brief asked for and it is not cosmetic: when two
    titles share a prefix, the longer one is the more specific finding, and a
    scan that reports the short one first names the wrong article in the error.
    """
    titles, _ = public_shape.private_strings(index_file)
    assert titles == sorted(titles, key=len, reverse=True)


# ---------------------------------------------------------------------------
# the five deviations
# ---------------------------------------------------------------------------

def test_the_page_row_is_reduced_to_the_single_current_entry(public_html):
    """Catches the private six-item row shipping on a page that has one page.

    WEEKS, YEARS, SOURCES, SUBJECTS and ARTICLES are pages this build does not
    write, so on the public record they would be five 404s in the chrome of the
    only page there is. COVER stays, marked, exactly as it renders privately.
    Skip the row narrowing and this fails.
    """
    row = re.search(r'<nav class="pagelinks"[^>]*>(.*?)</nav>', public_html, re.S)
    assert row, "the page row is gone entirely"
    assert row.group(1) == '<span class="here">Cover</span>', row.group(1)


def test_the_public_page_has_no_anchor_that_does_not_resolve(public_html):
    """Catches any link on the public record pointing at a private page.

    Every anchor must be an absolute URL on the public tier. A relative href on
    this page reaches nothing: the record is one file. Drop the call to
    `neutralize_links` and the footer's `/weeks/` link comes straight through.
    """
    hrefs = [h for h, _ in anchors(public_html)]
    assert hrefs, "the page has no links at all - the sibling bar is gone"
    for href in hrefs:
        assert href.startswith(public_shape.PUBLIC_BASE), href


def test_a_private_anchor_becomes_a_span_with_the_same_classes():
    """Catches a neutralizer that drops the element, or its classes, or its text.

    "Same classes, span instead of a" is the requirement: the page must lay out
    identically, so the replacement carries the class attribute across and the
    href is the only thing lost.
    """
    out = public_shape.neutralize_links(
        '<p><a class="mk hot" href="../weeks/">All weeks</a> and '
        '<a href="https://data.adamthede.com/books/">Books</a></p>')
    assert out == ('<p><span class="mk hot">All weeks</span> and '
                   '<a href="https://data.adamthede.com/books/">Books</a></p>')


def test_the_sibling_bar_points_at_the_public_tier(public_html):
    """Catches the sibling bar still pointing at the Access-walled hosts.

    books.adamthede.com and viewing.adamthede.com are behind Cloudflare Access.
    From a public page those two links are a login wall, not a sibling site.
    Leave `htmlkit.SIBLING_SITES` installed and this fails.
    """
    bar = re.search(r'<span class="sitelinks">(.*?)</span>\s*\n', public_html, re.S)
    assert bar, "the sibling row is gone"
    assert bar.group(1) == (
        '<span class="on">Reading</span>'
        '<a href="https://data.adamthede.com/books/">Books</a>'
        '<a href="https://data.adamthede.com/viewing/">Viewing</a>'), bar.group(1)
    assert "books.adamthede.com" not in public_html
    assert "viewing.adamthede.com" not in public_html


def test_the_wordmark_points_at_the_public_index(public_html):
    """Catches a wordmark still linking to `./`, which on the record is itself.

    On the private site the wordmark is the way home to the cover. On the public
    tier home is the coverage-map index one level up.
    """
    m = re.search(r'<a class="wordmark" href="([^"]*)"', public_html)
    assert m and m.group(1) == "https://data.adamthede.com/", m and m.group(1)


def test_the_canonical_is_the_record_url(public_html):
    """Catches the private canonical shipping on the public page.

    `https://reading.adamthede.com/` on a page served at
    `data.adamthede.com/reading/` tells every crawler the public record is a
    duplicate of a page it cannot reach.
    """
    assert ('<link rel="canonical" href="https://data.adamthede.com/reading/">'
            in public_html)
    assert "reading.adamthede.com/\">" not in public_html


def test_the_footer_says_the_full_record_is_private(public_html):
    """Catches the footer still offering the weekly syntheses.

    "The weekly syntheses are at /weeks/" is an invitation to a page that is
    not published. The public footer states the record is kept for the family,
    in the same span, and carries no link.
    """
    footer = re.search(r"<footer>(.*?)</footer>", public_html, re.S).group(1)
    assert "The full record is kept for the family." in footer
    assert "/weeks/" not in footer
    assert "<a " not in footer


def test_the_public_cover_is_the_private_cover_but_for_the_five_deviations(
        synth_dir, index_file):
    """Catches a sixth difference of any kind between the two covers.

    This is the fidelity contract stated as an equality rather than as a list of
    spot checks: the private cover, with the five transformations applied by
    hand here, has to be the public cover character for character. Change a
    figure, a class, a heading or a note on the public path only, and this
    fails naming the diff. It is also why the deviations in the provenance note
    can be trusted to be complete.
    """
    import corpus as corpus_mod
    full = corpus_mod.load_corpus(index_file)
    weeks = gen.load_weeks(synth_dir)
    dd = public_shape.deep_dive_counts(full)

    private = cover.render_cover(full, weeks, deep_dives=dd, today=TODAY)
    public = public_shape.render_public_cover(full, weeks, deep_dives=dd,
                                              today=TODAY)

    expected = private
    # (1) the page row, reduced to its current entry
    expected = re.sub(r'(<nav class="pagelinks"[^>]*>).*?(</nav>)',
                      r'\1<span class="here">Cover</span>\2', expected, flags=re.S)
    # (3) the sibling bar and the wordmark
    expected = expected.replace(
        '<a href="https://books.adamthede.com/">Books</a>'
        '<a href="https://viewing.adamthede.com/">Viewing</a>',
        '<a href="https://data.adamthede.com/books/">Books</a>'
        '<a href="https://data.adamthede.com/viewing/">Viewing</a>')
    expected = expected.replace('<a class="wordmark" href="./"',
                                '<a class="wordmark" href="https://data.adamthede.com/"')
    # (4) the canonical
    expected = expected.replace(
        '<link rel="canonical" href="https://reading.adamthede.com/">',
        '<link rel="canonical" href="https://data.adamthede.com/reading/">')
    # (5) the footer, which is also (2): the only private anchor on the page
    expected = expected.replace(
        '<span class="label">The weekly syntheses are at '
        '<a href="weeks/">/weeks/</a></span>',
        '<span class="label">The full record is kept for the family.</span>')
    # the stylesheet, inlined rather than linked, is not a deviation in the
    # cover's DOM - it is the self-contained rule, checked on its own below.
    expected = expected.replace(
        '<link rel="stylesheet" href="style.css">',
        public_shape.style_tag())

    assert public == expected


# ---------------------------------------------------------------------------
# self-contained
# ---------------------------------------------------------------------------

def test_the_public_page_fetches_nothing_at_view_time(public_html):
    """Catches any resource the page would go and get after the document.

    One request, the document. A `<link rel=stylesheet>`, a `<script src>`, an
    `<img>`, an `@import`, a `url()` in the inlined CSS or a webfont are each a
    second request, and each one fails the press's first clause. Emit
    `style.css` beside the page and link it and this fails.
    """
    assert "<link rel=\"stylesheet\"" not in public_html
    assert "<script" not in public_html
    assert "<img" not in public_html
    assert "@import" not in public_html
    assert "url(" not in public_html
    assert "fonts.googleapis" not in public_html
    assert "fetch(" not in public_html
    # Every absolute URL on the page is a link a reader clicks, not a resource
    # the browser goes and gets.
    for url in re.findall(r'https?://[^"\s<>]+', public_html):
        assert url.startswith(public_shape.PUBLIC_BASE), url


def test_the_inlined_stylesheet_is_the_rules_the_private_cover_renders_under(
        public_html):
    """Catches a public page styled by a fork of the site's stylesheet.

    The record keeps its own design, so the rules are the site's own: the base
    stylesheet and the cover's block, verbatim. Retype either into this module
    and the two drift on the next change to the private cover.
    """
    css = re.search(r"<style>(.*?)</style>", public_html, re.S).group(1)
    assert gen.STYLE in css
    assert cover.COVER_STYLE in css
    assert css == gen.STYLE + cover.COVER_STYLE


# ---------------------------------------------------------------------------
# the private build is not touched
# ---------------------------------------------------------------------------

def test_the_private_build_is_unchanged_by_a_public_build(
        synth_dir, index_file, tmp_path):
    """Catches the public path mutating shared module state and leaking it.

    `htmlkit` carries the page row, the sibling sites and the wordmark home as
    module state for the duration of one build. The public build installs its
    own; if it does not restore them, the next private build ships a cover
    linking to data.adamthede.com. The private site is hashed whole, before and
    after. Drop the restore in `public_shape.chrome` and this fails.
    """
    before = tmp_path / "before"
    gen.generate(synth_dir, before, index_path=index_file)
    digest_before = {p: hashlib.sha256((before / p).read_bytes()).hexdigest()
                     for p in files_under(before)}

    public_shape.build(synth_dir, tmp_path / "public", index_path=index_file,
                       today=TODAY, thumbnail=False)

    after = tmp_path / "after"
    gen.generate(synth_dir, after, index_path=index_file)
    digest_after = {p: hashlib.sha256((after / p).read_bytes()).hexdigest()
                    for p in files_under(after)}
    assert digest_before == digest_after


def test_the_private_build_shape_is_still_what_the_nightly_asks_for(
        synth_dir, index_file, tmp_path):
    """Catches `--public` becoming a condition inside the private build.

    The nightly rebuild path calls `generate(synthesis, out, index_path)` and
    that signature and its output must not move. The private root is still the
    cover, the weeks index is still at /weeks/, and the six-item row is back.
    """
    out = tmp_path / "_site"
    gen.generate(synth_dir, out, index_path=index_file)
    root = (out / "index.html").read_text(encoding="utf-8")
    assert '<link rel="stylesheet" href="style.css">' in root
    assert (out / "weeks" / "index.html").exists()
    assert '<a href="https://books.adamthede.com/">Books</a>' in root
    assert root.count('class="pagelinks"') == 1
    assert "Cover</span>" in root and ">Weeks</a>" in root


def test_the_deep_dive_count_matches_what_the_private_build_writes(
        synth_dir, index_file, tmp_path):
    """Catches the public cover's deep-dive figure drifting from the site's.

    The public record carries no year rollups and no facets, but its "deep
    dives" secondary still counts them - it is a fact about the archive's
    exploration, and the footer already says the full record is private. The
    number therefore has to be the number the private build really wrote, not
    one this module guesses. Hardcode it and this fails.
    """
    import corpus as corpus_mod
    out = tmp_path / "_site"
    gen.generate(synth_dir, out, index_path=index_file)
    rollups = len(list((out / "years").glob("*/index.html")))
    facets = len([d for d in out.iterdir()
                  if d.is_dir() and d.name not in ("years", "weeks")
                  and (d / "index.html").exists()])
    counts = public_shape.deep_dive_counts(corpus_mod.load_corpus(index_file))
    assert counts == {"years": rollups, "facets": facets,
                      "total": rollups + facets}


# ---------------------------------------------------------------------------
# the provenance note and the thumbnail
# ---------------------------------------------------------------------------

def test_the_provenance_note_records_the_hash_of_the_page_beside_it(public_out):
    """Catches a note whose hash describes a page that is no longer there.

    The index repo's records each carry a note with the sha256 of the file
    committed beside it - `records/office/PROVENANCE.md` is the shape. A hash
    that does not match is worse than none, because it is checked by a test in
    that repo and would go red there rather than here.
    """
    note = (public_out / "PROVENANCE.md").read_text(encoding="utf-8")
    digest = hashlib.sha256((public_out / "index.html").read_bytes()).hexdigest()
    assert digest in note
    assert "## index.html" in note
    assert "## The deviations" in note


def test_the_provenance_note_lists_every_deviation(public_out):
    """Catches a deviation made in code and not written down.

    The note is what a reviewer of the index repo reads instead of diffing two
    covers, so the five have to be in it by name.
    """
    note = (public_out / "PROVENANCE.md").read_text(encoding="utf-8")
    for phrase in ("page row", "canonical", "sibling", "wordmark", "footer"):
        assert phrase in note.lower(), phrase


def test_the_thumbnail_is_a_capture_of_the_page_beside_it(public_out):
    """Catches a thumbnail captured from a page other than the published one.

    The leak scan reads text; a thumbnail is pixels, so a capture taken from
    the private cover would carry the private chrome into the index as an image
    and every text test would stay green. The note records the hash of the file
    the capture was taken from, and it has to be the file beside it.
    """
    thumb = public_out / "thumb.jpg"
    if not thumb.exists():
        pytest.skip("built with thumbnail=False; the committed capture is "
                    "checked in docs/qa/2026-09-10-public-shape/")
    note = (public_out / "PROVENANCE.md").read_text(encoding="utf-8")
    digest = hashlib.sha256((public_out / "index.html").read_bytes()).hexdigest()
    assert f"captured-from sha256 | `{digest}`" in note


# ---------------------------------------------------------------------------
# the live archive
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def live_synthesis(tmp_path_factory):
    """The vault's synthesis directory, staged on local disk.

    The vault is an SMB mount and 855 small reads over it cost about two
    minutes. Both live tests need the same files, so they are copied once. The
    copy is read-only as far as these tests are concerned; nothing is written
    back to the vault, ever.
    """
    if not LIVE_VAULT or not LIVE_INDEX.exists():
        pytest.skip("needs the mounted vault and the parquet index")
    src = Path(LIVE_VAULT) / "synthesis"
    if not src.exists():
        pytest.skip(f"no synthesis directory at {src}")
    dest = tmp_path_factory.mktemp("live") / "synthesis"
    shutil.copytree(src, dest)
    return dest


@pytest.fixture(scope="module")
def live_corpus():
    if not LIVE_INDEX.exists():
        pytest.skip("needs the parquet index")
    import corpus as corpus_mod
    return corpus_mod.load_corpus(LIVE_INDEX)


def test_the_live_archive_public_build_leaks_nothing(tmp_path, live_synthesis):
    """Catches a leak that only a real corpus can show.

    Every title in the real index, longest first, and every URL path, against
    every file a real public build writes. The fixture corpus has eleven
    articles and four weeks; this one has the whole archive, which is where a
    title that happens to equal a controlled-vocabulary entry, or a path that
    happens to equal a host, would show up.
    """
    out = tmp_path / "public"
    public_shape.build(live_synthesis, out, index_path=LIVE_INDEX, today=TODAY,
                       thumbnail=False)
    titles, paths = public_shape.private_strings(LIVE_INDEX)
    assert len(titles) > 10000, f"only {len(titles)} titles scanned"
    assert public_shape.leak_scan(out, titles, paths) == []


def test_the_live_public_cover_prints_the_figures_the_private_one_prints(
        live_synthesis, live_corpus):
    """Catches the reduction changing a number on the real archive.

    Lossless is checked on the fixture above; this is the same claim on the
    corpus that actually ships, where a column's dtype or a null-heavy field
    could make the reduced frame compute differently.
    """
    full = live_corpus
    weeks = [m for m in gen.load_weeks(live_synthesis)
             if str(m["week"]) >= gen.SITE_EPOCH_WEEK]
    dd = public_shape.deep_dive_counts(full)
    assert (cover.render_cover(full, weeks, deep_dives=dd, today=TODAY)
            == cover.render_cover(public_shape.reduce_corpus(full),
                                  public_shape.reduce_weeks(weeks),
                                  deep_dives=dd, today=TODAY))
