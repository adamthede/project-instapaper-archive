"""The unread corpus fetch stage: the listing, the queue, and the resolve chain.

Every test here names, in its docstring, the mutation it catches.

Nothing in this file touches the network or Adam's credentials. The Instapaper
client and the HTTP session are both fakes that serve canned payloads, and the
sleeper is a recorder, so the rate-limit behaviour is asserted rather than
waited through.

The plan these tests hold to the wall is
`docs/plans-to-do/2026-09-15-unread-corpus-what-i-meant-to-read.md`, whose
measured inventory is `docs/2026-09-15-unread-corpus-resolve-rate.md`.
"""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from unread import queue as q  # noqa: E402
from unread import resolve as rs  # noqa: E402
from unread import instapaper as ip  # noqa: E402


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, status_code=200, text="", url=None, headers=None):
        self.status_code = status_code
        self.text = text
        self.content = text.encode("utf-8")
        self.url = url or "https://example.com/"
        self.headers = headers or {"Content-Type": "text/html"}


class FakeHTTP:
    """Stands in for a requests.Session for the direct and Wayback legs.

    Records every URL it was asked for, which is how the tests assert that the
    Wayback leg never touches the availability or CDX endpoints.
    """

    def __init__(self, gets=None, heads=None):
        self._gets = gets or {}
        self._heads = heads or {}
        self.get_calls = []
        self.head_calls = []

    def get(self, url, **kwargs):
        self.get_calls.append(url)
        if url in self._gets:
            return self._gets[url]
        for prefix, resp in self._gets.items():
            if url.startswith(prefix):
                return resp
        return FakeResponse(404, "", url=url)

    def head(self, url, **kwargs):
        self.head_calls.append(url)
        if url in self._heads:
            return self._heads[url]
        for prefix, resp in self._heads.items():
            if url.startswith(prefix):
                return resp
        return FakeResponse(404, "", url=url)

    @property
    def all_calls(self):
        return self.get_calls + self.head_calls


class FakeInstapaper:
    """Stands in for InstapaperClient. `texts` maps bookmark id -> (status, html)."""

    def __init__(self, texts=None, folders=None, listings=None):
        self._texts = texts or {}
        self._folders = folders or {}
        self._listings = listings or {}
        self.text_calls = []
        self.listing_calls = []

    def folders(self):
        return dict(self._folders)

    def bookmarks(self, folder_id, limit=500):
        self.listing_calls.append(folder_id)
        return [dict(b) for b in self._listings.get(folder_id, [])]

    def get_text(self, bookmark_id):
        self.text_calls.append(bookmark_id)
        return self._texts.get(bookmark_id, (400, ""))


class Sleeper:
    def __init__(self):
        self.calls = []

    def __call__(self, seconds):
        self.calls.append(seconds)


def body(words=400, lead="The article opens here."):
    """An HTML body with a known word count, above the floor by default."""
    filler = " ".join(f"word{i}" for i in range(words))
    return f"<html><body><article><p>{lead} {filler}</p></article></body></html>"


def bookmark(bid=1, url="https://example.com/a-long-article-path",
             title="An article title", time_saved=1500000000, progress=0.0,
             starred="0", **extra):
    item = {
        "type": "bookmark",
        "bookmark_id": bid,
        "url": url,
        "title": title,
        "time": time_saved,
        "progress": progress,
        "progress_timestamp": 1375386433,
        "starred": starred,
    }
    item.update(extra)
    return item


def row_for(url="https://example.com/a-long-article-path", bid=1, saved_year=2016,
            **extra):
    row = q.new_row(bookmark(bid=bid, url=url), folder=None)
    row["saved_year"] = saved_year
    row.update(extra)
    return row


class RecordingSession:
    """An OAuth session that records the keyword arguments of every call."""

    def __init__(self, payload=None, text="<p>body</p>", status=200):
        self.calls = []
        self._payload = payload if payload is not None else []
        self._text = text
        self._status = status

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        resp = FakeResponse(self._status, self._text, url=url)
        resp.json = lambda: self._payload
        resp.raise_for_status = lambda: None
        return resp


# ---------------------------------------------------------------------------
# the API client
# ---------------------------------------------------------------------------

def test_every_instapaper_request_carries_a_timeout():
    """Mutation: a request with no timeout, which hangs the whole run forever.

    This is not hypothetical. The first full pass over the 492 stalled at item
    288 and sat there for fifteen minutes at 0% CPU with a socket in SYN_SENT,
    because these three calls went out with no timeout at all while the direct
    and Wayback legs had one. `requests` blocks indefinitely by default, and a
    resumable pipeline that hangs is not resumable, it is stopped.
    """
    session = RecordingSession(payload={"bookmarks": []})
    client = ip.InstapaperClient(session, sleeper=Sleeper())
    client.bookmarks("unread")
    client.get_text(7)
    session._payload = []
    client.folders()

    assert session.calls
    for url, kwargs in session.calls:
        assert kwargs.get("timeout"), f"{url} went out with no timeout"


