"""The public shape of this site: the cover alone, redacted at the data layer.

    site/generate.py --public --out records/reading

Emits the Reading record for the public tier described in command-center's
`docs/planning/2026-09-09-data-adamthede-com.md`: a coverage-map index at
data.adamthede.com over public shapes of the private lifelogging records, each
record keeping its own design. This module is the Reading record's build.

The private nightly build is not touched. `generate.generate()` is the same
function it was, this is a second entry point beside it, and
`test_the_private_build_is_unchanged_by_a_public_build` hashes the whole
private site before and after a public build to hold that.

Three properties, in the order they matter.

**Redaction happens at the data layer, not in the template.** `reduce_corpus`
hands the cover eight columns and `reduce_weeks` hands it four keys. Titles,
URLs, authors, summaries and the per-week article roster never reach a
renderer, so no template can leak what it never saw - the press's own wording.
The reduction is lossless for this page, and that is asserted rather than
assumed: the cover drawn from the reduced view is character-for-character the
cover drawn from the whole index.

**The scan fails the build closed.** After every file is written and before the
directory is published, walk all of them for every article title in the index
and every source URL path, longest first, at twelve characters or more. A
finding raises; nothing is published.

**One request.** The stylesheet is inlined, so the page fetches nothing at view
time. That is the press's first clause and it is the reason there is no
`style.css` beside the page.

## The six deviations from the private cover

Everything else about the two documents is identical, which
`test_the_public_cover_is_the_private_cover_but_for_the_six_deviations`
asserts as an equality rather than as spot checks.

1. The page row is reduced to its single current entry, COVER, which already
   renders as a marked span. The private row's other five items are pages this
   build does not write, so on the record they would be five dead links in the
   chrome of the only page there is. The `<nav>` stays, because all three
   sibling surfaces carry that bar and mark the current page in it.
2. Any anchor reaching a private page becomes a span with the same classes.
   Today the cover's body has exactly one, the footer's link to `/weeks/`, and
   deviation 5 replaces that line outright - so this pass is a no-op on the
   cover as it stands. It runs anyway, and is asserted, because the failure it
   guards against is a future column growing a link and shipping it live.
3. The sibling bar and the wordmark point at the public tier. READING stays the
   marked span it is privately: the record IS `/reading/`, and a link to the
   page you are on is the same category of dead as a 404.
4. Canonical is the record's public URL.
5. The footer says the full record is private, in the same span, with no link.

6. The masthead kicker reads `data.adamthede.com/reading`. The private byline
   names the host the page is served from, and that host is behind Cloudflare
   Access, so on a public page it points at a login wall.
"""
import argparse
import datetime as dt
import hashlib
import html as html_mod
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd

import cover
import htmlkit

# Where the record is published, and the only prefix an anchor on it may carry.
PUBLIC_BASE = "https://data.adamthede.com/"
RECORD_URL = PUBLIC_BASE + "reading/"

# The sibling row as it reads from inside the public tier: the other two
# records, at their paths under the index, not the Access-walled hosts.
PUBLIC_SIBLINGS = [
    ("Reading", None),
    ("Books", PUBLIC_BASE + "books/"),
    ("Viewing", PUBLIC_BASE + "viewing/"),
]

PUBLIC_PAGE_ROW = [("cover", "Cover", "")]

# The masthead kicker. The private cover bylines itself with the host it is
# served from, and that host sits behind Cloudflare Access: naming it on a
# public page points readers at a login wall. The record bylines itself with
# where the record is.
PUBLIC_DOMAIN = "data.adamthede.com/reading"

FOOTER_NOTE = ('<span class="label">The full record is kept for the '
               'family.</span>')

