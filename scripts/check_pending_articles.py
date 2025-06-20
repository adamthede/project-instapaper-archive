#!/usr/bin/env python3
# check_pending_articles.py
# Utility to compare Instapaper CSV export with the bulk import manifest
# to identify pending articles and articles that failed processing.

import os
import json
import csv
from pathlib import Path
from datetime import datetime
import logging
from dotenv import load_dotenv

load_dotenv()

# --- Configuration (should match bulk_import_instapaper_from_csv.py defaults or use .env)
# Path to your main Instapaper CSV export file
INSTAPAPER_CSV_FILE = Path(os.getenv("INSTAPAPER_CSV_FILE", "../2025-05-12-instapaper-export-bookmarks.csv"))
# Path to the manifest file generated by the bulk import script
BULK_MANIFEST_FILE = Path(os.getenv("INSTAPAPER_BULK_MANIFEST_FILE", Path.home() / ".instapaper_bulk_import_manifest.json"))

# Output CSV file name
OUTPUT_CSV_FILENAME_PREFIX = "article_processing_status_"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
log = logging.getLogger("CheckPendingArticles")

def find_project_root(marker_file=".env"):
    """Find the project root by looking for a marker file (e.g., .env) or a common directory."""
    current_path = Path(__file__).resolve().parent
    for _ in range(5): # Limit search depth
        if (current_path / marker_file).exists() or (current_path / "scripts").is_dir():
            return current_path
        if current_path.parent == current_path:
            break # Reached filesystem root
        current_path = current_path.parent
    return Path.cwd() # Fallback to current working directory

def main():
    project_root = find_project_root()
    log.info(f"Using project root: {project_root}")

    # Adjust INSTAPAPER_CSV_FILE path to be relative to project root if it was relative to script dir
    if not INSTAPAPER_CSV_FILE.is_absolute() and INSTAPAPER_CSV_FILE.parts[0] == ".." :
        absolute_csv_path = (project_root / INSTAPAPER_CSV_FILE.name).resolve()
        if absolute_csv_path.exists():
            csv_to_read = absolute_csv_path
            log.info(f"Adjusted INSTAPAPER_CSV_FILE to absolute path: {csv_to_read}")
        else:
            csv_to_read = project_root / INSTAPAPER_CSV_FILE # Try original relative to project root
            log.warning(f"Could not find CSV at {absolute_csv_path}, trying {csv_to_read}")
    elif INSTAPAPER_CSV_FILE.is_absolute():
        csv_to_read = INSTAPAPER_CSV_FILE
    else: # if path is like "my_csv.csv" (relative to where script is called from or project root)
        csv_to_read = project_root / INSTAPAPER_CSV_FILE

    if not csv_to_read.exists():
        log.error(f"Instapaper CSV file not found at the determined path: {csv_to_read}")
        log.error("Please ensure INSTAPAPER_CSV_FILE is set correctly in your .env file or the script.")
        return
    log.info(f"Reading Instapaper CSV from: {csv_to_read}")

    if not BULK_MANIFEST_FILE.exists():
        log.warning(f"Manifest file {BULK_MANIFEST_FILE} not found. Cannot determine processing status.")
        log.warning("If you haven't run the bulk import script yet, this is expected.")
        log.warning("Otherwise, all articles from the CSV will be considered 'pending'.")
        manifest_data = {}
    else:
        try:
            manifest_data = json.loads(BULK_MANIFEST_FILE.read_text())
            log.info(f"Loaded {len(manifest_data)} entries from manifest file: {BULK_MANIFEST_FILE}")
        except json.JSONDecodeError as e:
            log.error(f"Error decoding JSON from manifest file {BULK_MANIFEST_FILE}: {e}")
            return

    archived_articles_from_csv = {}
    try:
        with open(csv_to_read, mode='r', encoding='utf-8') as infile:
            reader = csv.DictReader(infile)
            for row in reader:
                try:
                    bid = row.get("ID", "").strip()
                    if not bid:
                        continue # Skip rows without an ID

                    # Consider only archived articles for this check, adjust if needed
                    is_archived = row.get("Archived", "0").strip().lower() in ['1', 'true']
                    if is_archived:
                        archived_articles_from_csv[bid] = {
                            "title": row.get("Title", "Untitled").strip(),
                            "url": row.get("URL", "").strip()
                        }
                except Exception as e:
                    log.error(f"Error processing a row from CSV: {row}. Error: {e}")
                    continue
        log.info(f"Found {len(archived_articles_from_csv)} archived articles in the CSV file.")
    except FileNotFoundError:
        log.error(f"Could not find the CSV file at {csv_to_read}")
        return
    except Exception as e:
        log.error(f"An error occurred while reading the CSV file {csv_to_read}: {e}")
        return

    pending_articles = []
    failed_in_manifest_articles = []

    for bid_str, article_data in archived_articles_from_csv.items():
        if bid_str not in manifest_data:
            pending_articles.append({
                "bookmark_id": bid_str,
                "title": article_data["title"],
                "url": article_data["url"],
                "status": "pending_processing",
                "reason": "Not found in manifest (script may not have reached it, or a non-manifested error like file write failure occurred)"
            })
        else:
            manifest_entry = manifest_data[bid_str]
            status = manifest_entry.get("status", "unknown")
            if status not in ["success", "success_migrated"]:
                failed_in_manifest_articles.append({
                    "bookmark_id": bid_str,
                    "title": article_data["title"],
                    "url": article_data["url"],
                    "status": status,
                    "reason": manifest_entry.get("error_message", "No error message recorded.")
                })

    log.info(f"Found {len(pending_articles)} archived articles pending processing (not in manifest)." )
    log.info(f"Found {len(failed_in_manifest_articles)} archived articles in manifest with a non-success status.")

    # --- Added: Summarize failure types ---
    if failed_in_manifest_articles:
        log.info("--- Failure Summary from Manifest ---")
        failure_type_counts = {}
        for article in failed_in_manifest_articles:
            status = article.get("status", "unknown_status_in_manifest")
            failure_type_counts[status] = failure_type_counts.get(status, 0) + 1

        if failure_type_counts:
            for status_type, count in failure_type_counts.items():
                log.info(f"  - Status '{status_type}': {count} articles")
        else:
            # This case should ideally not be hit if failed_in_manifest_articles is populated,
            # but as a safeguard for empty/malformed status entries.
            log.info("  No specific failure statuses found to summarize, though non-success entries exist.")
        log.info("  (Refer to the output CSV for detailed error messages per article)")
        log.info("-----------------------------------")
    # --- End Added ---

    if not pending_articles and not failed_in_manifest_articles:
        log.info("No pending or failed articles found. All archived articles from CSV appear to be successfully processed or logged with a failure in the manifest.")
        return

    output_filename = project_root / f"{OUTPUT_CSV_FILENAME_PREFIX}{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.csv"
    try:
        with open(output_filename, 'w', newline='', encoding='utf-8') as outfile:
            fieldnames = ["bookmark_id", "title", "url", "status", "reason"]
            writer = csv.DictWriter(outfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(pending_articles)
            writer.writerows(failed_in_manifest_articles)
        log.info(f"Report written to: {output_filename}")
    except Exception as e:
        log.error(f"Failed to write output CSV to {output_filename}: {e}")

if __name__ == "__main__":
    main()