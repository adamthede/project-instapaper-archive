#!/usr/bin/env python3
# get_instapaper_stats.py
# Counts bookmarks in each Instapaper folder (built-in and custom).

import os
import time
import json
import logging
from pathlib import Path
import requests
from requests_oauthlib import OAuth1Session
from dotenv import load_dotenv

load_dotenv() # Load variables from .env file

# ── CONFIG ─────────────────────────────────────────────────────────────────────
CONSUMER_KEY    = os.getenv("INSTAPAPER_CONSUMER_KEY")
CONSUMER_SECRET = os.getenv("INSTAPAPER_CONSUMER_SECRET")
USERNAME        = os.getenv("INSTAPAPER_USERNAME")
PASSWORD        = os.getenv("INSTAPAPER_PASSWORD")
LOG_FILE        = Path.home() / "instapaper_stats.log"

API_BASE        = "https://www.instapaper.com/api/1"
MAX_LIMIT       = 500  # Max allowed by API for bookmarks/list
RATE_DELAY      = 1.0  # Delay between API calls
MAX_RETRIES     = 5
BACKOFF_FACTOR  = 2

# ── SETUP LOGGING ──────────────────────────────────────────────────────────────
# Console Handler (INFO level)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_formatter = logging.Formatter("%(asctime)s %(levelname)-8s %(message)s", datefmt='%Y-%m-%d %H:%M:%S')
console_handler.setFormatter(console_formatter)

# File Handler (DEBUG level)
try:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(LOG_FILE, mode='w', encoding='utf-8') # Overwrite log each run
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter("%(asctime)s %(levelname)-8s [%(funcName)s] %(message)s", datefmt='%Y-%m-%d %H:%M:%S')
    file_handler.setFormatter(file_formatter)
except Exception as e:
    logging.error(f"Failed to configure file logging to {LOG_FILE}: {e}")
    file_handler = None # Ensure it's None if setup fails

# Root logger configuration
log = logging.getLogger("InstapaperStats")
log.setLevel(logging.DEBUG) # Process all messages >= DEBUG
log.addHandler(console_handler)
if file_handler:
    log.addHandler(file_handler)

log.info(f"Logging DEBUG+ level to: {LOG_FILE}")
log.info(f"Logging INFO+ level to console.")


# ── CONFIG VALIDATION ──────────────────────────────────────────────────────────
for var, val in [
    ("INSTAPAPER_CONSUMER_KEY",    CONSUMER_KEY),
    ("INSTAPAPER_CONSUMER_SECRET", CONSUMER_SECRET),
    ("INSTAPAPER_USERNAME",        USERNAME),
    ("INSTAPAPER_PASSWORD",        PASSWORD),
]:
    if not val:
        log.error(f"Environment variable {var} is not set")
        raise RuntimeError(f"Environment variable {var} is not set")

# ── OAUTH FLOW (Copied from export script) ───────────────────────────────────
def get_oauth_session():
    """Perform xAuth to get access token and return a signed session."""
    log.info("Initiating OAuth 1.0a xAuth flow...")
    oauth = OAuth1Session(CONSUMER_KEY, client_secret=CONSUMER_SECRET)
    try:
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
    except requests.exceptions.RequestException as e:
        log.error(f"OAuth request failed: {e}")
        if hasattr(e, 'response') and e.response is not None:
            log.error(f"Response status: {e.response.status_code}")
            log.error(f"Response text: {e.response.text}")
        raise