# The columns the cover's arithmetic reads, and nothing else. Every one of them
# is load-bearing and `test_the_reduced_view_still_draws_the_cover_the_full_one_draws`
# is what says so: drop one and the public cover prints a different number.
#
#   source            era_split, the "how it was saved" list
#   word_count        the words hero, the cumulative step, the busiest year
#   reading_time_min  the reading-time secondary
#   canonical_entries the whole Subjects column
#   date_read         the shared quarter axis and every strip
#   year              the era bands, the seam, the AI window
#   domain            the Sources column - a bare host, never a path
#   proxy_dated       the "how to read the dates" note
#
# `title`, `url`, `author`, `summary`, `content_snippet`, `file_path`,
# `topics`, `people`, `orgs`, `locations`, `concepts`, `sentiment`, `emotion`,
# `grade_level` and the two service ids are all dropped here.
ROW_COLUMNS = ("source", "word_count", "reading_time_min", "canonical_entries",
               "date_read", "year", "domain", "proxy_dated")

# What the cover reads off a week record. `articles` - the roster of every
# title and URL that week - is the feed the plan of record says to drop.
WEEK_KEYS = ("week", "article_count", "total_words", "top_topics")

# The facet pages a private build writes, which is what the cover's "deep
# dives" secondary counts. Named here rather than guessed, and tied to a real
# build by test_the_deep_dive_count_matches_what_the_private_build_writes.
ALWAYS_FACETS = ("orgs", "locations", "people", "trends", "articles")
GATED_FACETS = ("concepts", "together")

# The floor on a leak needle. A four-character title matches inside an
# unrelated word on any page and turns the scan into a permanent red; twelve
# characters is specific enough to name an article and short enough that almost
# every real title clears it.
MIN_NEEDLE = 12

RECORD_FILES = ("index.html", "thumb.jpg", "PROVENANCE.md")

THUMB_WIDTH, THUMB_HEIGHT = 1200, 750


# ---------------------------------------------------------------------------
# the reduced view
# ---------------------------------------------------------------------------

def reduce_corpus(corpus_data):
    """The index as the public cover is allowed to see it.

    A copy carrying ROW_COLUMNS and nothing else, wrapped in the same Corpus
    the private renderers take, so the cover code is one code path rather than
    two. `years` is carried across because the axis and the year rollup count
    are computed from it.
    """
    import corpus as corpus_mod

    missing = [c for c in ROW_COLUMNS if c not in corpus_data.rows.columns]
    if missing:
        raise SystemExit(
            "Refusing to build a public shape from an index missing "
            f"{', '.join(missing)}: the cover would print zeros for it.")
    return corpus_mod.Corpus(rows=corpus_data.rows[list(ROW_COLUMNS)].copy(),
                             years=list(corpus_data.years))


def reduce_weeks(weeks):
    """The week records as the public cover is allowed to see them."""
    return [{k: m[k] for k in WEEK_KEYS if k in m} for m in weeks]


def deep_dive_counts(corpus_data):
    """What a private build of this index would write, counted.

    The public record carries no year rollups and no facet pages, but the
    cover's "deep dives" secondary still reports them: it is a fact about the
    archive's exploration, not a link, and the footer beneath it already says
    the full record is private. The figure therefore has to be the one the
    private site really prints, so it is computed by the same rule rather than
    typed.
    """
    import generate as gen

    years = len(corpus_data.years)
    facets = len(ALWAYS_FACETS)
    if gen.concepts_gate(corpus_data)[0]:
        facets += len(GATED_FACETS)
    return {"years": years, "facets": facets, "total": years + facets}


# ---------------------------------------------------------------------------
# the page
# ---------------------------------------------------------------------------

ANCHOR = re.compile(r'<a\b([^>]*)>(.*?)</a>', re.S)
HREF = re.compile(r'\s*href="([^"]*)"')


def neutralize_links(html):
    """Every anchor that does not resolve on the public tier becomes a span.

    Same classes, same text, no href - so the page lays out identically and
    nothing on it 404s. An anchor whose target is an absolute URL under
    PUBLIC_BASE is left alone; everything else, relative or absolute, is not
    reachable from a record that is one file on another host.
    """
    def sub(m):
        attrs, inner = m.group(1), m.group(2)
        href = HREF.search(attrs)
        if href and href.group(1).startswith(PUBLIC_BASE):
            return m.group(0)
        return f"<span{HREF.sub('', attrs)}>{inner}</span>"

    return ANCHOR.sub(sub, html)


