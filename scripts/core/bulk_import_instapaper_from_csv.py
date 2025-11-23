#!/usr/bin/env python3
# bulk_import_instapaper_from_csv.py
# Script to bulk import Instapaper articles from a CSV export.
# - Reads article metadata from CSV.
# - Fetches full text from Instapaper API for archived articles.
# - Converts to Markdown.
# - Saves with rich frontmatter.
# - Idempotent using a manifest file.

import os
import time
import json
import logging
import csv
from pathlib import Path
from datetime import datetime
import requests
from requests_oauthlib import OAuth1Session
from markdownify import markdownify as md
from dotenv import load_dotenv

load_dotenv() # Load variables from .env file

# ── CONFIG ─────────────────────────────────────────────────────────────────────
CONSUMER_KEY    = os.getenv("INSTAPAPER_CONSUMER_KEY")
CONSUMER_SECRET = os.getenv("INSTAPAPER_CONSUMER_SECRET")
USERNAME        = os.getenv("INSTAPAPER_USERNAME")
PASSWORD        = os.getenv("INSTAPAPER_PASSWORD")
VAULT_PATH      = Path(os.getenv("INSTAPAPER_VAULT_PATH", Path.home()/"Obsidian"/"Vault"/"Instapaper"/"Archived"))
CSV_EXPORT_FILE = Path(os.getenv("INSTAPAPER_CSV_FILE", "../2025-05-12-instapaper-export-bookmarks.csv")) # Assumes script is in 'scripts' subdir
BULK_MANIFEST_FILE = Path.home()/".instapaper_bulk_import_manifest.json"

API_BASE        = "https://www.instapaper.com/api/1"
RATE_DELAY      = float(os.getenv("INSTAPAPER_RATE_DELAY", 1.0))
MAX_RETRIES     = int(os.getenv("INSTAPAPER_MAX_RETRIES", 5))
BACKOFF_FACTOR  = int(os.getenv("INSTAPAPER_BACKOFF_FACTOR", 2))

# ── SETUP ──────────────────────────────────────────────────────────────────────
for var, val in [
    ("INSTAPAPER_CONSUMER_KEY",    CONSUMER_KEY),
    ("INSTAPAPER_CONSUMER_SECRET", CONSUMER_SECRET),
    ("INSTAPAPER_USERNAME",        USERNAME),
    ("INSTAPAPER_PASSWORD",        PASSWORD),
]:
    if not val:
        raise RuntimeError(f"Environment variable {var} is not set")

VAULT_PATH.mkdir(parents=True, exist_ok=True)
logging.basicConfig(level=logging.DEBUG,
                    format="%(asctime)s %(levelname)-8s %(message)s")
log = logging.getLogger("InstapaperBulkImport")

# ── MANIFEST ──────────────────────────────────────────────────────────────────
def load_manifest():
    log.info(f"Attempting to load manifest from {BULK_MANIFEST_FILE}")
    # Manifest is now a dictionary: { "bookmark_id_str": {"status": "...", "error_message": "..."} }
    manifest_data = json.loads(BULK_MANIFEST_FILE.read_text()) if BULK_MANIFEST_FILE.exists() else {}
    log.info(f"Loaded {len(manifest_data)} processed bookmark entries from manifest.")
    return manifest_data # Return the whole dict

def save_manifest(manifest_data):
    log.info(f"Saving {len(manifest_data)} processed bookmark entries to {BULK_MANIFEST_FILE}")
    BULK_MANIFEST_FILE.write_text(json.dumps(manifest_data, indent=4))

# ── OAUTH FLOW ────────────────────────────────────────────────────────────────
def get_oauth_session():
    """Perform xAuth to get access token and return a signed session."""
    log.info("Initiating OAuth 1.0a xAuth flow...")
    oauth = OAuth1Session(CONSUMER_KEY, client_secret=CONSUMER_SECRET)
    resp = oauth.post(f"{API_BASE}/oauth/access_token", data={
        "x_auth_username": USERNAME,
        "x_auth_password": PASSWORD,
        "x_auth_mode": "client_auth"
    })
    resp.raise_for_status()
    creds = dict(pair.split("=") for pair in resp.text.split("&"))
    log.info("OAuth successful. Creating signed session.")
    return OAuth1Session(
        CONSUMER_KEY,
        client_secret=CONSUMER_SECRET,
        resource_owner_key=creds["oauth_token"],
        resource_owner_secret=creds["oauth_token_secret"],
        signature_method="HMAC-SHA1"
    )

