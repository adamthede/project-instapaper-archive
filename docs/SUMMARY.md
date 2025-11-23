# Instapaper API Investigation Summary

## Problem Statement
We needed to export 7,320+ articles from Instapaper's Archive folder to Markdown files, but the API was only returning 500 articles.

## Investigation Steps

1. **Initial Export Script**
   - Created `export_instapaper_to_obsidian.py` to convert Instapaper articles to Markdown
   - Implemented OAuth authentication, rate limiting, and error handling
   - Successfully retrieved article text, but limited to 500 items

2. **API Diagnostics**
   - Created `scripts/diagnostic_scripts/get_instapaper_stats.py` to count articles across folders
   - Confirmed API returns only 500 articles from Archive folder
   - Discovered inconsistent API response formats between folders

3. **Pagination Testing**
   - Implemented and tested multiple pagination approaches:
     - Cursor-based pagination with `since` parameter
     - ID-based exclusion with `have` parameter
     - Timestamp-based filtering with `before` parameter
     - Offset-based pagination with `skip` parameter
   - All approaches consistently returned the same 500 articles

4. **Premium Subscription Testing**
   - Upgraded to premium subscription to test if limits could be bypassed
   - Confirmed `subscription_is_active` value changed from "0" to "1"
   - Premium status did not resolve the 500 article limit
   - Created `scripts/diagnostic_scripts/test_premium_instapaper.py` and `scripts/diagnostic_scripts/test_premium_instapaper_by_date.py` to verify

5. **Date Range Testing**
   - Attempted to retrieve articles in different date ranges
   - API ignored date parameters and returned the same 500 articles
   - Results always had timestamps from 2018-04-30 to 2025-04-21 regardless of date parameters

6. **Development of CSV-Based Bulk Import Solution**
   - Leveraged the Instapaper web CSV export (which lists all articles) as a source of article IDs.
   - Created `scripts/bulk_import_instapaper_from_csv.py` to:
     - Read the CSV.
     - For each article ID, call the `/bookmarks/get_text` API endpoint (which is not limited by the 500-item folder restriction) to fetch full HTML.
     - Convert HTML to Markdown.
     - Save with rich frontmatter from CSV data.
     - Implement idempotency using a manifest file to track processed articles.
   - This approach successfully allows for the export of all articles, bypassing the folder listing limitations.

## Key Findings

1. **Hard 500 Item Limit (for Direct API Folder Listing)**: The Instapaper API has a fixed limit of 500 items per folder when using the `/bookmarks/list` endpoint, with no functional pagination for the Archive folder beyond these 500.

2. **Parameter Behavior**:
   - The `have` parameter works in limited cases (when excluding a small number of items)
   - Date range parameters (`from`/`to`) are ignored entirely
   - The `since` cursor isn't returned for Archive/Starred folders
   - Using `skip` for offset-based pagination fails to advance past the first 500 items

3. **Web Interface vs API**: While the web interface allows viewing all 7,320+ articles (175+ pages × 40 items/page), the API is limited to the 500 most recent items.

4. **Premium Not a Factor**: The API's 500-item limit applies regardless of premium subscription status.

## Alternative Solutions

1. **Web Scraping**: Several community solutions use web scraping instead of the API to access the complete archive

2. **CSV Export**: Instapaper's web interface offers a CSV export option that includes more than 500 items (but not full text)

3. **Batched Processing**: For the most recent 500 articles, the API approach works well

## Conclusion

The Instapaper API has an inherent limitation of 500 articles per folder with no documented way to bypass this limit. For complete archive access, web scraping or CSV export are the only viable options.
## Implemented Solution & Conclusion

The Instapaper API's `/bookmarks/list` endpoint has an inherent limitation of 500 articles per folder, making direct full archive export via this endpoint impossible for large archives.

**The implemented and recommended solution is a two-step process:**
1.  **Export a CSV of all bookmarks** from the Instapaper web interface. This CSV contains metadata for all articles.
2.  **Use the `scripts/bulk_import_instapaper_from_csv.py` script.** This script processes the CSV, and for each article ID, it uses the `/bookmarks/get_text` API endpoint (which is not subject to the 500-item folder limit) to retrieve the full article content and convert it to Markdown.

This combination allows for a complete export of all articles from any folder, including very large archives, by leveraging the comprehensive CSV export for article discovery and the per-article API endpoint for content retrieval. The original `scripts/export_instapaper_to_obsidian.py` remains useful for fetching recent articles (up to 500) on an ongoing basis without needing a CSV export.