def style_tag():
    """The site's own rules, inlined.

    The base stylesheet and the cover's block, verbatim from the modules the
    private site renders under - not a copy - so the record cannot drift away
    from the design it is a shape of. The four deep-dive blocks are left out
    because this record has no deep-dive pages; the fidelity pair in
    docs/qa/2026-09-10-public-shape/ is what proves none of their rules reached
    the cover.
    """
    import generate as gen

    return "<style>" + gen.STYLE + cover.COVER_STYLE + "</style>"


def inline_stylesheet(html):
    """Swap the stylesheet link for the rules themselves.

    Raises if the link is not there exactly once: a public page that silently
    lost its styles would still pass every text assertion in the suite.
    """
    link = '<link rel="stylesheet" href="style.css">'
    if html.count(link) != 1:
        raise SystemExit(
            f"Expected exactly one stylesheet link to inline, found "
            f"{html.count(link)}.")
    return html.replace(link, style_tag())


class chrome:
    """Install the public tier's bar for the duration of one render.

    The page row, the sibling row and the wordmark's destination are module
    state on htmlkit for the length of a build, the way generate() already
    installs the narrowed page row. Restoring them here is what keeps a public
    build from leaking its chrome into the next private one - which is the
    failure `test_the_private_build_is_unchanged_by_a_public_build` exists to
    catch, and the reason this is a context manager rather than three calls.
    """

    def __enter__(self):
        self._row = htmlkit.set_page_row(PUBLIC_PAGE_ROW)
        self._siblings = htmlkit.set_sibling_sites(PUBLIC_SIBLINGS)
        self._home = htmlkit.set_wordmark_home(PUBLIC_BASE)
        return self

    def __exit__(self, *exc):
        htmlkit.set_page_row(self._row)
        htmlkit.set_sibling_sites(self._siblings)
        htmlkit.set_wordmark_home(self._home)
        return False


def render_public_cover(corpus_data, weeks, deep_dives, today=None,
                        site_title=None, domain=None):
    """The record, as one self-contained document.

    Takes the same arguments the private cover takes and reduces them here, so
    a caller cannot forget to: the full corpus goes in, the reduced view is
    what reaches `cover.render_cover`, and the two are proved to draw the same
    page.
    """
    import generate as gen

    with chrome():
        html = cover.render_cover(
            reduce_corpus(corpus_data), reduce_weeks(weeks),
            deep_dives=deep_dives, today=today,
            site_title=site_title or gen.SITE_TITLE,
            domain=domain or PUBLIC_DOMAIN,
            canonical=RECORD_URL, footer_note=FOOTER_NOTE)
    return inline_stylesheet(neutralize_links(html))


# ---------------------------------------------------------------------------
# the leak scan
# ---------------------------------------------------------------------------

def private_strings(index_path):
    """(titles, url paths) from the index, longest first, deduplicated.

    Longest first is not cosmetic. When two titles share a prefix the longer is
    the more specific finding, and a scan that reports the shorter one names
    the wrong article in the failure.

    A URL's PATH, not the whole URL and not the host: the Sources column ranks
    publications by host on purpose, and a host is a fact about the archive
    while a path identifies one article in it.
    """
    df = pd.read_parquet(index_path, columns=["title", "url"])

    titles = set()
    for t in df["title"]:
        s = str(t).strip() if t is not None and not _isna(t) else ""
        if len(s) >= MIN_NEEDLE:
            titles.add(s)

    paths = set()
    for u in df["url"]:
        if u is None or _isna(u):
            continue
        path = urlparse(str(u)).path.rstrip("/")
        if len(path) >= MIN_NEEDLE:
            paths.add(path)

    longest = (lambda s: sorted(s, key=lambda x: (-len(x), x)))
    return longest(titles), longest(paths)


