"""The fetch chain: Instapaper stored text, then a plain GET, then Wayback.

The order is the finding, not a preference. On a random sample of 100 drawn
with `random.seed(20260915)`, Instapaper resolved 79, a direct fetch 51 and
Wayback 90, but the overlap is lopsided: 32 items only Instapaper could reach
against 4 only a direct fetch could, and Wayback rescued 15 that neither of the
first two could touch. Any of the three reached 98; two items are dead
everywhere.

Instapaper is first because its stored text is the article as it was when it
was saved, which for a 2016 page is a materially different document from
whatever the URL serves today.

Three things here were expensive to learn and are the reusable part.

**The word floor is not optional.** Success is an HTTP outcome plus 150 words
of extracted text. A 200 carrying a consent page or a JavaScript shell clears
every status check and no floor. Even so the floor is a filter and not a
judgement: the enrichment prompt's own CONTENT_VALID guard is the second gate,
and the direct-GET rate should be read as an upper bound.

**Which Wayback endpoint you pick decides whether the leg is feasible.** The
availability API returned HTTP 429 to all 100 probes at 5 concurrent. The CDX
search endpoint timed out on 46 of the first 71 at 4 concurrent and took 60 to
90 seconds per URL even serially. A HEAD against the dated snapshot path
answers the same question in 0.6 to 2.2 seconds with no throttling observed.

**A Wayback 200 is not a snapshot.** The probe has to land on a
`web.archive.org/web/` address; Wayback redirects a missing snapshot to the
live site, and counting that as a hit would silently inflate the survival rate,
which is the most interesting number in the analysis.
"""
import contextlib
import datetime as dt
import logging
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from . import queue as queue_mod

log = logging.getLogger("unread.resolve")

# 150 words of extracted text, the same floor the measured sample used.
WORD_FLOOR = 150

# The measured direct-GET rate was taken with a desktop Chrome agent. The
# default python-requests agent changes the answer on exactly the two domains
# that make up a third of the queue.
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 "
              "Safari/537.36")

INSTAPAPER_DELAY = 0.8
DIRECT_DELAY = 0.25
WAYBACK_DELAY = 1.0

TIMEOUT = 30

# A wall-clock ceiling on one item, across all three legs.
#
# This is belt over braces and it is here because the braces failed. Twice on
# the first live pass the run froze with the process alive at 0% CPU and a
# single socket in SYN_SENT: `requests` applies its timeout per socket
# operation, not to the whole call, so a connection that never completes and a
# server that trickles bytes forever both sit outside it. The queue is written
# after every item, so nothing was lost - but nothing was moving either, and
# only a process listing said so.
#
# The three legs at their slowest are about two minutes of timeouts and waits,
# so 180 seconds is generous for a healthy item and decisive for a stuck one.
ITEM_DEADLINE = 180

WAYBACK_HOST = "web.archive.org"
WAYBACK_PREFIX = f"https://{WAYBACK_HOST}/web/"

INSTAPAPER, DIRECT, WAYBACK, METADATA = "instapaper", "direct", "wayback", "metadata"
LEGS = (INSTAPAPER, DIRECT, WAYBACK)


class ItemStalled(Exception):
    """One item exceeded its wall-clock deadline. The pass goes on without it."""


@contextlib.contextmanager
def deadline(seconds):
    """Interrupt the block if it runs longer than `seconds`.

    SIGALRM rather than a thread, because the thing being bounded is a blocking
    syscall inside a C extension: a worker thread stuck in the same place
    cannot be joined out of it, and a timer that cannot interrupt the stall is
    not a deadline. Main thread only, which is where this pipeline runs; where
    the signal is unavailable the guard is a no-op rather than a failure.
    """
    if not seconds or not hasattr(signal, "SIGALRM"):
        yield
        return

    def fire(signum, frame):
        raise ItemStalled(f"exceeded {seconds}s")

    previous = signal.signal(signal.SIGALRM, fire)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


