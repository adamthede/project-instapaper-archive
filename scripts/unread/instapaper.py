"""The Instapaper listing, and what counts as the meant-to-read pool.

The pool is 492: the whole live unread folder (395) plus the folder items that
were organized but never opened (97 of 105). That is the plan's scope decision,
settled by Adam on 2026-09-16, and `listing_to_rows` is where it lives.

Two traps this module exists to not fall into, both measured rather than
assumed in `docs/2026-09-15-unread-corpus-resolve-rate.md`:

**The API carries no archived flag.** A bookmark's state is which listing
returns it. All 105 folder items are absent from the unread listing and from
the first 500 of the archive listing, so read progress is the only thing that
separates an organized intention from a finished act.

**`progress_timestamp` is not a reading act.** It is populated even where
progress is 0, and 73 of the 105 folder items share a single backfilled value
from 1 August 2013. Only `progress` is read here.
"""
import logging
import os
import time

from . import queue as queue_mod

API_BASE = "https://www.instapaper.com/api/1"

# Instapaper caps every folder listing at 500 regardless of pagination, which
# is the documented reason the read archive was built from the CSV export
# instead (docs/INSTAPAPER_API_LIMITATIONS.md). Unread returned 395 against
# this limit on 2026-09-15, so it is a true count and not a truncated one; the
# starred folder does cap, at exactly 500.
MAX_LIMIT = 500

RATE_DELAY = 0.8
MAX_RETRIES = 4
BACKOFF_FACTOR = 2

log = logging.getLogger("unread.instapaper")


class InstapaperError(RuntimeError):
    pass


def oauth_session():
    """An xAuth-signed session, from the credentials already in the project env.

    The four variables are the same ones `scripts/core/export_instapaper_to_obsidian.py`
    reads. Nothing here prints or logs them.
    """
    from dotenv import load_dotenv
    from requests_oauthlib import OAuth1Session

    load_dotenv()
    consumer_key = os.getenv("INSTAPAPER_CONSUMER_KEY")
    consumer_secret = os.getenv("INSTAPAPER_CONSUMER_SECRET")
    username = os.getenv("INSTAPAPER_USERNAME")
    password = os.getenv("INSTAPAPER_PASSWORD")
    missing = [name for name, value in (
        ("INSTAPAPER_CONSUMER_KEY", consumer_key),
        ("INSTAPAPER_CONSUMER_SECRET", consumer_secret),
        ("INSTAPAPER_USERNAME", username),
        ("INSTAPAPER_PASSWORD", password)) if not value]
    if missing:
        raise InstapaperError(
            "Instapaper credentials absent: " + ", ".join(missing))

    oauth = OAuth1Session(consumer_key, client_secret=consumer_secret)
    resp = oauth.post(f"{API_BASE}/oauth/access_token", data={
        "x_auth_username": username,
        "x_auth_password": password,
        "x_auth_mode": "client_auth",
    })
    resp.raise_for_status()
    creds = dict(pair.split("=", 1) for pair in resp.text.split("&"))
    return OAuth1Session(
        consumer_key,
        client_secret=consumer_secret,
        resource_owner_key=creds["oauth_token"],
        resource_owner_secret=creds["oauth_token_secret"],
        signature_method="HMAC-SHA1",
    )


class InstapaperClient:
    """The three calls this project makes: folders, listings, stored text."""

    def __init__(self, session, sleeper=time.sleep, delay=RATE_DELAY):
        self.session = session
        self.sleeper = sleeper
        self.delay = delay

    @classmethod
    def from_env(cls, **kwargs):
        return cls(oauth_session(), **kwargs)

    def _post(self, path, data=None):
        delay = 1
        last = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self.session.post(f"{API_BASE}{path}", data=data or {})
                if resp.status_code == 503:
                    raise InstapaperError("503")
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:  # noqa: BLE001 - retried or re-raised below
                last = exc
                if attempt == MAX_RETRIES:
                    raise
                log.warning("retry %s/%s on %s after %s", attempt, MAX_RETRIES, path, exc)
                self.sleeper(delay)
                delay *= BACKOFF_FACTOR
        raise last

    def folders(self):
        """User folder titles to ids. The five the plan names, plus any new one."""
        data = self._post("/folders/list")
        if not isinstance(data, list):
            raise InstapaperError(f"/folders/list returned {type(data).__name__}")
        return {f["title"]: f["folder_id"] for f in data
                if isinstance(f, dict) and f.get("type") == "folder"}

    def bookmarks(self, folder_id, limit=MAX_LIMIT):
        """One listing. The API is inconsistent about dict-vs-list, so both are read."""
        data = self._post("/bookmarks/list",
                          {"limit": limit, "folder_id": folder_id})
        if isinstance(data, dict):
            items = data.get("bookmarks") or []
        elif isinstance(data, list):
            items = [i for i in data
                     if isinstance(i, dict) and i.get("type") == "bookmark"]
        else:
            raise InstapaperError(f"/bookmarks/list returned {type(data).__name__}")
        return items

    def get_text(self, bookmark_id):
        """`(status, html)` from /bookmarks/get_text.

        The status is returned rather than raised because a 400 is data: it
        means Instapaper holds no parsed text for that bookmark, which happened
        on 20 of the 100 sampled items and clusters on social posts and link
        shorteners.
        """
        try:
            resp = self.session.post(f"{API_BASE}/bookmarks/get_text",
                                     data={"bookmark_id": bookmark_id})
        except Exception as exc:  # noqa: BLE001 - a connection failure is an outcome
            log.warning("get_text %s failed: %s", bookmark_id, exc)
            return (None, "")
        if resp.status_code == 200:
            return (200, resp.text)
        return (resp.status_code, "")


def listing_to_rows(unread_items, folder_items_by_name):
    """The meant-to-read pool, as queue rows, deduplicated by URL hash.

    The whole unread listing, including the 119 items that were opened and
    abandoned - that band is the point of the `abandonment` field, not
    something to filter away - plus only the folder items at progress exactly
    0.0.
    """
    rows = []
    seen = set()

    def take(item, folder):
        row = queue_mod.new_row(item, folder=folder)
        if not row["url"] or row["url_sha256"] in seen:
            return
        seen.add(row["url_sha256"])
        rows.append(row)

    for item in unread_items or []:
        take(item, None)

    for folder_name, items in (folder_items_by_name or {}).items():
        for item in items or []:
            try:
                progress = float(item.get("progress") or 0.0)
            except (TypeError, ValueError):
                progress = 0.0
            if progress > 0.0:
                continue
            take(item, folder_name)

    return rows


def fetch_pool(client):
    """Live listing of the pool: unread, then every user folder.

    Returns `(rows, report)` where the report carries the per-listing counts so
    a run can be checked against the plan's measured inventory rather than
    trusted.
    """
    unread = client.bookmarks("unread")
    folders = client.folders()
    folder_items = {}
    for title, folder_id in folders.items():
        folder_items[title] = client.bookmarks(folder_id)

    rows = listing_to_rows(unread, folder_items)
    never_opened = sum(
        1 for items in folder_items.values() for i in items
        if float(i.get("progress") or 0.0) <= 0.0)
    report = {
        "unread": len(unread),
        "folders": {t: len(i) for t, i in folder_items.items()},
        "folder_items": sum(len(i) for i in folder_items.values()),
        "folder_never_opened": never_opened,
        "pool": len(rows),
    }
    return rows, report