# ── API HELPERS (Copied and adapted from export script) ───────────────────────
def retry_request(sess, url, **kwargs):
    """Retry wrapper for endpoints returning JSON (dict or list).

    Handles retries for network errors and 503s.
    Returns parsed JSON data.
    Raises exceptions on persistent errors or non-retryable API errors.
    """
    delay = 1
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        data = None
        try:
            resp = sess.post(url, **kwargs)
            log.info(f"API Request Status: {resp.status_code} for {url}")
            log.debug(f"API Request Headers: {resp.headers}")
            log.debug(f"API Response Text (first 500 chars): {resp.text[:500]}...")

            resp.raise_for_status() # Check for HTTP errors (4xx/5xx)

            try:
                data = resp.json()
            except requests.exceptions.JSONDecodeError as json_err:
                log.error(f"Failed to decode JSON response: {json_err}")
                log.error(f"Raw text was: {resp.text[:500]}...")
                last_error = json_err
                raise ValueError(f"JSON Decode Error: {json_err}") from json_err

            # Check for Instapaper API-level errors if response is a dictionary
            if isinstance(data, dict) and data.get('type') == 'error':
                error_code = data.get('error_code', 'N/A')
                message = data.get('message', 'No message')
                log.error(f"Instapaper API Error {error_code}: {message}")
                last_error = requests.exceptions.RequestException(f"Instapaper API Error {error_code}: {message}")
                raise last_error

            return data # Success (dict or list)

        except (requests.exceptions.RequestException, ValueError) as e:
            # Exclude the specific API error RequestException we raised above from retries
            if last_error is e and isinstance(e, requests.exceptions.RequestException) and "Instapaper API Error" in str(e):
                 log.error(f"Non-retryable Instapaper API Error encountered: {e}")
                 should_retry = False
            else:
                last_error = e
                should_retry = False
                if isinstance(e, requests.exceptions.HTTPError) and e.response is not None:
                    if e.response.status_code == 503:
                        log.warning(f"HTTP 503 error detected.")
                        should_retry = True
                elif isinstance(e, (requests.exceptions.ConnectionError, requests.exceptions.Timeout, ValueError)):
                    log.warning(f"Network/JSON error detected: {type(e).__name__}")
                    should_retry = True
                elif isinstance(e, requests.exceptions.RequestException):
                    log.warning(f"RequestException encountered: {type(e).__name__}. Not retrying by default.")
                    should_retry = False

            if not should_retry or attempt == MAX_RETRIES:
                log.error(f"Non-retryable error or max retries ({MAX_RETRIES}) hit for {url}: {e}")
                raise last_error if last_error else e

            log.warning(f"Transient error for {url} ({type(e).__name__}); retry #{attempt}/{MAX_RETRIES} in {delay}s")
            time.sleep(delay)
            delay *= BACKOFF_FACTOR

    raise last_error or RuntimeError(f"Retry loop completed without success for {url}")

def fetch_folders(sess):
    """Retrieve all user-created folders."""
    log.info("Fetching folder list...")
    try:
        data = retry_request(sess, f"{API_BASE}/folders/list")
        if not isinstance(data, list):
            log.error(f"Expected list from /folders/list, but got {type(data).__name__}")
            return {} # Return empty dict on error
        folders = {f["title"]: f["folder_id"] for f in data if isinstance(f, dict) and f.get("type") == "folder"}
        log.info(f"Found {len(folders)} user folders: {list(folders.keys())}")
        return folders
    except Exception as e:
        log.error(f"Failed to fetch folders: {e}")
        return {}

