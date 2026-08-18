#!/usr/bin/env python3
"""Pull Matter reading into the Article Archive.

Matter (hq.getmatter.com) is where Adam reads now; Instapaper is where he read
from ~2008 to 2025. This keeps both in one corpus by writing Matter items into
the vault in the same Markdown + YAML shape the rest of the archive uses, so the
existing enrich -> index -> dashboard pipeline picks them up with no new code.

Common commands:

    # Verify the token and show the account (no writes, two API calls)
    python3 scripts/core/export_matter_to_archive.py --check-auth

    # Show what a sync would do, without writing anything
    python3 scripts/core/export_matter_to_archive.py --dry-run

    # First backfill, in chunks (the markdown endpoint allows 20/min)
    python3 scripts/core/export_matter_to_archive.py --full --max-items 200

    # What the nightly job runs
    python3 scripts/core/export_matter_to_archive.py --sync --rebuild-index

Exit codes: 0 success, 1 sync completed with per-item errors, 2 could not start
(no token, no vault, auth rejected).
"""

import argparse
import json
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from matter import sync as sync_module  # noqa: E402
from matter.api import MatterClient  # noqa: E402
from matter.credentials import load_token, looks_like_matter_token, redact, token_path  # noqa: E402
from matter.errors import MatterError  # noqa: E402
from matter.state import utcnow  # noqa: E402
from matter.sync import (  # noqa: E402
    DEFAULT_HEARTBEAT,
    DEFAULT_STATUS,
    DEFAULT_SUBDIR,
    SyncConfig,
    SyncResult,
    resolve_vault_path,
    run_sync,
    write_heartbeat,
)

log = logging.getLogger("matter")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="export_matter_to_archive.py",
        description="Sync Matter articles and highlights into the Article Archive vault.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--sync", action="store_true",
                      help="Incremental sync since the last watermark (the default).")
    mode.add_argument("--full", action="store_true",
                      help="Ignore the watermark and walk the whole library.")
    mode.add_argument("--check-auth", action="store_true",
                      help="Verify the token and print the account, then exit. No writes.")

    parser.add_argument("--dry-run", action="store_true",
                        help="Verify auth and report what would change, without writing.")
    parser.add_argument("--vault", help="Vault directory (default: $INSTAPAPER_VAULT_PATH).")
    parser.add_argument("--subdir", default=DEFAULT_SUBDIR,
                        help=f"Subdirectory within the vault for Matter files (default: {DEFAULT_SUBDIR}). "
                             "Pass '' to write flat alongside the Instapaper files.")
    parser.add_argument("--status", default=DEFAULT_STATUS,
                        help=f"Matter statuses to pull, comma-separated (default: {DEFAULT_STATUS}). "
                             "'inbox' is Matter's unsaved discovery feed and is excluded by default.")
    parser.add_argument("--token-file", help=f"Override the credential path (default: {token_path()}).")
    parser.add_argument("--max-items", type=int,
                        help="Stop after N items. The watermark does not advance on a truncated run.")
    parser.add_argument("--refetch-content", action="store_true",
                        help="Re-download article bodies for items already on disk.")
    parser.add_argument("--no-record-rereads", action="store_true",
                        help="Do not annotate an existing archive article when Matter reports "
                             "reading it again; just count it.")
    parser.add_argument("--enrich-local", action="store_true",
                        help="After a sync that wrote new articles, enrich them "
                             "via LM Studio (Qwen) before the index rebuild. "
                             "Non-fatal if LM Studio is down.")
    parser.add_argument("--rebuild-index", action="store_true",
                        help="Run build_index.py afterwards so the dashboard sees the new articles.")
    parser.add_argument("--heartbeat", default=str(DEFAULT_HEARTBEAT),
                        help=f"Heartbeat JSON path (default: {DEFAULT_HEARTBEAT}).")
    parser.add_argument("--no-heartbeat", action="store_true", help="Do not write a heartbeat file.")
    parser.add_argument("--allow-insecure-token", action="store_true",
                        help="Permit a token file that is group/other readable. Not recommended.")
    parser.add_argument("--json", action="store_true", help="Print the run summary as JSON.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging.")
    parser.add_argument("-q", "--quiet", action="store_true", help="Warnings and errors only.")
    return parser


def configure_logging(args) -> None:
    level = logging.INFO
    if args.verbose:
        level = logging.DEBUG
    elif args.quiet:
        level = logging.WARNING
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)-7s %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S")


