"""The cover at `/`, the weeks index at `/weeks/`, and the page row on both.

Adam approved `docs/mockups/2026-09-08-reading-cover.html` on 2026-09-08 and
settled where it lives: the cover becomes the root page and the weeks index
moves to `/weeks/`, its week pages already being under that directory. The
sticky bar gains the page row the books and viewing bars carry.

Every test here names, in its docstring, the mutation it catches.

Three things these tests deliberately do NOT do.

They do not drive a browser. This suite has none and the repo has no CI to run
one in, so the viewport claims - no horizontal overflow at 390px and 1400px, a
bar that still pins after a scroll - are asserted at the stylesheet level and
measured for real on the pull request, the same split
`test_site_sibling_nav.py` settled.

They do not retype the mockup's figures as expectations on the live archive
without saying so: `test_the_live_archive_reproduces_the_figures_adam_approved`
is the one test that asserts 16,382 and its companions, it recomputes each of
them from the index rather than reading them off `cover.facts`, and it skips
where the vault is not mounted.

They do not verify the move by comparing the new code to itself. The claim
"`/weeks/` is the old index" is checked against a build of `site/generate.py`
as it stood at the commit this branch forked from, byte for byte, modulo the
three differences the move is allowed to make.
"""
import datetime as dt
import html as html_mod
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

MOCKUP = REPO / "docs" / "mockups" / "2026-09-08-reading-cover.html"

# The tip of `main` this branch forked from. Pinned to a SHA rather than to
# `origin/main`, which moves: once this work merges, a floating ref would
# compare the new generator against itself and the test would vouch for
# nothing.
BASE_COMMIT = "4442ad0"

# Read at import, deliberately. conftest's autouse `isolate_credentials`
# fixture deletes INSTAPAPER_VAULT_PATH from the environment of every test, so
# that no test can reach the real vault by accident. The one test that is
# ABOUT the real vault has to capture the path before that runs, or it would
# skip on the only machine where it can say anything.
LIVE_VAULT = os.environ.get("INSTAPAPER_VAULT_PATH")


def _live_index():
    """The Parquet index, wherever this checkout can see one.

    `data/archive_index.parquet` is gitignored, so it exists in the main
    checkout and not in a worktree - and this work was built in a worktree.
    `mutation_audit.sh` already resolves the test interpreter through
    `--git-common-dir`; the same hop finds the index.
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


# ---------------------------------------------------------------------------
# a corpus small enough to add up by hand, and shaped like the real one
# ---------------------------------------------------------------------------

# (year-month-day read, source, url, minutes, canonical entries)
# word_count is 238 * minutes throughout, which is the reading speed the site
# already prints, so words and hours can each be checked on their own.
ROWS = [
    ("2008-03-01", "legacy_pdf", None, 500, ["Social Media"]),
    ("2011-02-01", "legacy_doc", None, 1000, ["Mobile Devices"]),
    ("2011-05-01", "legacy_txt", None, 750, ["Social Media"]),
    ("2011-09-01", "legacy_htm", None, 250, ["Innovation"]),
    ("2012-01-10", "instapaper", "https://www.example.com/a", 600,
     ["Social Media", "Mobile Devices"]),
    # Between the seam and the AI window on purpose: without a row here,
    # "since 2018" and "since 2016" select the same articles and the constant
    # that says which is a free variable no test can see.
    ("2016-05-05", "instapaper", "https://old.example.net/g", 200,
     ["Innovation"]),
    ("2018-04-02", "instapaper", "https://sub.example.org/b", 400,
     ["Artificial Intelligence"]),
    ("2019-06-03", "instapaper", "https://news.example.net/c", 1500,
     ["Artificial Intelligence", "Innovation"]),
    ("2020-08-04", "instapaper", "https://www.example.com/d", 300, ["Innovation"]),
    ("2024-02-05", "matter", "https://matter.example.com/e", 1100,
     ["Artificial Intelligence"]),
    ("2026-01-06", "matter", "https://www.example.com/f", 450, ["Mobile Devices"]),
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
- title: Alpha {week}
  url: https://www.example.com/a
  words: {words}
  date_read: '{start}'
---

Only paragraph of the digest for {week}.
"""

# Three consecutive ISO weeks and one on its own, so the longest streak is a
# measured 3 rather than "all of them".
WEEKS = [("2012-W02", 1, 1200), ("2012-W03", 2, 2400), ("2012-W04", 3, 3600),
         ("2013-W06", 1, 1000)]


def _host(url):
    if not url:
        return ""
    host = urlparse(str(url)).netloc.lower()
    return host[4:] if host.startswith("www.") else host


