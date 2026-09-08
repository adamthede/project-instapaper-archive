"""The sibling-site bar: reading, books and viewing as one family of surfaces.

Three quantified-self sites are live - reading.adamthede.com,
books.adamthede.com, viewing.adamthede.com - and each carries the same slim
sticky bar so a reader can move between them. The bar's markup and rules are
lifted from the books surface (`reading-life/site/htmlkit.py` and `styles.py`
in Project - QS - Books) rather than retyped, which is what makes "the three
bars look identical" checkable rather than a matter of opinion.

This file covers the SIBLING row. The page row that joined it on 2026-09-08 -
COVER, WEEKS, YEARS, SOURCES, SUBJECTS, ARTICLES - is covered in
test_site_cover.py, alongside the cover that gave it something to point at.

Every test here names, in its docstring, the mutation it catches.

What these tests deliberately do NOT do: pin the sticky behaviour by driving a
browser. This suite has no browser and the repo has no CI to run one in, so the
assertions live at the stylesheet level - `position:sticky` on the bar, the
1180px measure that aligns the three, and the `overflow-x` pair on html and body
that decides whether sticky pins at all. The real-viewport measurement (390px
drag, sticky offset after a scroll) is on the pull request as evidence,
following the same split the books repo settled.

The pair that has to stay in step: `test_every_emitted_page_carries_the_sibling_bar`
walks whatever the build actually wrote, and `PAGE_KINDS` is the inventory that
says the walk saw one of every kind. Neither is sufficient alone. A review
proved the gap by making one renderer build its own document shell: a real build
shipped a bar-less /concepts/ page with the whole suite green, because the
fixture never cleared the taxonomy gate and so never wrote that page for the
walk to find.
"""
import re
import sys
from pathlib import Path

import pandas as pd
import pytest

SITE = Path(__file__).resolve().parents[1] / "site"
sys.path.insert(0, str(SITE))

import generate as gen  # noqa: E402
import htmlkit  # noqa: E402


BOOKS = "https://books.adamthede.com/"
VIEWING = "https://viewing.adamthede.com/"

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

WEEK2_MD = (WEEK_MD.replace("2012-W02", "2013-W06")
            .replace("'2012-01-09'", "'2013-02-04'")
            .replace("'2012-01-15'", "'2013-02-10'")
            .replace("'2012-01-10'", "'2013-02-05'"))


def _row(**over):
    base = {
        "instapaper_id": None, "matter_id": None, "source": "instapaper",
        "content_type": "article", "title": "A Title",
        "url": "https://www.example.com/a", "author": "Ada Lovelace",
        "date_saved": "2012-03-01", "date_archived": "2012-03-02",
        "word_count": 1000, "content_corrupted": False, "reading_time_min": 5.0,
        "topics": ["AI"], "people": ["Ada Lovelace"], "orgs": ["Google"],
        "locations": ["Berlin"], "concepts": ["Social Media"],
        # The taxonomy join's output column. /concepts/ and /together/ are the
        # only page kinds built conditionally - the gate wants this column
        # present, the top-20 article coverage over the 40% bar, and a readable
        # taxonomy - so a fixture without it never builds them and every
        # assertion about those two pages passes over nothing. Names are taken
        # from the committed data/taxonomy/v1.yaml so the join is real.
        "canonical_entries": ["Social Media"],
        "sentiment": "Neutral",
        "emotion": "Analytical", "summary": "s", "file_path": "/x.md",
        "content_snippet": "snip",
    }
    base.update(over)
    return base


@pytest.fixture
def synth_dir(tmp_path):
    d = tmp_path / "synthesis"
    d.mkdir()
    (d / "2012-W02.md").write_text(WEEK_MD, encoding="utf-8")
    (d / "2013-W06.md").write_text(WEEK2_MD, encoding="utf-8")
    return d