def test_a_hung_instapaper_call_is_an_outcome_not_a_stall():
    """Mutation: letting a connection failure propagate and kill the pass.

    A timeout on one bookmark is one item's worth of data, and the chain has
    two more legs to try. Killing the run would cost the other 491.
    """
    class Hanging:
        def post(self, url, **kwargs):
            raise OSError("timed out")

    client = ip.InstapaperClient(Hanging(), sleeper=Sleeper())
    assert client.get_text(7) == (None, "")


# ---------------------------------------------------------------------------
# the listing: what is in the pool
# ---------------------------------------------------------------------------

def test_folder_items_that_were_opened_are_out_of_the_pool():
    """Mutation: taking all 105 folder items instead of the 97 never opened.

    The plan's pool is 492 = 395 unread + 97 folder items at progress exactly
    0.0. The eight partially-read folder items are a different category of
    intention and the inventory counts them out.
    """
    unread = [bookmark(bid=i, url=f"https://example.com/unread-{i}") for i in range(3)]
    folder = [
        bookmark(bid=100, url="https://example.com/never-opened", progress=0.0),
        bookmark(bid=101, url="https://example.com/started", progress=0.18),
        bookmark(bid=102, url="https://example.com/finished", progress=1.0),
    ]
    rows = ip.listing_to_rows(unread, {"Steve Jobs": folder})
    urls = {r["url"] for r in rows}
    assert "https://example.com/never-opened" in urls
    assert "https://example.com/started" not in urls
    assert "https://example.com/finished" not in urls
    assert len(rows) == 4


def test_a_partially_read_unread_item_stays_in_the_pool():
    """Mutation: applying the folder progress filter to the unread listing too.

    119 of the 395 live unread items were opened and abandoned. They are the
    whole point of the `abandonment` field; filtering them out would delete the
    most interesting band in the corpus.
    """
    unread = [bookmark(bid=1, url="https://example.com/abandoned", progress=0.66)]
    rows = ip.listing_to_rows(unread, {})
    assert len(rows) == 1
    assert rows[0]["read_progress"] == pytest.approx(0.66)


def test_a_folder_item_carries_its_folder_name():
    """Mutation: dropping the folder name, which is the whole `saved_context`.

    The CSV export cannot see folders at all - the header declares a Folder
    column the data rows do not carry - so the API is the only place this
    exists.
    """
    rows = ip.listing_to_rows([], {"CS183 - Startup": [bookmark(bid=5, progress=0.0)]})
    assert rows[0]["folder"] == "CS183 - Startup"


def test_progress_timestamp_is_never_read_as_having_been_opened():
    """Mutation: inferring `opened` from a populated progress_timestamp.

    73 of the 105 folder items share the timestamp 1375386433, a platform-side
    backfill from 1 August 2013 that appears 283 times in the CSV export. Read
    progress is the only signal.
    """
    item = bookmark(bid=9, progress=0.0, progress_timestamp=1375386433)
    rows = ip.listing_to_rows([], {"PaperTrail": [item]})
    assert len(rows) == 1
    assert rows[0]["abandonment"] == "never_opened"


def test_the_same_url_saved_twice_is_one_row():
    """Mutation: keying the queue on bookmark id instead of URL hash.

    The plan keys on the URL hash because the same URL can be saved twice and
    because the Matter era already dedupes on URL across sources.
    """
    same = "https://example.com/the-same-article-path"
    rows = ip.listing_to_rows(
        [bookmark(bid=1, url=same), bookmark(bid=2, url=same)], {})
    assert len(rows) == 1


def test_saved_year_comes_from_the_save_timestamp():
    """Mutation: dating the Wayback probe from today rather than the save.

    The snapshot the project wants is the article as it was when the intention
    was formed, so the saved year has to survive the listing.
    """
    # 1 July 2016 UTC
    rows = ip.listing_to_rows([bookmark(bid=1, time_saved=1467331200)], {})
    assert rows[0]["saved_year"] == 2016
    assert rows[0]["saved_date"].startswith("2016-")


# ---------------------------------------------------------------------------
# the queue file
# ---------------------------------------------------------------------------

def test_the_queue_writes_after_every_item_not_at_the_end(tmp_path):
    """Mutation: buffering the queue and flushing at the end of the run.

    The plan's resume guarantee is that killing the script mid-run costs one
    item. That only holds if `mark` reaches disk before the next item starts.
    """
    path = tmp_path / "unread_queue.jsonl"
    queue = q.Queue(path)
    queue.upsert(ip.listing_to_rows(
        [bookmark(bid=1, url="https://example.com/one-long-path"),
         bookmark(bid=2, url="https://example.com/two-long-path")], {}))
    queue.mark(queue.rows()[0]["url_sha256"], outcome="resolved",
               resolve_path="instapaper")

    on_disk = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    assert len(on_disk) == 2
    assert [r for r in on_disk if r["outcome"] == "resolved"]


