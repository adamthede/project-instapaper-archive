#!/usr/bin/env python3
import os
import pandas as pd
import frontmatter
from pathlib import Path
from datetime import datetime
import textstat

# Config
VAULT_PATH = Path.home() / "Obsidian" / "Vault" / "Instapaper"
DATA_DIR = Path(__file__).parent.parent / "data"
INDEX_PATH = DATA_DIR / "archive_index.parquet"

def parse_article(file_path):
    """
    Parses a single Markdown file to extract frontmatter and metrics.
    """
    try:
        # Read raw text first to handle encoding issues more robustly
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content_raw = f.read()

        # Aggressively filter out non-printable control characters
        # Valid XML chars: #x9 | #xA | #xD | [#x20-#xD7FF] | [#xE000-#xFFFD] | [#x10000-#x10FFFF]
        # This logic removes control chars 0x00-0x1F (except \n \r \t) and 0x7F-0x9F
        clean_content = ""
        for ch in content_raw:
            code = ord(ch)
            if (code == 0x09 or code == 0x0A or code == 0x0D or code >= 0x20) and not (0x7F <= code <= 0x9F):
                clean_content += ch
            else:
                clean_content += " "

        # Parse frontmatter from the cleaned raw text
        post = frontmatter.loads(clean_content)

        fm = post.metadata
        content = post.content

        # Basic Metadata
        title = fm.get("title", file_path.stem)
        url = fm.get("original_url", "")
        instapaper_id = fm.get("instapaper_id", None)
        author = fm.get("author", "Unknown")

        # Date Handling
        date_saved = fm.get("date_saved", None)
        if isinstance(date_saved, str):
            try:
                date_saved = datetime.strptime(date_saved, "%Y-%m-%d").date()
            except ValueError:
                date_saved = None
        elif isinstance(date_saved, datetime):
            date_saved = date_saved.date()

        # Metrics
        word_count = fm.get("word_count", len(content.split()))
        # Standard reading speed: 238 words per minute
        reading_time_min = round(word_count / 238, 2)

        # Complexity (Flesch-Kincaid Grade Level)
        grade_level = None
        if word_count > 50:
            try:
                grade_level = textstat.flesch_kincaid_grade(content)
            except Exception:
                grade_level = None

        # AI Enriched Fields (if they exist)
        topics = fm.get("ai_topics", [])
        sentiment = fm.get("ai_sentiment", None)
        summary = fm.get("ai_summary", None)
        people = fm.get("ai_people", [])
        orgs = fm.get("ai_orgs", [])
        locations = fm.get("ai_locations", [])
        concepts = fm.get("ai_concepts", [])
        emotion = fm.get("ai_emotion", None)

        return {
            "instapaper_id": instapaper_id,
            "title": title,
            "url": url,
            "author": author,
            "date_saved": date_saved,
            "word_count": word_count,
            "reading_time_min": reading_time_min,
            "grade_level": grade_level,
            "topics": topics,
            "sentiment": sentiment,
            "summary": summary,
            "people": people,
            "orgs": orgs,
            "locations": locations,
            "concepts": concepts,
            "emotion": emotion,
            "file_path": str(file_path),
            "content_snippet": content[:500],  # Keep a snippet for preview if needed
        }

    except Exception as e:
        print(f"Error parsing {file_path.name}: {e}")
        return None

def build_index():
    print(f"Scanning vault at: {VAULT_PATH}")

    if not VAULT_PATH.exists():
        print(f"Error: Vault path not found: {VAULT_PATH}")
        return

    records = []
    files = list(VAULT_PATH.rglob("*.md"))
    print(f"Found {len(files)} Markdown files.")

    for i, file_path in enumerate(files):
        if i % 100 == 0:
            print(f"Processed {i}/{len(files)}...")

        data = parse_article(file_path)
        if data:
            records.append(data)

    print(f"Successfully parsed {len(records)} articles.")

    if not records:
        print("No records found. Exiting.")
        return

    df = pd.DataFrame(records)

    # Ensure data types
    df["date_saved"] = pd.to_datetime(df["date_saved"])

    # Save
    DATA_DIR.mkdir(exist_ok=True)
    df.to_parquet(INDEX_PATH)
    print(f"Index saved to {INDEX_PATH}")

if __name__ == "__main__":
    build_index()