@pytest.fixture
def index_file(tmp_path):
    p = tmp_path / "index.parquet"
    df = pd.DataFrame([
        _row(title="Alpha", orgs=["Google", "Apple"], date_archived="2012-01-10",
             word_count=1200,
             canonical_entries=["Social Media", "Mobile Devices"]),
        _row(title="Beta", orgs=["Google"], date_archived="2012-06-10",
             word_count=800, url="https://sub.example.org/b",
             canonical_entries=["Social Media", "Innovation"]),
        _row(title="Gamma", orgs=["Apple"], date_archived="2013-02-10",
             word_count=400,
             canonical_entries=["Mobile Devices", "Innovation"]),
    ])
    for c in ("date_saved", "date_archived"):
        df[c] = pd.to_datetime(df[c])
    df.to_parquet(p)
    return p


@pytest.fixture
def site(synth_dir, index_file, tmp_path):
    """Every page the generator emits, keyed by path, as raw HTML."""
    out = tmp_path / "_site"
    gen.generate(synth_dir, out, index_path=index_file)
    return {p.relative_to(out).as_posix(): p.read_text(encoding="utf-8")
            for p in sorted(out.rglob("*.html"))}


# ---------------------------------------------------------------------------
# the bar reaches every page
# ---------------------------------------------------------------------------

# The page kinds the generator can emit. A build that produces none of a kind
# would make its row vacuous, so each is asserted to have produced at least one
# page as well - see test_the_build_under_test_covers_every_page_kind.
PAGE_KINDS = {
    "cover": lambda p: p == "index.html",
    "weeks index": lambda p: p == "weeks/index.html",
    "week": lambda p: p.startswith("weeks/") and p != "weeks/index.html",
    "years index": lambda p: p == "years/index.html",
    "year": lambda p: p.startswith("years/") and p != "years/index.html",
    "orgs facet": lambda p: p.startswith("orgs/"),
    "people facet": lambda p: p.startswith("people/"),
    "locations facet": lambda p: p.startswith("locations/"),
    "trends": lambda p: p.startswith("trends/"),
    "article browser": lambda p: p.startswith("articles/"),
    # Built conditionally, behind the taxonomy gate, which makes them the two
    # likeliest to drift out of a fixture unnoticed. An adversarial review
    # proved the cost: with these absent, a renderer rewritten to build its own
    # document shell shipped a bar-less /concepts/ page on a real build with
    # the whole suite green. The fixture now carries a canonical_entries column
    # so the gate opens and both pages are really inspected.
    "concepts": lambda p: p.startswith("concepts/"),
    "together": lambda p: p.startswith("together/"),
}


def test_the_build_under_test_covers_every_page_kind(site):
    """Catches a fixture that stopped exercising a page kind.

    The bar test below walks whatever the build emitted; if the deep-dive leg
    silently stopped emitting a kind - a renamed directory, a facet gated off,
    a taxonomy gate that closed - that test would pass over a set missing the
    kind and vouch for nothing. This is what makes the walk meaningful: drop
    the fixture's canonical_entries column, or shrink the corpus until /years/
    or /orgs/ is not written, and this fails first.
    """
    for kind, match in PAGE_KINDS.items():
        assert any(match(p) for p in site), f"no {kind} page in the build"


def test_the_page_kinds_named_here_account_for_the_whole_build(site):
    """Catches a NEW page kind that no row above claims.

    The pair of tests only holds if the two halves stay in step: the walk
    inspects every emitted page, and PAGE_KINDS is the inventory that says the
    walk saw one of each. A kind added to the generator and to nothing else
    would still be walked, but nothing would notice if a later change stopped
    emitting it. Add a renderer with a new output directory and this fails
    until the kind is named.
    """
    unclaimed = [p for p in site
                 if not any(match(p) for match in PAGE_KINDS.values())]
    assert not unclaimed, (
        f"these emitted pages match no kind in PAGE_KINDS: {unclaimed[:5]}")


def test_every_emitted_page_carries_the_sibling_bar(site):
    """Catches a page kind that renders without the bar.

    The bar is emitted from htmlkit.page(), which every renderer goes through,
    so this holds that no renderer grows its own document shell. Hand-build a
    page's HTML anywhere in generate.py, deepdives.py, trends.py or
    vocabulary.py instead of calling page(), and that page loses the bar and
    fails here.
    """
    for name, raw in site.items():
        assert '<div class="stickynav">' in raw, f"{name} has no sibling bar"
        assert '<span class="sitelinks">' in raw, f"{name} has no sibling links"


