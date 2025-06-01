# Instapaper API Limitations

After extensive testing, we've discovered some significant limitations in the Instapaper API that affect the ability to export all bookmarks from large folders.

## Key Findings

1. **500 Item Limit**: The Instapaper API appears to have a hard limit of returning only the first 500 items in any folder, regardless of pagination attempts. This is especially apparent for the Archive folder.

2. **Pagination Issues**: While the documentation suggests pagination should work with the `have` parameter (to exclude already processed IDs), the API responds with an empty list when attempting this, essentially cutting off access after the first 500 items.

3. **Response Format Inconsistencies**: The API documentation indicates that `/bookmarks/list` should return a dictionary with a `bookmarks` key and a `since` cursor, but it actually returns a list for most folders, including Archive and Starred.

4. **Timestamp/Cursor Pagination Fails**: We tried multiple pagination approaches:
   - Using the `since` cursor - doesn't advance for list-formatted responses
   - Using `before` parameter with timestamps - returns the same 500 items
   - Using `skip` or offset-based pagination - returns the same 500 items
   - Using `have` parameter to exclude items - returns empty list after first batch

## Implications

- The export script can only export the **most recent 500 items** from each folder, including the Archive folder.
- There appears to be no API-based way to retrieve more than 500 items from a folder.
- If you have more than 500 items in your Archive (as confirmed via the web interface), you'll only be able to access the 500 most recent ones *directly* via the API's `/bookmarks/list` endpoint for a specific folder.

## Possible Solutions

1. **Recommended: CSV Export + Bulk Import Script**:
   - **Export CSV from Instapaper**: Use the Instapaper website to export a CSV of all your bookmarks. This CSV contains the metadata (ID, URL, Title, etc.) for *all* articles, not just 500.
   - **Use `scripts/bulk_import_instapaper_from_csv.py`**: This script reads the exported CSV, identifies all archived articles (or articles from other specified folders if modified), and then uses the Instapaper API's `/bookmarks/get_text` endpoint (which works per-article ID) to fetch the full text for each one.
   - This combination effectively bypasses the 500-item folder listing limitation for achieving a full archive export.

2. **Contact Instapaper Support**: You can still ask them about the API limitations, but the CSV export method is a reliable workaround.

3. **Web Scraping Alternative (Less Ideal)**: Exploring web scraping the Instapaper website directly remains an option but is complex and may violate terms of service. The CSV export method is preferred.

4. **Multiple Folder Strategy (Less Ideal for Bulk Archive)**: While theoretically possible, reorganizing a large archive into many small folders is impractical for most users.

## API Behavior Details

For technical reference, here's what happens when trying different pagination approaches:

```
1. When providing 'have' with processed IDs:
   - First request: 500 bookmarks returned
   - Second request: Empty bookmarks list returned

2. When using 'before' with timestamps:
   - First request: 500 bookmarks returned
   - Second request: Same 500 bookmarks returned

3. When using offset with 'skip':
   - First request: 500 bookmarks returned
   - Second request: Same 500 bookmarks returned
```

These tests indicate that the API is designed to only provide the most recent 500 items per folder, regardless of pagination attempts.