@dataclass
class Attempt:
    path: str
    status: object = None
    ok: bool = False
    words: int = 0
    note: str = ""

    def as_dict(self):
        return {"path": self.path, "status": self.status, "ok": self.ok,
                "words": self.words, "note": self.note,
                "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}


@dataclass
class Resolution:
    path: str
    text: str = ""
    html: str = ""
    ok: bool = False
    attempts: list = field(default_factory=list)


def extract_text(html):
    """Readable text from an HTML document, with script and style removed.

    The removal is the point. The four x.com items Instapaper refused all clear
    a naive word count with 1,481 to 7,420 words apiece, which on a tweet page
    is the script payload rather than an article.
    """
    if not html:
        return ""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    return soup.get_text(" ", strip=True)


def word_count(text):
    return len(text.split()) if text else 0


def wayback_snapshot_url(url, saved_year):
    """The dated snapshot path, targeted at the year the item was saved.

    Nearest the saved date rather than newest: the document this project wants
    is the article as it was when the intention was formed.
    """
    year = saved_year or dt.date.today().year
    return f"{WAYBACK_PREFIX}{year}/{url}"


def is_snapshot_url(url):
    if not url:
        return False
    parts = urlsplit(str(url))
    return parts.netloc.lower().endswith(WAYBACK_HOST) and parts.path.startswith("/web/")


def _accept(text):
    words = word_count(text)
    return words >= WORD_FLOOR, words


def _leg_instapaper(row, client, sleeper):
    sleeper(INSTAPAPER_DELAY)
    bookmark_id = row.get("bookmark_id")
    if bookmark_id is None:
        return Attempt(INSTAPAPER, None, False, 0, "no bookmark id"), "", ""
    status, html = client.get_text(bookmark_id)
    text = extract_text(html) if status == 200 else ""
    ok, words = _accept(text) if status == 200 else (False, 0)
    note = "" if status == 200 else "no stored text"
    if status == 200 and not ok:
        note = "below word floor"
    return Attempt(INSTAPAPER, status, ok, words, note), text, (html if ok else "")


def _leg_direct(row, http, sleeper):
    sleeper(DIRECT_DELAY)
    url = row.get("url")
    try:
        resp = http.get(url, headers={"User-Agent": BROWSER_UA,
                                      "Accept": "text/html,application/xhtml+xml"},
                        timeout=TIMEOUT, allow_redirects=True)
    except Exception as exc:  # noqa: BLE001 - a connection failure is an outcome
        return Attempt(DIRECT, None, False, 0, f"{type(exc).__name__}"), "", ""
    if resp.status_code != 200:
        return Attempt(DIRECT, resp.status_code, False, 0, ""), "", ""
    text = extract_text(resp.text)
    ok, words = _accept(text)
    return (Attempt(DIRECT, 200, ok, words, "" if ok else "below word floor"),
            text, (resp.text if ok else ""))


def _leg_wayback(row, http, sleeper):
    sleeper(WAYBACK_DELAY)
    probe = wayback_snapshot_url(row.get("url"), row.get("saved_year"))
    try:
        head = http.head(probe, timeout=TIMEOUT, allow_redirects=True)
    except Exception as exc:  # noqa: BLE001
        return Attempt(WAYBACK, None, False, 0, f"{type(exc).__name__}"), "", ""
    if head.status_code != 200:
        return Attempt(WAYBACK, head.status_code, False, 0, "no snapshot"), "", ""
    landed = getattr(head, "url", None)
    if not is_snapshot_url(landed):
        # A 200 that left the archive is the live site, not a snapshot.
        return Attempt(WAYBACK, 200, False, 0, "redirected off the archive"), "", ""

    sleeper(WAYBACK_DELAY)
    try:
        resp = http.get(landed, headers={"User-Agent": BROWSER_UA},
                        timeout=TIMEOUT, allow_redirects=True)
    except Exception as exc:  # noqa: BLE001
        return Attempt(WAYBACK, 200, False, 0, f"snapshot GET {type(exc).__name__}"), "", ""
    if resp.status_code != 200:
        return Attempt(WAYBACK, resp.status_code, False, 0, "snapshot GET failed"), "", ""
    text = extract_text(resp.text)
    ok, words = _accept(text)
    # Roughly one Wayback hit in seven is a snapshot of a soft 404 or a
    # redirect page, so the body clears the same floor as any other path.
    return (Attempt(WAYBACK, 200, ok, words, "" if ok else "snapshot below word floor"),
            text, (resp.text if ok else ""))


def resolve_one(row, *, instapaper, http, sleeper=time.sleep):
    """Run the chain over one row and return what resolved it.

    Every leg that runs is recorded, whether or not it succeeded: the breakdown
    of how each path fails is what the resolve-rate document is made of.
    """
    attempts = []

    attempt, text, html = _leg_instapaper(row, instapaper, sleeper)
    attempts.append(attempt)
    if attempt.ok:
        return Resolution(INSTAPAPER, text, html, True, attempts)

    attempt, text, html = _leg_direct(row, http, sleeper)
    attempts.append(attempt)
    if attempt.ok:
        return Resolution(DIRECT, text, html, True, attempts)

    attempt, text, html = _leg_wayback(row, http, sleeper)
    attempts.append(attempt)
    if attempt.ok:
        return Resolution(WAYBACK, text, html, True, attempts)

    # An item that resolves nowhere still carries a title, a domain, a saved
    # date and a word count, and it is still an intention.
    return Resolution(METADATA, "", "", False, attempts)


def body_path_for(url_sha256):
    """Sharded by the first two characters of the hash, named by the hash.

    Never by the title: titles are the private strings this corpus is scanned
    for, and a filename carrying one is a leak on a path rather than in text.
    """
    return f"{url_sha256[:2]}/{url_sha256}.txt"


def _log_failure(handle, row, attempt):
    handle.write(
        f"[{dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')}] "
        f"{row.get('url_sha256', '')[:12]} path={attempt.path} "
        f"status={attempt.status} words={attempt.words} "
        f"note={attempt.note or '-'} url={row.get('url', '')}\n")


def run(queue, *, instapaper, http, bodies_dir, sleeper=time.sleep,
        failure_log=None, resolver=None, limit=None, progress=None,
        item_deadline=ITEM_DEADLINE):
    """Resolve every pending row, writing after each one.

    Returns counts per leg. The queue is the resume record: only rows still
    marked pending are touched, so a second pass over a finished corpus makes
    no HTTP call at all.
    """
    resolver = resolver or resolve_one
    bodies = Path(bodies_dir)
    counts = {leg: 0 for leg in LEGS}
    counts[METADATA] = 0
    counts["total"] = 0
    counts["stalled"] = 0
    stalled = 0

    todo = queue.pending()
    if limit:
        todo = todo[:limit]

    handle = None
    if failure_log:
        Path(failure_log).parent.mkdir(parents=True, exist_ok=True)
        handle = open(failure_log, "a", encoding="utf-8")
    try:
        for index, row in enumerate(todo, start=1):
            try:
                with deadline(item_deadline):
                    result = resolver(row, instapaper=instapaper, http=http,
                                      sleeper=sleeper)
            except ItemStalled as exc:
                # One item's worth of time, not the pass - and NOT a verdict. A
                # stall is a bad connection, not a dead link, so the row stays
                # pending and the next run retries it. Recording it as
                # metadata_only would enter a live URL in the dead-link finding
                # permanently, which is the hazard the plan names for the
                # Wayback leg.
                log.warning("item %s stalled: %s", row.get("url_sha256", "")[:12], exc)
                stalled += 1
                queue.mark(
                    row["url_sha256"],
                    attempts=[Attempt(METADATA, None, False, 0,
                                      f"deadline: {exc}").as_dict()],
                    stalled_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                )
                if handle:
                    _log_failure(handle, row, Attempt(METADATA, None, False, 0,
                                                      f"deadline: {exc}"))
                    handle.flush()
                counts["stalled"] = stalled
                continue

            body_path = None
            if result.ok and result.text:
                body_path = body_path_for(row["url_sha256"])
                target = bodies / body_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(result.text, encoding="utf-8")

            queue.mark(
                row["url_sha256"],
                outcome=queue_mod.RESOLVED if result.ok else queue_mod.METADATA_ONLY,
                resolve_path=result.path,
                attempts=[a.as_dict() for a in result.attempts],
                body_path=body_path,
                body_words=word_count(result.text),
                fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            )

            counts[result.path] = counts.get(result.path, 0) + 1
            counts["total"] += 1

            if handle:
                for attempt in result.attempts:
                    if not attempt.ok:
                        _log_failure(handle, row, attempt)
                handle.flush()

            if progress:
                progress(index, len(todo), row, result)
    finally:
        if handle:
            handle.close()

    return counts