def test_a_resolved_row_is_skipped_on_the_next_run(tmp_path):
    """Mutation: dropping the resolved check, re-fetching the whole corpus nightly.

    Idempotent by URL hash is the plan's word. A second pass over a resolved
    row must make no HTTP call at all, which is what makes the nightly slot a
    refresh rather than a rebuild.
    """
    path = tmp_path / "unread_queue.jsonl"
    queue = q.Queue(path)
    queue.upsert(ip.listing_to_rows([bookmark(bid=1)], {}))
    key = queue.rows()[0]["url_sha256"]
    queue.mark(key, outcome="resolved", resolve_path="instapaper")

    reloaded = q.Queue(path)
    assert reloaded.pending() == []
    assert len(reloaded.rows()) == 1


def test_a_metadata_only_row_is_not_retried_either(tmp_path):
    """Mutation: treating metadata_only as pending and re-running the dead links.

    An item that resolves nowhere is a finding, not a failure to retry. The
    dead fraction is one of the six analysis questions.
    """
    path = tmp_path / "unread_queue.jsonl"
    queue = q.Queue(path)
    queue.upsert(ip.listing_to_rows([bookmark(bid=1)], {}))
    queue.mark(queue.rows()[0]["url_sha256"], outcome="metadata_only",
               resolve_path="metadata")
    assert q.Queue(path).pending() == []


def test_upsert_does_not_clobber_the_outcome_of_a_row_it_already_has(tmp_path):
    """Mutation: rewriting the queue from the listing on every run.

    The listing is re-read nightly. If upsert replaced rows wholesale, every
    resolved body would be re-fetched the next night and the resume guarantee
    would be a comment rather than a behaviour.
    """
    path = tmp_path / "unread_queue.jsonl"
    queue = q.Queue(path)
    queue.upsert(ip.listing_to_rows([bookmark(bid=1)], {}))
    key = queue.rows()[0]["url_sha256"]
    queue.mark(key, outcome="resolved", resolve_path="wayback")

    q.Queue(path).upsert(ip.listing_to_rows([bookmark(bid=1)], {}))
    again = q.Queue(path)
    assert again.rows()[0]["outcome"] == "resolved"
    assert again.rows()[0]["resolve_path"] == "wayback"
    assert again.pending() == []


def test_a_new_save_joins_an_existing_queue(tmp_path):
    """Mutation: an upsert that only ever writes on a first run.

    The nightly job is a refresh: it fetches only new saves. A queue that
    cannot take a new row would freeze the corpus at its first build.
    """
    path = tmp_path / "unread_queue.jsonl"
    queue = q.Queue(path)
    queue.upsert(ip.listing_to_rows([bookmark(bid=1, url="https://a.example/one-long-path")], {}))
    queue.mark(queue.rows()[0]["url_sha256"], outcome="resolved", resolve_path="instapaper")

    later = q.Queue(path)
    added = later.upsert(ip.listing_to_rows([
        bookmark(bid=1, url="https://a.example/one-long-path"),
        bookmark(bid=2, url="https://b.example/two-long-path")], {}))
    assert added == 1
    assert len(q.Queue(path).pending()) == 1


def test_the_queue_never_carries_a_body(tmp_path):
    """Mutation: inlining article text into the queue rows.

    Bodies are files on disk and the queue is provenance. Inlining would make
    the queue unreadable, and every rewrite would rewrite megabytes.
    """
    path = tmp_path / "unread_queue.jsonl"
    queue = q.Queue(path)
    queue.upsert(ip.listing_to_rows([bookmark(bid=1)], {}))
    row = queue.rows()[0]
    assert "text" not in row and "html" not in row and "body" not in row
    assert "body_path" in row


# ---------------------------------------------------------------------------
# the resolve chain
# ---------------------------------------------------------------------------

def test_instapaper_resolving_stops_the_chain():
    """Mutation: running all three legs and picking a winner afterwards.

    Instapaper's stored text is the article as it was when it was saved, and
    the measured overlap says it is worth four times what a direct fetch adds.
    It is first, and a hit ends the item.
    """
    http = FakeHTTP()
    client = FakeInstapaper(texts={1: (200, body())})
    res = rs.resolve_one(row_for(bid=1), instapaper=client, http=http, sleeper=Sleeper())
    assert res.path == "instapaper"
    assert http.all_calls == []


def test_a_short_instapaper_body_falls_through_to_the_direct_fetch():
    """Mutation: removing the 150-word floor.

    A 200 that carries a stub is not a resolution. Success in the measured
    sample is an HTTP outcome plus a word floor, and the floor is what keeps
    consent pages and empty shells out of the corpus.
    """
    http = FakeHTTP(gets={"https://example.com/a-long-article-path":
                          FakeResponse(200, body(400),
                                       url="https://example.com/a-long-article-path")})
    client = FakeInstapaper(texts={1: (200, body(20))})
    res = rs.resolve_one(row_for(bid=1), instapaper=client, http=http, sleeper=Sleeper())
    assert res.path == "direct"