def _isna(v):
    try:
        return bool(pd.isna(v))
    except (TypeError, ValueError):
        return False


def leak_scan(root, titles, paths, min_len=MIN_NEEDLE):
    """Every private string found in every file under `root`.

    Case-insensitive, and run over both the file's bytes and its
    HTML-unescaped text: a title carrying an ampersand or a curly quote would
    reach the page escaped, and a scan that only read the raw bytes would miss
    exactly the titles most likely to be printed verbatim.

    Returns a list of {"file", "needle", "kind"}, empty when clean. Callers
    decide what a finding costs; `build` treats it as fatal.
    """
    needles = ([(t, "title") for t in titles if len(t) >= min_len]
               + [(p, "url path") for p in paths if len(p) >= min_len])
    found = []
    for path in sorted(Path(root).rglob("*")):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # thumb.jpg and anything else that is not text
        haystack = (text + "\n" + html_mod.unescape(text)).casefold()
        for needle, kind in needles:
            if needle.casefold() in haystack:
                found.append({"file": path.relative_to(root).as_posix(),
                              "needle": needle, "kind": kind})
    return found


# ---------------------------------------------------------------------------
# the thumbnail
# ---------------------------------------------------------------------------

CAPTURE_JS = Path(__file__).resolve().parent / "capture.js"


def capture_thumbnail(page_path, out_path,
                      width=THUMB_WIDTH, height=THUMB_HEIGHT):
    """A real Playwright capture of the top of the published page.

    `newContext({viewport})`, not `--window-size`: headless Chrome clamps the
    window on this Mac, and a capture taken that way is a 500px layout cropped
    to the frame rather than a render at the frame's width.

    It is taken from the file that will be committed, never from the private
    build. The leak scan reads text; a thumbnail is pixels, so a capture of the
    private cover would carry the private chrome into the index as an image
    with every text test still green. The provenance note records the hash of
    the file this was taken from, and the index repo checks it.
    """
    node_path = os.environ.get("PLAYWRIGHT_NODE_PATH")
    env = dict(os.environ)
    if node_path:
        env["NODE_PATH"] = node_path
    try:
        subprocess.run(
            ["node", str(CAPTURE_JS), str(page_path), str(out_path),
             str(width), str(height)],
            check=True, capture_output=True, text=True, env=env)
    except FileNotFoundError:
        raise SystemExit(
            "node is not on PATH; the thumbnail needs it. Re-run with "
            "--no-thumbnail to build the page alone.")
    except subprocess.CalledProcessError as err:
        raise SystemExit(
            f"the thumbnail capture failed:\n{err.stderr.strip()}\n"
            "Install Playwright (npm i playwright && npx playwright install "
            "chromium) and point PLAYWRIGHT_NODE_PATH at its node_modules, or "
            "re-run with --no-thumbnail.")


# ---------------------------------------------------------------------------
# the provenance note
# ---------------------------------------------------------------------------

