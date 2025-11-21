#!/usr/bin/env python3
# export_instapaper_to_obsidian.py
# Full-featured Instapaper → Obsidian export:
# - OAuth 1.0a xAuth (HMAC-SHA1)  [oai_citation:6‡instapaper.com](https://www.instapaper.com/api)
# - Compliant pagination (limit=500)  [oai_citation:7‡instapaper.com](https://www.instapaper.com/api)
# - Dynamic folder discovery  [oai_citation:8‡instapaper.com](https://www.instapaper.com/api)
# - 'have' parameter for delta sync  [oai_citation:9‡instapaper.com](https://www.instapaper.com/api)
# - Exponential backoff on 503 or OAuth errors  [oai_citation:10‡instapaper.com](https://www.instapaper.com/api)

import os, time, json, logging
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
# Destination for exported Markdown archive (can be overridden via .env)
VAULT_PATH      = Path(
    os.getenv(
        "INSTAPAPER_VAULT_PATH",
        str(Path.home() / "Obsidian" / "Vault" / "Instapaper"),
    )
)
MANIFEST_FILE   = Path.home()/".instapaper_manifest.json"

API_BASE        = "https://www.instapaper.com/api/1"
MAX_LIMIT       = 500  # ↪ limit 1–500 per spec  [oai_citation:11‡instapaper.com](https://www.instapaper.com/api?utm_source=chatgpt.com)
RATE_DELAY      = 1.0
MAX_RETRIES     = 5
BACKOFF_FACTOR  = 2
# Default folder key: 'unread', 'starred', 'archive', or any custom title
FOLDER_KEY      = os.getenv("INSTAPAPER_FOLDER", "archive")

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
log = logging.getLogger("Instapaper→Obsidian")

# ── MANIFEST ──────────────────────────────────────────────────────────────────
def load_manifest():
    log.info(f"Attempting to load manifest from {MANIFEST_FILE}")
    manifest = set(json.loads(MANIFEST_FILE.read_text())) if MANIFEST_FILE.exists() else set()
    log.info(f"Loaded {len(manifest)} processed bookmark IDs from manifest.")
    return manifest

def save_manifest(ids):
    log.info(f"Saving {len(ids)} processed bookmark IDs to {MANIFEST_FILE}")
    MANIFEST_FILE.write_text(json.dumps(list(ids)))

# ── OAUTH FLOW ────────────────────────────────────────────────────────────────
def get_oauth_session():
    """Perform xAuth to get access token and return a signed session."""
    log.info("Initiating OAuth 1.0a xAuth flow...")
    oauth = OAuth1Session(CONSUMER_KEY, client_secret=CONSUMER_SECRET)
    # xAuth endpoint
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
def retry_request(fn, *args, **kwargs):
    """Retry wrapper with exponential backoff on 503 or JSON errors.

    Returns the parsed JSON data (can be dict or list depending on endpoint).
    Raises exceptions on HTTP errors, JSON decode errors, or non-retryable API errors.
    """
    delay = 1
    last_error = None
    for attempt in range(1, MAX_RETRIES+1):
        data = None # Initialize data to None in each attempt
        try:
            resp = fn(*args, **kwargs)

            # Log raw response for debugging
            log.info(f"API Response Status: {resp.status_code}")
            log.debug(f"API Response Headers: {resp.headers}")
            log.debug(f"API Response Text: {resp.text[:500]}...") # Log first 500 chars

            resp.raise_for_status() # Check for HTTP errors first (4xx/5xx)

            # Attempt to parse JSON
            try:
                data = resp.json()
            except requests.exceptions.JSONDecodeError as json_err:
                log.error(f"Failed to decode JSON response: {json_err}")
                log.error(f"Raw text was: {resp.text[:500]}...")
                last_error = json_err
                # Treat JSON decode error as potentially transient, trigger retry logic below
                raise ValueError(f"JSON Decode Error: {json_err}") from json_err

            # Check for Instapaper API-level errors if response is a dictionary
            if isinstance(data, dict) and data.get('type') == 'error':
                error_code = data.get('error_code', 'N/A')
                message = data.get('message', 'No message')
                log.error(f"Instapaper API Error {error_code}: {message}")
                # Treat API errors as non-retryable
                last_error = requests.exceptions.RequestException(f"Instapaper API Error {error_code}: {message}")
                raise last_error

            # Return the parsed data (could be dict or list)
            return data

        # Catch exceptions that might warrant a retry
        except (requests.exceptions.RequestException, ValueError) as e:
            # Exclude the specific API error RequestException we raised above from retries
            if last_error is e and isinstance(e, requests.exceptions.RequestException) and "Instapaper API Error" in str(e):
                 log.error(f"Non-retryable Instapaper API Error encountered: {e}")
                 should_retry = False
            else:
                last_error = e # Store the error
                # Determine if retry is appropriate
                should_retry = False
                if isinstance(e, requests.exceptions.HTTPError) and e.response is not None:
                    if e.response.status_code == 503:
                        log.warning(f"HTTP 503 error detected.")
                        should_retry = True
                # Retry on connection errors, timeout errors, or our synthetic ValueError from JSON decode failure
                elif isinstance(e, (requests.exceptions.ConnectionError, requests.exceptions.Timeout, ValueError)):
                    log.warning(f"Network/JSON error detected: {type(e).__name__}")
                    should_retry = True
                # Log other RequestExceptions but don't retry unless explicitly handled
                elif isinstance(e, requests.exceptions.RequestException):
                    log.warning(f"RequestException encountered: {type(e).__name__}. Not retrying by default.")
                    should_retry = False

            if not should_retry or attempt == MAX_RETRIES:
                log.error(f"Non-retryable error or max retries ({MAX_RETRIES}) hit: {e}")
                # Ensure we re-raise the *original* error that occurred in the loop
                raise last_error if last_error else e

            log.warning(f"Transient error ({type(e).__name__}); retry #{attempt}/{MAX_RETRIES} in {delay}s")
            time.sleep(delay)
            delay *= BACKOFF_FACTOR

    # Should only be reached if MAX_RETRIES is 0 or loop fails unexpectedly
    raise last_error or RuntimeError("Retry loop completed without success or specific error.")

