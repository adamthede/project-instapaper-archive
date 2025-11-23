#!/usr/bin/env python3
# find_missing_markdown_articles.py
# Compares the main Instapaper CSV export with Markdown files in the vault
# to identify articles that are archived in the CSV but do not have a
# corresponding .md file. It then removes these entries from the main bulk import manifest
# to allow them to be reprocessed.

import os
import csv
import json # Added for manifest operations
import shutil # Added for manifest backup
from pathlib import Path
from datetime import datetime
import logging
from dotenv import load_dotenv

load_dotenv()

# --- Configuration (should match bulk_import_instapaper_from_csv.py defaults or use .env)
INSTAPAPER_CSV_FILE_ENV = os.getenv("INSTAPAPER_CSV_FILE")
INSTAPAPER_VAULT_PATH_ENV = os.getenv("INSTAPAPER_VAULT_PATH")
# Path to the manifest file, consistent with other scripts
BULK_MANIFEST_FILE_ENV = os.getenv("INSTAPAPER_BULK_MANIFEST_FILE", Path.home() / ".instapaper_bulk_import_manifest.json")


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
log = logging.getLogger("FindAndRemediateMissingMarkdown")

# --- Helper functions (copied and adapted from bulk_import_instapaper_from_csv.py for consistency) ---
def parse_csv_datetime(datetime_str, column_name, bid_for_log):
    """Parses date strings from CSV, returns datetime object or None."""
    if not datetime_str:
        log.debug(f"Date/time string is initially None or empty for {column_name}, bookmark {bid_for_log}")
        return None
    dt_str = str(datetime_str).strip()
    if not dt_str:
        log.debug(f"Date/time string is empty after stripping for {column_name}, bookmark {bid_for_log}")
        return None
    formats_to_try = [
        '%m/%d/%y %H:%M',
        '%m/%d/%Y %I:%M:%S %p',
        '%m/%d/%y %I:%M %p',
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%d %H:%M',
        '%m/%d/%Y %H:%M',
    ]
    for fmt in formats_to_try:
        try:
            return datetime.strptime(dt_str, fmt)
        except ValueError:
            log.debug(f"Failed to parse {column_name} string '{dt_str}' with format '{fmt}' for bookmark {bid_for_log}.")
            continue
    log.warning(f"Could not parse {column_name} string '{dt_str}' for bookmark {bid_for_log}. Original: '{datetime_str}'.")
    return None

def sanitize_title(t):
    """Sanitizes a title string to be safe for filenames."""
    return "".join(c for c in t if c not in r'<>:"/\|?*').strip()
# --- End Helper functions ---

def find_project_root(marker_file=".env"):
    """Find the project root by looking for a marker file or common directory."""
    current_path = Path(__file__).resolve().parent
    # Try to find a directory that contains 'scripts' or the marker_file
    for _ in range(5): # Limit search depth
        if (current_path / marker_file).exists() or (current_path / "scripts").is_dir() and (current_path / "scripts" / Path(__file__).name).exists():
             # If 'scripts' dir exists and this script is in it, project_root is its parent
            if (current_path / "scripts" / Path(__file__).name).exists() and (current_path / "scripts").is_dir():
                # Check if current_path is actually the scripts directory itself
                if current_path.name == "scripts":
                    return current_path.parent
                return current_path # current_path is the project root containing scripts/
            # If marker file is found, assume current_path is project root
            if (current_path / marker_file).exists():
                return current_path

        if current_path.parent == current_path: # Reached filesystem root
            break
        current_path = current_path.parent

    # Fallback if the above logic doesn't pinpoint it well, especially if .env is not at true root
    # Check if this script is in a 'scripts' subdirectory of cwd
    cwd_scripts = Path.cwd() / "scripts"
    if cwd_scripts.is_dir() and (cwd_scripts / Path(__file__).name).exists():
        log.warning(f"Could not reliably find project root by marker, falling back to CWD assuming it's project root: {Path.cwd()}")
        return Path.cwd()

    # Last fallback: directory of this script
    log.warning(f"Could not reliably find project root, using script's parent directory: {Path(__file__).resolve().parent.parent}")
    return Path(__file__).resolve().parent.parent # Assuming script is in "scripts/"