def test_instapaper_400_is_recorded_as_an_attempt_with_its_status():
    """Mutation: a failure log that says `failed` without the path or the status.

    20 of the 100 sampled items returned Instapaper 400, and the shape of those
    failures - social posts and shorteners - is a finding. Failures are data
    here, not noise.
    """
    http = FakeHTTP(gets={"https://example.com": FakeResponse(200, body())})
    client = FakeInstapaper(texts={1: (400, "")})
    res = rs.resolve_one(row_for(bid=1), instapaper=client, http=http, sleeper=Sleeper())
    first = res.attempts[0]
    assert first.path == "instapaper" and first.status == 400 and first.ok is False


# A minimal document with a PDF's magic bytes and structure. Synthetic, but it
# reproduces exactly what the live run hit: one saved link served a PDF, and
# 34,457 "words" of object tables and stream offsets went to the model, which
# summarised them as "a collection of PDF metadata ... does not contain a
# coherent narrative" and then marked CONTENT_VALID: YES.
PDF_BYTES = ("%PDF-1.4\n%\xe2\xe3\xcf\xd3\n528 0 obj\n<< /Linearized 1 >>\nendobj\n"
             + "xref\n528 0 obj << /Type /Page >> endobj stream endstream " * 200)

# A parked domain, as frontdeveloper.pl served one on the live run: 820 words of
# real Polish prose about buying the domain, which the model happily summarised
# as "a listing for the sale of the domain" at HIGH confidence.
PARKING_PAGE = ("<html><body><h1>Oferta sprzedazy domeny</h1><p>"
                + "Ta domena jest na sprzedaz w serwisie aftermarket. " * 80
                + "</p></body></html>")


def test_a_pdf_served_to_the_direct_leg_is_not_article_text():
    """Mutation: accepting any 200 with enough "words" in it.

    Found on the live corpus. A PDF's object tables and stream offsets split
    into 34,457 whitespace-separated tokens, which clears a 150-word floor by
    two orders of magnitude. The enrichment prompt's CONTENT_VALID guard did
    not catch it either - the model described the problem in its own summary
    and still returned YES.

    Bytes are not words. This is decided on the document, not on the model.
    """
    url = "https://example.com/a-long-article-path"
    http = FakeHTTP(gets={url: FakeResponse(200, PDF_BYTES, url=url)})
    res = rs.resolve_one(row_for(bid=1), instapaper=FakeInstapaper(texts={1: (400, "")}),
                         http=http, sleeper=Sleeper())
    assert res.path == "metadata"
    direct = [a for a in res.attempts if a.path == "direct"][0]
    assert "not html" in direct.note.lower() or "pdf" in direct.note.lower()


def test_a_non_html_content_type_is_refused_before_it_is_counted():
    """Mutation: sniffing the body but trusting the header, or the reverse.

    A server that labels a PDF correctly should be refused on the label alone,
    without the pipeline having to recognise every binary format by its magic
    bytes.
    """
    url = "https://example.com/a-long-article-path"
    resp = FakeResponse(200, body(400), url=url,
                        headers={"Content-Type": "application/pdf"})
    res = rs.resolve_one(row_for(bid=1), instapaper=FakeInstapaper(texts={1: (400, "")}),
                         http=FakeHTTP(gets={url: resp}), sleeper=Sleeper())
    assert res.path == "metadata"


def test_a_direct_fetch_that_lands_on_the_site_front_door_is_not_the_article():
    """Mutation: accepting whatever the host redirects you to.

    Measured on the live run: a dead article on a deep path redirects to the
    site's homepage, an index page, or a domain-for-sale notice - each with
    hundreds of words of perfectly coherent prose that the model summarises
    without complaint. The article is gone; the fetch succeeded at reaching
    something else.
    """
    saved = "https://example.com/2016/11/the-article-that-is-gone"
    http = FakeHTTP(gets={saved: FakeResponse(200, PARKING_PAGE,
                                              url="https://example.com/")})
    res = rs.resolve_one(row_for(bid=1, url=saved),
                         instapaper=FakeInstapaper(texts={1: (400, "")}),
                         http=http, sleeper=Sleeper())
    assert res.path == "metadata"
    direct = [a for a in res.attempts if a.path == "direct"][0]
    assert "front door" in direct.note.lower() or "root" in direct.note.lower()


def test_a_direct_fetch_that_stays_on_the_article_is_accepted():
    """Mutation: a front-door guard so blunt it refuses every redirect.

    Publishers move articles and add tracking segments constantly. Landing on a
    different deep path is normal; landing on the root is the signal.
    """
    saved = "https://example.com/2016/11/the-article"
    landed = "https://example.com/2016/11/the-article-slug-v2?utm_source=x"
    http = FakeHTTP(gets={saved: FakeResponse(200, body(400), url=landed)})
    res = rs.resolve_one(row_for(bid=1, url=saved),
                         instapaper=FakeInstapaper(texts={1: (400, "")}),
                         http=http, sleeper=Sleeper())
    assert res.path == "direct"


