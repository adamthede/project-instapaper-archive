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


def n(x):
    return f"{int(x):,}"


def safe_url(url):
    """The escaped URL if it is http(s), else "".

    e() escapes quotes but not a javascript: scheme, and these are
    third-party scraped URLs. Callers render a non-link when this is empty.
    """
    u = str(url or "")
    return e(u) if u.lower().startswith(SAFE_SCHEMES) else ""


def nav(depth=0):
    """The sticky sibling-site bar that sits above every page's own header.

    Three surfaces are live - reading, books and viewing - and this bar is what
    says they are one family rather than three orphans. The markup and the
    rules behind it are lifted from the books surface (its own htmlkit.nav()
    and the `.stickynav`/`.snin`/`.wordmark`/`.sitelinks` block in its
    styles.py) rather than retyped, so the three bars are identical by
    construction and not by anyone's eye.

    The one intentional difference: this bar carries no `.pagelinks` row. Books
    and viewing each have four sibling pages to move between inside their own
    surface; the reading site navigates from its index and its year anchors, so
    a second row of page links here would be chrome with nothing to point at.

    `depth` rewrites the wordmark's home link for a page that many directories
    below the root, exactly as page() already does for the stylesheet.
    """
    links = []
    for label, href in SIBLING_SITES:
        if href is None:
            links.append(f'<span class="on">{e(label)}</span>')
        else:
            links.append(f'<a href="{safe_url(href)}">{e(label)}</a>')
    home = "../" * depth or "./"
    return f"""<div class="stickynav">
  <div class="snin">
    <a class="wordmark" href="{home}">adamthede<i>reading</i></a>
    <span class="sitelinks">{''.join(links)}</span>
  </div>
</div>"""


def page(title, body, depth=0, body_extra=""):
    css = "../" * depth + "style.css"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<link rel="stylesheet" href="{css}">
</head>
<body>
{nav(depth)}
<div class="page">
{body}
</div>
{body_extra}</body>
</html>
"""