# ── API HELPERS ───────────────────────────────────────────────────────────────
def retry_request_html(sess, url, **kwargs):
    """Retry wrapper specifically for endpoints returning HTML on success (like get_text)."""
    delay = 1
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = sess.post(url, **kwargs)
            log.info(f"HTML Request Status: {resp.status_code} for {url}")
            log.debug(f"HTML Request Headers: {resp.headers}")

            if resp.status_code == 200:
                log.debug(f"HTML Response Text (first 500 chars): {resp.text[:500]}...")
                return resp.text

            resp.raise_for_status()

            log.error(f"Unexpected non-200 status code ({resp.status_code}) without HTTPError for {url}.")
            last_error = requests.exceptions.RequestException(f"Unexpected status {resp.status_code}")
            raise last_error

        except requests.exceptions.RequestException as e:
            last_error = e
            should_retry = False
            if isinstance(e, requests.exceptions.HTTPError) and e.response is not None:
                if e.response.status_code == 503:
                    log.warning(f"HTTP 503 error detected for {url}.")
                    should_retry = True
                else:
                    try:
                        error_data = e.response.json()
                        if isinstance(error_data, list) and len(error_data) > 0 and isinstance(error_data[0], dict) and error_data[0].get("type") == "error":
                            err_code = error_data[0].get('error_code', 'N/A')
                            message = error_data[0].get('message', 'No message')
                            log.error(f"Instapaper API Error ({err_code}) on HTML request to {url}: {message}")
                            e.args = (f"Instapaper API Error {err_code}: {message} (HTTP {e.response.status_code})",)
                        else:
                            log.error(f"Non-503 HTTP error ({e.response.status_code}) for {url} with unexpected JSON content: {e.response.text[:200]}...")
                    except requests.exceptions.JSONDecodeError:
                        log.error(f"Non-503/non-JSON HTTP error ({e.response.status_code}) for {url}: {e.response.text[:200]}...")
                    should_retry = False
            elif isinstance(e, (requests.exceptions.ConnectionError, requests.exceptions.Timeout)):
                log.warning(f"Network error detected for {url}: {type(e).__name__}")
                should_retry = True

            if not should_retry or attempt == MAX_RETRIES:
                log.error(f"Non-retryable error or max retries ({MAX_RETRIES}) hit for {url}: {e}")
                raise last_error

            log.warning(f"Transient error for {url} ({type(e).__name__}); retry #{attempt}/{MAX_RETRIES} in {delay}s")
            time.sleep(delay)
            delay *= BACKOFF_FACTOR
    raise last_error or RuntimeError(f"Retry loop completed without success for {url}")

def fetch_full_text(sess, bid):
    """Call /bookmarks/get_text to retrieve reading-optimized HTML."""
    log.info(f"Fetching full text for bookmark {bid}...")
    try:
        html_content = retry_request_html(sess, f"{API_BASE}/bookmarks/get_text",
                                          data={"bookmark_id": bid})
        log.info(f"Fetched content length: {len(html_content)} chars for bookmark {bid}")
        return html_content, None # Return content and no error
    except Exception as e:
        log.error(f"Failed to fetch full text for bookmark {bid} after retries: {e}")
        return "", str(e) # Return empty string and the error message