@pytest.fixture
def synth_dir(tmp_path):
    d = tmp_path / "synthesis"
    d.mkdir()
    for week, count, words in WEEKS:
        y, w = week.split("-W")
        start = dt.date.fromisocalendar(int(y), int(w), 1)
        d.joinpath(f"{week}.md").write_text(
            WEEK_TEMPLATE.format(week=week, start=start.isoformat(),
                                 end=(start + dt.timedelta(days=6)).isoformat(),
                                 count=count, words=words,
                                 hours=round(words / WORDS_PER_MINUTE / 60, 2)),
            encoding="utf-8")
    return d


@pytest.fixture
def index_file(tmp_path):
    records = []
    for i, (day, source, url, minutes, entries) in enumerate(ROWS):
        archived = None if source.startswith("legacy") else day
        records.append({
            "instapaper_id": None, "matter_id": None, "source": source,
            "content_type": "article", "title": f"Title {i}", "url": url,
            "author": "Ada Lovelace", "date_saved": day,
            "date_archived": archived,
            "word_count": minutes * WORDS_PER_MINUTE,
            "content_corrupted": False, "reading_time_min": float(minutes),
            "topics": ["AI"], "people": ["Ada Lovelace"], "orgs": ["Google"],
            "locations": ["Berlin"], "concepts": list(entries),
            # Names taken from the committed data/taxonomy/v1.yaml, so the
            # taxonomy gate really opens and /concepts/ is really built.
            "canonical_entries": list(entries),
            "sentiment": "Neutral", "emotion": "Analytical", "summary": "s",
            "file_path": f"/{i}.md", "content_snippet": "snip",
        })
    df = pd.DataFrame(records)
    for c in ("date_saved", "date_archived"):
        df[c] = pd.to_datetime(df[c])
    p = tmp_path / "index.parquet"
    df.to_parquet(p)
    return p


@pytest.fixture
def built(synth_dir, index_file, tmp_path):
    out = tmp_path / "_site"
    gen.generate(synth_dir, out, index_path=index_file)
    return out


@pytest.fixture
def site(built):
    """Every page the generator emitted, keyed by path, as raw HTML."""
    return {p.relative_to(built).as_posix(): p.read_text(encoding="utf-8")
            for p in sorted(built.rglob("*.html"))}


# ---------------------------------------------------------------------------
# reading the cover back out of its own markup
# ---------------------------------------------------------------------------

HERO_RE = re.compile(
    r'<div class="fhero( accent)?"><span class="fhl label">(.*?)</span>'
    r'<span class="fhv num">(.*?)</span><span class="fhd label">(.*?)</span></div>')
# The lookahead has to name the two cell classes exactly: `<span class="fs`
# also prefixes the `fss` note INSIDE a cell, and a loose boundary ends every
# capture at the first note rather than at the next cell.
SEC_RE = re.compile(
    r'<span class="fs(?: wide)?"><span class="fsk label">(.*?)</span>(.*?)'
    r'(?=<span class="fs(?:"| wide")|</div>)', re.S)


def heroes(page_html):
    """(label, value, note, is_amber) for each column's hero, in order."""
    return [(m.group(2), m.group(3), m.group(4), bool(m.group(1)))
            for m in HERO_RE.finditer(page_html)]


def secondaries(page_html):
    """{label: rendered block} for every secondary cell on the cover."""
    return {m.group(1): m.group(2) for m in SEC_RE.finditer(page_html)}


def text_of(fragment):
    return html_mod.unescape(re.sub(r"<[^>]+>", " ", fragment))


# ---------------------------------------------------------------------------
# the figures
# ---------------------------------------------------------------------------

def test_the_cover_prints_the_figures_an_independent_recompute_gives(site):
    """Catches a figure computed the wrong way round, off the wrong column, or
    over the wrong subset.

    Nothing here calls `cover.facts`. Every expectation is summed in plain
    Python from ROWS and WEEKS above, so the two arithmetics have to agree:
    swap `word_count` for `reading_time_min`, count sources over the whole
    corpus instead of the rows that kept a URL, divide the seam by the later
    year instead of the earlier one, or take the AI share over everything
    rather than over the tagged rows since 2018, and one of these fails.
    """
    page_html = site["index.html"]
    minutes = sum(r[3] for r in ROWS)
    words = sum(r[3] * WORDS_PER_MINUTE for r in ROWS)
    hosts = {_host(r[2]) for r in ROWS if r[2]}
    url_bearing = sum(1 for r in ROWS if r[2])
    y2011 = sum(1 for r in ROWS if r[0].startswith("2011"))
    y2012 = sum(1 for r in ROWS if r[0].startswith("2012"))
    since_2018 = [r for r in ROWS if int(r[0][:4]) >= 2018]
    ai_new = sum(1 for r in since_2018
                 if "Artificial Intelligence" in r[4]) / len(since_2018) * 100

    labels = [h[0] for h in heroes(page_html)]
    assert labels == ["articles read", "weekly syntheses", "distinct sources",
                      "artificial intelligence"], labels
    values = {h[0]: h[1] for h in heroes(page_html)}
    assert values["articles read"] == f"{len(ROWS):,}"
    assert values["weekly syntheses"] == f"{len(WEEKS):,}"
    assert values["distinct sources"] == f"{len(hosts):,}"
    assert values["artificial intelligence"] == f"{round(ai_new, 1)}<em>%</em>"

    sec = secondaries(page_html)
    assert f"{words / 1e6:.1f}" in sec["words"]
    assert f"{words:,} in all" in text_of(sec["words"])
    assert f"{round(minutes / 60):,}" in sec["reading time"]
    assert f"&minus;{abs(round((y2012 - y2011) / y2011 * 100))}" in sec["the 2012 seam"]
    assert (f"{y2011:,} articles in 2011, {y2012:,} in 2012"
            in text_of(sec["the 2012 seam"]))
    assert f"{url_bearing:,} of {len(ROWS):,} articles" in text_of(sec["carries a link"])
    # Three consecutive ISO weeks and one apart from them.
    assert ">3<" in sec["longest streak"] or "3" == text_of(sec["longest streak"]).split()[0]
    assert "weeks unbroken, Jan 2012 to Jan 2012" in text_of(sec["longest streak"])


