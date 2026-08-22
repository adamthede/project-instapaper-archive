"""An index of URLs already in the archive, for cross-era duplicate detection.

The question this answers is: has Adam already got this article, from the
Instapaper era or the legacy import? Three sources, in order of preference:

  1. `data/archive_index.parquet` -- the built index, one row per article with a
     `url` column. Fast and complete, but needs pyarrow.
  2. The same Parquet file, read by a subprocess under an interpreter that does
     have pyarrow. The nightly runs on /opt/homebrew/bin/python3 (the holder of
     the TCC grant for ~/Documents), which does not, so without this step the
     nightly could never reach source 1.
  3. A scan of the vault's Markdown frontmatter. No third-party dependency, but
     it touches every file over SMB, so the result is cached and reused until
     the vault changes.

Source 3 is genuinely a last resort. It was the nightly's only reachable source
until 2026-08-22, and it cost ~50 of the run's 58 minutes -- with the cache
never once hitting, because the sync annotates re-read files as it goes, which
moves the newest mtime the cache is keyed on. Sources 1 and 2 read a 15 MB file
on local disk and never stat the vault at all.

If none is available the sync still runs. It falls back to the Matter manifest
alone, which prevents Matter-vs-Matter duplicates but not cross-era ones, and it
says so in the log rather than pretending the check happened.
"""

import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

from .normalize import normalize_url

log = logging.getLogger("matter.vaultindex")

URL_INDEX_CACHE = ".matter_url_index.json"
CACHE_VERSION = 1

# A healthy read of the 15 MB index off local disk is a couple of seconds. The
# cap is here so that a wedged interpreter degrades to the next source instead
# of holding the 04:45 nightly open.
PARQUET_SUBPROCESS_TIMEOUT = 300

# Vault subdirectories this pipeline WRITES to, which must never be read back in
# as articles. `synthesis/` holds the weekly digests written ABOUT the archive --
# 854 of them as of 2026-08-22.
#
# Canonical here rather than in scripts/core/build_index.py, which needs the same
# set: this module is import-safe under the launchd interpreter because it uses
# only the standard library, while build_index.py imports pandas, frontmatter,
# textstat and dotenv at module scope. The dependency can only point one way, so
# the shared fact lives on the light side and the heavy side imports it.
NON_ARTICLE_DIRS = {"synthesis"}

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


def _read_parquet_pairs(parquet_path: Path) -> list[list] | None:
    """The index's raw (url, file_path) rows, or None if pyarrow cannot read it.

    Raw on purpose: normalizing happens in `_pairs_to_urls`, which the
    subprocess path shares, so the two Parquet sources cannot drift apart while
    both still reporting themselves as "parquet".
    """
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
    return [[url, path] for url, path in zip(urls, paths)]


def _pairs_to_urls(pairs, skip_dirs: set[str]) -> dict[str, str]:
    """Normalize the index's rows into a URL lookup, minus our own files.

    build_index.py walks the entire vault, so the Parquet index includes the
    Matter subdirectory. Those rows must be excluded here for the same reason
    the vault scan skips that directory: they are this sync's own output, and
    letting an item match itself would file it as a cross-era duplicate.
    """
    skip_fragments = {f"/{name}/" for name in skip_dirs if name}

    out: dict[str, str] = {}
    for value, file_path in pairs:
        if file_path and any(fragment in str(file_path) for fragment in skip_fragments):
            continue
        normalized = normalize_url(value)
        if normalized:
            # The file's own path, not a constant naming the index: a matched
            # article has to be locatable so a re-read can be recorded on it.
            out.setdefault(normalized, str(file_path) if file_path else "archive_index.parquet")
    return out


def dump_parquet_pairs(parquet_path: str) -> None:
    """Print the index's (url, file_path) rows to stdout as JSON.

    The entry point `_from_parquet_subprocess` invokes under the venv
    interpreter. It exists so that the only thing crossing the process boundary
    is data pyarrow alone can produce -- normalize_url stays behind, in the
    parent, as the one implementation of what counts as the same URL.
    """
    pairs = _read_parquet_pairs(Path(parquet_path))
    if pairs is None:
        raise SystemExit(f"pyarrow could not read {parquet_path}")
    json.dump(pairs, sys.stdout)


# Run as `-c` rather than a helper script: argv carries the paths, so the repo
# living at "Project - Instapaper Archive" (spaces and all) needs no quoting,
# and there is no second file that can go missing from a deploy.
_SUBPROCESS_BOOTSTRAP = (
    "import sys; sys.path.insert(0, sys.argv[1]); "
    "from matter.vaultindex import dump_parquet_pairs; dump_parquet_pairs(sys.argv[2])"
)


def _validated_pairs(payload) -> list[list] | None:
    """The decoded payload, or None if it is not a list of 2-element rows.

    All-or-nothing by design. A partially-accepted index is the worst outcome
    available here: it looks like a successful dedupe while silently missing the
    URLs it dropped, and every one of those gets written to the vault a second
    time. Falling through to the vault scan is slow; this would be wrong.
    """
    if not isinstance(payload, list):
        return None
    out = []
    for row in payload:
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            return None
        out.append([row[0], row[1]])
    return out


