"""URL normalization, used only for duplicate detection.

The archive spans two eras and about 17,600 rows, and the same article can
legitimately appear in both: saved to Instapaper years ago, saved again to
Matter last week. Comparing raw URL strings would miss most of those, because
the same article arrives as http/https, with and without `www.`, with and
without a tracking query string.

Two rules shape the aggressiveness here:

  * A URL that normalizes to nothing NEVER matches anything, including another
    empty URL. Roughly 10,560 rows in the current index have an empty `url`
    (the legacy PDF/Word import had no URL to record). Treating those as equal
    would collapse them into one entry and drop thousands of real articles.

  * Only unambiguous tracking parameters are stripped. `utm_*` and `fbclid`
    carry no meaning. `ref` and `source` sometimes do -- on some sites they
    select actual content -- so they stay. A false merge silently discards an
    article, which is a worse outcome than a duplicate we can see and fix.
"""

import re
from urllib.parse import parse_qsl, urlsplit, urlunsplit, urlencode

# A scheme-looking prefix, e.g. "mailto:" or "tel:". Used to reject non-web
# schemes rather than mangling them into an https URL.
_SCHEME_PREFIX = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")

# Prefixes where every parameter is analytics noise.
_TRACKING_PREFIXES = ("utm_", "at_", "pk_", "hsa_", "_hs")

# Exact parameter names that are unambiguously click/campaign identifiers.
_TRACKING_KEYS = frozenset({
    "fbclid", "gclid", "gbraid", "wbraid", "dclid", "msclkid", "yclid",
    "twclid", "igshid", "mc_cid", "mc_eid", "vero_id", "vero_conv",
    "ref_src", "ref_url", "s_kwcid", "mkt_tok", "trk", "trkCampaign",
    "sc_campaign", "sc_channel", "sc_content", "sc_medium",
    "campaign_id", "cmpid", "spm", "share_id", "__twitter_impression",
})

_DEFAULT_PORTS = {"http": "80", "https": "443"}


def _is_tracking_param(key: str) -> bool:
    lowered = key.lower()
    if lowered in _TRACKING_KEYS:
        return True
    return any(lowered.startswith(prefix) for prefix in _TRACKING_PREFIXES)


def normalize_url(url) -> str | None:
    """Return a canonical form for comparison, or None when there is nothing to compare.

    None means "never treat this as a duplicate of anything" -- it is not an
    error, and callers must not put it in a lookup set.
    """
    if url is None:
        return None
    if not isinstance(url, str):
        return None

    raw = url.strip()
    if not raw:
        return None
    # Pandas hands back the string "nan" for missing values often enough to be
    # worth catching explicitly.
    if raw.lower() in {"nan", "none", "null", "url_missing"}:
        return None

    # A bare "example.com/x" is a URL in every practical sense; give it a scheme
    # so urlsplit puts the host in netloc rather than path. But only when there
    # is no scheme already: prepending to "mailto:adam@example.com" would parse
    # "mailto:adam" as userinfo and quietly yield "https://example.com".
    if "://" not in raw:
        if raw.startswith("//"):
            raw = "https:" + raw
        elif _SCHEME_PREFIX.match(raw):
            return None  # mailto:, tel:, data:, obsidian: -- not an article
        else:
            raw = "https://" + raw

    # The whole parse sits inside the try: urlsplit is lazy, so `.hostname` and
    # especially `.port` raise ValueError on a malformed authority
    # ("https://e.com:notaport/x", "https://e.com:99999/x") rather than at split
    # time. Letting that escape would take down a whole nightly run from
    # build_url_index, or pin the watermark forever from the per-item path.
    try:
        parts = urlsplit(raw)

        scheme = (parts.scheme or "https").lower()
        if scheme not in ("http", "https"):
            # mailto:, file:, obsidian:// and friends are not articles we dedupe on.
            return None

        host = (parts.hostname or "").lower()
        if not host:
            return None
        if host.startswith("www."):
            host = host[4:]

        netloc = host
        if parts.port and str(parts.port) != _DEFAULT_PORTS.get(scheme):
            netloc = f"{host}:{parts.port}"
    except ValueError:
        return None

    path = parts.path or ""
    # Trailing slashes are not meaningful for article identity; "/a/b/" == "/a/b".
    while len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    if path == "/":
        path = ""

    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not _is_tracking_param(k)]
    # Sorted so that ?a=1&b=2 and ?b=2&a=1 are the same article.
    query = urlencode(sorted(kept))

    # Fragments are client-side; #section-3 is the same article.
    return urlunsplit(("https", netloc, path, query, ""))


def normalize_all(urls) -> set[str]:
    """Normalize an iterable of URLs into a lookup set, dropping the un-comparable."""
    out: set[str] = set()
    for url in urls:
        normalized = normalize_url(url)
        if normalized:
            out.add(normalized)
    return out