def retry_request_html(sess, url, **kwargs):
    """Retry wrapper specifically for endpoints returning HTML on success (like get_text).

    Handles retries for network errors and 503s.
    Returns raw HTML text on success (HTTP 200).
    Raises an exception on persistent errors or non-200 status codes after parsing potential JSON error.
    """
    delay = 1
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = sess.post(url, **kwargs)
            log.info(f"HTML Request Status: {resp.status_code} for {url}")
            log.debug(f"HTML Request Headers: {resp.headers}")

            # Success: Return HTML content directly
            if resp.status_code == 200:
                log.debug(f"HTML Response Text (first 500 chars): {resp.text[:500]}...")
                return resp.text

            # Handle non-200 status codes (potential API errors)
            resp.raise_for_status() # Raises HTTPError for 4xx/5xx

            # If raise_for_status didn't trigger (e.g., other 2xx?), treat as unexpected
            log.error(f"Unexpected non-200 status code ({resp.status_code}) without HTTPError for {url}.")
            last_error = requests.exceptions.RequestException(f"Unexpected status {resp.status_code}")
            # Fall through to retry logic based on error type
            raise last_error

        except requests.exceptions.RequestException as e:
            last_error = e
            should_retry = False

            # Check for specific HTTP status codes that warrant retry (503)
            if isinstance(e, requests.exceptions.HTTPError) and e.response is not None:
                if e.response.status_code == 503:
                    log.warning(f"HTTP 503 error detected for {url}.")
                    should_retry = True
                else:
                    # For other HTTP errors (like 400), try to parse JSON error message
                    try:
                        error_data = e.response.json()
                        if isinstance(error_data, list) and len(error_data) > 0 and isinstance(error_data[0], dict) and error_data[0].get("type") == "error":
                            err_code = error_data[0].get('error_code', 'N/A')
                            message = error_data[0].get('message', 'No message')
                            log.error(f"Instapaper API Error ({err_code}) on HTML request to {url}: {message}")
                            # Make the original exception message more informative
                            e.args = (f"Instapaper API Error {err_code}: {message} (HTTP {e.response.status_code})",)
                        else:
                            log.error(f"Non-503 HTTP error ({e.response.status_code}) for {url} with unexpected JSON content: {e.response.text[:200]}...")
                    except requests.exceptions.JSONDecodeError:
                        log.error(f"Non-503/non-JSON HTTP error ({e.response.status_code}) for {url}: {e.response.text[:200]}...")
                    # Do not retry non-503 HTTP errors generally
                    should_retry = False

            # Retry on connection errors, timeout errors
            elif isinstance(e, (requests.exceptions.ConnectionError, requests.exceptions.Timeout)):
                log.warning(f"Network error detected for {url}: {type(e).__name__}")
                should_retry = True

            if not should_retry or attempt == MAX_RETRIES:
                log.error(f"Non-retryable error or max retries ({MAX_RETRIES}) hit for {url}: {e}")
                raise last_error # Re-raise the last caught error

            log.warning(f"Transient error for {url} ({type(e).__name__}); retry #{attempt}/{MAX_RETRIES} in {delay}s")
            time.sleep(delay)
            delay *= BACKOFF_FACTOR

    # Should only be reached if MAX_RETRIES is 0 or loop fails unexpectedly
    raise last_error or RuntimeError(f"Retry loop completed without success for {url}")

