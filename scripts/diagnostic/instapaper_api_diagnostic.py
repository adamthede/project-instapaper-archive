#!/usr/bin/env python3
# instapaper_api_diagnostic.py
# Diagnostic script to inspect Instapaper API responses

import os
import json
import time
import logging
import pprint
from pathlib import Path
from requests_oauthlib import OAuth1Session
import requests
from dotenv import load_dotenv

load_dotenv()  # Load environment variables from .env file

# ── CONFIG ─────────────────────────────────────────────────────────────────────
CONSUMER_KEY    = os.getenv("INSTAPAPER_CONSUMER_KEY")
CONSUMER_SECRET = os.getenv("INSTAPAPER_CONSUMER_SECRET")
USERNAME        = os.getenv("INSTAPAPER_USERNAME")
PASSWORD        = os.getenv("INSTAPAPER_PASSWORD")
API_BASE        = "https://www.instapaper.com/api/1"
MAX_LIMIT       = 500  # Max allowed by API for bookmarks/list

# Set up logging
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-8s %(message)s",
                    handlers=[
                        logging.FileHandler("instapaper_api_diagnostic.log"),
                        logging.StreamHandler()
                    ])
log = logging.getLogger("InstapaperAPITest")

# ── OAuth Flow ───────────────────────────────────────────────────────────────
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
        log.info(f"OAuth successful. Token: {creds['oauth_token'][:5]}...")
        return OAuth1Session(
            CONSUMER_KEY,
            client_secret=CONSUMER_SECRET,
            resource_owner_key=creds["oauth_token"],
            resource_owner_secret=creds["oauth_token_secret"],
            signature_method="HMAC-SHA1"
        )
    except Exception as e:
        log.error(f"OAuth request failed: {e}")
        if hasattr(e, 'response') and e.response is not None:
            log.error(f"Response status: {e.response.status_code}")
            log.error(f"Response text: {e.response.text}")
        raise

# ── API Request Function ───────────────────────────────────────────────────────
def make_api_request(sess, endpoint, params=None, test_name="default"):
    """Make a request to the Instapaper API and save the response for examination."""
    url = f"{API_BASE}/{endpoint}"
    log.info(f"Making request to {url} with params: {params}")

    try:
        start_time = time.time()
        response = sess.post(url, data=params)
        elapsed = time.time() - start_time

        log.info(f"Response received in {elapsed:.2f}s")
        log.info(f"Status code: {response.status_code}")
        log.info(f"Response headers: {dict(response.headers)}")

        # Save raw response
        output_dir = Path("api_responses")
        output_dir.mkdir(exist_ok=True)

        # Save headers
        with open(output_dir / f"{test_name}_headers.json", "w") as f:
            json.dump(dict(response.headers), f, indent=2)

        # Try to parse as JSON and save
        try:
            data = response.json()
            log.info(f"Parsed JSON response")

            # Log some stats about the response
            if isinstance(data, list):
                log.info(f"Response is a list with {len(data)} items")
                if data and isinstance(data[0], dict):
                    # Log the types of items in the list
                    types = set(item.get('type') for item in data if isinstance(item, dict))
                    log.info(f"Item types in list: {types}")

                    # Count bookmarks
                    bookmark_count = sum(1 for item in data if isinstance(item, dict) and item.get('type') == 'bookmark')
                    log.info(f"Number of bookmarks: {bookmark_count}")

                    # Extract bookmark IDs for debugging
                    if bookmark_count > 0:
                        bookmark_ids = [item.get('bookmark_id') for item in data
                                       if isinstance(item, dict) and item.get('type') == 'bookmark']
                        log.info(f"First 5 bookmark IDs: {bookmark_ids[:5]}")

            elif isinstance(data, dict):
                log.info(f"Response is a dictionary with keys: {data.keys()}")
                if 'bookmarks' in data and isinstance(data['bookmarks'], list):
                    log.info(f"Contains {len(data['bookmarks'])} bookmarks")
                if 'user' in data:
                    log.info(f"User data present with ID: {data.get('user', {}).get('user_id')}")
                if 'since' in data:
                    log.info(f"Since parameter: {data.get('since')}")

            # Save the full JSON response
            with open(output_dir / f"{test_name}_response.json", "w") as f:
                json.dump(data, f, indent=2)

            return data

        except ValueError:
            log.warning("Response is not valid JSON")
            # Save raw text response
            with open(output_dir / f"{test_name}_response.txt", "w") as f:
                f.write(response.text)
            return response.text

    except Exception as e:
        log.error(f"API request failed: {e}")
        if hasattr(e, 'response') and e.response is not None:
            log.error(f"Error response status: {e.response.status_code}")
            log.error(f"Error response text: {e.response.text[:500]}...")
        return None

