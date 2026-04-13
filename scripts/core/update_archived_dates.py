#!/usr/bin/env python3
"""
update_archived_dates.py

Safely updates existing markdown files to add date_archived and archived_time
from the Instapaper CSV export WITHOUT destroying existing AI enrichment data.

This script:
1. Reads the CSV to get archived timestamps for each bookmark
2. For each markdown file with an instapaper_id:
   - Loads existing frontmatter (preserving ALL fields)
   - Adds/updates ONLY date_archived and archived_time
   - Writes back with all enrichment data intact

Safe to run multiple times - idempotent operation.
"""

import os
import csv
import logging
from pathlib import Path
from datetime import datetime
import frontmatter
from dotenv import load_dotenv

load_dotenv()

# Config
VAULT_PATH = Path(os.getenv("INSTAPAPER_VAULT_PATH", Path.home() / "Obsidian" / "Vault" / "Instapaper"))
CSV_EXPORT_FILE = Path(os.getenv("INSTAPAPER_CSV_FILE", "2025-05-12-instapaper-export-bookmarks.csv"))

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
log = logging.getLogger("UpdateArchivedDates")

def parse_csv_datetime(date_str):
    """Parse CSV datetime string to datetime object."""
    if not date_str or not isinstance(date_str, str):
        return None
    try:
        # Try parsing with time
        return datetime.strptime(date_str.strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            # Try parsing date only
            return datetime.strptime(date_str.strip(), "%Y-%m-%d")
        except ValueError:
            return None

def load_archived_dates_from_csv():
    """Load archived dates from CSV, keyed by bookmark ID."""
    log.info(f"Loading archived dates from {CSV_EXPORT_FILE}")

    if not CSV_EXPORT_FILE.exists():
        log.error(f"CSV file not found: {CSV_EXPORT_FILE}")
        return {}

    archived_dates = {}

    try:
        with open(CSV_EXPORT_FILE, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)

            for row in reader:
                try:
                    bookmark_id = row.get("ID", "").strip()
                    if not bookmark_id:
                        continue

                    bookmark_id = int(bookmark_id)

                    # Only process if archived
                    is_archived = row.get("Archived", "0").strip().lower() in ['1', 'true']
                    if not is_archived:
                        continue

                    # Get archived time
                    archived_time_str = row.get("Archived Time", "").strip()
                    archived_dt = parse_csv_datetime(archived_time_str)

                    if archived_dt:
                        archived_dates[bookmark_id] = {
                            "date_archived": archived_dt.strftime("%Y-%m-%d"),
                            "archived_time": archived_dt.strftime("%Y-%m-%d %H:%M:%S")
                        }

                except (ValueError, KeyError) as e:
                    log.warning(f"Error processing CSV row: {e}")
                    continue

        log.info(f"Loaded archived dates for {len(archived_dates)} bookmarks from CSV")
        return archived_dates

    except Exception as e:
        log.error(f"Error reading CSV: {e}")
        return {}

def update_markdown_files(archived_dates):
    """Update markdown files with archived dates while preserving all other frontmatter."""
    if not VAULT_PATH.exists():
        log.error(f"Vault path not found: {VAULT_PATH}")
        return

    # Find all markdown files
    md_files = list(VAULT_PATH.rglob("*.md"))
    md_files = [f for f in md_files if not f.name.startswith("._")]  # Skip macOS resource forks

    log.info(f"Found {len(md_files)} markdown files to process")

    updated_count = 0
    skipped_no_id = 0
    skipped_no_csv_data = 0
    skipped_already_has = 0
    error_count = 0

    for file_path in md_files:
        try:
            # Read the file with frontmatter
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                post = frontmatter.load(f)

            # Get instapaper_id
            instapaper_id = post.metadata.get("instapaper_id")
            if instapaper_id is None:
                skipped_no_id += 1
                continue

            instapaper_id = int(instapaper_id)

            # Check if we have archived date for this bookmark
            if instapaper_id not in archived_dates:
                skipped_no_csv_data += 1
                continue

            # Check if already has date_archived
            if "date_archived" in post.metadata and post.metadata["date_archived"]:
                skipped_already_has += 1
                log.debug(f"Skipping {file_path.name} - already has date_archived")
                continue

            # Add/update archived dates
            csv_data = archived_dates[instapaper_id]
            post.metadata["date_archived"] = csv_data["date_archived"]
            post.metadata["archived_time"] = csv_data["archived_time"]

            # Write back to file (preserves all other frontmatter)
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(frontmatter.dumps(post))

            updated_count += 1
            if updated_count % 100 == 0:
                log.info(f"Updated {updated_count} files so far...")

        except Exception as e:
            log.error(f"Error processing {file_path.name}: {e}")
            error_count += 1
            continue

    # Summary
    log.info("=" * 60)
    log.info("Update Summary:")
    log.info(f"  ✅ Updated with archived dates: {updated_count}")
    log.info(f"  ⏭️  Skipped (no instapaper_id): {skipped_no_id}")
    log.info(f"  ⏭️  Skipped (no CSV data): {skipped_no_csv_data}")
    log.info(f"  ⏭️  Skipped (already has date): {skipped_already_has}")
    log.info(f"  ❌ Errors: {error_count}")
    log.info("=" * 60)

def main():
    log.info("Starting archived dates update process...")
    log.info(f"Vault: {VAULT_PATH}")
    log.info(f"CSV: {CSV_EXPORT_FILE}")

    # Load archived dates from CSV
    archived_dates = load_archived_dates_from_csv()

    if not archived_dates:
        log.error("No archived dates loaded from CSV. Exiting.")
        return

    # Update markdown files
    update_markdown_files(archived_dates)

    log.info("Update complete! You can now rebuild the index to see archived dates.")

if __name__ == "__main__":
    main()
