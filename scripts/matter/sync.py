"""The sync itself: Matter's library -> Markdown files in the vault.

WHAT THIS ARCHIVE IS, and why the default status is `archive` alone
------------------------------------------------------------------
The Article Archive is a timestamped record of what Adam has ACTUALLY READ and
when. It is not a record of what he meant to read. Matter's statuses map onto
that distinction directly:

    archive  -> he read it. This is reading history.
    queue    -> he saved it and has not read it yet. This is intent.
    inbox    -> unsaved discovery feed. Not even intent.

So DEFAULT_STATUS is `archive` alone. Pulling `queue` would put unread articles
into a corpus whose whole value is that everything in it was read -- and worse
than merely being present, they would be *dated*: the dashboard derives
`date_read` from `date_archived` and falls back to `date_saved`, so an unread
article would enter the read timeline on the day it was saved. `--status` still
accepts `queue` for deliberate use, and the dashboard defends against that case
independently rather than trusting this default (see derive_date_read in
dashboard/app.py).

The pleasant consequence of syncing nightly: when Adam finishes an article and
Matter moves it to `archive`, the transition is observed within ~24 hours, so
`date_archived` lands within a day of the true reading date. That is a far
better record than any backfill can produce -- items pulled in the initial
backfill can only carry Matter's `updated_at` and say so in `date_saved_source`.
The archive gets more accurate from the day this starts running, which is the
opposite of the usual direction of travel.

Order of operations, and why:

  1. Capture the checkpoint timestamp BEFORE fetching anything.
  2. Ask Matter for items changed since the last watermark.
  3. For each item decide: unchanged / duplicate / new / update.
  4. Fetch body and highlights only for items we are going to write.
  5. Write the file, then record the item in the manifest.
  6. Only after every item succeeded, advance the watermark to the checkpoint.

Step 6 is the one that matters. The watermark is the sync's memory; advancing it
after a partial run would mean the items that failed are never asked for again.
Leaving it put costs a few redundant reads on the next run and nothing else.
"""

import logging
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import mapping
from .api import MatterClient
from .credentials import load_token, looks_like_matter_token, redact, token_path
from .errors import MatterAuthError, MatterError, MatterForbiddenError, VaultNotFoundError
from .state import MANIFEST_FILENAME, SyncState, atomic_write_text, to_iso, utcnow
from .vaultindex import build_url_index

log = logging.getLogger("matter.sync")

# scripts/matter/sync.py -> the repo. The nightly entry point computes the same
# thing from scripts/core/, but run_sync needs it too, to hand the venv
# interpreter to the dedupe index.
REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_VAULT_ENV = "INSTAPAPER_VAULT_PATH"
DEFAULT_VAULT = Path("~/Obsidian/Vault/Instapaper")
DEFAULT_SUBDIR = "matter"
# Read articles only -- see the module docstring. `queue` is saved-not-read and
# `inbox` is not even saved; neither belongs in a record of what was read.
DEFAULT_STATUS = "archive"
DEFAULT_HEARTBEAT = Path("~/Library/Logs/MatterSync/nightly-heartbeat.json")


@dataclass
class SyncConfig:
    vault_path: Path
    subdir: str = DEFAULT_SUBDIR
    status: str = DEFAULT_STATUS
    token_file: Path | None = None
    parquet_path: Path | None = None
    heartbeat_path: Path | None = None
    max_items: int | None = None
    full: bool = False
    dry_run: bool = False
    refetch_content: bool = False
    require_secure_perms: bool = True
    annotate_rereads: bool = True
    save_every: int = 20

    @property
    def target_dir(self) -> Path:
        return self.vault_path / self.subdir if self.subdir else self.vault_path

    @property
    def manifest_path(self) -> Path:
        return self.vault_path / MANIFEST_FILENAME


