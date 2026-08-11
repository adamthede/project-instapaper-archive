"""Sync state: the watermark and the per-item manifest.

Both live in one JSON file next to the archive it describes
(`<vault>/.matter_manifest.json`), following the Instapaper exporter, which
keeps its manifest in the vault too. State beside the data is the right call
here: the vault is on an external SSD, and a manifest that lived in the repo
while the data lived on the SSD could disagree with reality the moment the drive
was unplugged.

The watermark is Matter's `updated_since` cursor. Two rules keep it honest:

  * It is captured BEFORE the fetch begins, so anything changed mid-run is
    picked up next time rather than skipped. (Matter's own docs recommend this.)
  * It is only advanced after a fully successful run. A partial sync leaves the
    old watermark in place; re-reading a few items is free, missing one is not.
"""

import json
import os
import stat
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

MANIFEST_FILENAME = ".matter_manifest.json"
SCHEMA_VERSION = 1

# Rewind the watermark slightly on each run. `updated_since` is an exclusive
# server-side filter against timestamps we do not control, so a small overlap
# absorbs clock skew between this machine and Matter's. Re-syncing a handful of
# unchanged items costs nothing -- they are skipped on the `updated_at` check.
WATERMARK_OVERLAP = timedelta(minutes=5)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(moment: datetime) -> str:
    """ISO-8601 in UTC with a Z suffix, which is what the API's examples use."""
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def atomic_write_text(path: Path, text: str) -> None:
    """Write a file such that a crash leaves either the old content or the new.

    Same-directory temp file, flushed and fsynced, then os.replace (atomic on
    POSIX). Nothing in this sync rewrites the credential file, but the manifest
    is the record of what has already been pulled: a torn write there would
    either re-download the whole library or, worse, convince the next run that
    articles had already been saved when they had not.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    temp_path = Path(temp_name)
    try:
        # mkstemp creates 0600, which os.replace would then impose on an
        # existing file. Article files in the vault are ordinary 0644 documents
        # Adam opens in Obsidian; silently tightening them on every update
        # would be a surprising side effect of a sync.
        try:
            os.chmod(temp_path, stat.S_IMODE(path.stat().st_mode))
        except OSError:
            pass  # new file: keep mkstemp's conservative default

        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)

        # fsync the directory too, so the rename itself survives a power loss
        # and not just the bytes it points at.
        try:
            dir_fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass  # not all filesystems allow this; the replace is still atomic
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


class SyncState:
    """The manifest: what has been synced, when, and to which file."""

    def __init__(self, path: Path, data: dict | None = None):
        self.path = Path(path)
        data = data or {}
        self.version = data.get("version", SCHEMA_VERSION)
        self.watermark: str | None = data.get("watermark")
        self.last_run: dict = data.get("last_run") or {}
        self.items: dict[str, dict] = data.get("items") or {}

    # ---- persistence ------------------------------------------------------

    @classmethod
    def load(cls, path: Path) -> "SyncState":
        path = Path(path)
        if not path.exists():
            return cls(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # A corrupt manifest must not wedge the nightly job forever. Keep
            # the damaged file for inspection and start clean. Starting clean is
            # safe because the sync adopts existing files by their frontmatter
            # matter_id (see sync.OrphanIndex) rather than trusting this file to
            # be the only record -- so the cost is re-fetching, not duplicates.
            backup = path.with_suffix(path.suffix + ".corrupt")
            try:
                path.replace(backup)
            except OSError:
                pass
            return cls(path)
        if not isinstance(data, dict):
            return cls(path)
        return cls(path, data)

    def save(self) -> None:
        payload = {
            "version": SCHEMA_VERSION,
            "watermark": self.watermark,
            "last_run": self.last_run,
            "items": self.items,
        }
        atomic_write_text(self.path, json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))

    # ---- watermark --------------------------------------------------------

    def updated_since(self) -> str | None:
        """The value to pass as `updated_since`, with the overlap applied."""
        if not self.watermark:
            return None
        try:
            parsed = datetime.fromisoformat(self.watermark.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return to_iso(parsed - WATERMARK_OVERLAP)

    def advance_watermark(self, checkpoint: datetime) -> None:
        """Move the watermark forward -- only ever called after a clean run."""
        self.watermark = to_iso(checkpoint)

    # ---- item records -----------------------------------------------------

    def get_item(self, matter_id: str) -> dict | None:
        record = self.items.get(matter_id)
        return record if isinstance(record, dict) else None

    def record_item(self, matter_id: str, **fields) -> dict:
        record = dict(self.items.get(matter_id) or {})
        record.update(fields)
        self.items[matter_id] = record
        return record

    def known_urls(self) -> dict[str, str]:
        """normalized_url -> the vault-relative path we wrote for it."""
        out: dict[str, str] = {}
        for record in self.items.values():
            if not isinstance(record, dict):
                continue
            normalized = record.get("normalized_url")
            path = record.get("path")
            if normalized and path:
                out[normalized] = path
        return out

    def is_unchanged(self, matter_id: str, updated_at: str | None) -> bool:
        """Whether this item is already on disk at this exact `updated_at`.

        The check requires a recorded path, so an item previously skipped as a
        cross-era duplicate is never mistaken for one we wrote.
        """
        record = self.get_item(matter_id)
        if not record or not record.get("path"):
            return False
        return bool(updated_at) and record.get("updated_at") == updated_at
