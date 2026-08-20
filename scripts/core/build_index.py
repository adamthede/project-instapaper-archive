#!/usr/bin/env python3
import os
import sys
import pandas as pd
import frontmatter
from pathlib import Path
from datetime import datetime
import textstat
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
import entity_hygiene  # noqa: E402

# Load environment variables from .env if present
load_dotenv()

# Config
# Location of the Instapaper Markdown archive.
# Can be overridden by setting INSTAPAPER_VAULT_PATH in your environment or .env file.
VAULT_PATH = Path(
    os.getenv(
        "INSTAPAPER_VAULT_PATH",
        str(Path.home() / "Obsidian" / "Vault" / "Instapaper"),
    )
)
# Anchored to the repo root, which is where dashboard/app.py reads the index
# from. This script used to live at scripts/build_index.py, where
# `parent.parent` was the repo root; moving it into scripts/core/ silently
# repointed the output at scripts/data/ while the dashboard kept reading
# data/, so the index had to be copied across by hand after every rebuild.
REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
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

        # Which reading era this article came from. The legacy importer and the
        # Matter sync both stamp `source` explicitly; the Instapaper exporter
        # predates the field, so its files are recognised by carrying an
        # instapaper_id instead.
        source = fm.get("source", None)
        if not source:
            source = "instapaper" if instapaper_id is not None else "unknown"

        # Matter carries podcasts, PDFs and tweets alongside articles; the
        # Instapaper era was articles only.
        content_type = fm.get("content_type", "article")
        matter_id = fm.get("matter_id", None)

        # Times Matter saw this article read again after it was already in the
        # archive. Recorded on the original file; the first read date is never
        # revised, so this is the only trace a re-read leaves.
        reread_count = fm.get("matter_reread_count", 0)

        # Date Handling - support both Instapaper and legacy formats
        # Instapaper articles use "date_saved" and "date_archived"
        # Legacy articles use "date_published"
        date_saved = fm.get("date_saved", None)

        if date_saved is None:
            # Fallback to date_published for legacy articles
            date_saved = fm.get("date_published", None)

        if date_saved is None:
            # Last fallback to date_imported
            date_saved = fm.get("date_imported", None)

        if isinstance(date_saved, str):
            try:
                date_saved = datetime.strptime(date_saved, "%Y-%m-%d").date()
            except ValueError:
                date_saved = None
        elif isinstance(date_saved, datetime):
            date_saved = date_saved.date()

        # Date Archived - when the article was actually read/archived
        date_archived = fm.get("date_archived", None)

        if isinstance(date_archived, str):
            try:
                date_archived = datetime.strptime(date_archived, "%Y-%m-%d").date()
            except ValueError:
                date_archived = None
        elif isinstance(date_archived, datetime):
            date_archived = date_archived.date()

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
            "matter_id": matter_id,
            "reread_count": reread_count,
            "source": source,
            "content_type": content_type,
            "title": title,
            "url": url,
            "author": author,
            "date_saved": date_saved,
            "date_archived": date_archived,
            "word_count": word_count,
            # Carried so downstream candidate selection (enrich_archive_local)
            # can exclude junk-scrape files instead of re-judging them nightly.
            "content_corrupted": bool(fm.get("content_corrupted", False)),
            # The remaining five keys the 2026-08-11 audit found written to
            # markdown but never carried into the index - the deep-dive pages
            # (reading-progress, highlight density, tag facets) need them.
            "matter_status": fm.get("matter_status"),
            "matter_progress": fm.get("matter_progress"),
            "matter_highlight_count": fm.get("matter_highlight_count"),
            "date_saved_source": fm.get("date_saved_source"),
            "tags": list(fm.get("tags") or []),
            "favorite": bool(fm.get("favorite", False)),
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

def _norm_title_key(t):
    import re as _re
    return _re.sub(r"[^a-z0-9]+", " ", str(t or "").lower()).strip()


def dedupe_articles(df):
    """Collapse duplicate ARTICLES at the index layer; vault files stay put.

    Two real duplicate classes, measured 2026-08-19:

    1. Same instapaper_id, two files (582 bookmarks / 1,222 rows): the
       Obsidian exporter wrote a save-date-named top-level copy with no
       archive date, and the CSV bulk import wrote an Archived/ copy with
       the real one. Keep the copy with a date_archived (tiebreak: has an
       AI summary, then longer content), so reading dates stay honest.

    2. Matter-era re-push (49 same-title cross-source pairs): for a time
       Adam pushed Matter reads into Instapaper because only Instapaper had
       an API. Where a matter row and an instapaper row share a normalized
       title and their dates fall within 30 days, keep the MATTER row
       (Adam's rule, 2026-08-19) - its archive date is the truer read date.
    """
    before = len(df)

    def _quality(row):
        return (
            int(pd.notna(row.get("date_archived"))),
            int(bool(str(row.get("summary") or "").strip())),
            int(row.get("word_count") or 0),
        )

    ip = df["instapaper_id"].notna()
    keep_idx = []
    for _, grp in df[ip].groupby("instapaper_id"):
        if len(grp) > 1:
            keep_idx.append(grp.apply(_quality, axis=1).idxmax())
        else:
            keep_idx.append(grp.index[0])
    df = pd.concat([df[~ip], df.loc[sorted(set(keep_idx))]])
    same_id_dropped = before - len(df)

    matter = df[df["source"] == "matter"]
    insta = df[df["source"] == "instapaper"]
    m_by_title = {}
    for _, r in matter.iterrows():
        m_by_title.setdefault(_norm_title_key(r["title"]), []).append(r)
    drop = set()
    for idx, r in insta.iterrows():
        key = _norm_title_key(r["title"])
        if not key or key not in m_by_title:
            continue
        i_date = r["date_archived"] if pd.notna(r["date_archived"]) else r["date_saved"]
        if pd.isna(i_date):
            continue
        for mrow in m_by_title[key]:
            m_date = mrow["date_archived"]
            if pd.notna(m_date) and abs((m_date - i_date).days) <= 30:
                drop.add(idx)
                break
    df = df.drop(index=drop)

    print(f"Deduped: {same_id_dropped} same-instapaper-id rows, "
          f"{len(drop)} matter-superseded rows ({before} -> {len(df)}).")
    return df.reset_index(drop=True)


def build_index():
    print(f"Scanning vault at: {VAULT_PATH}")

    if not VAULT_PATH.exists():
        print(f"Error: Vault path not found: {VAULT_PATH}")
        return

    records = []
    # Scan for markdown files, excluding macOS resource fork files (._*)
    all_md_files = VAULT_PATH.rglob("*.md")
    files = [f for f in all_md_files if not f.name.startswith("._")]
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
    df["date_archived"] = pd.to_datetime(df["date_archived"])

    df = dedupe_articles(df)

    # Fabricated entity clusters, killed at the source. Scrubbed AFTER dedupe
    # so the printed report describes the index that actually ships, and
    # before to_parquet so every downstream reader - the static site, the
    # Streamlit dashboard (which does not filter content_corrupted at all) -
    # inherits the fix without repeating it.
    df, _ = entity_hygiene.scrub(df, column="people")

    # Save
    DATA_DIR.mkdir(exist_ok=True)
    df.to_parquet(INDEX_PATH)
    print(f"Index saved to {INDEX_PATH}")

if __name__ == "__main__":
    build_index()


