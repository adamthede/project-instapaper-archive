"""An index of URLs already in the archive, for cross-era duplicate detection.

The question this answers is: has Adam already got this article, from the
Instapaper era or the legacy import? Two sources, in order of preference:

  1. `data/archive_index.parquet` -- the built index, one row per article with a
     `url` column. Fast and complete, but needs pyarrow, which the nightly
     interpreter does not have.
  2. A scan of the vault's Markdown frontmatter. No third-party dependency, but
     it touches every file, so the result is cached and reused until the vault
     changes.

If neither is available the sync still runs. It falls back to the Matter
manifest alone, which prevents Matter-vs-Matter duplicates but not cross-era
ones, and it says so in the log rather than pretending the check happened.
"""

import json
import os
import re
from pathlib import Path

from .normalize import normalize_url

URL_INDEX_CACHE = ".matter_url_index.json"
CACHE_VERSION = 1

# Matches `original_url: "https://..."` in a frontmatter block. Deliberately a
# regex over the file head rather than a YAML parse: this runs over ~17,600
# files and only ever needs one key.
_URL_LINE = re.compile(r"^original_url:\s*(.+?)\s*$", re.MULTILINE)
_FRONTMATTER_HEAD_BYTES = 4096


class UrlIndex:
    """Normalized URLs already present in the archive."""

    def __init__(self, urls: dict[str, str] | None = None, *, source: str = "empty", degraded: bool = False):
        self.urls: dict[str, str] = urls or {}
        self.source = source
        self.degraded = degraded

    def __len__(self) -> int:
        return len(self.urls)

    def lookup(self, url) -> str | None:
        """Return where this URL already lives, or None.

        A URL that normalizes to None never matches: see normalize.py for why
        that matters to the ~10,560 rows with no URL at all.
        """
        normalized = normalize_url(url)
        if not normalized:
            return None
        return self.urls.get(normalized)

    def add(self, url, location: str) -> None:
        normalized = normalize_url(url)
        if normalized:
            self.urls.setdefault(normalized, location)


def _from_parquet(parquet_path: Path, skip_dirs: set[str]) -> dict[str, str] | None:
    """Read the `url` column out of the built index, minus our own files.

    build_index.py walks the entire vault, so the Parquet index includes the
    Matter subdirectory. Those rows must be excluded here for the same reason
    the vault scan skips that directory: they are this sync's own output, and
    letting an item match itself would file it as a cross-era duplicate.
    """
    if not parquet_path.exists():
        return None
    try:
        import pyarrow.parquet as pq  # optional: absent under the launchd interpreter
    except ImportError:
        return None

    try:
        table = pq.read_table(str(parquet_path), columns=["url", "file_path"])
    except (OSError, ValueError, KeyError):
        try:
            table = pq.read_table(str(parquet_path), columns=["url"])
        except (OSError, ValueError, KeyError):
            return None

    urls = table.column("url").to_pylist()
    paths = table.column("file_path").to_pylist() if "file_path" in table.column_names else [None] * len(urls)
    skip_fragments = {f"/{name}/" for name in skip_dirs if name}

    out: dict[str, str] = {}
    for value, file_path in zip(urls, paths):
        if file_path and any(fragment in str(file_path) for fragment in skip_fragments):
            continue
        normalized = normalize_url(value)
        if normalized:
            out.setdefault(normalized, "archive_index.parquet")
    return out


def _vault_fingerprint(vault_path: Path, skip_dirs: set[str]) -> tuple[int, int]:
    """(file count, newest mtime) for the vault's Markdown, without reading any of it."""
    count = 0
    newest = 0
    for root, dirnames, filenames in os.walk(vault_path):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs and not d.startswith(".")]
        for name in filenames:
            if not name.endswith(".md") or name.startswith("._"):
                continue
            count += 1
            try:
                mtime = int(os.stat(os.path.join(root, name)).st_mtime)
            except OSError:
                continue
            newest = max(newest, mtime)
    return count, newest


def _from_vault_scan(vault_path: Path, skip_dirs: set[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for root, dirnames, filenames in os.walk(vault_path):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs and not d.startswith(".")]
        for name in filenames:
            if not name.endswith(".md") or name.startswith("._"):
                continue
            full = Path(root) / name
            try:
                with open(full, "r", encoding="utf-8", errors="replace") as stream:
                    head = stream.read(_FRONTMATTER_HEAD_BYTES)
            except OSError:
                continue
            match = _URL_LINE.search(head)
            if not match:
                continue
            raw = match.group(1).strip().strip('"').strip("'")
            normalized = normalize_url(raw)
            if normalized:
                out.setdefault(normalized, str(full.relative_to(vault_path)))
    return out


def build_url_index(
    vault_path: Path,
    *,
    parquet_path: Path | None = None,
    cache_path: Path | None = None,
    skip_dirs: set[str] | None = None,
    allow_vault_scan: bool = True,
    write_cache: bool = True,
) -> UrlIndex:
    """Build (or reuse a cached) index of URLs already in the archive.

    `write_cache` exists for --dry-run. Caching the scan is a pure performance
    win in a normal run, but a dry run promises to leave the vault untouched,
    and a promise with an exception in it is not one Adam can act on.
    """
    vault_path = Path(vault_path)
    skip_dirs = skip_dirs or set()

    if parquet_path:
        urls = _from_parquet(Path(parquet_path), skip_dirs)
        if urls:
            return UrlIndex(urls, source=f"parquet ({len(urls)} urls)")

    if not allow_vault_scan:
        return UrlIndex(
            source="unavailable",
            degraded=True,
        )

    fingerprint = _vault_fingerprint(vault_path, skip_dirs)
    cache_path = Path(cache_path) if cache_path else vault_path / URL_INDEX_CACHE

    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if (
                cached.get("version") == CACHE_VERSION
                and tuple(cached.get("fingerprint") or ()) == fingerprint
                and isinstance(cached.get("urls"), dict)
            ):
                return UrlIndex(cached["urls"], source=f"cache ({len(cached['urls'])} urls)")
        except (OSError, ValueError):
            pass

    urls = _from_vault_scan(vault_path, skip_dirs)
    if write_cache:
        try:
            from .state import atomic_write_text
            atomic_write_text(cache_path, json.dumps({
                "version": CACHE_VERSION,
                "fingerprint": list(fingerprint),
                "urls": urls,
            }))
        except OSError:
            pass  # a cache we cannot write is a slow next run, not a failed one

    # An empty index over a non-empty vault is not a clean "no duplicates"; it
    # means the scan found no URLs where there should be thousands, so every
    # Matter item would look new. Flag it rather than reporting success.
    files_scanned = fingerprint[0]
    degraded = not urls and files_scanned > 0
    return UrlIndex(urls, source=f"vault scan ({len(urls)} urls from {files_scanned} files)",
                    degraded=degraded)