def test_the_hero_row_is_the_one_adam_approved_with_articles_the_only_amber(site):
    """Catches a second amber hero, or a swapped one.

    Adam approved the heroes per column as mocked, with Articles the only amber
    and the AI share kept as the Subjects hero. A cover with two accents has no
    hero at all, and moving the accent to another column changes what the page
    is about. Add `accent=True` to a second call in `cover.columns`, or drop it
    from the first, and this fails.
    """
    page_html = site["index.html"]
    amber = [h[0] for h in heroes(page_html) if h[3]]
    assert amber == ["articles read"], amber
    assert page_html.count('class="fhero accent"') == 1


def test_the_deep_dive_count_is_the_pages_the_build_wrote(site, built):
    """Catches the mockup's typed 29 surviving into the generator.

    "22 year rollups, 7 facets" was a number someone counted by hand on the day
    the mockup was rendered. It is now the pages this build actually produced,
    so a facet added or a year page lost moves it. Hardcode it back and this
    fails on a fixture whose archive has eight years, not twenty-two.
    """
    # The years index itself is years/index.html, not years/<year>/index.html,
    # so globbing one level down counts rollups and not the directory page.
    rollups = len(list((built / "years").glob("*/index.html")))
    facets = len([d for d in built.iterdir()
                  if d.is_dir() and d.name not in ("years", "weeks")
                  and (d / "index.html").exists()])
    block = text_of(secondaries(site["index.html"])["deep dives"])
    assert f"{rollups} year rollups, {facets} facets" in block, block
    assert f"{rollups + facets}" in block


@pytest.mark.skipif(
    not LIVE_INDEX.exists() or not LIVE_VAULT
    or not Path(LIVE_VAULT, "synthesis").is_dir(),
    reason="needs the live index and the mounted vault")
def test_the_live_archive_reproduces_the_figures_adam_approved():
    """Catches the cover drifting away from the mockup Adam signed off.

    These are the figures on `docs/mockups/2026-09-08-reading-cover.html`. Each
    is recomputed here from the index and the week records rather than read off
    `cover.facts`, so this is a second opinion on the same archive and not a
    restatement of the first. It skips where the vault is not mounted, which is
    every machine but Adam's.
    """
    import corpus as C
    c = C.load_corpus(LIVE_INDEX)
    synth = Path(LIVE_VAULT) / "synthesis"
    weeks = [m for m in gen.load_weeks(synth)
             if str(m["week"]) >= gen.SITE_EPOCH_WEEK]
    rows = c.rows

    assert len(rows) == 16382
    assert round(int(rows["word_count"].fillna(0).sum()) / 1e6, 1) == 17.4
    assert round(float(rows["reading_time_min"].fillna(0).sum()) / 60) == 1216
    assert len(weeks) == 827
    assert len({d for d in rows["domain"] if d}) == 1164

    per_year = rows.groupby("year").size()
    assert round((int(per_year[2012]) - int(per_year[2011]))
                 / int(per_year[2011]) * 100) == -49

    assert cover.longest_streak(weeks)[0] == 358

    tagged = rows[[bool(C.as_list(v)) for v in rows[cover.CANONICAL_COLUMN]]]
    since = tagged[tagged["year"] >= 2018]
    share = sum(1 for v in since[cover.CANONICAL_COLUMN]
                if "Artificial Intelligence" in C.as_list(v)) / len(since) * 100
    assert round(share, 1) == 14.8


# ---------------------------------------------------------------------------
# the weeks index arrived at its new address intact
# ---------------------------------------------------------------------------