def test_the_bar_names_the_three_surfaces_exactly(site):
    """Catches a wrong host, a missing sibling, or a linked self.

    Reading is the current site, so it is a span, not a link; books and viewing
    are anchors at their exact production hosts. Point one at the wrong
    hostname, drop a scheme, link Reading to itself, or reorder the three, and
    this fails. The order is asserted because it is the same on all three
    surfaces: Reading, Books, Viewing.
    """
    expected = ('<span class="sitelinks">'
                '<span class="on">Reading</span>'
                f'<a href="{BOOKS}">Books</a>'
                f'<a href="{VIEWING}">Viewing</a>'
                '</span>')
    for name, raw in site.items():
        assert expected in raw, f"{name} does not carry the exact sibling links"


def test_the_wordmark_reads_adamthede_reading_and_links_home_from_any_depth(site):
    """Catches a wordmark that 404s from a nested page.

    The href is rewritten per depth, so the root page links to `./` and a week
    page two directories down links to `../../`. Hard-code the href, or drop
    the depth argument in one renderer, and a reader on a week page lands on a
    path that does not exist. The `<i>reading</i>` half is what the stylesheet
    turns into " / READING", so losing it silently halves the wordmark.
    """
    assert '<a class="wordmark" href="./">adamthede<i>reading</i></a>' in site["index.html"]
    assert ('<a class="wordmark" href="../../">adamthede<i>reading</i></a>'
            in site["weeks/2012-W02/index.html"])
    assert ('<a class="wordmark" href="../">adamthede<i>reading</i></a>'
            in site["orgs/index.html"])
    assert ('<a class="wordmark" href="../">adamthede<i>reading</i></a>'
            in site["weeks/index.html"])


def test_the_bar_sits_above_the_page_and_leaves_the_masthead_alone(site):
    """Catches a bar dropped inside the content column, or one that displaced
    the existing masthead.

    The bar has to be a sibling of `.page`, not a child: `.page` is a centred
    column - 720px on the reading pages, 1180px on the cover - and a sticky
    element inside it would be inset and would scroll with the column's
    padding. And each page's own eyebrow and title must survive underneath it
    untouched - the bar is added chrome, not a replacement header. Move the nav
    call inside the `.page` div, or edit either masthead, and this fails.

    Two pages are checked because the root changed hands on 2026-09-08: the
    cover took `/` and the weeks index moved to `/weeks/`, and the index's
    masthead has to arrive at its new address intact.
    """
    cover = site["index.html"]
    assert cover.index('<div class="stickynav">') < cover.index('<div class="page wide">'), \
        "the bar must be outside and above the cover's column"
    assert "<h1>A Reading Life</h1>" in cover
    assert cover.index('<div class="stickynav">') < cover.index("<h1>A Reading Life</h1>")

    weeks = site["weeks/index.html"]
    assert weeks.index('<div class="stickynav">') < weeks.index('<div class="page">'), \
        "the bar must be outside and above the .page column"
    # The eyebrow is written lowercase and uppercased by `.label`; asserting
    # the source string rather than the rendered one keeps this about the
    # markup surviving, not about the stylesheet.
    assert '<span class="label kicker">reading.adamthede.com</span>' in weeks
    assert "<h1>The Week in Reading</h1>" in weeks
    assert weeks.index('<div class="stickynav">') < weeks.index("<h1>The Week in Reading</h1>")