def test_an_item_saved_as_a_site_root_may_still_resolve_at_the_root():
    """Mutation: a guard that fires on depth it should never have expected.

    Some saves ARE a homepage. Refusing those would invent a dead link.
    """
    saved = "https://example.com/"
    http = FakeHTTP(gets={saved: FakeResponse(200, body(400), url="https://example.com/")})
    res = rs.resolve_one(row_for(bid=1, url=saved),
                         instapaper=FakeInstapaper(texts={1: (400, "")}),
                         http=http, sleeper=Sleeper())
    assert res.path == "direct"


def test_the_x_com_long_form_articles_are_not_discarded():
    """Mutation: a host guard on x.com or twitter.com.

    The plan predicted that x.com bodies would be JavaScript payloads rather
    than articles, and round 2 of review asked for them to be classed
    metadata-only on that basis. Checked against the live corpus first: all 15
    are real prose, 358 to 8,123 words - fourteen are X's long-form Articles
    and one is a substantive thread post. Discarding them would have thrown
    away two Ray Dalio essays and an Anthropic announcement.

    The guards here are about the document, never about the host.
    """
    tweet_article = ("<html><body><article><h1>The Big Thing</h1><p>"
                     + "I will start off by wishing you well in these times. " * 60
                     + "</p></article></body></html>")
    saved = "https://x.com/RayDalio/status/2041531182018367773"
    http = FakeHTTP(gets={saved: FakeResponse(200, tweet_article, url=saved)})
    res = rs.resolve_one(row_for(bid=1, url=saved),
                         instapaper=FakeInstapaper(texts={1: (400, "")}),
                         http=http, sleeper=Sleeper())
    assert res.path == "direct"


def test_the_direct_leg_sends_a_browser_user_agent():
    """Mutation: fetching with python-requests' default agent.

    The measured 51% direct rate was taken with a desktop Chrome agent. The
    default agent changes the result on exactly the domains that make up a
    third of the queue.
    """
    captured = {}

    class Recorder(FakeHTTP):
        def get(self, url, **kwargs):
            captured.update(kwargs.get("headers") or {})
            return super().get(url, **kwargs)

    http = Recorder(gets={"https://example.com": FakeResponse(200, body(),
                                                             url="https://example.com")})
    rs.resolve_one(row_for(bid=1), instapaper=FakeInstapaper(texts={1: (400, "")}),
                   http=http, sleeper=Sleeper())
    assert "Mozilla" in captured.get("User-Agent", "")


def test_wayback_probes_the_dated_snapshot_path_and_nothing_else():
    """Mutation: switching to the availability API or the CDX endpoint.

    The availability API returned 429 to all 100 probes at 5 concurrent and CDX
    timed out on 46 of the first 71. A HEAD against the dated snapshot path
    answers the same question in under two seconds. This cost an hour to learn.
    """
    snap = "https://web.archive.org/web/2016/https://example.com/a-long-article-path"
    http = FakeHTTP(
        heads={snap: FakeResponse(
            200, "", url="https://web.archive.org/web/20160701123456/https://example.com/a-long-article-path")},
        gets={"https://web.archive.org/web/": FakeResponse(
            200, body(),
            url="https://web.archive.org/web/20160701123456/https://example.com/a-long-article-path")})
    res = rs.resolve_one(row_for(bid=1, saved_year=2016),
                         instapaper=FakeInstapaper(texts={1: (400, "")}),
                         http=http, sleeper=Sleeper())
    assert res.path == "wayback"
    assert http.head_calls == [snap]
    joined = " ".join(http.all_calls)
    assert "archive.org/wayback/available" not in joined
    assert "/cdx/" not in joined


def test_the_wayback_probe_uses_the_saved_year_not_the_current_one():
    """Mutation: probing `/web/2/` or today's year.

    The point is the article as it was when the intention was formed. A 2026
    snapshot of a 2014 save is a different document and sometimes a different
    site entirely.
    """
    assert rs.wayback_snapshot_url("https://example.com/x", 2014) == \
        "https://web.archive.org/web/2014/https://example.com/x"


def test_a_wayback_redirect_that_lands_off_the_archive_is_not_a_snapshot():
    """Mutation: counting any HTTP 200 from the probe as a hit.

    Wayback redirects a missing snapshot to the live site often enough that a
    bare status check silently inflates the survival rate, which is the most
    interesting number in the analysis.
    """
    snap = rs.wayback_snapshot_url("https://example.com/a-long-article-path", 2016)
    live = "https://example.com/live-page"
    # The live page serves a perfectly good article. The only thing wrong with
    # it is that it is not a snapshot, so accepting it would resolve the item
    # against today's web while recording it as archived.
    http = FakeHTTP(heads={snap: FakeResponse(200, "", url=live)},
                    gets={live: FakeResponse(200, body(400), url=live)})
    res = rs.resolve_one(row_for(bid=1, saved_year=2016),
                         instapaper=FakeInstapaper(texts={1: (400, "")}),
                         http=http, sleeper=Sleeper())
    assert res.path == "metadata"
    assert http.get_calls == ["https://example.com/a-long-article-path"]