# ── CSV PROCESSING ─────────────────────────────────────────────────────────────
def parse_csv_datetime(datetime_str, column_name, bid):
    """Parses date strings from CSV, returns datetime object or None."""
    if not datetime_str: # Handles None from row.get() or initial empty string
        log.debug(f"Date/time string is initially None or empty for {column_name}, bookmark {bid}")
        return None

    dt_str = str(datetime_str).strip() # Convert to string just in case, then strip whitespace

    if not dt_str: # Check again after stripping if it became an empty string
        log.debug(f"Date/time string is empty after stripping for {column_name}, bookmark {bid}")
        return None

    # List of formats to try in order of expected likelihood or specificity
    formats_to_try = [
        '%m/%d/%y %H:%M',           # e.g., '10/11/10 5:38' or '10/14/10 21:50' (2-digit year, 24hr)
        '%m/%d/%Y %I:%M:%S %p',     # e.g., '4/15/2023 12:06:54 PM' (4-digit year, 12hr + AM/PM)
        '%m/%d/%y %I:%M %p',        # e.g., '10/11/10 5:38 PM' (2-digit year, 12hr + AM/PM)
        '%Y-%m-%d %H:%M:%S',        # e.g., '2023-04-15 12:06:54' (ISO-like 24hr)
        '%Y-%m-%d %H:%M',           # e.g., '2023-04-15 12:06' (ISO-like 24hr, no seconds)
        '%m/%d/%Y %H:%M',           # e.g., '04/15/2023 12:06' (4-digit year, 24hr)
    ]

    for fmt in formats_to_try:
        try:
            parsed_dt = datetime.strptime(dt_str, fmt)
            log.debug(f"Successfully parsed {column_name} string '{dt_str}' with format '{fmt}' for bookmark {bid}.")
            return parsed_dt
        except ValueError:
            log.debug(f"Failed to parse {column_name} string '{dt_str}' with format '{fmt}' for bookmark {bid}.")
            continue # Try next format

    # If all formats failed
    log.warning(f"Could not parse {column_name} string '{dt_str}' with any of the known formats for bookmark {bid}. Original value from CSV was: '{datetime_str}'. Skipping this date.")
    return None

def load_archived_bookmarks_from_csv():
    """Loads and filters archived bookmarks from the CSV file."""
    bookmarks_to_process = []
    csv_file_path = CSV_EXPORT_FILE
    if not csv_file_path.exists():
        log.error(f"CSV file not found at {csv_file_path}. Please check INSTAPAPER_CSV_FILE env var or path.")
        return bookmarks_to_process

    log.info(f"Loading bookmarks from CSV: {csv_file_path}")
    try:
        with open(csv_file_path, mode='r', encoding='utf-8') as infile:
            reader = csv.DictReader(infile)
            for row_num, row in enumerate(reader, 1):
                try:
                    bid = int(row.get("ID", "").strip())
                    if not bid:
                        log.warning(f"CSV row {row_num} missing ID. Skipping.")
                        continue

                    # Check if archived (common values: '1', 'true')
                    is_archived = row.get("Archived", "0").strip().lower() in ['1', 'true']
                    if not is_archived:
                        continue

                    title = row.get("Title", "Untitled").strip()
                    url = row.get("URL", "").strip()
                    if not url:
                        log.warning(f"Bookmark {bid} ('{title}') is missing 'URL' in CSV. Will still process.")

                    description = row.get("Description", "").strip()
                    author = row.get("Author", "").strip()
                    words_str = row.get("Words", "0").strip()
                    try:
                        words = int(words_str) if words_str else 0
                    except ValueError:
                        log.warning(f"Could not parse 'Words' ('{words_str}') for bookmark {bid} as int. Defaulting to 0.")
                        words = 0

                    folder = row.get("Folder", "Unknown").strip()

                    # Date parsing
                    saved_time_dt = parse_csv_datetime(row.get("Saved Time"), "Saved Time", bid)
                    published_time_dt = parse_csv_datetime(row.get("Published Time"), "Published Time", bid)
                    archived_time_dt = parse_csv_datetime(row.get("Archived Time"), "Archived Time", bid)

                    bookmarks_to_process.append({
                        "id": bid,
                        "title": title,
                        "url": url,
                        "description": description,
                        "author": author,
                        "words": words,
                        "folder": folder,
                        "saved_time_dt": saved_time_dt,
                        "published_time_dt": published_time_dt,
                        "archived_time_dt": archived_time_dt,
                    })
                except Exception as e:
                    log.error(f"Error processing CSV row {row_num}: {row}. Error: {e}")
                    continue # Skip malformed row
        log.info(f"Loaded {len(bookmarks_to_process)} archived bookmarks from CSV.")
    except FileNotFoundError:
        log.error(f"CSV file not found: {csv_file_path}")
    except Exception as e:
        log.error(f"Failed to read or process CSV {csv_file_path}: {e}")
    return bookmarks_to_process

# ── MAIN EXPORT LOOP ───────────────────────────────────────────────────────────
def sanitize_title(t):
    return "".join(c for c in t if c not in r'<>:"/\|?*').strip()