def _from_parquet_subprocess(
    parquet_path: Path,
    skip_dirs: set[str],
    interpreter: Path | str | None,
    timeout: float = PARQUET_SUBPROCESS_TIMEOUT,
) -> dict[str, str] | None:
    """Read the index through an interpreter that has pyarrow.

    The same move `rebuild_index` and `enrich_local` make in sync.py: re-enter
    through the repo venv for the one thing the TCC-granted interpreter cannot
    do, rather than doing without it.

    Every failure returns None so the caller falls through to the vault scan.
    This runs unattended at 04:45, where a missing dedupe degrades the night and
    an exception would end it.
    """
    if interpreter is None:
        return None
    interpreter = Path(interpreter)
    if not interpreter.exists():
        log.warning("No interpreter at %s to read the Parquet index with", interpreter)
        return None

    scripts_dir = Path(__file__).resolve().parents[1]
    try:
        completed = subprocess.run(
            [str(interpreter), "-c", _SUBPROCESS_BOOTSTRAP, str(scripts_dir), str(parquet_path)],
            capture_output=True, text=True, timeout=timeout,
            # errors="replace" because text=True otherwise decodes BOTH streams
            # strictly, and UnicodeDecodeError is a ValueError -- caught by
            # neither handler below, so a single stray byte on stderr would take
            # down a run whose stdout was perfectly good.
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        log.warning("Reading the Parquet index via %s timed out after %ss", interpreter, timeout)
        return None
    except OSError as exc:
        log.warning("Could not run %s to read the Parquet index: %s", interpreter, exc)
        return None

    if completed.returncode != 0:
        log.warning("Reading the Parquet index via %s exited %d: %s", interpreter,
                    completed.returncode, (completed.stderr or "").strip()[-300:])
        return None

    try:
        payload = json.loads(completed.stdout)
    except ValueError as exc:
        log.warning("Parquet index reader returned unparseable output: %s", exc)
        return None

    pairs = _validated_pairs(payload)
    if pairs is None:
        log.warning("Parquet index reader returned JSON of an unexpected shape")
        return None
    return _pairs_to_urls(pairs, skip_dirs)


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
    helper_python: Path | str | None = None,
    subprocess_timeout: float = PARQUET_SUBPROCESS_TIMEOUT,
) -> UrlIndex:
    """Build (or reuse a cached) index of URLs already in the archive.

    `helper_python` is an interpreter that has pyarrow, for when this process
    does not; sync.py supplies the repo venv. Without it the nightly cannot
    reach the Parquet index at all and walks the vault instead.

    `write_cache` exists for --dry-run. Caching the scan is a pure performance
    win in a normal run, but a dry run promises to leave the vault untouched,
    and a promise with an exception in it is not one Adam can act on.
    """
    vault_path = Path(vault_path)
    # Unioned in rather than left to the caller: which directories hold this
    # pipeline's own output is a property of the vault, not of anyone's config,
    # and a caller who forgets gets a silently wrong index. Every source below
    # is handed the same set, so all four honour it.
    skip_dirs = (skip_dirs or set()) | NON_ARTICLE_DIRS

    # Both Parquet sources return before `_vault_fingerprint` runs, and that
    # ordering is the fix. The fingerprint os.stats all 18,491 vault files over
    # SMB, so on a night that reaches the Parquet index the vault is never
    # walked -- not for the data, and not for the cache key either.
    if parquet_path and Path(parquet_path).exists():
        parquet_path = Path(parquet_path)
        # Broad on purpose. The handlers inside these two produce better
        # messages and should keep doing the work; this is the guarantee under
        # them, so that "the sync still runs" is structural rather than a list
        # of exception types someone has to keep complete. pyarrow alone can
        # raise ArrowNotImplementedError, ArrowMemoryError and ArrowTypeError,
        # which subclass NotImplementedError, MemoryError and TypeError
        # respectively -- three different branches of the hierarchy.
        try:
            pairs = _read_parquet_pairs(parquet_path)
            if pairs is not None:
                urls = _pairs_to_urls(pairs, skip_dirs)
                if urls:
                    return UrlIndex(urls, source=f"parquet ({len(urls)} urls)")
            urls = _from_parquet_subprocess(parquet_path, skip_dirs, helper_python,
                                            timeout=subprocess_timeout)
            if urls:
                return UrlIndex(urls, source=f"parquet subprocess ({len(urls)} urls)")
        except Exception:  # noqa: BLE001 - a slow index beats a dead 04:45 run
            log.warning("Reading the Parquet index raised; falling back to the vault scan",
                        exc_info=True)

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
        # AttributeError and TypeError too: a cache file that is valid JSON but
        # not an object (`[1,2]`) makes .get() throw, and a non-iterable
        # fingerprint makes tuple() throw. Reachable only on a night where both
        # Parquet routes already failed -- exactly the degraded run this must
        # not also crash.
        except (OSError, ValueError, AttributeError, TypeError):
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