# ── COUNTING LOGIC ────────────────────────────────────────────────────────────
def count_bookmarks_in_folder(sess, folder_id):
    """Counts bookmarks in a specific folder using 'have'-based pagination."""
    log.info(f"Starting count for folder_id: {folder_id}")
    total_count = 0
    processed_ids = set()

    # For safety to prevent infinite loops
    safety_counter = 0
    MAX_SAFETY_LOOPS = 30  # Set a reasonable limit to prevent infinite loops

    while safety_counter < MAX_SAFETY_LOOPS:
        safety_counter += 1
        log.info(f"Requesting batch for folder {folder_id}. Current count: {total_count}, processed IDs: {len(processed_ids)}")

        payload = {
            "limit": MAX_LIMIT,
            "folder_id": folder_id,
        }

        # Add 'have' parameter if we've already processed some bookmarks
        if processed_ids:
            # Just include the first 5 IDs in the log to keep it readable
            sample_ids = list(processed_ids)[:5]
            log.info(f"Adding 'have' parameter with {len(processed_ids)} IDs. Sample: {sample_ids}...")
            payload["have"] = ",".join(map(str, processed_ids))

        try:
            log.info(f"API Request Payload: {payload}")
            data = retry_request(sess, f"{API_BASE}/bookmarks/list", data=payload)
        except Exception as e:
            log.error(f"Failed to fetch bookmark batch for folder {folder_id}: {e}. Stopping count for this folder.")
            break

        bookmarks_in_batch = []

        # Handle inconsistent API response (dict or list)
        if isinstance(data, dict):
            log.debug("Received dictionary format from /bookmarks/list.")
            bookmarks_in_batch = data.get("bookmarks", [])
        elif isinstance(data, list):
            log.warning(f"Received list format from /bookmarks/list for folder {folder_id}.")
            bookmarks_in_batch = [item for item in data if isinstance(item, dict) and item.get("type") == "bookmark"]
        else:
            log.error(f"Unexpected response type ({type(data).__name__}) from /bookmarks/list for folder {folder_id}. Stopping count.")
            break

        # Ensure bookmarks_in_batch is always a list
        if bookmarks_in_batch is None:
             bookmarks_in_batch = []

        if not bookmarks_in_batch:
            log.info(f"No more bookmarks returned for folder {folder_id}. Counting complete.")
            break

        # Check if all returned bookmark IDs are already in processed_ids
        returned_ids = [int(bm.get("bookmark_id", 0)) for bm in bookmarks_in_batch if bm.get("bookmark_id")]
        already_seen_ids = [bid for bid in returned_ids if bid in processed_ids]

        log.info(f"API returned {len(bookmarks_in_batch)} bookmarks, of which {len(already_seen_ids)} were already processed")
        if already_seen_ids and len(already_seen_ids) == len(returned_ids):
            log.warning(f"ALL returned bookmarks were already processed! API might be ignoring the 'have' parameter.")

            # Show the first few IDs
            sample_returned = returned_ids[:5]
            sample_processed = list(processed_ids)[:5]
            log.info(f"Sample returned IDs: {sample_returned}")
            log.info(f"Sample processed IDs: {sample_processed}")

        # Count only new bookmarks received in this batch
        newly_added_count = 0
        for bm in bookmarks_in_batch:
             try:
                 bid = int(bm.get("bookmark_id", 0))
                 if bid and bid not in processed_ids:
                     processed_ids.add(bid)
                     newly_added_count += 1
                 elif not bid:
                      log.warning(f"Bookmark data missing 'bookmark_id': {bm}")
             except (ValueError, TypeError):
                  log.warning(f"Invalid 'bookmark_id' in data: {bm}")

        total_count += newly_added_count
        log.info(f"Fetched {len(bookmarks_in_batch)} items, {newly_added_count} new. Folder {folder_id} total: {total_count}")

        # If we got no new bookmarks in this batch, the API might be returning duplicates or we're done
        if newly_added_count == 0:
            log.warning(f"No new bookmarks found in this batch. API might be returning duplicates or we've completed the folder.")
            break

        time.sleep(RATE_DELAY) # Be nice to the API

    if safety_counter >= MAX_SAFETY_LOOPS:
        log.warning(f"Reached maximum safety loop limit ({MAX_SAFETY_LOOPS}) for folder {folder_id}.")

    log.info(f"Finished counting for folder_id: {folder_id}. Final count: {total_count}")
    return total_count

# ── MAIN EXECUTION ────────────────────────────────────────────────────────────
def main():
    log.info("Starting Instapaper stats collection.")
    sess = get_oauth_session()
    custom_folders = fetch_folders(sess)
    folder_counts = {}

    # Folders to check: built-ins + custom
    folders_to_check = {
        "Unread": "unread",
        "Archive": "archive",
        "Starred": "starred"
    }
    # Add custom folders using their titles as keys and IDs as values
    folders_to_check.update(custom_folders)

    log.info(f"Found folders to check: {list(folders_to_check.keys())}")

    for folder_title, folder_id in folders_to_check.items():
        log.info(f"--- Counting folder: {folder_title} (ID: {folder_id}) ---")
        count = count_bookmarks_in_folder(sess, folder_id)
        folder_counts[folder_title] = count
        log.info(f"--- Count for {folder_title}: {count} ---")

    log.info("Stats collection complete.")
    print("\n--- Instapaper Folder Counts ---")
    for title, count in folder_counts.items():
        print(f"{title}: {count}")
    print("------------------------------")

if __name__ == "__main__":
    main()