def test_the_bar_brings_no_external_resource(site):
    """Catches a bar that reaches off the machine to render.

    The whole site is self-contained: no font link, no CDN script, no remote
    image. The two sibling anchors are the only outbound references the bar
    adds and both must be https. Add a webfont for the wordmark, or link a
    sibling over plain http, and this fails.
    """
    for name, raw in site.items():
        bar = re.search(r'<div class="stickynav">.*?</div>\s*</div>', raw, re.S)
        assert bar, f"{name} has no bar to inspect"
        b = bar.group(0)
        assert "<script" not in b.lower(), f"{name}: the bar carries a script"
        assert "<link" not in b.lower(), f"{name}: the bar links a resource"
        assert not re.search(r"<(?:img|iframe|source|video|audio|embed)\b", b), \
            f"{name}: the bar embeds a remote asset"
        for href in re.findall(r'href="([^"]+)"', b):
            if href.startswith("http"):
                assert href.startswith("https://"), f"{name} links insecurely to {href}"


# ---------------------------------------------------------------------------
# the rules that make it pin, and clip
# ---------------------------------------------------------------------------

def _css(site_dir=None):
    return (gen.STYLE + __import__("deepdives").EXTRA_STYLE
            + __import__("trends").TRENDS_STYLE
            + __import__("vocabulary").VOCAB_STYLE)


def test_the_inner_measure_is_the_one_that_aligns_the_three_bars():
    """Catches the silent break of the whole cross-site alignment claim.

    1180px is not this site's measure - its content column is 720px, and the
    books surface's is 980px. It is the books BAR's measure, and copying it is
    the entire reason the wordmark's left edge lands at 134px and the sibling
    links' right edge at 1266px on all three surfaces at the same viewport
    width. Those two edges are what a reader moving between the sites actually
    sees line up.

    An adversarial review found this escaping: changing 1180px to this site's
    own 980px left the family alignment broken with every test green. The
    number now has a watcher.

    `flex-wrap: wrap` is asserted with it because it is the other half of the
    same rule's job: at 390px the bar wraps rather than overflowing. Until
    2026-09-08 that wrapping was also what let this site keep the sibling links
    on a phone where the books bar hides them; with the page row added there is
    no longer room for both, and this bar now hides the siblings below 880px
    exactly as the books bar does - see
    test_the_bar_hides_the_siblings_on_a_phone_the_way_the_books_bar_does.
    """
    css = re.sub(r"\s+", "", _css())
    rule = re.search(r"\.snin\{[^}]*\}", css)
    assert rule, ".snin has no rule at all"
    assert "max-width:1180px" in rule.group(0), (
        "the bar's inner measure must stay 1180px, the value the books and "
        "viewing bars use, or the three bars stop lining up")
    assert "flex-wrap:wrap" in rule.group(0), (
        "without flex-wrap the bar overflows at 390px instead of wrapping")
    assert "margin:0auto" in rule.group(0), \
        "the measure only centres the bar while the auto margins are there"


def test_the_shared_palette_values_are_the_books_values():
    """Catches a token that is declared but retuned away from the shared skin.

    The existing token test holds the bar's rules to naming `--ink-4` and
    `--mono` rather than literals, which is the right guard against a hex code
    pasted into a rule. It does not watch the tokens' own values, and the same
    review found `--ink-4` could be moved from #57504c to #8a8078 with the
    suite green - which recolours the wordmark's second half on this site only
    and breaks the match with the other two bars.

    These two tokens exist solely for the bar and are lifted from the books
    skin, so they are pinned to the books values. Retuning them is a decision
    about all three surfaces and should have to be made here, in the open.
    """
    css = re.sub(r"\s+", "", _css())
    assert "--ink-4:#57504c" in css, \
        "--ink-4 must stay the books skin's value or the wordmarks diverge"
    assert '--mono:ui-monospace,"SFMono",Menlo,Consolas,monospace' in css, \
        "the bar is set in the books skin's mono stack"


def test_the_bar_is_sticky_at_the_top_of_the_viewport():
    """Catches a bar that scrolls away.

    `position:sticky; top:0` is the whole behaviour. Drop either declaration,
    or the z-index that keeps it over the content it overlaps, and the bar
    stops holding at the top. Measured in a real 1400x900 viewport for the PR;
    asserted here because this suite has no browser.
    """
    css = re.sub(r"\s+", "", _css())
    rule = re.search(r"\.stickynav\{[^}]*\}", css)
    assert rule, ".stickynav has no rule at all"
    assert "position:sticky" in rule.group(0), "the bar must be sticky"
    assert "top:0" in rule.group(0), "a sticky element with no top never pins"
    assert "z-index:20" in rule.group(0), "the bar must sit over the content"
    assert "background:var(--bg)" in rule.group(0), \
        "a transparent sticky bar shows the content scrolling under its text"