def check_auth(args) -> int:
    """Verify the credential end to end and report what it can reach."""
    path = Path(args.token_file).expanduser() if args.token_file else token_path()
    token = load_token(path, require_secure_perms=not args.allow_insecure_token)

    print(f"Token file:   {path}")
    print(f"Token:        {redact(token)}")
    if not looks_like_matter_token(token):
        print("              WARNING: does not start with 'mat_' -- check you pasted the API token.")
    # Flushed before the first network call so that if the API rejects the
    # token, the error (on stderr) appears after the path it came from.
    sys.stdout.flush()

    client = MatterClient(token)
    account = client.me()
    limits = account.get("rate_limit") or {}

    print(f"Account:      {account.get('name') or '?'} <{account.get('email') or '?'}>")
    print(f"Account id:   {account.get('id') or '?'}")
    if limits:
        print("Rate limits:  " + ", ".join(f"{k}={v}" for k, v in sorted(limits.items())))

    # One real item read proves the library endpoint works, not just /me.
    probe = next(client.iter_items(status=args.status, page_size=1), None)
    if probe is None:
        print("Library:      reachable, but no items matched "
              f"status={args.status!r}. Try --status all.")
    else:
        print(f"Library:      reachable -- newest updated item is {probe.get('title')!r} "
              f"({probe.get('status')}, updated {probe.get('updated_at')})")
    print("\nAuthentication OK.")
    return 0


def _record_failure(args, message: str) -> None:
    """Write a heartbeat for a run that never produced a SyncResult.

    Without this the heartbeat only ever records successes, which makes it
    useless for the thing it exists to answer: did last night's job work?
    """
    if getattr(args, "no_heartbeat", False) or getattr(args, "dry_run", False):
        return
    moment = utcnow()
    result = SyncResult(started_at=moment, finished_at=moment,
                        outcome="fail", error_message=message)
    write_heartbeat(Path(args.heartbeat).expanduser(), result)


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(args)

    try:
        if args.check_auth:
            return check_auth(args)

        vault_path = resolve_vault_path(args.vault)
        config = SyncConfig(
            vault_path=vault_path,
            subdir=args.subdir,
            status=args.status,
            token_file=Path(args.token_file).expanduser() if args.token_file else None,
            parquet_path=REPO_ROOT / "data" / "archive_index.parquet",
            heartbeat_path=None if args.no_heartbeat else Path(args.heartbeat).expanduser(),
            max_items=args.max_items,
            full=args.full,
            dry_run=args.dry_run,
            refetch_content=args.refetch_content,
            annotate_rereads=not args.no_record_rereads,
            require_secure_perms=not args.allow_insecure_token,
        )

        result = run_sync(config)

    except MatterError as exc:
        # These carry their own remediation; a traceback would bury it.
        log.error("%s", exc)
        _record_failure(args, str(exc))
        return 2
    except KeyboardInterrupt:
        log.error("Interrupted. The watermark was not advanced; re-run to continue.")
        _record_failure(args, "interrupted")
        return 2

    summary = result.as_dict()
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(
            f"\n{'[dry run] ' if args.dry_run else ''}"
            f"{result.new} new, {result.updated} updated, {result.unchanged} unchanged, "
            f"{result.duplicates} already in the archive "
            f"({result.reread_candidates} of them read again in Matter"
            f"{'' if args.dry_run else f', {result.rereads_recorded} newly recorded'}), "
            f"{result.errors} failed "
            f"({result.seen} items seen, {result.highlights} highlights, "
            f"{result.requests} API calls)"
        )
        if result.duplicate_examples:
            print("\nAlready in the archive (no second file written):")
            for example in result.duplicate_examples:
                print(f"  - {example['title']}\n      already at {example['existing']}")
        if result.error_examples:
            print("\nFailed:")
            for example in result.error_examples:
                print(f"  - {example['id']} {example['title']}: {example['error']}")

    if config.heartbeat_path and not args.dry_run:
        write_heartbeat(config.heartbeat_path, result)

    # Enrich BEFORE the rebuild so tonight's articles reach the dashboard
    # with their ai_* fields in one pass. Enrichment failure never blocks
    # the rebuild - see enrich_local's docstring.
    # Unconditional (not gated on result.new): articles left unenriched by a
    # previous night's LM Studio outage are picked up here, and the scan is a
    # cheap no-op when nothing is pending.
    enriched_any = False
    if args.enrich_local and not args.dry_run:
        enriched_any = sync_module.enrich_local(REPO_ROOT)

    if args.rebuild_index and not args.dry_run and (result.new or result.updated or enriched_any):
        sync_module.rebuild_index(REPO_ROOT)

    return 0 if result.errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