def fetch_folders(sess):
    """Retrieve all user-created folders for dynamic folder IDs."""
    log.info("Fetching folder list...")
    data = retry_request(sess.post, f"{API_BASE}/folders/list")

    # Endpoint /folders/list returns a list directly
    if not isinstance(data, list):
        log.error(f"Expected list from /folders/list, but got {type(data).__name__}")
        raise TypeError(f"API Error: Expected list for folders, got {type(data).__name__}")

    folders = {f["title"]: f["folder_id"] for f in data if isinstance(f, dict) and f.get("type")=="folder"}
    log.info(f"Found {len(folders)} user folders: {list(folders.keys())}")
    return folders

def fetch_bookmarks(sess, have_list, folder_id):
    """Call /bookmarks/list with pagination using only 'have' parameter."""
    log.info(f"Fetching bookmarks: folder={folder_id}, have_count={len(have_list)}")
    payload = {
        "limit": MAX_LIMIT,
        "folder_id": folder_id,
    }
    if have_list:
        payload["have"] = ",".join(map(str, have_list))
    data = retry_request(sess.post, f"{API_BASE}/bookmarks/list", data=payload)

    bookmarks = []

    # Handle potentially inconsistent API response format for /bookmarks/list
    if isinstance(data, dict):
        # Expected format (as per specific docs for /bookmarks/list)
        log.info("Received expected dictionary format from /bookmarks/list.")
        bookmarks = data.get("bookmarks", [])
        log.info(f"Received {len(bookmarks)} bookmarks.")
    elif isinstance(data, list):
        # Unexpected format (matches general API doc, contradicts specific endpoint doc)
        log.warning("Received unexpected list format from /bookmarks/list. Documentation states it should be a dictionary.")
        log.warning("Filtering list for items with type='bookmark'.")
        bookmarks = [item for item in data if isinstance(item, dict) and item.get("type") == "bookmark"]
        log.info(f"Filtered {len(bookmarks)} bookmarks from list.")
    else:
        # Neither dict nor list - raise error
        log.error(f"Expected dict or list from /bookmarks/list, but got {type(data).__name__}")
        raise TypeError(f"API Error: Expected dict or list for bookmarks, got {type(data).__name__}")

    # Ensure bookmarks is always a list, even if API returns null/None inside dict
    if bookmarks is None:
        bookmarks = []

    return bookmarks

def fetch_full_text(sess, bid):
    """Call /bookmarks/get_text to retrieve reading-optimized HTML."""
    log.info(f"Fetching full text for bookmark {bid}...")
    try:
        # Use the dedicated HTML retry function
        html_content = retry_request_html(sess, f"{API_BASE}/bookmarks/get_text",
                                          data={"bookmark_id": bid})
        log.info(f"Fetched content length: {len(html_content)} chars for bookmark {bid}")
        return html_content
    except Exception as e:
        # Log the error from retry_request_html if it failed
        log.error(f"Failed to fetch full text for bookmark {bid} after retries: {e}")
        return "" # Return empty string on failure to fetch text

# ── MAIN EXPORT LOOP ───────────────────────────────────────────────────────────
def sanitize_title(t):
    return "".join(c for c in t if c not in r'<>:"/\\|?*').strip()

