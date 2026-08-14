"""URL normalization -- the basis of cross-era duplicate detection."""

import pytest

from matter.normalize import normalize_all, normalize_url


@pytest.mark.parametrize("variant", [
    "https://example.com/article",
    "http://example.com/article",
    "https://www.example.com/article",
    "https://EXAMPLE.com/Article".replace("Article", "article"),
    "https://example.com/article/",
    "https://example.com/article#section-3",
    "https://example.com/article?utm_source=twitter&utm_medium=social",
    "https://example.com/article?fbclid=IwAR123",
    "https://example.com:443/article",
    "example.com/article",
])
def test_equivalent_forms_collapse_to_one(variant):
    assert normalize_url(variant) == normalize_url("https://example.com/article")


def test_meaningful_query_params_are_kept():
    assert normalize_url("https://example.com/a?id=42") != normalize_url("https://example.com/a")


def test_query_param_order_does_not_matter():
    assert normalize_url("https://e.com/a?b=2&a=1") == normalize_url("https://e.com/a?a=1&b=2")


def test_ambiguous_params_are_kept_because_they_sometimes_select_content():
    """`ref` and `source` are not stripped: a false merge silently drops an article."""
    assert normalize_url("https://e.com/a?ref=hn") != normalize_url("https://e.com/a")
    assert normalize_url("https://e.com/a?source=x") != normalize_url("https://e.com/a")


def test_different_paths_stay_different():
    assert normalize_url("https://e.com/a") != normalize_url("https://e.com/b")


def test_different_hosts_stay_different():
    assert normalize_url("https://a.com/x") != normalize_url("https://b.com/x")


def test_subdomains_other_than_www_are_significant():
    assert normalize_url("https://blog.e.com/x") != normalize_url("https://e.com/x")


@pytest.mark.parametrize("empty", ["", "   ", None, float("nan").__str__(), "nan", "URL_MISSING"])
def test_uncomparable_urls_return_none(empty):
    """The legacy import left ~10,560 rows with no URL; they must never match each other."""
    assert normalize_url(empty) is None


def test_non_string_input_returns_none():
    assert normalize_url(12345) is None
    assert normalize_url(["https://e.com"]) is None


def test_non_http_schemes_are_not_deduped():
    assert normalize_url("mailto:adam@example.com") is None
    assert normalize_url("file:///Users/adam/doc.pdf") is None


def test_normalize_all_drops_the_uncomparable_rather_than_grouping_them():
    urls = ["https://e.com/a", "", None, "   ", "https://www.e.com/a/", "https://e.com/b"]
    assert normalize_all(urls) == {normalize_url("https://e.com/a"), normalize_url("https://e.com/b")}


def test_root_url_with_and_without_slash_match():
    assert normalize_url("https://e.com") == normalize_url("https://e.com/")


@pytest.mark.parametrize("value", [
    "http://", "https://[", "://x", "h ttp://e.com",
    # urlsplit is lazy: these raise on .port / .hostname, not at split time.
    # Escaping from build_url_index would kill a whole nightly run.
    "https://example.com:notaport/x",
    "https://example.com:99999/x",
    "https://example.com:-1/x",
    "http://[::1/x",
])
def test_malformed_input_does_not_raise(value):
    assert normalize_url(value) is None or isinstance(normalize_url(value), str)