def test_a_wayback_snapshot_of_a_soft_404_is_rejected_by_the_word_floor():
    """Mutation: trusting the snapshot because the probe said one exists.

    The CDX cross-check found a genuine 200-status snapshot for only 32 of 37,
    so roughly one Wayback hit in seven is a snapshot of something other than
    the article. Every Wayback body clears the same floor as any other path.
    """
    snap = rs.wayback_snapshot_url("https://example.com/a-long-article-path", 2016)
    landed = "https://web.archive.org/web/20160701/https://example.com/a-long-article-path"
    http = FakeHTTP(
        heads={snap: FakeResponse(200, "", url=landed)},
        gets={"https://web.archive.org/web/": FakeResponse(
            200, "<html><body><p>Page not found.</p></body></html>", url=landed)})
    res = rs.resolve_one(row_for(bid=1, saved_year=2016),
                         instapaper=FakeInstapaper(texts={1: (400, "")}),
                         http=http, sleeper=Sleeper())
    assert res.path == "metadata"


def test_everything_failing_still_produces_a_row_carrying_the_intention():
    """Mutation: dropping unresolvable items from the corpus.

    An item that resolves nowhere still has a title, a domain, a saved date and
    a word count, and it is still an intention. 2% of the sample is dead
    everywhere and that 2% is a finding.
    """
    res = rs.resolve_one(row_for(bid=1), instapaper=FakeInstapaper(texts={1: (400, "")}),
                         http=FakeHTTP(), sleeper=Sleeper())
    assert res.path == "metadata"
    assert res.ok is False
    assert [a.path for a in res.attempts] == ["instapaper", "direct", "wayback"]


def test_the_instapaper_leg_waits_between_calls():
    """Mutation: dropping the rate limit and hammering the API.

    The plan's budget is 0.8s between Instapaper calls; the constraint on this
    pipeline is rate limits, not compute.
    """
    sleeper = Sleeper()
    rs.resolve_one(row_for(bid=1), instapaper=FakeInstapaper(texts={1: (200, body())}),
                   http=FakeHTTP(), sleeper=sleeper)
    assert sleeper.calls and min(sleeper.calls) >= 0.8


def test_the_wayback_leg_waits_too():
    """Mutation: running the Wayback leg at full speed or concurrently.

    Wayback rate limits hard. A probe that times out produces a false negative
    indistinguishable from a dead link, which would silently inflate the dead
    fraction.
    """
    sleeper = Sleeper()
    snap = rs.wayback_snapshot_url("https://example.com/a-long-article-path", 2016)
    http = FakeHTTP(heads={snap: FakeResponse(404, "", url=snap)})
    rs.resolve_one(row_for(bid=1, saved_year=2016),
                   instapaper=FakeInstapaper(texts={1: (400, "")}),
                   http=http, sleeper=sleeper)
    assert len(sleeper.calls) >= 2


def test_extracted_text_drops_script_and_style():
    """Mutation: counting a JavaScript payload as article words.

    The four x.com items Instapaper refused all cleared a naive word floor with
    1,481 to 7,420 words apiece, which on a tweet page is the script payload.

    `noscript` is here on purpose. BeautifulSoup's `get_text` already skips
    script and style contents on its own, so those two assertions hold whether
    or not this module removes the tags - but noscript it happily returns, and
    a noscript block is exactly where a paywalled page keeps the sentence
    telling you to enable JavaScript.
    """
    html = ("<html><head><style>.a{color:red}</style></head><body>"
            "<script>var x = 'word '.repeat(500);</script>"
            "<noscript>Please enable JavaScript to read this article.</noscript>"
            "<p>Only these six words are real.</p></body></html>")
    text = rs.extract_text(html)
    assert "color:red" not in text
    assert "var x" not in text
    assert "enable JavaScript" not in text
    assert "Only these six words are real." in text


# ---------------------------------------------------------------------------
# the run: resume, bodies, failure log
# ---------------------------------------------------------------------------

def test_a_run_killed_midway_resumes_where_it_stopped(tmp_path):
    """Mutation: a run that starts over, or one that skips what it had not done.

    The plan's promise is that killing the script mid-run costs one item. This
    kills it on the third and asserts both halves: the first two are not
    re-fetched, the third and fourth are.
    """
    path = tmp_path / "unread_queue.jsonl"
    bodies = tmp_path / "bodies"
    items = [bookmark(bid=i, url=f"https://example.com/article-number-{i}") for i in range(1, 5)]
    queue = q.Queue(path)
    queue.upsert(ip.listing_to_rows(items, {}))

    client = FakeInstapaper(texts={i: (200, body()) for i in range(1, 5)})

    class Boom(Exception):
        pass

    def fail_on_third(row, **kwargs):
        if len(client.text_calls) >= 2:
            raise Boom()
        return rs.resolve_one(row, **kwargs)

    with pytest.raises(Boom):
        rs.run(queue, instapaper=client, http=FakeHTTP(), bodies_dir=bodies,
               sleeper=Sleeper(), resolver=fail_on_third)

    done_first_pass = len(client.text_calls)
    assert 0 < done_first_pass < 4

    resumed = q.Queue(path)
    rs.run(resumed, instapaper=client, http=FakeHTTP(), bodies_dir=bodies,
           sleeper=Sleeper())
    assert len(client.text_calls) == 4
    assert q.Queue(path).pending() == []