def main():
    sess = get_oauth_session()
    # determine folder ID: built-ins or dynamic
    log.info("Determining target folder ID...")
    user_folders = fetch_folders(sess)
    folder_id = user_folders.get(FOLDER_KEY, FOLDER_KEY)
    log.info(f"Using Folder ID: {folder_id} (for requested key: '{FOLDER_KEY}')")

    processed = load_manifest()
    count = 0

    while True:
        log.info(f"Starting fetch cycle. Processed count: {len(processed)}")
        bms = fetch_bookmarks(sess, processed, folder_id)

        if not bms:
            log.info("No new bookmarks returned in this batch. Export complete.")
            break # Exit loop if no bookmarks are returned

        batch_processed_count = 0
        for bm in bms:
            # Log the raw bookmark structure for debugging
            log.debug(f"Raw bookmark data received: {bm}")

            bid = int(bm["bookmark_id"])
            if bid in processed:
                log.info(f"Skipping already processed bookmark {bid}")
                continue

            title = bm.get("title","Untitled")
            url   = bm.get("url", "URL_MISSING")
            if url == "URL_MISSING":
                log.warning(f"Bookmark {bid} ('{title}') is missing 'url'.")

            # Handle potentially missing/differently named timestamp
            saved_dt = None
            saved_date_source = "unknown"
            time_saved_val = bm.get("time_saved")
            time_val = bm.get("time")

            timestamp_to_use = None
            if time_saved_val:
                timestamp_to_use = time_saved_val
                saved_date_source = "original - time_saved"
                log.debug(f"Using 'time_saved' ({timestamp_to_use}) for bookmark {bid}")
            elif time_val:
                timestamp_to_use = time_val
                saved_date_source = "original - time"
                log.debug(f"Using 'time' ({timestamp_to_use}) for bookmark {bid}")
            else:
                log.warning(f"Missing both 'time_saved' and 'time' for bookmark {bid}. Using current date.")
                saved_date_source = "fallback - missing"

            if timestamp_to_use:
                try:
                    saved_dt = datetime.fromtimestamp(int(timestamp_to_use))
                except (ValueError, TypeError):
                    log.warning(f"Invalid timestamp format ('{timestamp_to_use}') from key '{saved_date_source.split(' - ')[1]}' for bookmark {bid}. Using current date.")
                    saved_dt = None # Ensure fallback is used
                    saved_date_source = f"fallback - invalid format ({saved_date_source.split(' - ')[1]})"

            # Fallback to current time if no valid timestamp was found/parsed
            if saved_dt is None:
                saved_dt = datetime.now()
                # Ensure source reflects fallback if not already set
                if not saved_date_source.startswith("fallback"):
                    saved_date_source = "fallback - unknown error"

            saved = saved_dt.strftime("%Y-%m-%d")

            safe  = sanitize_title(title)[:80]
            fname = f"{saved} – {safe}.md"
            out   = VAULT_PATH/fname

            log.info(f"Processing bookmark {bid}: '{title}'")
            html = fetch_full_text(sess, bid)
            if not html:
                log.warning(f"No content fetched for bookmark {bid}. Skipping file creation.")
                processed.add(bid) # Mark as processed even if empty to avoid retrying
                continue

            log.info(f"Converting HTML to Markdown for bookmark {bid}...")
            mdt  = md(html, heading_style="ATX")

            # Escape quotes in title for YAML frontmatter
            escaped_title = title.replace('"', '\\"')

            fm = ["---",
                  f'title: "{escaped_title}"',
                  f"original_url: \"{url}\"",
                  f"instapaper_id: {bid}",
                  f"date_saved: {saved}",
                  f"date_saved_source: {saved_date_source}", # Indicate if date is original or fallback
                  "---", ""]
            log.info(f"Writing Markdown to: {out}")
            try:
                with open(out, "w", encoding="utf-8") as f:
                    f.write("\n".join(fm) + mdt)
            except Exception as e:
                log.error(f"Failed to write file {out} for bookmark {bid}: {e}")
                continue # Skip adding to processed/count if write fails

            processed.add(bid)
            count += 1
            batch_processed_count += 1
            log.info(f"Successfully processed and saved bookmark {bid}. Sleeping for {RATE_DELAY}s...")
            time.sleep(RATE_DELAY)

        log.info(f"Finished processing batch. {batch_processed_count} new bookmarks processed in this batch.")

        # Save state periodically within the loop in case of interruption
        save_manifest(processed)
        log.info("Intermediate manifest saved.")

    # Final save state & manifest for next sync
    log.info("Sync loop finished. Saving final manifest.")
    save_manifest(processed)
    log.info(f"Sync complete: {count} total new files added across all batches.")

if __name__ == "__main__":
    main()