@dataclass
class SyncResult:
    started_at: datetime
    finished_at: datetime | None = None
    outcome: str = "running"
    new: int = 0
    updated: int = 0
    unchanged: int = 0
    duplicates: int = 0
    reread_candidates: int = 0
    rereads_recorded: int = 0
    errors: int = 0
    # What the code-freshness check found at the top of the run. It is in the
    # heartbeat rather than only the log because the failure this feature exists
    # to fix -- a nightly quietly running stale code -- went unnoticed for two
    # days precisely BECAUSE its only symptom would have been in a log nobody
    # reads at 04:45. A WARNING in that same file is the same failure at one
    # remove.
    #
    # HALF DONE, deliberately. command-center's launchd_stats.py::_read_heartbeat
    # is defensive -- it picks out start/finish/outcome and DISCARDS everything
    # else -- so this key is safe to add (it cannot break that reader) but is
    # not yet surfaced by it. Making a stale night visible in the cockpit needs
    # either a command-center change to carry the key through, or a decision
    # here about whether stale-but-successful should influence `outcome` and be
    # picked up by the existing red/green logic. That is a design call, not a
    # wiring one, so the value is recorded and the surfacing is left open.
    freshness: str = "not checked"
    seen: int = 0
    highlights: int = 0
    requests: int = 0
    throttled_seconds: float = 0.0
    watermark_before: str | None = None
    watermark_after: str | None = None
    dedupe_source: str = "none"
    dedupe_degraded: bool = False
    error_message: str | None = None
    duplicate_examples: list[dict] = field(default_factory=list)
    error_examples: list[dict] = field(default_factory=list)
    # Per-leg status for the post-sync chain (enrich / rebuild_index /
    # rebuild_site / deploy). The heartbeat carries these so the cockpit can
    # say WHICH leg failed, not just that the night did.
    legs: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "started_at": to_iso(self.started_at),
            "finished_at": to_iso(self.finished_at) if self.finished_at else None,
            "outcome": self.outcome,
            "new": self.new,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "duplicates": self.duplicates,
            "reread_candidates": self.reread_candidates,
            "rereads_recorded": self.rereads_recorded,
            "errors": self.errors,
            "items_seen": self.seen,
            "highlights": self.highlights,
            "api_requests": self.requests,
            "throttled_seconds": round(self.throttled_seconds, 1),
            "watermark_before": self.watermark_before,
            "watermark_after": self.watermark_after,
            "freshness": self.freshness,
            "dedupe_source": self.dedupe_source,
            "dedupe_degraded": self.dedupe_degraded,
            "error": self.error_message,
            "error_examples": self.error_examples,
            "legs": self.legs,
        }