def main():
    log.info("Starting Instapaper bulk import from CSV...")
    sess = get_oauth_session()
    # processed_manifest is now a dictionary, keys are stringified BIDs
    processed_manifest = load_manifest()

    archived_bookmarks = load_archived_bookmarks_from_csv()
    if not archived_bookmarks:
        log.info("No archived bookmarks found in CSV or CSV not processed. Exiting.")
        return

    # Get a set of stringified BIDs that are already in the manifest for quick lookup
    processed_bids_set = set(processed_manifest.keys())
    total_to_process = len([bm for bm in archived_bookmarks if str(bm["id"]) not in processed_bids_set])
    log.info(f"Found {len(archived_bookmarks)} archived articles in CSV. {total_to_process} new articles to process (or retry if failed differently before).")

    count = 0
    processed_in_session = 0

    for bm_data in archived_bookmarks:
        bid = bm_data["id"]
        bid_str = str(bid) # Use string version for manifest keys

        if bid_str in processed_manifest:
            # We could add logic here to retry if the previous status was a failure type we want to retry
            # For now, if it's in manifest, we skip (as per original idempotency for simple success)
            # Or, more robustly, check status if we want to retry specific failures:
            # manifest_entry = processed_manifest[bid_str]
            # if manifest_entry.get("status") == "success":
            log.info(f"Skipping already processed/logged bookmark {bid} ('{bm_data['title']}'). Current status: {processed_manifest[bid_str].get('status', 'unknown')}")
            continue

        log.info(f"Processing bookmark {bid}: '{bm_data['title']}'")

        html_content, fetch_error_msg = fetch_full_text(sess, bid)

        if not html_content:
            log.warning(f"No content fetched for bookmark {bid}. Skipping file creation.")
            processed_manifest[bid_str] = {
                "status": "text_fetch_failed",
                "title": bm_data["title"],
                "error_message": fetch_error_msg or "No HTML content returned, no specific error captured."
            }
            processed_in_session += 1
            if processed_in_session % 50 == 0:
                save_manifest(processed_manifest)
            continue

        log.info(f"Converting HTML to Markdown for bookmark {bid}...")
        try:
            md_content = md(html_content, heading_style="ATX")
        except Exception as e:
            log.error(f"Error converting HTML to Markdown for bookmark {bid}: {e}. Skipping.")
            # Mark as processed with error to avoid retrying faulty conversion
            processed_manifest[bid_str] = {
                "status": "markdown_conversion_failed",
                "title": bm_data["title"],
                "error_message": str(e)
            }
            processed_in_session +=1
            continue

        # Prepare frontmatter
        escaped_title = bm_data["title"].replace('"', '\\"')

        # Date for filename: Prioritize Archived Time, then Saved Time
        filename_date_str = "YYYY-MM-DD_unknown_date" # Default
        if bm_data["archived_time_dt"] is not None:
            filename_date_str = bm_data["archived_time_dt"].strftime("%Y-%m-%d")
        elif bm_data["saved_time_dt"] is not None: # Fallback if saved_time_dt is available and archived_time_dt was None
            filename_date_str = bm_data["saved_time_dt"].strftime("%Y-%m-%d")
        else: # Fallback if neither parsed archived_time_dt nor saved_time_dt is available
            log.warning(f"Bookmark {bid} missing successfully parsed 'Archived Time' and 'Saved Time' for filename. Using generic date: {filename_date_str}")
            # The filename_date_str remains "YYYY-MM-DD_unknown_date"

        frontmatter = ["---"]
        frontmatter.append(f'title: "{escaped_title}"')
        frontmatter.append(f'original_url: "{bm_data["url"]}"')
        frontmatter.append(f"instapaper_id: {bid}")

        # Date saved (date part of Saved Time)
        if bm_data["saved_time_dt"]:
            frontmatter.append(f'date_saved: {bm_data["saved_time_dt"].strftime("%Y-%m-%d")}')

        # Saved Time (full timestamp) - NEW
        if bm_data["saved_time_dt"]:
            frontmatter.append(f'saved_time: {bm_data["saved_time_dt"].strftime("%Y-%m-%d %H:%M:%S")}')

        if bm_data["description"]:
            desc_text = bm_data["description"]
            # The string literal '\\"' results in a string containing: backslash, quote (\")
            # This is the correct replacement for YAML escaping of quotes.
            escaped_desc = desc_text.replace('"', '\\"')
            frontmatter.append(f'description: "{escaped_desc}"')
        if bm_data["author"]:
            author_text = bm_data["author"]
            escaped_author = author_text.replace('"', '\\"')
            frontmatter.append(f'author: "{escaped_author}"')
        if bm_data["words"]: # words can be 0, so check if it exists (non-empty string originally)
             frontmatter.append(f"words: {bm_data['words']}")
        if bm_data["published_time_dt"]:
            frontmatter.append(f'published_time: {bm_data["published_time_dt"].strftime("%Y-%m-%d %H:%M:%S")}')

        # Archived Time (full timestamp) - MODIFIED format
        if bm_data["archived_time_dt"]:
            frontmatter.append(f'archived_time: {bm_data["archived_time_dt"].strftime("%Y-%m-%d %H:%M:%S")}')

        # Date archived (date part of Archived Time) - NEW
        if bm_data["archived_time_dt"]:
            frontmatter.append(f'date_archived: {bm_data["archived_time_dt"].strftime("%Y-%m-%d")}')

        if bm_data["folder"]:
            folder_text = bm_data["folder"]
            escaped_folder = folder_text.replace('"', '\\"')
            frontmatter.append(f'folder: "{escaped_folder}"')
        frontmatter.append("---")
        frontmatter.append("") # Newline after frontmatter

        # File saving
        safe_title = sanitize_title(bm_data["title"])[:80]
        file_name = f"{filename_date_str} – {safe_title}.md"
        output_file_path = VAULT_PATH / file_name

        log.info(f"Writing Markdown to: {output_file_path}")
        try:
            with open(output_file_path, "w", encoding="utf-8") as f:
                f.write("\n".join(frontmatter) + md_content)
        except Exception as e:
            log.error(f"Failed to write file {output_file_path} for bookmark {bid}: {e}")
            # Do not add to processed_manifest here for file write errors, so it can be retried
            continue

        processed_manifest[bid_str] = {"status": "success", "title": bm_data["title"], "file_path": str(output_file_path)}
        count += 1
        processed_in_session += 1
        log.info(f"Successfully processed and saved bookmark {bid}. Total new this session: {count}. Sleeping for {RATE_DELAY}s...")
        time.sleep(RATE_DELAY)

        if processed_in_session % 20 == 0: # Save manifest every 20 successful items
            save_manifest(processed_manifest)
            log.info(f"Intermediate manifest saved. Processed {processed_in_session} items so far in this session.")

    log.info("Bulk import loop finished.")
    save_manifest(processed_manifest)
    log.info(f"Bulk import complete: {count} total new files added in this session. Manifest contains {len(processed_manifest)} entries.")

