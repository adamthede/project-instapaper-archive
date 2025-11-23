#!/usr/bin/env python3
# test_instapaper_pagination.py
# Simple test to verify pagination using only the 'have' parameter

import os
import time
import json
import logging
from pathlib import Path
import requests
from requests_oauthlib import OAuth1Session
from dotenv import load_dotenv

load_dotenv()

# ── CONFIG ─────────────────────────────────────────────────────────────────────
CONSUMER_KEY    = os.getenv("INSTAPAPER_CONSUMER_KEY")
CONSUMER_SECRET = os.getenv("INSTAPAPER_CONSUMER_SECRET")
USERNAME        = os.getenv("INSTAPAPER_USERNAME")
PASSWORD        = os.getenv("INSTAPAPER_PASSWORD")

API_BASE        = "https://www.instapaper.com/api/1"
MAX_LIMIT       = 500  # Max allowed by API for bookmarks/list
RATE_DELAY      = 1.0  # Delay between API calls

# ── BASIC LOGGING ────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
log = logging.getLogger("InstapaperPaginationTest")

# ── OAUTH FLOW ──────────────────────────────────────────────────────────────
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
    except Exception as e:
        log.error(f"OAuth request failed: {e}")
        raise

# ── SIMPLE API REQUEST ───────────────────────────────────────────────────────
def fetch_bookmarks(sess, have_ids=None, folder_id="archive"):
    """Simplified function to fetch bookmarks with just limit and have parameters."""
    payload = {
        "limit": MAX_LIMIT,
        "folder_id": folder_id
    }

    if have_ids:
        # Only use the first 50 IDs to keep the request smaller
        have_list = list(have_ids)[:50]
        payload["have"] = ",".join(map(str, have_list))
        log.info(f"Using 'have' parameter with {len(have_list)} IDs (out of {len(have_ids)} total). Sample: {have_list[:5]}...")

    log.info(f"Request payload: {payload}")
    response = sess.post(f"{API_BASE}/bookmarks/list", data=payload)
    response.raise_for_status()

    log.info(f"Response status: {response.status_code}")
    return response.json()

# ── MAIN PAGINATION TEST ──────────────────────────────────────────────────────
def main():
    log.info("Starting Instapaper pagination test")
    sess = get_oauth_session()

    folder_id = "archive"  # Target folder
    all_bookmark_ids = set()  # Track all bookmark IDs we've seen

    # Track pagination stats
    batch_number = 1
    total_retrieved = 0

    try:
        while True:
            log.info(f"Fetching batch {batch_number} for folder '{folder_id}'")

            # Get next batch
            data = fetch_bookmarks(sess, all_bookmark_ids if all_bookmark_ids else None, folder_id)

            # Handle potential response formats
            bookmarks = []
            if isinstance(data, dict):
                log.info("Received dictionary format response")
                bookmarks = data.get("bookmarks", [])
            elif isinstance(data, list):
                log.info("Received list format response")
                bookmarks = [item for item in data if isinstance(item, dict) and item.get("type") == "bookmark"]

            # Process results
            new_bookmark_count = 0
            for bookmark in bookmarks:
                bookmark_id = int(bookmark.get("bookmark_id", 0))
                if bookmark_id > 0 and bookmark_id not in all_bookmark_ids:
                    all_bookmark_ids.add(bookmark_id)
                    new_bookmark_count += 1

            log.info(f"Batch {batch_number}: Retrieved {len(bookmarks)} bookmarks, {new_bookmark_count} new")
            total_retrieved += new_bookmark_count

            # Stop condition - no new bookmarks in this batch
            if new_bookmark_count == 0:
                if len(bookmarks) > 0:
                    log.warning("Received bookmarks but none were new - API might be ignoring 'have' parameter")
                else:
                    log.info("No more bookmarks to retrieve - pagination complete")
                break

            # Prepare for next batch
            batch_number += 1
            log.info(f"Total unique bookmarks retrieved so far: {total_retrieved}")
            time.sleep(RATE_DELAY)  # Be nice to the API

    except Exception as e:
        log.error(f"Error during pagination test: {e}")

    log.info(f"Pagination test complete. Retrieved {total_retrieved} total unique bookmarks.")

if __name__ == "__main__":
    main()