@pytest.fixture
def old_index_html(synth_dir, index_file, tmp_path):
    """The weeks index as the generator rendered it at `/` before the move.

    Built by running `site/generate.py` from BASE_COMMIT against the same
    fixtures, in a subprocess so the two generators cannot share a module
    namespace. Everything else in `site/` is the current code, which is the
    point: this isolates the one file the move changed.
    """
    if not shutil.which("git"):
        pytest.skip("git is not available to fetch the pre-move generator")
    got = subprocess.run(["git", "-C", str(REPO), "show",
                          f"{BASE_COMMIT}:site/generate.py"],
                         capture_output=True, text=True)
    if got.returncode:
        pytest.skip(f"{BASE_COMMIT} is not in this checkout")

    # A whole repo root, not just a site directory: these modules resolve the
    # taxonomy and `entity_hygiene` relative to their own grandparent, so the
    # copy has to have the same shape or the old build fails to import, or
    # silently skips /concepts/ and gets compared against a different set of
    # facets.
    old_repo = tmp_path / "oldrepo"
    ignore = shutil.ignore_patterns("__pycache__")
    shutil.copytree(SITE, old_repo / "site", ignore=ignore)
    shutil.copytree(REPO / "scripts" / "core", old_repo / "scripts" / "core",
                    ignore=ignore)
    shutil.copytree(REPO / "data" / "taxonomy", old_repo / "data" / "taxonomy")
    old_site = old_repo / "site"
    (old_site / "generate.py").write_text(got.stdout, encoding="utf-8")

    out = tmp_path / "_oldsite"
    ran = subprocess.run([sys.executable, str(old_site / "generate.py"),
                          "--synthesis-dir", str(synth_dir),
                          "--index", str(index_file), "--out", str(out)],
                         capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr
    return (out / "index.html").read_text(encoding="utf-8")


def _without_the_three_differences(page_html, depth):
    """The page with the move's three permitted differences removed."""
    page_html = re.sub(r'<nav class="pagelinks".*?</nav>', "", page_html, flags=re.S)
    page_html = re.sub(r'\n<link rel="canonical"[^>]*>', "", page_html)
    if depth:
        page_html = page_html.replace('href="../"', 'href="./"')
        page_html = page_html.replace('href="../', 'href="')
    return page_html


def test_the_weeks_index_is_the_old_root_index_and_nothing_else_changed(
        site, old_index_html):
    """Catches any edit to the weeks index smuggled in with its move.

    The move was allowed to change three things and nothing else: the page's
    own URL (so every internal href and the stylesheet link gain one `../`),
    the new page row, and the canonical link. Strip those three from both
    sides and the bytes must be equal.

    This compares against a real build of the generator at BASE_COMMIT rather
    than against another call into the current one, because the interesting
    failure is a change to the index that the current code would make on both
    sides of the comparison. Reword the header, drop the era bar, reorder the
    facet nav, change a tooltip - each fails here and only here.
    """
    moved = _without_the_three_differences(site["weeks/index.html"], depth=1)
    before = _without_the_three_differences(old_index_html, depth=0)
    assert moved == before


def test_both_moved_pages_declare_where_they_now_live(site):
    """Catches a missing or wrong canonical URL on the two pages that moved.

    The cover took `/` and the index took `/weeks/`, so a link or a bookmark to
    either has to be able to say which page it means. Drop the `canonical`
    argument from `cover.render_cover` or from the `/weeks/` write, or point
    one at the other's address, and this fails.
    """
    assert ('<link rel="canonical" href="https://reading.adamthede.com/">'
            in site["index.html"])
    assert ('<link rel="canonical" href="https://reading.adamthede.com/weeks/">'
            in site["weeks/index.html"])


def test_the_week_pages_still_live_under_the_index_that_now_sits_above_them(site):
    """Catches a week page displaced by the index's arrival at /weeks/.

    `/weeks/index.html` is a new file in a directory that already held 827
    others. Write it before the week directories are created, or name a week
    directory `index`, and a week page is lost. This holds both to being
    present.
    """
    assert "weeks/index.html" in site
    for week, _, _ in WEEKS:
        assert f"weeks/{week}/index.html" in site


# ---------------------------------------------------------------------------
# the page row
# ---------------------------------------------------------------------------

# The page kinds that are deliberately NOT in the row. They are reachable from
# the weeks index's "Beyond the week" nav; the row is the six Adam named, not
# an index of everything on the site.
UNMARKED = ("people/", "locations/", "trends/")


def test_every_emitted_page_carries_the_page_row(site):
    """Catches a page kind that renders without the row.

    The row is emitted from `htmlkit.page()`, which every renderer goes
    through, so this holds that no renderer grows its own document shell. Hand
    -build a page's HTML anywhere in the four site modules and that page loses
    the row and fails here.
    """
    for name, raw in site.items():
        assert '<nav class="pagelinks"' in raw, f"{name} has no page row"


def test_every_page_in_the_row_marks_itself(site):
    """Catches a page that links to itself instead of marking where you are.

    The marker is the whole job of the row: it says which of the six you are
    looking at. Drop the `here=` argument from any renderer's `page()` call and
    that page renders six links and no current page. The three kinds that are
    deliberately outside the row are named above and asserted to mark nothing,
    so adding one to the row without marking it fails here too.
    """
    expected = {
        "index.html": "Cover",
        "weeks/index.html": "Weeks",
        "weeks/2012-W02/index.html": "Weeks",
        "years/index.html": "Years",
        "years/2011/index.html": "Years",
        "orgs/index.html": "Sources",
        "concepts/index.html": "Subjects",
        "together/index.html": "Subjects",
        "articles/index.html": "Articles",
    }
    for name, label in expected.items():
        assert f'<span class="here">{label}</span>' in site[name], \
            f"{name} does not mark {label}"
    for name, raw in site.items():
        if any(name.startswith(u) for u in UNMARKED):
            assert 'class="here"' not in raw, f"{name} marks a row item it is not"


def test_the_row_names_the_six_pages_in_the_order_adam_gave(site):
    """Catches a renamed, reordered, dropped or added row item.

    COVER, WEEKS, YEARS, SOURCES, SUBJECTS, ARTICLES - in that order, on every
    page. Two of the labels are deliberately not the directory name: /orgs/ is
    SOURCES here and /concepts/ is SUBJECTS. Rename either back, reorder the
    list, or add a seventh, and this fails.
    """
    labels = ["Cover", "Weeks", "Years", "Sources", "Subjects", "Articles"]
    for name, raw in site.items():
        row = re.search(r'<nav class="pagelinks".*?</nav>', raw, re.S).group(0)
        got = re.findall(r'>([A-Z][a-z]+)</(?:a|span)>', row)
        assert got == labels, f"{name} row is {got}"


def test_every_row_target_is_a_page_that_exists(site, built):
    """Catches a row item pointing at a page the build never wrote.

    The row is chrome on every page, so a dead target is not one broken link,
    it is one per page. The targets are resolved here against the built tree
    exactly as a browser would resolve them from the page they appear on.
    """
    for name, raw in site.items():
        row = re.search(r'<nav class="pagelinks".*?</nav>', raw, re.S).group(0)
        here = built / name
        for href in re.findall(r'href="([^"]+)"', row):
            target = (here.parent / href / "index.html").resolve()
            assert target.exists(), f"{name} links to {href}, which is not built"


def test_a_row_target_that_was_not_built_stops_the_build(tmp_path):
    """Catches the guard itself being removed or weakened.

    `check_page_row_targets` is what turns a mispredicted row into a failed
    build - which costs a night's rebuild and keeps the last good site - rather
    than into a site whose every page carries a 404. Delete the call, or make
    it warn instead of raise, and this fails.
    """
    root = tmp_path / "tree"
    (root / "weeks").mkdir(parents=True)
    (root / "index.html").write_text("x")
    (root / "weeks" / "index.html").write_text("x")
    rows = [("cover", "Cover", ""), ("weeks", "Weeks", "weeks/")]
    gen.check_page_row_targets(root, rows)  # both exist: no complaint
    with pytest.raises(SystemExit) as err:
        gen.check_page_row_targets(root, rows + [("orgs", "Sources", "orgs/")])
    assert "orgs/" in str(err.value)


def test_the_cumulative_series_end_where_the_totals_do(index_file, synth_dir):
    """Catches a step chart drawn off the wrong column.

    The four step charts are the only figures on the cover with no printed
    value of their own except their endpoint, which makes them the easiest
    place for a wrong column to hide: swap `word_count` for
    `reading_time_min` in `cover.series` and the cumulative-words line still
    rises plausibly, still ends at a number, and every printed fact stays
    right. Pinning each cumulative series to the total it is a running sum of
    is what makes that visible.
    """
    import corpus as C
    c = C.load_corpus(index_file)
    weeks = gen.load_weeks(synth_dir)
    axis = cover.axis_for(c.rows, weeks)
    S, _ = cover.series(axis, c.rows, weeks)

    assert sum(S["art"]) == len(ROWS)
    assert S["wcum"][-1] == sum(r[3] * WORDS_PER_MINUTE for r in ROWS)
    assert S["acum"][-1] == sum(w[1] for w in WEEKS)
    assert sum(S["wkq"]) == len(WEEKS)
    assert S["dcum"][-1] == len({_host(r[2]) for r in ROWS if r[2]})
    # Every series is drawn on the one shared axis, or the four columns are
    # four different charts wearing the same tick labels.
    for key in ("art", "wcum", "acum", "wkq", "dq", "dcum", "vq", "airoll"):
        assert len(S[key]) == axis.count, key


def test_every_link_on_the_moved_index_resolves_from_its_new_address(site, built):
    """Catches an href the move forgot to rewrite.

    This is the failure the byte-comparison cannot see: that test strips the
    `../` from every href before comparing, so an href that was never rewritten
    normalises to exactly the same string as one that was. Resolving each link
    against the built tree from the page's real location is what tells them
    apart - a week link left as `weeks/2012-W02/` resolves to
    `/weeks/weeks/2012-W02/` and is not there.
    """
    page_html = site["weeks/index.html"]
    here = (built / "weeks").resolve()
    checked = 0
    for href in re.findall(r'href="([^"]+)"', page_html):
        if href.startswith(("http", "#")):
            continue
        target = (here / href).resolve()
        if href.endswith(".css"):
            assert target.is_file(), href
        else:
            assert (target / "index.html").is_file(), \
                f"{href} does not resolve from /weeks/"
        checked += 1
    assert checked > len(WEEKS), "the index links almost nothing: check the regex"


def test_a_build_whose_row_names_a_missing_page_refuses_to_swap(
        synth_dir, index_file, tmp_path, monkeypatch):
    """Catches the broken-row guard being disconnected from the build.

    The guard function has its own test; this one holds that `generate()` still
    calls it. The row is forced to name a page nothing will write, and the
    build has to abort before the swap - which is what keeps the last good site
    standing on the night a prediction goes wrong. Delete the call and this
    build ships a site whose every page carries a dead link.
    """
    real = gen.page_row
    monkeypatch.setattr(gen, "page_row",
                        lambda built, weeks_at_root=False:
                        real(built, weeks_at_root) + [("x", "Ghost", "ghost/")])
    out = tmp_path / "_ghost"
    with pytest.raises(SystemExit) as err:
        gen.generate(synth_dir, out, index_path=index_file)
    assert "ghost/" in str(err.value)
    assert not out.exists(), "a refused build must not swap a broken site in"


def test_the_row_is_generated_once_not_pasted_into_a_renderer(site):
    """Catches a copy of the row markup in a second module.

    One function emits it, so a change to the row is one edit. Paste the markup
    into deepdives.py, trends.py, vocabulary.py or cover.py and the copies
    drift.
    """
    assert '<nav class="pagelinks"' in htmlkit.nav(0)
    for module in ("generate.py", "deepdives.py", "trends.py", "vocabulary.py",
                   "cover.py", "corpus.py"):
        src = (SITE / module).read_text(encoding="utf-8")
        assert 'class="pagelinks"' not in src, \
            f"{module} spells the row out instead of calling htmlkit.nav()"


def test_a_build_does_not_leak_its_row_into_the_next_one(synth_dir, tmp_path):
    """Catches the module-level row outliving the build that installed it.

    The row is narrowed per build, which is module state on htmlkit. A build
    that installs a narrow row and does not restore it would hand the next
    build - or a renderer called on its own, as every other test file does - a
    row from a corpus it knows nothing about. `generate()` restores it in a
    finally block; drop that and this fails.

    The build here is the weeks-only one, deliberately: a full build's row IS
    the module default, so a leak from it is invisible. This one installs a
    single-item row, which is exactly what must not survive.
    """
    before = list(htmlkit._page_row)
    gen.generate(synth_dir, tmp_path / "_weeksonly", index_path=None)
    assert htmlkit._page_row == before
    assert htmlkit._page_row == htmlkit.PAGE_ROW


# ---------------------------------------------------------------------------
# the cover matches the design it was approved from
# ---------------------------------------------------------------------------

def _classes(markup):
    out = set()
    for m in re.finditer(r'class="([^"]*)"', markup):
        out.update(m.group(1).split())
    return out


def test_every_class_the_approved_cover_uses_is_emitted(site, built):
    """Catches a stylesheet rule the cover can never match.

    The cover's rules are lifted whole from the approved mockup, so every class
    the mockup's own cover markup carries is a hook the generator has to
    provide. The sibling books repo shipped its cover as a bare `pg`, which
    left `.cover h1{font-size:88px}` dead and rendered the largest element in
    the design a quarter too small with four screenshots attached to the PR.
    This is the structural version of that gate: drop `wide=True` from
    `render_cover`, or stop emitting `.fhero`, `.mini` or `.fsec`, and it
    fails.

    Only the mockup's own cover article is compared. Its page chrome - the
    mockup banner, the embedded live index, the decisions strip - is scaffolding
    for Adam's review and was never meant to ship.
    """
    approved = MOCKUP.read_text(encoding="utf-8")
    article = approved[approved.index('<article id="top">'):approved.index("</article>")]
    wanted = _classes(article) - {"stamp", "pgn", "url", "note", "wrap"}
    emitted = _classes(site["index.html"])
    styled = set(re.findall(r"\.([A-Za-z][\w-]*)",
                            "\n".join(b.split("{", 1)[0] for b in re.findall(
                                r"[^{}]*\{[^{}]*\}", cover.COVER_STYLE))))
    lost = sorted(c for c in wanted - emitted if c in styled or c in wanted)
    assert not lost, (
        f"the approved cover uses {lost} and the generator never emits them")


def test_the_cover_rules_invent_no_colour_of_their_own(site, built):
    """Catches a hex code pasted into the cover's stylesheet.

    The three surfaces look alike because they name the same tokens, not
    because someone matched hex values by eye. The cover's rules are allowed
    exactly two literals - the bar fill and the step stroke, which are chart
    ink rather than palette - and everything else has to be a var(). Paste
    `#fbbf24` in place of `var(--amber)` and this fails.
    """
    literals = set(re.findall(r"#[0-9a-fA-F]{3,6}", cover.COVER_STYLE))
    assert literals <= {"#6d635c", "#2f2a27"}, sorted(literals)
    for token in re.findall(r"var\((--[\w-]+)\)", cover.COVER_STYLE):
        assert f"{token}:" in gen.STYLE, f"{token} is used but never declared"


def test_the_stylesheet_still_declares_one_root(built):
    """Catches the cover's tokens arriving in a second palette block.

    `--serif` and `--rule-2` are new; they belong in the existing `:root`, not
    in one of their own next to the cover's rules, or the two palettes can
    drift apart.
    """
    assert (built / "style.css").read_text().count(":root {") == 1


def test_the_cover_reaches_nothing_off_this_machine(site):
    """Catches a webfont, a chart library or a remote image on the cover.

    Every chart on this site is drawn from the data at build time, in SVG, with
    no script and no request. The cover's serif is a stack of faces that are
    already on the machine or are not. Add a Google Fonts link for Charter, or
    an <img> for a chart, and this fails.
    """
    page_html = site["index.html"]
    assert "<script" not in page_html.lower()
    assert not re.search(r"<(?:img|iframe|source|video|audio|embed)\b", page_html)
    for href in re.findall(r'href="([^"]+)"', page_html):
        if href.startswith("http"):
            assert href.startswith("https://books.adamthede.com/") \
                or href.startswith("https://viewing.adamthede.com/") \
                or href == "https://reading.adamthede.com/", href


def test_two_builds_of_the_same_data_are_byte_identical(synth_dir, index_file,
                                                        tmp_path):
    """Catches a figure or a coordinate that depends on iteration order.

    A cover built twice from one archive must be the same file, or a nightly
    rebuild ships a diff every night and nobody can tell a real change from
    float formatting. Sets are iterated in several places here; replace a
    sorted() with one of them and this fails.
    """
    a, b = tmp_path / "a", tmp_path / "b"
    gen.generate(synth_dir, a, index_path=index_file)
    gen.generate(synth_dir, b, index_path=index_file)
    for page_path in sorted(a.rglob("*.html")):
        rel = page_path.relative_to(a)
        assert page_path.read_bytes() == (b / rel).read_bytes(), rel


# ---------------------------------------------------------------------------
# the rules that keep it from scrolling sideways, and keep the bar pinned
# ---------------------------------------------------------------------------

def _css(built):
    return (built / "style.css").read_text(encoding="utf-8")


def _squash(css):
    """The stylesheet with whitespace and optional final semicolons removed,
    so an assertion is about the rule and not about how it was typed."""
    return re.sub(r";\}", "}", re.sub(r"\s+", "", css))


def test_the_document_still_clips_horizontally_at_every_width(built):
    """Catches the pairing that breaks sticky, in either direction.

    Measured on the books surface at 390px: `overflow-x:hidden` on a document
    element makes it a scroll container and a sticky child stops pinning, while
    dropping the clip entirely lets a wide child drag the page sideways. The
    cover is the widest thing this site has ever rendered - four columns on a
    1180px measure - so it is the page most able to drag. Reorder the two
    declarations, or add an `overflow-x:hidden` after them, and this fails.
    """
    css = _css(built)
    rule = re.search(r"html,\s*body\s*\{[^}]*\}", css)
    assert rule, "html/body has no overflow rule"
    decls = re.findall(r"overflow-x:\s*(\w+)", rule.group(0))
    assert decls[-1] == "clip", f"clip must be the winning declaration, got {decls}"
    assert "overflow-x:hidden" not in re.sub(r"\s+", "", css[rule.end():])


def test_nothing_on_the_cover_is_pinned_wider_than_the_viewport(built, site):
    """Catches a cover element that cannot shrink below 390px.

    Every wide thing on this page is either a grid that collapses at a
    breakpoint or an SVG with a viewBox and `width:100%`, which is what lets
    the cover render at 390px without a sideways drag. Give a strip a fixed
    pixel width, or drop the single-column rule at 760px, and the page starts
    scrolling horizontally on a phone. Measured for real at 390px and 1400px
    on the pull request; asserted here because this suite has no browser.
    """
    css = _squash(_css(built))
    assert ".fstrip{display:block;width:100%;height:auto" in css
    assert ".fstep{display:block;width:100%;height:auto" in css
    assert "@media(max-width:760px){.wideh1{font-size:48px}.fgrid{grid-template-columns:1fr" in css
    assert "@media(max-width:1080px){.fgrid{grid-template-columns:1fr1fr" in css
    # The cover's measure is a max-width, not a width: at 390px the column is
    # 390px wide, not 1180 with 790 hanging off the side. And the class has to
    # be emitted, or the rule is dead and the cover renders in the 720px
    # reading column with four columns crushed into it.
    assert ".page.wide{max-width:1180px}" in css
    assert '<div class="page wide">' in site["index.html"]
    for svg in re.findall(r"<svg[^>]*>", site["index.html"]):
        assert "viewBox=" in svg, svg
        assert not re.search(r'\swidth="\d', svg), svg


def test_the_bar_hides_the_siblings_on_a_phone_the_way_the_books_bar_does(built):
    """Catches the 880px rule going missing now that there are two rows.

    While this bar had one row it kept the sibling links all the way down to
    390px, and that was right. With the page row added there is no longer space
    for both, and the books surface already settled which half goes: the
    siblings hide, the page row tightens, the wordmark drops its second half.
    Delete this media block and the bar wraps to three lines on a phone.
    """
    css = _squash(_css(built))
    block = re.search(r"@media\(max-width:880px\)\{(.*?)\}\s*@media", css, re.S)
    assert block, "the 880px block is gone"
    body = block.group(1)
    assert ".sitelinks{display:none}" in body
    assert ".pagelinks{gap:14px" in body
    assert ".wordmarki{display:none}" in body


def test_the_bar_is_still_sticky_over_the_cover(built):
    """Catches a bar that scrolls away on the new root page.

    `position:sticky; top:0` and a z-index above the content is the whole
    behaviour, and the cover is the page it most has to survive: it is the
    tallest and the widest on the site. Measured after a real scroll in a
    1400x900 viewport for the PR; asserted here because this suite has no
    browser.
    """
    css = _squash(_css(built))
    rule = re.search(r"\.stickynav\{[^}]*\}", css)
    assert rule, ".stickynav has no rule at all"
    for decl in ("position:sticky", "top:0", "z-index:20", "background:var(--bg)"):
        assert decl in rule.group(0), decl


# ---------------------------------------------------------------------------
# fail-open
# ---------------------------------------------------------------------------

def test_an_index_without_the_taxonomy_join_keeps_the_site_it_had(
        synth_dir, index_file, tmp_path):
    """Catches a hollow cover shipping on an index that cannot fill it.

    A quarter of the cover is the Subjects column and it is drawn entirely from
    `canonical_entries`. Without that column the four Subjects measures would
    render as zeros presented as facts, so there is no cover, the weeks index
    keeps the root it has always had, and the row narrows to what exists.
    Remove the `can_render` guard and this build ships a cover with an empty
    quarter and a row pointing at a /weeks/ that was never written.
    """
    df = pd.read_parquet(index_file).drop(columns=[cover.CANONICAL_COLUMN])
    thin = tmp_path / "thin.parquet"
    df.to_parquet(thin)

    out = tmp_path / "_thin"
    gen.generate(synth_dir, out, index_path=thin)
    root = (out / "index.html").read_text(encoding="utf-8")
    assert "<h1>The Week in Reading</h1>" in root
    assert "A Reading Life" not in root
    assert not (out / "weeks" / "index.html").exists()
    row = re.search(r'<nav class="pagelinks".*?</nav>', root, re.S).group(0)
    assert "Cover" not in row and "Subjects" not in row
    assert '<span class="here">Weeks</span>' in row
    assert "Years" in row and "Sources" in row


def test_a_weeks_only_build_still_has_a_root_page_and_an_honest_row(
        synth_dir, tmp_path):
    """Catches the root going missing when there is no index at all.

    `--no-index` and an unreadable parquet both degrade to a weeks-only site,
    and the whole point of that posture is that a site still exists. Move the
    index unconditionally to /weeks/ and the root 404s on the one night the
    vault is slow. The row here is the weeks index alone, because that is all
    there is.
    """
    out = tmp_path / "_weeksonly"
    gen.generate(synth_dir, out, index_path=None)
    root = (out / "index.html").read_text(encoding="utf-8")
    assert "<h1>The Week in Reading</h1>" in root
    assert not (out / "weeks" / "index.html").exists()
    row = re.search(r'<nav class="pagelinks".*?</nav>', root, re.S).group(0)
    assert re.findall(r'>([A-Z][a-z]+)</(?:a|span)>', row) == ["Weeks"]