def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def generator_commit():
    try:
        return subprocess.run(
            ["git", "-C", str(Path(__file__).resolve().parent),
             "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return "unknown"


PROVENANCE = """# records/reading

Published at `/reading/`.

## index.html

Built, not vendored. `site/generate.py --public --out <dir>` in
`adamthede/project-instapaper-archive` produces this directory, and re-running
it on the same index and the same day reproduces this file byte for byte.

| | |
|---|---|
| source | the private cover at `reading.adamthede.com/`, same generator |
| generator commit | `{commit}` |
| built | {built} |
| index.html sha256 | `{page_hash}` |
| articles behind it | {articles} |
| weeks behind it | {weeks} |

### Redaction

At the data layer, before any template runs. The cover is handed eight columns
of the index - source, word count, reading time, canonical entries, read date,
year, host and the proxy-dated flag - and four keys per week record - the ISO
week, its article count, its word count and its subjects. Article titles, URLs,
authors, summaries and the whole per-week article roster never reach a
renderer, so no template can leak what it never saw.

Hosts are kept and paths are not. The Sources column ranks publications by
host, which is a fact about the archive; a path identifies one article in it.

The build then walks every emitted file for every article title in the index
and every source URL path, longest first, at twelve characters or more, and
refuses to publish on a single finding. {scanned}

## The deviations

The public page is the private cover with six differences and no others,
asserted as an equality in
`tests/test_public_shape.py::test_the_public_cover_is_the_private_cover_but_for_the_five_deviations`.

| # | where | what changed | why |
|---|---|---|---|
| 1 | the sticky bar's page row | reduced to its single current entry, COVER, which already renders as a marked span | the other five name pages this build does not write; they would be five dead links in the chrome of the only page there is |
| 2 | any anchor to a private page | becomes a `<span>` with the same classes | nothing on the record may 404. Today the cover's body has exactly one such anchor and deviation 5 replaces that line outright, so this pass changes nothing as the page stands; it runs against a future column growing a link |
| 3 | the sibling bar and the wordmark | BOOKS and VIEWING resolve to `{base}books/` and `{base}viewing/`, the wordmark to `{base}` | the private hosts are behind Cloudflare Access, so from a public page those links are a login wall. READING stays the marked span it is privately: the record is `/reading/` |
| 4 | canonical | `{record}` | the private canonical would tell every crawler this page duplicates one it cannot reach |
| 5 | the footer's first span | "The full record is kept for the family." | it offered the weekly syntheses at `/weeks/`, which the public tier does not publish |
| 6 | the masthead kicker | `{domain}` | the private byline names the Access-walled host the page is served from; the record bylines itself with where the record is |

Also not a deviation: the "deep dives" secondary still counts the private
site's year rollups and facet pages. It is a number, not a link, and the footer
directly beneath it says the full record is private.

### Self-contained

One directory, {filecount} files. The stylesheet is inlined into the document,
so the page makes exactly one network request - the document - and nothing else
is fetched at view time. No `<script>`, no `<link>`, no `<img>`, no `@import`,
no `url()`, no webfont. Proved at runtime in
`docs/qa/2026-09-10-public-shape/`.

## thumb.jpg

{thumb}
"""

THUMB_PRESENT = """A {w}x{h} capture of this record's own top of page, taken from the
`index.html` committed beside it in a real Playwright viewport
(`newContext({{viewport}})`, not `--window-size`, which headless Chrome clamps
on this Mac).

| | |
|---|---|
| captured from | `records/reading/index.html` |
| captured-from sha256 | `{page_hash}` |
| thumb.jpg sha256 | `{thumb_hash}` |

The capture-source hash is what closes the hole the leak scan cannot see: that
scan reads text, and a thumbnail regenerated from the private cover would carry
the private chrome into the index as pixels and build green."""

THUMB_ABSENT = """Not built in this run (`--no-thumbnail`). The published record carries a
{w}x{h} capture of its own top of page; see `docs/qa/2026-09-10-public-shape/`."""


def provenance(out, page_hash, articles, weeks, scanned, thumb_path=None,
               today=None):
    today = today or dt.date.today()
    if thumb_path and Path(thumb_path).exists():
        thumb = THUMB_PRESENT.format(w=THUMB_WIDTH, h=THUMB_HEIGHT,
                                     page_hash=page_hash,
                                     thumb_hash=sha256(thumb_path))
    else:
        thumb = THUMB_ABSENT.format(w=THUMB_WIDTH, h=THUMB_HEIGHT)
    return PROVENANCE.format(
        commit=generator_commit(), built=today.isoformat(),
        page_hash=page_hash, articles=f"{articles:,}", weeks=f"{weeks:,}",
        scanned=scanned, base=PUBLIC_BASE, record=RECORD_URL,
        domain=PUBLIC_DOMAIN,
        filecount=len(list(Path(out).iterdir())) + 1, thumb=thumb)


# ---------------------------------------------------------------------------
# the build
# ---------------------------------------------------------------------------

def _guard(out):
    """Refuse an --out that this build did not make.

    The same posture generate() takes with its marker file, without a marker:
    the record is three known names, so a directory holding anything else is
    somebody's else's, and clearing it would be the destructive kind of
    surprise. An empty or absent directory is fine, and so is a previous
    record.
    """
    if not out.exists():
        return
    strays = [p.name for p in out.iterdir() if p.name not in RECORD_FILES]
    if strays:
        raise SystemExit(
            f"Refusing to overwrite {out}: it holds {', '.join(sorted(strays))}, "
            f"which no public build wrote. Pick another --out.")


def build(synthesis_dir, out_dir, index_path, today=None, thumbnail=True):
    """Render, capture, scan, and only then publish.

    The order is the whole design. Everything is written into a sibling
    directory; the leak scan runs over that directory whole; a single finding
    raises before anything is moved into place. A build that leaks does not
    publish a page and then complain about it.
    """
    import corpus as corpus_mod
    import generate as gen

    weeks = gen.load_weeks(synthesis_dir)
    weeks = [m for m in weeks if str(m["week"]) >= gen.SITE_EPOCH_WEEK]
    if not weeks:
        raise SystemExit(f"No synthesis files found in {synthesis_dir}")

    corpus_data = corpus_mod.load_corpus(index_path)
    if not cover.can_render(corpus_data):
        raise SystemExit(
            "This index cannot draw a cover (no taxonomy join), and the cover "
            "is the whole record. Refusing to publish a hollow one.")

    out = Path(out_dir).resolve()
    _guard(out)
    tmp = out.parent / (out.name + ".building")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    try:
        page_path = tmp / "index.html"
        page_path.write_text(
            render_public_cover(corpus_data, weeks,
                                deep_dives=deep_dive_counts(corpus_data),
                                today=today),
            encoding="utf-8")

        thumb_path = tmp / "thumb.jpg"
        if thumbnail:
            capture_thumbnail(page_path, thumb_path)

        titles, paths = private_strings(index_path)
        scanned = (f"On this build that is {len(titles):,} titles and "
                   f"{len(paths):,} paths.")
        (tmp / "PROVENANCE.md").write_text(
            provenance(tmp, sha256(page_path), len(corpus_data.rows), len(weeks),
                       scanned, thumb_path if thumbnail else None, today),
            encoding="utf-8")

        found = leak_scan(tmp, titles, paths)
        if found:
            lines = "\n".join(f"  {f['file']}: {f['kind']} {f['needle']!r}"
                              for f in found[:20])
            raise SystemExit(
                f"Refusing to publish: the leak scan found {len(found)} "
                f"private string(s) in the public build.\n{lines}")

        if out.exists():
            shutil.rmtree(out)
        tmp.replace(out)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)

    print(f"Public shape: {len(corpus_data.rows):,} articles, {len(weeks):,} "
          f"weeks -> {out}/ ({', '.join(sorted(p.name for p in out.iterdir()))})")
    print(f"Leak scan clean over {len(titles):,} titles and {len(paths):,} "
          f"URL paths.")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    vault = os.environ.get("INSTAPAPER_VAULT_PATH")
    ap.add_argument("--synthesis-dir",
                    default=str(Path(vault) / "synthesis") if vault else None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--index",
                    default=str(Path(__file__).resolve().parents[1] / "data"
                                / "archive_index.parquet"))
    ap.add_argument("--no-thumbnail", action="store_true",
                    help="skip the Playwright capture (needs node)")
    args = ap.parse_args(argv)
    if not args.synthesis_dir:
        sys.exit("Set INSTAPAPER_VAULT_PATH or pass --synthesis-dir.")
    build(args.synthesis_dir, args.out, index_path=args.index,
          thumbnail=not args.no_thumbnail)