def resolve_vault_path(explicit: str | Path | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get(DEFAULT_VAULT_ENV)
    return Path(env).expanduser() if env else DEFAULT_VAULT.expanduser()


def ensure_vault(vault_path: Path) -> None:
    """Fail loudly when the vault is absent -- never create it.

    The archive lives on an external SSD. Creating the directory would produce an
    empty vault on the mount point, and the next index rebuild would report that
    17,637 articles had vanished.
    """
    if not vault_path.exists():
        raise VaultNotFoundError(
            f"Vault directory not found: {vault_path}\n"
            "Refusing to create it -- the archive normally lives on an external drive, and "
            "creating an empty vault here would look like the archive had been wiped.\n"
            "Check that the drive is mounted, or point at the right place with "
            f"{DEFAULT_VAULT_ENV} or --vault."
        )
    if not vault_path.is_dir():
        raise VaultNotFoundError(f"Vault path is not a directory: {vault_path}")


def _unique_path(directory: Path, filename: str, matter_id: str, owned_paths: set[str]) -> Path:
    """A path for a new file that will not clobber an existing article.

    Two different articles can share a title and a date -- and a Matter item can
    collide with an Instapaper file already in the vault, since both use the same
    `date – title.md` naming. When that happens the Matter id disambiguates, and
    it stays stable across runs so the file does not move around.
    """
    candidate = directory / filename
    if not candidate.exists() and str(candidate) not in owned_paths:
        return candidate

    stem = Path(filename).stem
    suffix = (matter_id or "").replace("itm_", "")[:8] or "dup"
    disambiguated = directory / f"{stem} ({suffix}).md"
    if not disambiguated.exists() and str(disambiguated) not in owned_paths:
        return disambiguated

    # Same id, same name, already there: fall back to a counter rather than
    # overwrite something.
    for n in range(2, 100):
        numbered = directory / f"{stem} ({suffix}-{n}).md"
        if not numbered.exists() and str(numbered) not in owned_paths:
            return numbered
    raise RuntimeError(f"Could not find a free filename for {filename!r} in {directory}")


def _load_existing(path: Path) -> tuple[dict, str, str]:
    """Read an existing article, reporting whether its frontmatter was readable."""
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        raise MatterError(f"Could not read existing article {path}: {exc}") from exc
    return mapping.parse_document(text)


_MATTER_ID_LINE = re.compile(r"^matter_id:\s*[\"']?(itm_[A-Za-z0-9_-]+)", re.MULTILINE)


def _matter_id_of(path: Path) -> str | None:
    """The matter_id recorded in a file's frontmatter, if it has one.

    Falls back to a regex when the YAML will not parse: a file with broken
    frontmatter still needs to be recognised as ours, or the sync would write a
    second copy beside it instead of refusing to touch it.
    """
    try:
        metadata, _, status = _load_existing(path)
    except MatterError:
        return None
    if status == mapping.PARSE_OK:
        value = metadata.get("matter_id")
        return value if isinstance(value, str) else None
    try:
        match = _MATTER_ID_LINE.search(path.read_text(encoding="utf-8-sig", errors="replace")[:4096])
    except OSError:
        return None
    return match.group(1) if match else None


class OrphanIndex:
    """Finds files this sync wrote whose manifest record has been lost.

    Built lazily and at most once per run, because the healthy case -- a
    manifest that knows about every file -- never needs it. It is only consulted
    when an item with no manifest record is about to be written, which is
    exactly the situation a killed run or a reset manifest produces.
    """

    def __init__(self, directory: Path):
        self._directory = directory
        self._by_id: dict[str, Path] | None = None

    def lookup(self, matter_id: str) -> Path | None:
        if self._by_id is None:
            self._by_id = self._build()
        return self._by_id.get(matter_id)

    def forget(self, matter_id: str) -> None:
        if self._by_id is not None:
            self._by_id.pop(matter_id, None)

    def _build(self) -> dict[str, Path]:
        found: dict[str, Path] = {}
        if not self._directory.is_dir():
            return found
        for path in sorted(self._directory.glob("*.md")):
            if path.name.startswith("._"):
                continue
            existing_id = _matter_id_of(path)
            if not existing_id:
                continue
            previous = found.get(existing_id)
            if previous is None:
                found[existing_id] = path
                continue
            # Two files claiming one item: the older is the one enrichment more
            # likely ran against, so adopt that and leave the newer alone.
            log.warning(
                "Two files carry matter_id %s (%s and %s); adopting the older one",
                existing_id, previous.name, path.name,
            )
            if path.stat().st_mtime < previous.stat().st_mtime:
                found[existing_id] = path
        if found:
            log.debug("Indexed %s existing Matter files for orphan adoption", len(found))
        return found


def run_sync(config: SyncConfig, *, client: MatterClient | None = None) -> SyncResult:
    result = SyncResult(started_at=utcnow())

    ensure_vault(config.vault_path)

    state = SyncState.load(config.manifest_path)
    result.watermark_before = state.watermark

    if client is None:
        token = load_token(config.token_file, require_secure_perms=config.require_secure_perms)
        if not looks_like_matter_token(token):
            log.warning(
                "Token in %s does not start with 'mat_' (%s). Continuing, but if the API "
                "rejects it, that is the first thing to check.",
                config.token_file or token_path(), redact(token),
            )
        client = MatterClient(token)

    account = client.me()
    applied = client.adopt_account_rate_limits(account)
    log.info(
        "Authenticated as %s <%s>%s",
        account.get("name") or "?", account.get("email") or "?",
        f"; tightened rate limits to {applied}" if applied else "",
    )

    # Captured before the first fetch: anything that changes while we run is
    # picked up next time rather than missed.
    checkpoint = utcnow()
    updated_since = None if config.full else state.updated_since()
    log.info(
        "Sync mode: %s (updated_since=%s, status=%s)",
        "full" if config.full else "incremental", updated_since or "beginning", config.status,
    )

    url_index = build_url_index(
        config.vault_path,
        parquet_path=config.parquet_path,
        skip_dirs={config.subdir} if config.subdir else set(),
        write_cache=not config.dry_run,
        # The nightly interpreter has no pyarrow, so without an interpreter that
        # does, the index has no way to reach the Parquet file and falls back to
        # walking the vault over SMB -- which was most of the 58-minute run.
        helper_python=_venv_python(REPO_ROOT),
    )
    result.dedupe_source = url_index.source
    result.dedupe_degraded = url_index.degraded
    if url_index.degraded:
        log.warning(
            "Cross-era duplicate detection is DEGRADED: no Parquet index and no vault scan. "
            "Matter-vs-Matter duplicates are still prevented by the manifest, but an article "
            "already saved in the Instapaper era may be written a second time."
        )
    else:
        log.info("Duplicate index built from %s", url_index.source)

    # Anything the manifest already knows about is ours; it must not be treated
    # as a cross-era duplicate of itself.
    for normalized, path in state.known_urls().items():
        url_index.urls.setdefault(normalized, path)

    target_dir = config.target_dir
    if not config.dry_run:
        target_dir.mkdir(parents=True, exist_ok=True)

    owned_paths = {
        str(config.vault_path / record["path"])
        for record in state.items.values()
        if isinstance(record, dict) and record.get("path")
    }

    orphans = OrphanIndex(target_dir)

    # Whether a previous run has listed the WHOLE archive. Only then does an
    # article appearing for the first time prove it entered the archive since --
    # an OBSERVED transition, whose updated_at is at most one sync interval old.
    #
    # The test is a completed full listing, not merely a non-empty manifest. A
    # chunked backfill (--full --max-items 200) leaves the manifest full of items
    # while most of the library has never been listed, and every article the run
    # had not reached yet would then be labelled a transition nobody witnessed.
    # Verified: under the old test, 4 of 6 articles archived years earlier
    # claimed observed-transition on the second chunk.
    #
    # It also requires --full for this run: --sync filters the listing by
    # updated_since, so absence from those results proves nothing at all.
    def _statuses(spec):
        return {s.strip() for s in (spec or "").split(",") if s.strip()}

    steady_state = (
        config.full
        and bool(state.full_listing_completed_at)
        # Every status being pulled now must have been covered by that listing,
        # or an article "appearing for the first time" may simply never have
        # been asked for before.
        and _statuses(config.status) <= _statuses(state.full_listing_status)
    )
    log.info(
        "Read-date estimates: %s",
        "observed transitions (a previous run has completed)" if steady_state
        else "updated_at fallback (cold start, or --sync rather than --full)",
    )

    pending_saves = 0
    truncated = False
    try:
        for item in client.iter_items(status=config.status, updated_since=updated_since):
            result.seen += 1

            try:
                outcome = _sync_one(item, config, state, url_index, client, owned_paths,
                                    orphans, result, steady_state=steady_state)
            except (MatterAuthError, MatterForbiddenError):
                # The credential died mid-run. Every remaining item would fail
                # the same way, so stop rather than logging thousands of
                # identical failures and hammering a rejecting API.
                raise
            except Exception as exc:  # one bad item must not end the night
                result.errors += 1
                item_id = item.get("id", "?")
                log.error("Item %s failed: %s", item_id, exc)
                if len(result.error_examples) < 10:
                    result.error_examples.append({"id": item_id, "title": item.get("title"), "error": str(exc)})
                outcome = "error"

            if outcome in ("new", "updated"):
                pending_saves += 1
                if not config.dry_run and pending_saves >= config.save_every:
                    state.save()
                    pending_saves = 0

            # The budget counts work done, not items looked at. Counting every
            # item would make a chunked backfill spend its whole allowance
            # re-skipping the items it already has and never reach new ones.
            if config.max_items is not None and _work_done(result) >= config.max_items:
                log.info("Stopping at --max-items=%s; the watermark will not advance.", config.max_items)
                # A truncated run has not seen everything, so the watermark must
                # stay where it is or the unseen remainder is lost forever.
                truncated = True
                break

    finally:
        result.requests = client.request_count
        result.throttled_seconds = client.throttled_seconds
        # Save unconditionally: files may already be on disk, and a manifest
        # that does not know about them would have the next run write duplicates.
        # This runs even when the loop raised -- so it is wrapped, because an
        # exception escaping a finally would replace the real failure with a
        # confusing one and cost the run its exit code and failure heartbeat.
        if not config.dry_run and config.vault_path.is_dir():
            try:
                state.save()
            except Exception as exc:
                log.error("Could not save the sync manifest to %s: %s", state.path, exc)

    # The vault can disappear mid-run (the drive is external). If it has, every
    # per-item write already failed, so this is a failed run whatever the
    # counters say -- and there is nowhere to record it.
    vault_present = config.vault_path.is_dir()
    if not vault_present:
        result.errors = max(result.errors, 1)
        result.error_message = (
            f"The vault {config.vault_path} disappeared during the run; the drive was "
            "probably unmounted. Nothing was written and the watermark was not advanced."
        )
        log.error("%s", result.error_message)

    if result.errors == 0 and not truncated and not config.dry_run:
        state.advance_watermark(checkpoint)
        result.watermark_after = state.watermark
        if config.full:
            # The whole archive was listed and every item handled, so from here
            # on a first-time appearance is a witnessed transition -- for these
            # statuses, which is why the set is recorded alongside the time.
            state.full_listing_completed_at = to_iso(checkpoint)
            state.full_listing_status = config.status
    elif result.errors:
        log.warning(
            "%s item(s) failed, so the watermark stays at %s -- next run re-reads this window.",
            result.errors, state.watermark or "the beginning",
        )

    if not config.dry_run and vault_present:
        state.last_run = result.as_dict()
        try:
            state.save()  # second save: records the run summary and the new watermark
        except Exception as exc:
            log.error("Could not save the sync manifest to %s: %s", state.path, exc)

    result.finished_at = utcnow()
    result.outcome = "ok" if result.errors == 0 else "fail"
    return result


def _resolve_inside_vault(config, location: str) -> Path | None:
    """Resolve a match location to a real file INSIDE the vault, or None.

    `location` comes from the Parquet index's `file_path` column, which holds
    absolute paths recorded whenever that index was last built. They can be
    stale and they can name a different vault entirely -- and note that
    `vault / "/abs/path"` collapses to the absolute path in pathlib, so a naive
    join is no protection at all. Containment is therefore checked explicitly:
    this is the gate on the only writes the sync makes outside its own
    directory.
    """
    raw = Path(location)
    candidate = raw if raw.is_absolute() else config.vault_path / raw
    try:
        resolved = candidate.resolve()
        vault = config.vault_path.resolve()
    except OSError:
        return None
    if not resolved.is_file():
        return None
    if not resolved.is_relative_to(vault):
        log.warning(
            "Refusing to record a re-read on %s: it is outside the vault %s. That path came "
            "from the Parquet index and is probably stale.", resolved, vault,
        )
        return None
    return resolved


def _record_reread(config, location: str, read_date: str, item_url) -> str:
    """Add a re-read date to an article the archive already holds.

    Returns a reason string: "recorded" when the file changed, otherwise why
    not. The reason goes into the manifest, so a refusal is diagnosable later
    from the data rather than only from a log line nobody re-reads.

    This is the only write the sync makes to a file it did not create, so it is
    deliberately timid. It refuses unless it can locate the file *inside the
    vault*, decode it without loss, confirm it really is the article in
    question, and parse its frontmatter cleanly. Any doubt and it declines and
    says so -- the re-read is still counted and recorded in the manifest, so the
    only thing lost is the annotation.
    """
    target = _resolve_inside_vault(config, location)
    if target is None:
        log.warning(
            "Matter read an article the archive already has, but %s could not be located "
            "inside the vault, so the re-read was counted and not written to a file.", location,
        )
        return "not-in-vault"

    try:
        # Strict decoding, unlike the rest of the pipeline. build_index and the
        # enrichment pass read with errors="replace" because they only produce
        # derived data; here the replaced text would be written back and
        # whatever those bytes were would be gone. The corpus is known to hold
        # damaged files -- there are two cleanup scripts for them.
        text = target.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        log.warning(
            "Not recording a re-read on %s: it is not valid UTF-8, and rewriting it would "
            "replace the undecodable bytes with substitution characters.", target,
        )
        return "encoding-damaged"
    except OSError as exc:
        log.warning("Could not read %s to record a re-read: %s", target, exc)
        return "unreadable"

    metadata, body, status = mapping.parse_document(text)
    if status != mapping.PARSE_OK:
        log.warning(
            "Not recording a re-read on %s: its frontmatter could not be read (%s). "
            "Touching it would risk the enrichment already in it.", target, status,
        )
        return "frontmatter-unparseable"

    # Confirm this really is the article Matter matched: a stale index entry can
    # name a path whose file has since been replaced by a different article.
    from .normalize import normalize_url
    if normalize_url(metadata.get("original_url")) != normalize_url(item_url):
        log.warning(
            "Not recording a re-read on %s: its original_url does not match the Matter item, "
            "so the index entry pointing here is stale.", target,
        )
        return "identity-mismatch"

    updated, changed = mapping.annotate_reread(metadata, read_date)
    if not changed:
        # Distinct from the manifest's "already-recorded": reaching here means
        # the file had to be opened to find that out, which is the cost the
        # manifest short-circuit exists to avoid.
        return "already-in-file"

    try:
        atomic_write_text(target, mapping.dump_markdown(updated, body))
    except OSError as exc:
        # Never raises: a failed annotation is a lost note, not a failed sync.
        # Turning it into an item error would pin the watermark over a cosmetic
        # addition to somebody else's file.
        log.warning("Could not write the re-read annotation to %s: %s", target, exc)
        return "write-failed"
    return "recorded"


def _sync_one(item, config, state, url_index, client, owned_paths, orphans, result,
              steady_state: bool = False) -> str:
    matter_id = item.get("id")
    if not matter_id:
        raise ValueError("item has no id")

    updated_at = item.get("updated_at")
    previous = state.get_item(matter_id) or {}
    recorded_path = previous.get("path")
    existing_file = (config.vault_path / recorded_path) if recorded_path else None

    # 1. Already have it, unchanged, and the file is still there.
    if state.is_unchanged(matter_id, updated_at) and existing_file and existing_file.exists():
        result.unchanged += 1
        return "unchanged"

    # 2. A file we wrote on an earlier run that the manifest has since lost --
    #    the run was killed before its state was saved, or the manifest was
    #    corrupt and got reset. This is checked BEFORE the duplicate check,
    #    because the URL index can legitimately contain our own files (the
    #    Parquet index is built from the whole vault, and --subdir '' puts our
    #    files in the scanned tree). Checked the other way round, an item would
    #    be filed as a duplicate of itself, get no `path` recorded, and be
    #    re-skipped that way every night thereafter -- silently frozen.
    adopted_metadata = None
    if not recorded_path:
        orphan = orphans.lookup(matter_id)
        if orphan is not None:
            log.info("Adopting %s, which this sync wrote before losing its manifest record", orphan.name)
            existing_file = orphan
            recorded_path = str(orphan.relative_to(config.vault_path))
            # The manifest is gone, so the file itself is the only record of the
            # sticky dates. Without this the dates would be recomputed from the
            # current updated_at and the article would jump forward in every
            # timeline -- exactly what stickiness exists to prevent.
            adopted_metadata, _, adopted_status = _load_existing(orphan)
            if adopted_status == mapping.PARSE_OK:
                previous = {
                    key: adopted_metadata[key]
                    for key in ("date_saved", "date_saved_source", "date_archived")
                    if key in adopted_metadata
                } | previous

    # 3. The archive already has this article, from the Instapaper or legacy
    #    era. Never a second file. If Matter says it was READ again, that is a
    #    genuine reading event and gets recorded on the existing file -- as an
    #    addition, never as a revision of the original read date.
    if not recorded_path:
        duplicate_of = url_index.lookup(item.get("url"))
        if duplicate_of:
            result.duplicates += 1
            if len(result.duplicate_examples) < 10:
                result.duplicate_examples.append({
                    "title": item.get("title"), "url": item.get("url"), "existing": duplicate_of,
                })

            reread_date = None
            if item.get("status") == "archive":
                # Only an archived item is a read. A queued one is just the same
                # article sitting unread in a second app.
                reread_date = mapping._date_string(updated_at)
                # Counted even on a dry run, and even when annotation is off:
                # this is the number that answers "how much re-reading is in
                # here", and a dry run that reported zero would understate the
                # very thing it is being run to find out.
                result.reread_candidates += 1

            reread_status = None
            recorded = False
            # Already annotated with this exact date on an earlier run. Skipping
            # here is what keeps a nightly --full from opening ~998 files on an
            # external drive to discover there is nothing to add.
            already = (previous.get("reread_date") == reread_date
                       and previous.get("reread_recorded"))
            if already:
                reread_status = "already-recorded"
            elif reread_date and config.annotate_rereads and not config.dry_run:
                reread_status = _record_reread(config, duplicate_of, reread_date, item.get("url"))
                recorded = reread_status == "recorded"
                if recorded:
                    result.rereads_recorded += 1

            if not config.dry_run:
                state.record_item(
                    matter_id,
                    skipped_reason="duplicate_url",
                    duplicate_of=duplicate_of,
                    url=item.get("url"),
                    updated_at=updated_at,
                    reread_date=reread_date,
                    reread_recorded=recorded,
                    reread_status=reread_status,
                    checked_at=to_iso(utcnow()),
                )
            log.info("Already in the archive as %s%s", duplicate_of,
                     f"; recorded a re-read on {reread_date}" if recorded else "")
            return "duplicate"

    is_update = bool(existing_file and existing_file.exists())
    # No manifest record + a run that has seen the whole archive before = this
    # article entered the archive since the last run.
    observed_transition = steady_state and not previous.get("date_saved")

    if config.dry_run:
        result.updated += 1 if is_update else 0
        result.new += 0 if is_update else 1
        log.info("[dry-run] would %s: %s", "update" if is_update else "create", item.get("title"))
        return "updated" if is_update else "new"

    if is_update:
        existing_metadata, existing_body, parse_status = _load_existing(existing_file)
        if parse_status != mapping.PARSE_OK:
            # Rewriting would discard whatever is in there -- including the ai_*
            # enrichment, which costs real money to regenerate. Refuse, and say
            # which file needs a human. Counted as an error, so the watermark
            # stays put and the item is retried once the file is fixed.
            raise MatterError(
                f"Refusing to update {existing_file}: its YAML frontmatter could not be read "
                f"({parse_status}), so preserving the ai_* enrichment already in it is "
                f"impossible. Fix the frontmatter (an unquoted colon in a value is the usual "
                f"cause), or delete the file to have it re-synced from scratch."
            )
    else:
        existing_metadata, existing_body = {}, ""

    # Re-fetch the article body only when we do not already have it. A nightly
    # delta is dominated by items that reappeared because of a new highlight.
    have_content = (
        is_update
        and existing_metadata.get("matter_content_source") == "markdown"
        and existing_body.strip()
        and not config.refetch_content
    )
    if have_content:
        carried_body = mapping.strip_highlights(existing_body)
        detail = item
    else:
        detail = client.get_item(matter_id, include_markdown=True)
        carried_body = None

    annotations = list(client.iter_annotations(matter_id))
    result.highlights += len(annotations)

    metadata, document = mapping.render_item(
        detail,
        annotations,
        previous=previous,
        existing_metadata=existing_metadata,
        existing_body=carried_body,
        observed_transition=observed_transition,
    )

    if is_update:
        # Keep the original filename even if the title changed: renaming would
        # leave the old file behind as a duplicate, and the date is sticky
        # anyway.
        destination = existing_file
    else:
        filename = mapping.build_filename(metadata.get("date_saved"), metadata.get("title", ""))
        destination = _unique_path(config.target_dir, filename, matter_id, owned_paths)

    atomic_write_text(destination, document)
    owned_paths.add(str(destination))
    orphans.forget(matter_id)

    relative = str(destination.relative_to(config.vault_path))
    state.record_item(
        matter_id,
        path=relative,
        url=item.get("url"),
        normalized_url=_normalized(item.get("url")),
        title=metadata.get("title"),
        date_saved=metadata.get("date_saved"),
        date_saved_source=metadata.get("date_saved_source"),
        date_archived=metadata.get("date_archived"),
        status=item.get("status"),
        updated_at=updated_at,
        highlight_count=len(annotations),
        synced_at=to_iso(utcnow()),
        skipped_reason=None,
    )
    url_index.add(item.get("url"), relative)

    if is_update:
        result.updated += 1
        log.info("Updated %s (%s highlights)", relative, len(annotations))
        return "updated"
    result.new += 1
    log.info("Created %s (%s highlights)", relative, len(annotations))
    return "new"


def _work_done(result) -> int:
    """Items this run actually acted on, as opposed to merely looked at."""
    return result.new + result.updated + result.duplicates + result.errors


def _normalized(url):
    from .normalize import normalize_url
    return normalize_url(url)


# ---- side effects ---------------------------------------------------------

def write_heartbeat(path: Path, result: SyncResult) -> None:
    """Write the fleet-standard heartbeat JSON.

    Keys match what command-center's launchd_stats.py reads (started_at /
    finished_at / outcome). Note that writing this file is necessary but not
    sufficient for the job to appear in the cockpit's launchd panel: that panel
    reads a hardcoded job registry in command-center, so an entry has to be
    added there separately.
    """
    import json
    try:
        # create_parents: the heartbeat lives under ~/Library/Logs, where
        # making the directory is correct -- unlike the vault.
        atomic_write_text(Path(path).expanduser(), json.dumps(result.as_dict(), indent=2),
                          create_parents=True)
    except OSError as exc:
        log.warning("Could not write heartbeat to %s: %s", path, exc)


def _venv_python(repo_root: Path, allow_current: bool = False) -> Path | None:
    """The interpreter that has pandas/pyarrow/frontmatter, or None.

    The launchd job runs on /opt/homebrew/bin/python3 (the interpreter holding
    the TCC grant for ~/Documents), which deliberately has none of those, so
    every leg that needs them re-enters through the repo venv. Override with
    MATTER_INDEX_PYTHON when the venv moves.

    `allow_current` adds sys.executable as a last resort - correct for the
    index rebuild (a hand-run from an already-capable interpreter should just
    work), wrong for legs invoked only by the nightly, where falling back to
    the TCC interpreter would fail confusingly on a missing import.
    """
    candidates = []
    override = os.environ.get("MATTER_INDEX_PYTHON")
    if override:
        candidates.append(Path(override).expanduser())
    candidates += [repo_root / ".venv" / "bin" / "python",
                   repo_root / "venv" / "bin" / "python"]
    if allow_current:
        candidates.append(Path(sys.executable))
    return next((c for c in candidates if c.exists()), None)


def enrich_local(repo_root: Path) -> bool:
    """Run the local (LM Studio/Qwen) enrichment over new matter/ files.

    Same interpreter strategy as rebuild_index: the enricher needs pandas and
    python-frontmatter, so it runs under the repo venv. Failure is NON-FATAL
    by design - an unenriched article is picked up by the next night's scan,
    while a dead LM Studio must never block the sync or the index rebuild.
    """
    script = repo_root / "scripts" / "core" / "enrich_archive_local.py"
    if not script.exists():
        log.error("Cannot enrich locally: %s not found", script)
        return False
    interpreter = _venv_python(repo_root)
    if interpreter is None:
        log.error("Cannot enrich locally: no venv interpreter found")
        return False
    try:
        proc = subprocess.run([str(interpreter), str(script), "scan-matter"],
                              capture_output=True, text=True, timeout=3600)
        for line in (proc.stdout or "").strip().splitlines():
            log.info("enrich-local: %s", line)
        if proc.returncode != 0:
            log.warning("Local enrichment exited %d: %s", proc.returncode,
                        (proc.stderr or "").strip()[-300:])
            return False
        # True only when something was written - the return value gates an
        # otherwise-skipped index rebuild, and a no-op night must not cost one.
        m = re.search(r"Done: (\d+) enriched", proc.stdout or "")
        return bool(m and int(m.group(1)) > 0)
    except subprocess.TimeoutExpired:
        log.warning("Local enrichment timed out after 1h; articles will be "
                    "picked up by the next nightly scan.")
        return False


def rebuild_index(repo_root: Path) -> bool:
    """Run build_index.py so the dashboard sees the new articles.

    Deliberately a subprocess with a different interpreter: build_index.py needs
    pandas, pyarrow and python-frontmatter, and the interpreter that runs this
    sync under launchd has neither pyarrow nor frontmatter. The repo virtualenv
    does.
    """
    script = repo_root / "scripts" / "core" / "build_index.py"
    if not script.exists():
        log.error("Cannot rebuild index: %s not found", script)
        return False

    # Rebuilding against a vault that is not there would compile an empty index
    # over the real one and make 17,637 articles look deleted.
    vault = resolve_vault_path()
    if not vault.is_dir():
        log.error(
            "Refusing to rebuild the index: the vault %s is not available. "
            "The Markdown files are safe; re-run build_index.py once the drive is mounted.",
            vault,
        )
        return False

    interpreter = _venv_python(repo_root, allow_current=True)
    if interpreter is None:
        log.error("Cannot rebuild index: no usable Python interpreter found")
        return False

    log.info("Rebuilding Parquet index with %s", interpreter)
    try:
        completed = subprocess.run(
            [str(interpreter), str(script)], cwd=str(repo_root),
            capture_output=True, text=True, timeout=3600,
        )
    except subprocess.TimeoutExpired:
        # The other three legs already caught this; only rebuild_index let it
        # raise, which propagated out of main() and skipped the heartbeat
        # write - leaving yesterday's "ok" on disk. Not hypothetical: the
        # 2026-08-21 run spent 53 minutes just scanning 18,491 vault files
        # over SMB, and build_index.py reads the same NAS.
        log.error("build_index.py timed out after 1h. The Markdown files are "
                  "written and safe; re-run it by hand.")
        return False
    if completed.returncode != 0:
        log.error(
            "build_index.py failed (exit %s). The Markdown files are written and safe; "
            "re-run it by hand.\n%s",
            completed.returncode, (completed.stderr or completed.stdout or "")[-2000:],
        )
        return False
    log.info("Index rebuilt: %s", (completed.stdout or "").strip().splitlines()[-1:] or "")
    return True


SITE_DIR_NAME = "_site"
SITE_MARKER = ".reading-site"
PAGES_PROJECT = os.environ.get("READING_PAGES_PROJECT", "reading-adamthede")
PAGES_BRANCH = os.environ.get("READING_PAGES_BRANCH", "main")


def rebuild_site(repo_root: Path) -> bool:
    """Regenerate _site from the vault's synthesis files.

    Unconditional when asked, unlike the index rebuild: the WEEKLY synthesis
    job (Sundays 20:00) writes new week files that this nightly never sees in
    its own sync counters, so gating on `result.new` would leave Monday's site
    missing Sunday's digest. A no-change regeneration is ~1 minute of local
    I/O, which is cheaper than reasoning about every upstream writer.

    generate.py renders to a temp dir and swaps atomically, so a failure here
    leaves the previous good _site untouched - which is why deploy is allowed
    to be skipped rather than publishing a half-built site.
    """
    script = repo_root / "site" / "generate.py"
    if not script.exists():
        log.error("Cannot rebuild the site: %s not found", script)
        return False
    interpreter = _venv_python(repo_root)
    if interpreter is None:
        log.error("Cannot rebuild the site: no venv interpreter found")
        return False
    try:
        completed = subprocess.run(
            [str(interpreter), str(script), "--out", SITE_DIR_NAME],
            cwd=str(repo_root), capture_output=True, text=True, timeout=3600,
        )
    except subprocess.TimeoutExpired:
        log.error("Site rebuild timed out after 1h.")
        return False
    if completed.returncode != 0:
        log.error("site/generate.py failed (exit %s):\n%s", completed.returncode,
                  (completed.stderr or completed.stdout or "")[-2000:])
        return False
    for line in (completed.stdout or "").strip().splitlines():
        log.info("rebuild-site: %s", line)
    return True


def _wrangler() -> Path | None:
    """wrangler, or None. PATH first, then volta's shim directory explicitly -
    launchd starts with a minimal environment and ~/.volta/bin is the gotcha
    the audit called out."""
    found = shutil.which("wrangler")
    if found:
        return Path(found)
    volta = Path.home() / ".volta" / "bin" / "wrangler"
    return volta if volta.exists() else None


DEPLOY_OPT_IN_ENV = "READING_DEPLOY"


def deploy_site(repo_root: Path) -> bool:
    """Publish _site to Cloudflare Pages.

    Requires READING_DEPLOY=1. That is not ceremony: PAGES_PROJECT is a
    hardcoded LIVE target, so ANY caller reaching this function publishes to
    the real website - including a test, a mutation run, or an adversarial
    review probing whether the guards below hold. That is not hypothetical.
    On 2026-08-21 a review agent asked exactly the right question ("does the
    guard accept a foreign directory?"), answered it by calling this function,
    and published its fixtures to reading.adamthede.com seven times. The
    fixture that reached the live site literally read "someone else's site".

    So publishing is now opt-in, set only by the nightly plist and by a
    deliberate hand-run. Everything else refuses and says why.

    Beyond that: refuses to deploy anything this generator did not produce -
    the directory must exist, carry an index.html, and carry generate.py's
    own marker. Publishing is the one leg with a blast radius outside this
    machine, so it checks what it is about to ship rather than trusting a path.
    """
    if os.environ.get(DEPLOY_OPT_IN_ENV) != "1":
        log.error(
            "Refusing to deploy: %s is not set to 1. This publishes to the "
            "live project %r, so it must be opted into explicitly (the nightly "
            "plist sets it). Nothing was published.",
            DEPLOY_OPT_IN_ENV, PAGES_PROJECT)
        return False
    site = repo_root / SITE_DIR_NAME
    if not (site / "index.html").exists() or not (site / SITE_MARKER).exists():
        log.error("Refusing to deploy %s: not a generated site (missing "
                  "index.html or %s). Nothing was published.", site, SITE_MARKER)
        return False
    wrangler = _wrangler()
    if wrangler is None:
        log.error("Cannot deploy: wrangler not found on PATH or at "
                  "~/.volta/bin/wrangler. Add ~/.volta/bin to the plist PATH.")
        return False
    try:
        completed = subprocess.run(
            [str(wrangler), "pages", "deploy", SITE_DIR_NAME,
             "--project-name", PAGES_PROJECT, "--branch", PAGES_BRANCH],
            cwd=str(repo_root), capture_output=True, text=True, timeout=1800,
        )
    except subprocess.TimeoutExpired:
        log.error("Deploy timed out after 30m; the site on Cloudflare is "
                  "unchanged (last good deployment still serving).")
        return False
    if completed.returncode != 0:
        log.error("wrangler deploy failed (exit %s). The last good deployment "
                  "is still serving:\n%s", completed.returncode,
                  (completed.stderr or completed.stdout or "")[-2000:])
        return False
    for line in (completed.stdout or "").strip().splitlines()[-4:]:
        log.info("deploy: %s", line)
    return True
