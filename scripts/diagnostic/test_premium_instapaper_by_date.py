#!/usr/bin/env python3
# test_premium_instapaper_by_date.py
# Test if premium subscription allows retrieving articles by date ranges

import os
import json
import time
import logging
from pathlib import Path
from datetime import datetime, timedelta
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
RESULTS_DIR     = Path("date_range_results")

# ── BASIC LOGGING ───────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
log = logging.getLogger("InstapaperDateRangeTest")

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

def save_response(data, name):
    """Save API response to JSON file."""
    RESULTS_DIR.mkdir(exist_ok=True)
    with open(RESULTS_DIR / f"{name}.json", "w") as f:
        json.dump(data, f, indent=4)
    log.info(f"Saved response to {RESULTS_DIR / name}.json")

def test_date_range_pagination(sess, folder_id="archive"):
    """Test retrieving bookmarks by date range chunks."""
    log.info(f"Testing date range pagination for folder: {folder_id}")

    all_bookmarks = []
    all_bookmark_ids = set()

    # Start with the earliest possible date for Instapaper (founded in 2008)
    # and work forward in 6-month chunks
    start_date = datetime(2008, 1, 1)
    end_date = datetime.now()
    chunk_size = timedelta(days=180)  # 6 months

    current_date = start_date
    chunk_num = 0

    while current_date < end_date and chunk_num < 30:  # 30 chunks = 15 years max
        chunk_num += 1
        next_date = min(current_date + chunk_size, end_date)

        # Convert to Unix timestamps
        from_time = int(current_date.timestamp())
        to_time = int(next_date.timestamp())

        log.info(f"Retrieving chunk {chunk_num}: {current_date.strftime('%Y-%m-%d')} to {next_date.strftime('%Y-%m-%d')}")
        log.info(f"Time range: {from_time} to {to_time}")

        payload = {
            "limit": MAX_LIMIT,
            "folder_id": folder_id,
            "from": from_time,  # Try 'from' parameter for date filtering
            "to": to_time       # Try 'to' parameter for date filtering
        }

        try:
            response = sess.post(f"{API_BASE}/bookmarks/list", data=payload)
            response.raise_for_status()
            data = response.json()

            save_response(data, f"{folder_id}_date_chunk_{chunk_num}")

            if isinstance(data, list):
                # Filter out non-bookmark items
                bookmarks = [item for item in data if item.get("type") == "bookmark"]
                log.info(f"Chunk {chunk_num}: Got {len(bookmarks)} bookmarks.")

                if bookmarks:
                    # Only add new bookmarks to our collection
                    new_bookmarks = [bm for bm in bookmarks if bm["bookmark_id"] not in all_bookmark_ids]
                    all_bookmarks.extend(new_bookmarks)

                    new_ids = {bm["bookmark_id"] for bm in new_bookmarks}
                    all_bookmark_ids.update(new_ids)

                    log.info(f"Added {len(new_bookmarks)} new bookmarks from this chunk.")
                    log.info(f"Total unique bookmarks so far: {len(all_bookmark_ids)}")

                    # Look at timestamp ranges in this batch
                    if bookmarks:
                        timestamps = [int(bm.get("time", 0)) for bm in bookmarks]
                        min_ts = min(timestamps) if timestamps else 0
                        max_ts = max(timestamps) if timestamps else 0
                        log.info(f"Timestamp range in this batch: {min_ts} to {max_ts}")
                        if min_ts > 0 and max_ts > 0:
                            min_date = datetime.fromtimestamp(min_ts).strftime('%Y-%m-%d')
                            max_date = datetime.fromtimestamp(max_ts).strftime('%Y-%m-%d')
                            log.info(f"Date range in this batch: {min_date} to {max_date}")
            else:
                log.error(f"Unexpected response format: {type(data)}")

        except Exception as e:
            log.error(f"Error retrieving chunk {chunk_num}: {e}")

        # Move to next chunk
        current_date = next_date
        # Small delay to avoid rate limiting
        time.sleep(1)

    log.info(f"Date range search complete. Retrieved {len(all_bookmarks)} unique bookmarks in {chunk_num} chunks.")
    save_response(all_bookmarks, f"{folder_id}_all_bookmarks_by_date")
    return all_bookmarks

def main():
    log.info("=== Starting Instapaper Premium Date Range Test ===")

    RESULTS_DIR.mkdir(exist_ok=True)
    sess = get_oauth_session()

    # Get subscription status
    response = sess.post(f"{API_BASE}/bookmarks/list", data={"limit": 1, "folder_id": "unread"})
    data = response.json()

    # Extract user object which should contain subscription info
    user_obj = next((item for item in data if item.get("type") == "user"), None)

    if user_obj:
        is_premium = user_obj.get("subscription_is_active") == "1"
        log.info(f"Subscription status: {'ACTIVE' if is_premium else 'INACTIVE'}")
        save_response(user_obj, "user_premium_status")
    else:
        log.warning("Could not determine subscription status")

    # Test date range queries
    bookmarks = test_date_range_pagination(sess, "archive")

    log.info("=== Instapaper Premium Date Range Test Complete ===")
    log.info(f"Retrieved a total of {len(bookmarks)} unique bookmarks")

if __name__ == "__main__":
    main()