def main():
    project_root = find_project_root()
    log.info(f"Determined project root: {project_root}")

    if not INSTAPAPER_CSV_FILE_ENV:
        log.error("INSTAPAPER_CSV_FILE not set in .env. Cannot locate the main CSV export.")
        return
    if not INSTAPAPER_VAULT_PATH_ENV:
        log.error("INSTAPAPER_VAULT_PATH not set in .env. Cannot locate the Markdown vault.")
        return

    main_csv_path = Path(INSTAPAPER_CSV_FILE_ENV)
    if not main_csv_path.is_absolute():
        # Prefer resolving relative to project_root if it's not an "up-directory" path
        if main_csv_path.parts[0] != "..":
            test_path = (project_root / main_csv_path).resolve()
            if test_path.exists():
                main_csv_path = test_path
                log.info(f"Resolved INSTAPAPER_CSV_FILE relative to project root: {main_csv_path}")
            else: # Fallback to script dir relative path if project root relative fails
                script_dir_relative_path = (Path(__file__).resolve().parent / main_csv_path).resolve()
                if script_dir_relative_path.exists():
                    main_csv_path = script_dir_relative_path
                    log.info(f"Resolved INSTAPAPER_CSV_FILE relative to script directory: {main_csv_path}")
                else:
                    log.error(f"Could not resolve INSTAPAPER_CSV_FILE. Tried {test_path} and {script_dir_relative_path}")
                    return
        else: # For "../" paths, resolve relative to script directory's parent (likely project root)
            script_parent_relative_path = (Path(__file__).resolve().parent.parent / main_csv_path.name).resolve()
            if script_parent_relative_path.exists():
                main_csv_path = script_parent_relative_path
                log.info(f"Resolved INSTAPAPER_CSV_FILE (e.g., '../{main_csv_path.name}') to: {main_csv_path}")
            else:
                log.error(f"Could not find main CSV using path {INSTAPAPER_CSV_FILE_ENV}. Tried: {script_parent_relative_path}")
                return

    if not main_csv_path.exists():
        log.error(f"Main Instapaper CSV file not found at determined path: {main_csv_path}")
        return

    vault_path = Path(INSTAPAPER_VAULT_PATH_ENV)
    if not vault_path.is_dir():
        log.error(f"Instapaper vault path is not a directory or does not exist: {vault_path}")
        return

    bulk_manifest_file_path = Path(BULK_MANIFEST_FILE_ENV) # Already resolved by Path.home() or absolute from env

    log.info(f"Reading main Instapaper CSV from: {main_csv_path}")
    log.info(f"Checking for Markdown files in vault: {vault_path}")
    log.info(f"Will operate on manifest file: {bulk_manifest_file_path}")

    try:
        existing_md_files = {f.name for f in vault_path.glob("*.md")}
        log.info(f"Found {len(existing_md_files)} Markdown files in the vault.")
    except Exception as e:
        log.error(f"Error listing files in vault path {vault_path}: {e}")
        return

    missing_article_bids = [] # Store just the BIDs of missing articles

    try:
        with open(main_csv_path, mode='r', encoding='utf-8') as infile:
            reader = csv.DictReader(infile)
            processed_rows_count = 0
            for row_num, row in enumerate(reader, 1):
                processed_rows_count = row_num
                try:
                    bid = row.get("ID", "").strip()
                    if not bid:
                        log.warning(f"CSV row {row_num} missing ID. Skipping.")
                        continue

                    is_archived = row.get("Archived", "0").strip().lower() in ['1', 'true']
                    if not is_archived:
                        continue

                    title = row.get("Title", "Untitled").strip()

                    archived_time_dt = parse_csv_datetime(row.get("Archived Time"), "Archived Time", bid)
                    saved_time_dt = parse_csv_datetime(row.get("Saved Time"), "Saved Time", bid)

                    filename_date_str = "YYYY-MM-DD_unknown_date"
                    if archived_time_dt:
                        filename_date_str = archived_time_dt.strftime("%Y-%m-%d")
                    elif saved_time_dt:
                        filename_date_str = saved_time_dt.strftime("%Y-%m-%d")
                    else:
                        log.debug(f"Bookmark {bid} ('{title}') missing parseable 'Archived Time' and 'Saved Time' for filename. Using default date for check.")

                    safe_title = sanitize_title(title)[:80]
                    expected_filename = f"{filename_date_str} – {safe_title}.md"

                    if expected_filename not in existing_md_files:
                        log.debug(f"Identified missing MD file for BID {bid}: '{title}'. Expected filename: '{expected_filename}'")
                        missing_article_bids.append(bid) # Add just the BID

                except Exception as e:
                    log.error(f"Error processing CSV row {row_num} (ID: {row.get('ID','N/A')}): {row}. Error: {e}")
                    continue
        log.info(f"Processed {processed_rows_count} rows from CSV.")

    except FileNotFoundError:
        log.error(f"Could not find the main CSV file at {main_csv_path}")
        return
    except Exception as e:
        log.error(f"An error occurred while reading the main CSV file {main_csv_path}: {e}")
        return

    if not missing_article_bids:
        log.info("No missing Markdown files found for archived articles. Vault seems up-to-date with the CSV. Manifest will not be changed.")
        return

    log.info(f"Found {len(missing_article_bids)} archived articles from CSV that appear to be missing Markdown files in the vault.")
    log.info(f"Proceeding to remove these entries from the manifest: {bulk_manifest_file_path}")

    # Load the manifest
    if not bulk_manifest_file_path.exists():
        log.warning(f"Manifest file {bulk_manifest_file_path} not found. Cannot remove entries. Please run bulk import script first to create it.")
        return

    try:
        manifest_data = json.loads(bulk_manifest_file_path.read_text())
        log.info(f"Successfully loaded manifest with {len(manifest_data)} entries.")
    except json.JSONDecodeError as e:
        log.error(f"Error decoding JSON from manifest file {bulk_manifest_file_path}: {e}. Cannot proceed.")
        return
    except Exception as e:
        log.error(f"Could not read manifest file {bulk_manifest_file_path}: {e}. Cannot proceed.")
        return

    # Back up the old manifest file
    backup_file_name = bulk_manifest_file_path.parent / f"{bulk_manifest_file_path.name}.missing_removed_{datetime.now().strftime('%Y%m%d_%H%M%S')}.bak"
    try:
        shutil.copy2(bulk_manifest_file_path, backup_file_name)
        log.info(f"Backed up current manifest to: {backup_file_name}")
    except Exception as e:
        log.error(f"Could not back up manifest file: {e}. Halting to prevent data loss.")
        return

    # Remove missing article entries from manifest
    removed_count = 0
    for bid_to_remove in missing_article_bids:
        bid_str_to_remove = str(bid_to_remove).strip() # Ensure it's string and stripped
        if bid_str_to_remove in manifest_data:
            del manifest_data[bid_str_to_remove]
            removed_count += 1
            log.debug(f"Removed BID {bid_str_to_remove} from manifest.")

    log.info(f"Removed {removed_count} entries from the manifest corresponding to missing Markdown files.")
    if removed_count != len(missing_article_bids):
        log.warning(f"Mismatch: Identified {len(missing_article_bids)} missing files, but only {removed_count} corresponding entries were found and removed from manifest. Some might have already been absent.")

    # Save the new manifest data
    try:
        bulk_manifest_file_path.write_text(json.dumps(manifest_data, indent=4))
        log.info(f"Successfully saved updated manifest data (with {len(manifest_data)} entries) to: {bulk_manifest_file_path}")
        log.info("Remediation complete. You can now re-run scripts/bulk_import_instapaper_from_csv.py (using your *main* CSV file).")
        log.info("It will attempt to process the articles whose entries were just removed from this manifest.")
    except Exception as e:
        log.error(f"Could not write updated manifest data: {e}")
        log.error(f"Your original manifest (before these removals) is backed up at {backup_file_name}.")

if __name__ == "__main__":
    main()