# ── Folder List Test ───────────────────────────────────────────────────────────
def test_folders_list(sess):
    """Test and log response from folders/list endpoint."""
    return make_api_request(sess, "folders/list", test_name="folders_list")

# ── Bookmarks List Tests ─────────────────────────────────────────────────────
def test_bookmarks_list(sess, folder_id="archive", limit=MAX_LIMIT, have_ids=None, test_name=None):
    """Test and log response from bookmarks/list endpoint with different parameters."""
    params = {
        "limit": limit,
        "folder_id": folder_id
    }

    if have_ids:
        params["have"] = ",".join(map(str, have_ids))
        if not test_name:
            test_name = f"bookmarks_list_have_{len(have_ids)}"
    else:
        if not test_name:
            test_name = f"bookmarks_list_{folder_id}"

    return make_api_request(sess, "bookmarks/list", params, test_name)

# ── Pagination Test ─────────────────────────────────────────────────────────
def test_pagination(sess, folder_id="archive"):
    """Test pagination approach in detail."""
    # First request - get initial bookmarks
    first_batch = test_bookmarks_list(sess, folder_id, test_name=f"pagination_1st_{folder_id}")

    bookmark_ids = []

    # Extract bookmark IDs depending on response format
    if isinstance(first_batch, list):
        bookmark_ids = [item['bookmark_id'] for item in first_batch
                      if isinstance(item, dict) and item.get('type') == 'bookmark']
    elif isinstance(first_batch, dict) and 'bookmarks' in first_batch:
        bookmark_ids = [item['bookmark_id'] for item in first_batch['bookmarks']
                      if isinstance(item, dict)]

    if not bookmark_ids:
        log.error(f"No bookmark IDs found in first batch for folder {folder_id}")
        return

    log.info(f"Found {len(bookmark_ids)} bookmarks in first batch")

    # Second request - try with all IDs from first batch
    log.info(f"Testing pagination with all {len(bookmark_ids)} IDs")
    second_batch_all = test_bookmarks_list(
        sess, folder_id, have_ids=bookmark_ids,
        test_name=f"pagination_2nd_all_{folder_id}"
    )

    # Third request - try with just first 10 IDs
    if len(bookmark_ids) >= 10:
        log.info("Testing pagination with first 10 IDs only")
        second_batch_10 = test_bookmarks_list(
            sess, folder_id, have_ids=bookmark_ids[:10],
            test_name=f"pagination_2nd_10_{folder_id}"
        )

    # Fourth request - try with just first ID
    log.info("Testing pagination with first ID only")
    second_batch_1 = test_bookmarks_list(
        sess, folder_id, have_ids=[bookmark_ids[0]],
        test_name=f"pagination_2nd_1_{folder_id}"
    )

    return {
        "first_batch": first_batch,
        "second_batch_all": second_batch_all,
        "second_batch_1": second_batch_1
    }

# ── Main Execution ─────────────────────────────────────────────────────────────
def main():
    """Run a series of tests against the Instapaper API."""
    log.info("=== Starting Instapaper API Diagnostics ===")

    # Initialize OAuth session
    sess = get_oauth_session()

    # Test folders list endpoint
    log.info("Testing folders/list endpoint...")
    folders = test_folders_list(sess)

    # Test bookmarks/list for different folders
    for folder_id in ["unread", "archive", "starred"]:
        log.info(f"Testing bookmarks/list for {folder_id}...")
        test_bookmarks_list(sess, folder_id)

    # Test pagination in detail for archive folder
    log.info("Testing pagination for archive folder...")
    test_pagination(sess, "archive")

    # Test pagination with smaller limit to see if that affects results
    log.info("Testing bookmarks/list with smaller limit (100)...")
    test_bookmarks_list(sess, "archive", 100, test_name="bookmarks_list_archive_100")

    log.info("=== Instapaper API Diagnostics Completed ===")
    log.info("Check the api_responses directory for detailed response data")

if __name__ == "__main__":
    main()