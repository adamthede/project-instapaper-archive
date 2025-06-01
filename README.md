# Instapaper Archive Export Tool

A collection of scripts for exporting and analyzing content from Instapaper, with specific focus on working around API limitations.

## Background

This project was created to export a large archive (7,000+ articles) from Instapaper to Markdown files for use in Obsidian or other note-taking applications. During development, we discovered significant limitations in the Instapaper API that prevent retrieving more than 500 articles from certain folders, particularly the Archive folder.

## Key Findings

After extensive testing with various API approaches, we've confirmed:

1. **500 Article Limit**: The Instapaper API appears to have a hard limit of returning only 500 articles from the Archive folder in a single request.

2. **Pagination Does Not Work**: Standard pagination using the `have` parameter does not work as expected for the Archive folder. The API consistently returns the same 500 most recent articles regardless of what IDs are provided in the `have` parameter.

3. **Date Filtering Does Not Work**: Attempts to filter by date ranges using `from` and `to` parameters also return the same 500 articles from the Archive folder.

4. **Premium Status Not a Factor**: Even with an active premium subscription, the API still enforces these limitations.

5. **Web Interface Access**: The web interface does allow accessing all archived articles (175+ pages with 40 articles per page), suggesting this is an API-specific limitation.

## Scripts

### Main Export Script
- **export_instapaper_to_obsidian.py**: The main export script that converts Instapaper articles to Markdown files with YAML frontmatter.

### Diagnostic Scripts
- **get_instapaper_stats.py**: Counts articles in each folder to verify API behavior and limits.
- **instapaper_api_diagnostic.py**: Performs detailed analysis of API responses and saves them for inspection.
- **test_premium_instapaper.py**: Tests if premium subscription allows retrieving more than 500 articles using various pagination approaches.
- **test_premium_instapaper_by_date.py**: Tests date-range based retrieval to see if it can bypass the 500 article limit.

## Setup

1. **Create a .env file** with the following credentials:
   ```
   INSTAPAPER_CONSUMER_KEY=your_consumer_key
   INSTAPAPER_CONSUMER_SECRET=your_consumer_secret
   INSTAPAPER_USERNAME=your_email@example.com
   INSTAPAPER_PASSWORD=your_password
   INSTAPAPER_FOLDER=archive
   ```

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

## Usage

### Export Articles to Markdown
```bash
python export_instapaper_to_obsidian.py
```
This will export up to 500 articles from your Archive to the Obsidian vault path defined in the script.

### Check Article Counts
```bash
python scripts/diagnostic_scripts/get_instapaper_stats.py
```
This will show the number of articles in each folder accessible via the API.

### Run API Diagnostics
```bash
python scripts/diagnostic_scripts/instapaper_api_diagnostic.py
```
This performs detailed API analysis and saves response data to the `api_responses/` directory.

### Test Premium Features
```bash
python scripts/diagnostic_scripts/test_premium_instapaper.py
```
Tests pagination with a premium account.

```bash
python scripts/diagnostic_scripts/test_premium_instapaper_by_date.py
```
Tests date range filtering with a premium account.

## Limitations & Recommendations

Based on our findings, if you need to export more than 500 articles from Instapaper:

1. **Web Scraping**: Consider using web scraping approaches rather than the API. Several open-source projects implement this approach:
   - [instapaper-auto-archiver](https://github.com/cdzombak/instapaper-auto-archiver)
   - [instapexport](https://github.com/karlicoss/instapexport)

2. **Multiple Exports**: If using the API, you'll only retrieve the 500 most recent articles from your Archive.

3. **CSV Export**: Instapaper offers a CSV export feature from the web interface that may be more suitable for bulk exports, but the CSV export does not include the full text of your articles.

## Requirements

See `requirements.txt` for a list of dependencies:
- requests
- requests-oauthlib
- python-dotenv
- markdownify (for HTML to Markdown conversion)