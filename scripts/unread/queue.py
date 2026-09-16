"""The queue file: one row per intention, keyed by URL hash.

`data/unread_queue.jsonl` is written once from the API listing and then only
ever updated in place. It is the provenance record for the whole project: which
leg of the fetch chain produced the text, what each leg answered, and when.

Two properties, and both are tested rather than asserted in a comment.

**Keyed by URL hash, not bookmark id.** The same URL can be saved twice, and
the Matter era already dedupes on URL across sources, so the hash is the
identity that survives both.

**Written after every item.** `mark` reaches disk before the next item starts,
which is what makes the plan's promise true: killing the run mid-pass costs one
item, not a pass.

Bodies are not in here. The queue is provenance and stays small enough to read;
the article text lives in files under `data/unread_bodies/` that rows point at.
"""
import hashlib
import json
import os
import tempfile
from collections import OrderedDict
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from . import derive

# The listing-derived fields a refresh may update on a row it already has. The
# queue is re-read nightly and progress genuinely moves - 130 of the 462
# export-unread items settled over sixteen months - but a refresh must never
# touch what the fetch stage wrote, or every resolved body would be pulled
# again the next night.
REFRESHABLE = ("title", "folder", "starred", "read_progress", "abandonment",
               "bookmark_id")

PENDING = "pending"
RESOLVED = "resolved"
METADATA_ONLY = "metadata_only"

# An outcome that is not PENDING is settled: resolved text, or a dead link that
# carries its intention on metadata alone. Neither is retried. The dead
# fraction is one of the analysis's six questions, not a queue to work off.
SETTLED = (RESOLVED, METADATA_ONLY)


def normalize_url(url):
    """The form the hash is taken over.

    Scheme and host lowercased, the fragment dropped, a bare trailing slash
    dropped. The query string is kept: on the shape of URL this queue holds it
    is as often the article id as it is tracking.
    """
    if not url:
        return ""
    parts = urlsplit(str(url).strip())
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = parts.path
    if path.endswith("/") and len(path) > 1:
        path = path.rstrip("/")
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def url_key(url):
    """SHA-256 of the normalized URL: the queue's key and the body's filename."""
    return hashlib.sha256(normalize_url(url).encode("utf-8")).hexdigest()


def new_row(bookmark, folder=None):
    """A queue row from one Instapaper bookmark.

    `saved_date` is taken in UTC rather than local time so the by-year table is
    the same table wherever it is built. The API's own field is `time`; the
    exporter also accepts `time_saved`, so both are read, in that order of
    preference.
    """
    import datetime as dt

    url = bookmark.get("url") or ""
    stamp = bookmark.get("time_saved") or bookmark.get("time")
    saved_date, saved_year = None, None
    if stamp:
        try:
            moment = dt.datetime.fromtimestamp(int(stamp), tz=dt.timezone.utc)
            saved_date = moment.date().isoformat()
            saved_year = moment.year
        except (TypeError, ValueError, OSError):
            pass

    try:
        progress = float(bookmark.get("progress") or 0.0)
    except (TypeError, ValueError):
        progress = 0.0

    return {
        "url_sha256": url_key(url),
        "bookmark_id": bookmark.get("bookmark_id"),
        "url": url,
        "title": bookmark.get("title") or "",
        "description": bookmark.get("description") or "",
        "saved_date": saved_date,
        "saved_year": saved_year,
        "folder": folder,
        "starred": str(bookmark.get("starred") or "0") in ("1", "true", "True"),
        "read_progress": progress,
        "abandonment": derive.abandonment(progress),
        "resolve_path": None,
        "outcome": PENDING,
        "attempts": [],
        "body_path": None,
        "fetched_at": None,
    }


def _atomic_write(path, text):
    """Write so that a crash leaves either the old file or the new one.

    Same directory temp file, flushed and fsynced, then os.replace. The queue
    is rewritten after every item; a torn write here would either lose the
    record of what was fetched or, worse, convince the next run it was done.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class Queue:
    """The JSONL queue, loaded into an ordered map keyed by URL hash."""

    def __init__(self, path):
        self.path = Path(path)
        self._rows = OrderedDict()
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                self._rows[row["url_sha256"]] = row

    # -- reading ----------------------------------------------------------

    def rows(self):
        return list(self._rows.values())

    def get(self, key):
        return self._rows.get(key)

    def pending(self):
        return [r for r in self._rows.values() if r.get("outcome") == PENDING]

    def __len__(self):
        return len(self._rows)

    # -- writing ----------------------------------------------------------

    def upsert(self, rows):
        """Add rows that are new; refresh the listing fields on rows we have.

        Returns the number genuinely added. What the fetch stage wrote -
        outcome, resolve path, attempts, body path - is never touched here.
        """
        added = 0
        for row in rows:
            key = row["url_sha256"]
            existing = self._rows.get(key)
            if existing is None:
                self._rows[key] = dict(row)
                added += 1
                continue
            for field in REFRESHABLE:
                if row.get(field) is not None:
                    existing[field] = row[field]
        self.save()
        return added

    def mark(self, key, **fields):
        """Update one row and put it on disk before returning."""
        row = self._rows[key]
        row.update(fields)
        self.save()
        return row

    def save(self):
        body = "".join(json.dumps(r, ensure_ascii=False) + "\n"
                       for r in self._rows.values())
        _atomic_write(self.path, body)
