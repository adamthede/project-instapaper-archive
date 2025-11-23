#!/usr/bin/env python3
# test_premium_instapaper.py
# Test if premium subscription allows retrieving more than 500 articles

import os
import json
import time
import logging
from pathlib import Path
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
RESULTS_DIR     = Path("premium_api_results")

# ── BASIC LOGGING ───────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
log = logging.getLogger("InstapaperPremiumTest")

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

def test_progressive_pagination(sess, folder_id="archive"):
    """Test if we can retrieve more than 500 items using progressive pagination."""
    log.info(f"Testing progressive pagination for folder: {folder_id}")

    all_bookmarks = []
    bookmark_ids = set()
    batch_count = 0
    max_batches = 20  # Safety limit

    while batch_count < max_batches:
        batch_count += 1

        # Only use the most recent 50 IDs for the 'have' parameter to keep request size manageable
        have_ids = list(bookmark_ids)[-50:] if bookmark_ids else []

        log.info(f"Retrieving batch {batch_count}. Already have {len(bookmark_ids)} bookmarks total (using {len(have_ids)} IDs in 'have' parameter).")

        payload = {
            "limit": MAX_LIMIT,
            "folder_id": folder_id
        }

        if have_ids:
            payload["have"] = ",".join(map(str, have_ids))
            log.info(f"First few IDs in 'have' parameter: {have_ids[:5]} ...")

        try:
            response = sess.post(f"{API_BASE}/bookmarks/list", data=payload)
            response.raise_for_status()
            data = response.json()

            save_response(data, f"{folder_id}_batch_{batch_count}")

            if isinstance(data, list):
                # Filter out non-bookmark items
                bookmarks = [item for item in data if item.get("type") == "bookmark"]
                log.info(f"Batch {batch_count}: Got {len(bookmarks)} bookmarks.")

                if not bookmarks:
                    log.info("No more bookmarks returned. Pagination complete.")
                    break

                new_ids = {bm["bookmark_id"] for bm in bookmarks}

                # Check for overlap with our known bookmarks
                overlap = new_ids.intersection(bookmark_ids)
                if overlap:
                    log.info(f"Found {len(overlap)} overlapping bookmark IDs in this batch.")

                # Only add new bookmarks to our collection
                new_bookmarks = [bm for bm in bookmarks if bm["bookmark_id"] not in bookmark_ids]

                # If we're not seeing any new IDs, we might be in a loop
                if not new_bookmarks:
                    log.warning(f"Batch {batch_count}: No new bookmark IDs. Stopping pagination.")
                    break

                # Add to our collected data
                all_bookmarks.extend(new_bookmarks)
                bookmark_ids.update(new_ids)

                log.info(f"Total unique bookmarks so far: {len(bookmark_ids)}")

            else:
                log.error(f"Unexpected response format: {type(data)}")
                break

        except Exception as e:
            log.error(f"Error retrieving batch {batch_count}: {e}")
            break

        # Small delay to avoid rate limiting
        time.sleep(1)

    log.info(f"Pagination complete. Retrieved {len(all_bookmarks)} bookmarks in {batch_count} batches.")
    save_response(all_bookmarks, f"{folder_id}_all_bookmarks")
    return all_bookmarks

def main():
    log.info("=== Starting Instapaper Premium API Test ===")

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

    # Test progressive pagination
    bookmarks = test_progressive_pagination(sess, "archive")

    log.info("=== Instapaper Premium API Test Complete ===")
    log.info(f"Retrieved a total of {len(bookmarks)} unique bookmarks")

if __name__ == "__main__":
    main()