def test_one_stalled_item_cannot_stop_the_pass(tmp_path):
    """Mutation: trusting the per-request timeout to bound an item's total time.

    It does not, and this was measured twice on the live run. Both stalls
    looked identical: the process alive at 0% CPU with a single socket in
    SYN_SENT, the queue frozen, and nothing in the log. `requests` applies its
    timeout per socket operation rather than to the whole call, so a connection
    that never completes and a server that trickles bytes both sit outside it.

    A wall-clock deadline per item is the only thing that makes the plan's
    resume guarantee true. A stalled item becomes one metadata-only row with
    the stall recorded, and the pass goes on.
    """
    import time as real_time

    path = tmp_path / "unread_queue.jsonl"
    queue = q.Queue(path)
    queue.upsert(ip.listing_to_rows([
        bookmark(bid=1, url="https://example.com/the-one-that-hangs"),
        bookmark(bid=2, url="https://example.com/the-one-after-it")], {}))

    def hangs_on_the_first(row, **kwargs):
        if row["bookmark_id"] == 1:
            real_time.sleep(5)
        return rs.resolve_one(row, **kwargs)

    counts = rs.run(queue, instapaper=FakeInstapaper(texts={1: (200, body()), 2: (200, body())}),
                    http=FakeHTTP(), bodies_dir=tmp_path / "bodies", sleeper=Sleeper(),
                    resolver=hangs_on_the_first, item_deadline=1)

    rows = {r["url"]: r for r in q.Queue(path).rows()}
    stalled = rows["https://example.com/the-one-that-hangs"]
    # A stall is transient and stays retryable. Recording it as metadata_only
    # would permanently enter a live URL in the dead-link finding on the
    # strength of one bad connection - the exact hazard the plan names for the
    # Wayback leg, and adversarial review caught it here.
    assert stalled["outcome"] == "pending"
    assert any("deadline" in (a.get("note") or "") for a in stalled["attempts"])
    assert rows["https://example.com/the-one-after-it"]["outcome"] == "resolved"
    assert counts["stalled"] == 1
    assert len(q.Queue(path).pending()) == 1


def test_a_resolved_body_is_written_and_the_row_points_at_it(tmp_path):
    """Mutation: resolving without persisting, so the enrich stage re-fetches.

    The fetch stage is the only one that needs the network. If the body is not
    on disk, every enrichment re-run pays the rate limit again.
    """
    path = tmp_path / "unread_queue.jsonl"
    bodies = tmp_path / "bodies"
    queue = q.Queue(path)
    queue.upsert(ip.listing_to_rows([bookmark(bid=1)], {}))
    rs.run(queue, instapaper=FakeInstapaper(texts={1: (200, body())}),
           http=FakeHTTP(), bodies_dir=bodies, sleeper=Sleeper())

    row = q.Queue(path).rows()[0]
    assert row["outcome"] == "resolved"
    stored = bodies / row["body_path"]
    assert stored.exists()
    assert "word1" in stored.read_text()


def test_the_body_filename_is_the_url_hash_not_the_title(tmp_path):
    """Mutation: naming body files after titles.

    Titles are the private strings this corpus is scanned for. A filename
    carrying one is a leak the text scan would have to catch on a path, and the
    hash is already the queue's key.
    """
    path = tmp_path / "unread_queue.jsonl"
    bodies = tmp_path / "bodies"
    queue = q.Queue(path)
    queue.upsert(ip.listing_to_rows(
        [bookmark(bid=1, title="A Very Distinctive Article Title")], {}))
    key = queue.rows()[0]["url_sha256"]
    rs.run(queue, instapaper=FakeInstapaper(texts={1: (200, body())}),
           http=FakeHTTP(), bodies_dir=bodies, sleeper=Sleeper())

    names = [p.name for p in bodies.rglob("*") if p.is_file()]
    assert names == [f"{key}.txt"]
    assert not any("Distinctive" in n for n in names)
    assert q.Queue(path).rows()[0]["body_path"] == f"{key[:2]}/{key}.txt"


