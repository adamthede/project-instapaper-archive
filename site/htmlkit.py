"""Shared HTML primitives for the reading site.

Extracted from generate.py so the deep-dive pages (year rollups, orgs facet,
article detail) can share the same escaping discipline and page chrome
without a circular import. The stylesheet stays in generate.py; deep dives
append to it rather than forking it.
"""
import html

e = html.escape

SAFE_SCHEMES = ("http://", "https://")


def n(x):
    return f"{int(x):,}"


def safe_url(url):
    """The escaped URL if it is http(s), else "".

    e() escapes quotes but not a javascript: scheme, and these are
    third-party scraped URLs. Callers render a non-link when this is empty.
    """
    u = str(url or "")
    return e(u) if u.lower().startswith(SAFE_SCHEMES) else ""


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
<div class="page">
{body}
</div>
{body_extra}</body>
</html>
"""
