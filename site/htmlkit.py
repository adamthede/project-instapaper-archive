"""Shared HTML primitives for the reading site.

Extracted from generate.py so the deep-dive pages (year rollups, orgs facet,
article detail) can share the same escaping discipline and page chrome
without a circular import. The stylesheet stays in generate.py; deep dives
append to it rather than forking it.
"""
import html

e = html.escape

SAFE_SCHEMES = ("http://", "https://")

# The three quantified-self surfaces, in the order all three sites show them.
# Reading is this site, so it is marked rather than linked.
SIBLING_SITES = [
    ("Reading", None),
    ("Books", "https://books.adamthede.com/"),
    ("Viewing", "https://viewing.adamthede.com/"),
]

# The page-links row, in the order it is shown. Each entry is
# (key, label, root-relative target). `key` is what a page passes as `here` to
# mark itself; the target is what the anchor points at.
#
# Two labels are deliberately not the directory name. /orgs/ is titled
# "Organizations" on its own page, but in a six-item row next to "Subjects" it
# reads as SOURCES, which is what a reader of this site is looking for; same
# for /concepts/, which is SUBJECTS here. Adam named the six in this order on
# 2026-09-08.
PAGE_ROW = [
    ("cover", "Cover", ""),
    ("weeks", "Weeks", "weeks/"),
    ("years", "Years", "years/"),
    ("orgs", "Sources", "orgs/"),
    ("concepts", "Subjects", "concepts/"),
    ("articles", "Articles", "articles/"),
]

# The row this build renders. generate() narrows it to the pages actually
# written, the same discipline the index's facet nav already follows: a row
# item pointing at a page the deep-dive leg declined to build would be a 404 in
# the chrome of every page on the site. Renderers called directly - by a test,
# or by anything that is not a full build - get the full row.
_page_row = list(PAGE_ROW)


def set_page_row(rows):
    """Install this build's row and return the previous one, for restoring.

    Module state rather than an argument threaded through nine renderers in
    four modules. The build is one pass in one process, and generate() restores
    the previous value in a finally block so one build cannot leak its row into
    the next.
    """
    global _page_row
    previous = _page_row
    _page_row = list(rows)
    return previous


def n(x):
    return f"{int(x):,}"


def safe_url(url):
    """The escaped URL if it is http(s), else "".

    e() escapes quotes but not a javascript: scheme, and these are
    third-party scraped URLs. Callers render a non-link when this is empty.
    """
    u = str(url or "")
    return e(u) if u.lower().startswith(SAFE_SCHEMES) else ""


def weeks_index_href(depth):
    """Where the weeks index lives, from a page `depth` directories down.

    Read off the installed page row rather than hardcoded, because the index
    has two addresses: `/weeks/` when there is a cover to take the root, and
    `/` when there is not. Every "All weeks" link and every kicker carrying the
    site title has to land on the index in BOTH shapes.

    This exists because they did not. When the cover took the root on
    2026-09-08, 827 week pages and 22 year pages kept a kicker reading "The
    Week in Reading" and a nav link reading "All weeks", both still pointing at
    `../../` - which had been the index and had become the cover. An
    adversarial review found 1,712 links whose text named one page and whose
    target was another, with a green suite and no 404 anywhere, because the
    page they landed on does exist. One helper, so the answer is computed once.
    """
    for key, _, target in _page_row:
        if key == "weeks":
            return ("../" * depth + target) or "./"
    return "../" * depth or "./"


def nav(depth=0, here=None, under=None):
    """The sticky bar that sits above every page's own header.

    Two rows, answering two different questions. The sibling row says which of
    the three quantified-self surfaces you are on - reading, books, viewing -
    and the page row says where you are inside this one. The markup and the
    rules behind both are lifted from the books surface (its own htmlkit.nav()
    and the `.stickynav`/`.snin`/`.wordmark`/`.pagelinks`/`.sitelinks` block in
    its styles.py) rather than retyped, so the three bars are identical by
    construction and not by anyone's eye.

    The page row arrived 2026-09-08 with the cover. Until then this site was
    one page plus its year anchors, and a row would have been chrome with
    nothing to point at; a cover at the root and an index at /weeks/ is six
    destinations, which is what the books and viewing bars already carry.

    `here` is the key of the row entry whose OWN page this is. It renders as a
    span: you are already there, so there is nowhere to go.

    `under` is the key of the row entry this page sits BENEATH - a week page is
    under Weeks, a year rollup under Years, /together/ under Subjects. It
    renders as a marked ANCHOR: the row still says where you are, and the
    section's index is still one click away.

    That distinction is not decoration. Marking a child page's section with a
    span left 827 week pages and 22 year pages with no route to their own index
    at all, which is how the row's first version shipped past a green suite.
    Adam asked for the current page marked on every page kind; a marked link
    satisfies that and stays navigable.

    A page that is in neither position passes neither - /people/, /locations/
    and /trends/ are reachable from the weeks index's "Beyond the week" nav,
    and the row is the six Adam named, not an index of everything.

    `depth` rewrites the wordmark's home link and every row target for a page
    that many directories below the root, exactly as page() already does for
    the stylesheet.
    """
    up = "../" * depth
    links = []
    for label, href in SIBLING_SITES:
        if href is None:
            links.append(f'<span class="on">{e(label)}</span>')
        else:
            links.append(f'<a href="{safe_url(href)}">{e(label)}</a>')
    pages = []
    for key, label, target in _page_row:
        href = e(up + target) or "./"
        if key == here:
            pages.append(f'<span class="here">{e(label)}</span>')
        elif key == under:
            pages.append(f'<a class="here" href="{href}">{e(label)}</a>')
        else:
            pages.append(f'<a href="{href}">{e(label)}</a>')
    home = up or "./"
    return f"""<div class="stickynav">
  <div class="snin">
    <a class="wordmark" href="{home}">adamthede<i>reading</i></a>
    <nav class="pagelinks" aria-label="the pages of this site">{''.join(pages)}</nav>
    <span class="sitelinks">{''.join(links)}</span>
  </div>
</div>"""


def page(title, body, depth=0, body_extra="", here=None, under=None,
         canonical="", wide=False):
    """A complete document.

    `canonical` is the absolute URL the page is reachable at. It is set on the
    two pages whose address moved on 2026-09-08 - the cover took the root and
    the weeks index moved to /weeks/ - so a link to either still says which
    page it means.

    `wide` swaps the 720px reading column for the cover's 1180px measure. The
    cover is four columns on a shared axis; the rest of the site is one column
    of prose and tables.
    """
    css = "../" * depth + "style.css"
    link = (f'\n<link rel="canonical" href="{e(canonical)}">'
            if canonical else "")
    cls = "page wide" if wide else "page"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>{link}
<link rel="stylesheet" href="{css}">
</head>
<body>
{nav(depth, here, under)}
<div class="{cls}">
{body}
</div>
{body_extra}</body>
</html>
"""