def test_every_failed_leg_reaches_the_failure_log(tmp_path):
    """Mutation: a failure log that records only the final outcome.

    The plan wants the path attempted and the HTTP status, because the
    breakdown of how each path fails is what the resolve-rate document is made
    of.
    """
    path = tmp_path / "unread_queue.jsonl"
    log = tmp_path / "unread_fetch_failures.log"
    queue = q.Queue(path)
    queue.upsert(ip.listing_to_rows([bookmark(bid=1)], {}))
    rs.run(queue, instapaper=FakeInstapaper(texts={1: (400, "")}), http=FakeHTTP(),
           bodies_dir=tmp_path / "bodies", sleeper=Sleeper(), failure_log=log)

    text = log.read_text()
    assert "instapaper" in text and "400" in text
    assert "direct" in text and "wayback" in text


def test_the_run_records_which_leg_resolved_each_item(tmp_path):
    """Mutation: dropping `resolve_path`.

    Every other field has to be read through it: a summary built from a 2016
    Wayback snapshot and one built from today's live page are different
    evidence about the same intention.
    """
    path = tmp_path / "unread_queue.jsonl"
    queue = q.Queue(path)
    queue.upsert(ip.listing_to_rows([
        bookmark(bid=1, url="https://example.com/from-instapaper-path"),
        bookmark(bid=2, url="https://example.com/from-direct-get-path")], {}))
    http = FakeHTTP(gets={"https://example.com/from-direct-get-path":
                          FakeResponse(200, body(), url="https://example.com/from-direct-get-path")})
    rs.run(queue, instapaper=FakeInstapaper(texts={1: (200, body()), 2: (400, "")}),
           http=http, bodies_dir=tmp_path / "bodies", sleeper=Sleeper())

    paths = {r["url"]: r["resolve_path"] for r in q.Queue(path).rows()}
    assert paths["https://example.com/from-instapaper-path"] == "instapaper"
    assert paths["https://example.com/from-direct-get-path"] == "direct"


def test_the_run_reports_counts_per_leg(tmp_path):
    """Mutation: a run that resolves but cannot say how.

    The dispatch asks for the resolve counts, and the plan's expected yield is
    stated per leg. A run that only reports a total cannot be checked against
    the measured rates.
    """
    path = tmp_path / "unread_queue.jsonl"
    queue = q.Queue(path)
    queue.upsert(ip.listing_to_rows([
        bookmark(bid=1, url="https://example.com/one-long-path-here"),
        bookmark(bid=2, url="https://example.com/two-long-path-here")], {}))
    counts = rs.run(queue, instapaper=FakeInstapaper(texts={1: (200, body()), 2: (400, "")}),
                    http=FakeHTTP(), bodies_dir=tmp_path / "bodies", sleeper=Sleeper())
    assert counts["instapaper"] == 1
    assert counts["metadata"] == 1
    assert counts["total"] == 2


# ---------------------------------------------------------------------------
# re-running one leg after tightening it
# ---------------------------------------------------------------------------

def test_a_recheck_reopens_only_the_leg_named(tmp_path):
    """Mutation: a recheck that reopens the whole corpus, or nothing at all.

    Tightening the direct leg means its earlier verdicts have to be revisited -
    but re-running Instapaper's 351 would pay that rate limit again for nothing,
    and re-running none would leave the old verdicts standing under the new
    rule. This is the operation that makes a guard change auditable.
    """
    path = tmp_path / "unread_queue.jsonl"
    queue = q.Queue(path)
    queue.upsert(ip.listing_to_rows([
        bookmark(bid=1, url="https://a.example/from-instapaper-path"),
        bookmark(bid=2, url="https://b.example/from-the-direct-leg"),
        bookmark(bid=3, url="https://c.example/from-nowhere-at-all")], {}))
    for key, leg, outcome in zip(
            [r["url_sha256"] for r in queue.rows()],
            ["instapaper", "direct", "metadata"],
            ["resolved", "resolved", "metadata_only"]):
        queue.mark(key, outcome=outcome, resolve_path=leg)

    reopened = rs.reopen_leg(queue, "direct")
    assert reopened == 1

    again = q.Queue(path)
    pending = again.pending()
    assert [r["url"] for r in pending] == ["https://b.example/from-the-direct-leg"]
    assert {r["resolve_path"] for r in again.rows() if r["outcome"] != "pending"} == \
        {"instapaper", "metadata"}


def test_a_reopened_row_forgets_its_old_verdict(tmp_path):
    """Mutation: reopening the outcome but leaving the stale path and body.

    A row carrying `resolve_path: direct` and a body file while marked pending
    would report the old verdict in every aggregate until the re-run happened
    to finish, and would leave an orphan body if it never did.
    """
    path = tmp_path / "unread_queue.jsonl"
    queue = q.Queue(path)
    queue.upsert(ip.listing_to_rows([bookmark(bid=1)], {}))
    key = queue.rows()[0]["url_sha256"]
    queue.mark(key, outcome="resolved", resolve_path="direct",
               body_path="aa/bb.txt", body_words=900)

    rs.reopen_leg(queue, "direct")
    row = q.Queue(path).rows()[0]
    assert row["outcome"] == "pending"
    assert row["resolve_path"] is None
    assert row["body_path"] is None