if __name__ == "__main__":
    # Adjust CSV_EXPORT_FILE path if script is not in a 'scripts' subdirectory
    # For example, if script is in project root alongside CSV:
    # CSV_EXPORT_FILE = Path("2025-05-12-instapaper-export-bookmarks.csv")
    # Ensure your .env file is in the same directory as this script, or adjust load_dotenv()
    # Or ensure INSTAPAPER_CSV_FILE is set in your environment correctly.

    # Correct CSV_EXPORT_FILE path based on its actual location relative to this script
    # If this script is in /scripts/ and CSV is in project root:
    script_dir = Path(__file__).parent.resolve()
    project_root = script_dir.parent
    potential_csv_path = project_root / CSV_EXPORT_FILE.name # Use just the name from default

    # Logging for CSV path is deferred to main() after logging is configured.
    # This __main__ block will now only set up CSV_EXPORT_FILE path.

    if potential_csv_path.exists():
        CSV_EXPORT_FILE = potential_csv_path
        log.info(f"Adjusted CSV_EXPORT_FILE path to: {CSV_EXPORT_FILE}")
    elif CSV_EXPORT_FILE.is_absolute() and CSV_EXPORT_FILE.exists():
        log.info(f"Using absolute CSV_EXPORT_FILE path from env or default: {CSV_EXPORT_FILE}")
    elif (script_dir / CSV_EXPORT_FILE).exists(): # Check relative to script dir itself
        CSV_EXPORT_FILE = script_dir / CSV_EXPORT_FILE
        log.info(f"Using CSV_EXPORT_FILE relative to script dir: {CSV_EXPORT_FILE}")
    else:
        resolved_path_str = str(CSV_EXPORT_FILE.resolve() if not CSV_EXPORT_FILE.is_absolute() else CSV_EXPORT_FILE)
        log.warning(f"CSV_EXPORT_FILE default path {CSV_EXPORT_FILE} may not be correct. "
                    f"Attempting to use it. Effective path: {resolved_path_str}")

    main()