def test_the_document_still_clips_horizontally_so_the_bar_keeps_pinning():
    """Catches the pairing that breaks sticky, in either direction.

    Measured on the books surface at 390px: `overflow-x:hidden` on a document
    element makes it a scroll container and a sticky child stops pinning, while
    dropping the clip entirely lets a wide child drag the page sideways. This
    site already carried `hidden` then `clip` in one rule - the hidden is a
    fallback for engines with no `clip` support and the clip wins wherever both
    are understood, so it is kept rather than removed. What must not happen is
    a `hidden` that wins: reorder the two declarations, or add an
    `overflow-x:hidden` after this rule, and the bar unpins.
    """
    css = _css()
    rule = re.search(r"html,\s*body\s*\{[^}]*\}", css)
    assert rule, "html/body has no overflow rule"
    decls = re.findall(r"overflow-x:\s*(\w+)", rule.group(0))
    assert decls, "the document axes must be constrained"
    assert decls[-1] == "clip", \
        f"clip must be the winning declaration, got {decls}"
    after = css[rule.end():]
    assert "overflow-x:hidden" not in re.sub(r"\s+", "", after), \
        "a later overflow-x:hidden would reinstate the scroll container"


def test_the_bar_uses_the_shared_skin_variables_rather_than_new_colours():
    """Catches a bar hand-coloured with literals instead of the palette.

    The three surfaces look identical because they name the same tokens, not
    because someone matched hex values by eye. Replace `var(--brand)` with
    `#FF8F3B` in the wordmark rule and the bar still renders correctly today
    and drifts the next time the palette moves - so the rules are held to the
    tokens.
    """
    css = re.sub(r"\s+", "", _css())
    for selector, token in ((r"\.wordmark\{", "var(--brand)"),
                            (r"\.wordmark i\{", "var(--ink-4)"),
                            (r"\.sitelinks a\{", "var(--ink-3)"),
                            (r"\.sitelinks \.on\{", "var(--ink-2)"),
                            (r"\.stickynav\{", "var(--rule)")):
        rule = re.search(re.sub(r"\s+", "", selector) + r"[^}]*\}", css)
        assert rule, f"{selector} has no rule"
        assert token in rule.group(0), f"{selector} does not use {token}"
    assert "--ink-4:" in css, "the wordmark's second half needs the --ink-4 token"
    assert "--mono:" in css, "the bar is set in the shared mono token"


def test_the_stylesheet_still_declares_one_root(synth_dir, index_file, tmp_path):
    """Catches a second palette block sneaking in with the bar's tokens.

    `--ink-4` and `--mono` are new here; they belong in the existing `:root`,
    not in a second one appended next to the bar's rules. Add a `:root{}` of
    its own and the two palettes can drift apart.
    """
    out = tmp_path / "_site"
    gen.generate(synth_dir, out, index_path=index_file)
    assert (out / "style.css").read_text().count(":root {") == 1


def test_the_bar_markup_is_generated_once_not_per_renderer():
    """Catches a copy of the bar pasted into a second module.

    One function emits it, so a change to the bar is one edit. Paste the markup
    into deepdives.py or trends.py and the two copies drift; this holds the
    string to a single home by rendering it from htmlkit and asserting the
    renderers do not spell it themselves.
    """
    assert '<div class="stickynav">' in htmlkit.nav(0)
    for module in ("generate.py", "deepdives.py", "trends.py", "vocabulary.py",
                   "corpus.py"):
        src = (SITE / module).read_text(encoding="utf-8")
        assert 'class="stickynav"' not in src, \
            f"{module} spells the bar out instead of calling htmlkit.nav()"
