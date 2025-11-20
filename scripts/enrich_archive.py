#!/usr/bin/env python3
import os
import frontmatter
import ollama
import pandas as pd
from pathlib import Path
from tqdm import tqdm
import time

# Config
DATA_DIR = Path(__file__).parent.parent / "data"
INDEX_PATH = DATA_DIR / "archive_index.parquet"
MODEL_NAME = "qwen2.5:7b"

def get_enrichment(content):
    """
    Sends the article content to the local LLM for deeper analysis.
    """
    prompt = f"""
    Analyze the following article text deeply. I need structured insights for a personal knowledge base.

    Provide the following output fields exactly as formatted below:

    TOPICS: [List 3-5 high-level themes/topics, comma-separated]
    PEOPLE: [List key people mentioned, comma-separated. If none, write None]
    ORGANIZATIONS: [List key companies/orgs mentioned, comma-separated. If none, write None]
    LOCATIONS: [List notable cities/countries/regions/landmarks mentioned, comma-separated. If none, write None]
    CONCEPTS: [List 3-8 important abstract concepts or products (e.g., \"machine learning\", \"supply chains\"), comma-separated. If none, write None]
    SENTIMENT: [One word: Positive, Negative, or Neutral]
    EMOTION: [One word describing the emotional tone, e.g., Inspiring, Alarming, Analytical, Nostalgic, Controversial]
    SUMMARY: [A 2-3 sentence TL;DR summary capturing the core argument and conclusion. Max 80 words.]

    Article Text:
    {content[:3500]}
    """
    # Increased context window slightly to 3500 chars for better entity detection

    try:
        response = ollama.chat(model=MODEL_NAME, messages=[
            {'role': 'user', 'content': prompt},
        ])
        return response['message']['content']
    except Exception as e:
        print(f"Error calling Ollama: {e}")
        return None

def parse_llm_response(response_text):
    """
    Parses the richer structured text back into a dictionary.
    """
    if not response_text:
        return None

    lines = response_text.strip().split('\n')
    data = {
        "ai_topics": [],
        "ai_people": [],
        "ai_orgs": [],
        "ai_locations": [],
        "ai_concepts": [],
        "ai_sentiment": "Neutral",
        "ai_emotion": "Analytical",
        "ai_summary": ""
    }

    current_key = None

    # Helper for consistent capitalization of concepts
    def _titleize_concept(text: str) -> str:
        if not isinstance(text, str):
            return text
        words = []
        for w in text.split():
            # Preserve common acronyms
            if w.upper() in {"AI", "USA", "US", "EU", "UK"}:
                words.append(w.upper())
            else:
                words.append(w.capitalize())
        return " ".join(words)

    # Robust parsing to handle multi-line summaries or slight formatting deviations
    for line in lines:
        clean_line = line.strip()
        if not clean_line:
            continue

        if clean_line.startswith("TOPICS:"):
            val = clean_line.replace("TOPICS:", "").strip()
            data["ai_topics"] = [t.strip() for t in val.split(",") if t.strip() and t.strip().lower() != "none"]
        elif clean_line.startswith("PEOPLE:"):
            val = clean_line.replace("PEOPLE:", "").strip()
            data["ai_people"] = [t.strip() for t in val.split(",") if t.strip() and t.strip().lower() != "none"]
        elif clean_line.startswith("ORGANIZATIONS:"):
            val = clean_line.replace("ORGANIZATIONS:", "").strip()
            data["ai_orgs"] = [t.strip() for t in val.split(",") if t.strip() and t.strip().lower() != "none"]
        elif clean_line.startswith("LOCATIONS:"):
            val = clean_line.replace("LOCATIONS:", "").strip()
            data["ai_locations"] = [t.strip() for t in val.split(",") if t.strip() and t.strip().lower() != "none"]
        elif clean_line.startswith("CONCEPTS:"):
            val = clean_line.replace("CONCEPTS:", "").strip()
            raw_concepts = [t.strip() for t in val.split(",") if t.strip() and t.strip().lower() != "none"]
            data["ai_concepts"] = [_titleize_concept(t) for t in raw_concepts]
        elif clean_line.startswith("SENTIMENT:"):
            data["ai_sentiment"] = clean_line.replace("SENTIMENT:", "").strip()
        elif clean_line.startswith("EMOTION:"):
            data["ai_emotion"] = clean_line.replace("EMOTION:", "").strip()
        elif clean_line.startswith("SUMMARY:"):
            data["ai_summary"] = clean_line.replace("SUMMARY:", "").strip()
            current_key = "SUMMARY" # Flag to capture multi-line summary
        elif current_key == "SUMMARY":
            # Append continuation lines to summary
            data["ai_summary"] += " " + clean_line

    return data

def update_markdown_file(file_path, enrichment_data):
    """
    Writes the new rich AI fields back to the Markdown frontmatter.
    """
    try:
        path = Path(file_path)

        # Robust read: Clean control characters just like we do in build_index.py
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content_raw = f.read()
            # Sanitize
            clean_content = "".join(ch for ch in content_raw if (ord(ch) >= 32 or ch in "\n\r\t") and not (0x7F <= ord(ch) <= 0x9F))

        post = frontmatter.loads(clean_content)

        # Update metadata with new fields
        for k, v in enrichment_data.items():
            post.metadata[k] = v

        # Write back
        with open(path, "wb") as f:
            frontmatter.dump(post, f)

        return True
    except Exception as e:
        print(f"Error updating file {file_path}: {e}")
        return False

def run_enrichment(limit=None, force_update=False):
    if not INDEX_PATH.exists():
        print("Index not found. Run build_index.py first.")
        return

    df = pd.read_parquet(INDEX_PATH)

    # Check for new columns in dataframe to see if we need to backfill
    required_cols = ["ai_people", "ai_orgs", "ai_emotion"]

    # Determine candidates
    # 1. Articles with NO enrichment (topics is null/empty)
    # 2. Articles that have old enrichment (topics exist) but MISSING new fields
    #    (people, orgs, locations, concepts, emotion)

    if "topics" not in df.columns:
        df["topics"] = None

    # If force_update is True, we process everything regardless of state
    if force_update:
        candidates = df
    else:
        # Logic: Process if:
        # - topics is empty, OR
        # - any of the newer fields (people, orgs, locations, concepts, emotion) are missing/empty.
        #
        # This lets us "upgrade" articles enriched by older versions of the script
        # without reprocessing ones that already have the full schema.

        def is_empty_list(value):
            return value is None or (isinstance(value, (list, tuple, set)) and len(value) == 0)

        def is_blank(value):
            return value is None or (isinstance(value, str) and not value.strip())

        def needs_processing(row):
            topics = row.get("topics")
            people = row.get("people")
            orgs = row.get("orgs")
            locations = row.get("locations")
            concepts = row.get("concepts")
            emotion = row.get("emotion")

            # If never enriched at all
            if is_empty_list(topics):
                return True

            # If enriched previously but missing any of the newer fields
            if is_empty_list(people):
                return True
            if is_empty_list(orgs):
                return True
            if is_empty_list(locations):
                return True
            if is_empty_list(concepts):
                return True
            if is_blank(emotion):
                return True

            return False

        candidates = df[df.apply(needs_processing, axis=1)]

    print(f"Found {len(candidates)} articles needing enrichment (New or Upgrade).")

    if limit:
        candidates = candidates.head(limit)
        print(f"Processing limited batch of {limit} articles...")

    success_count = 0

    for index, row in tqdm(candidates.iterrows(), total=len(candidates)):
        file_path = row["file_path"]
        article_title = row["title"] if "title" in row else Path(file_path).stem

        # Print current status (tqdm handles the progress bar, we use write to not break it)
        tqdm.write(f"Processing: {article_title}")

        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content_raw = f.read()
                # Sanitize for safety
                clean_content = "".join(ch for ch in content_raw if (ord(ch) >= 32 or ch in "\n\r\t") and not (0x7F <= ord(ch) <= 0x9F))
                post = frontmatter.loads(clean_content)
                content = post.content
        except Exception as e:
            print(f"Skipping {Path(file_path).name} due to read error: {e}")
            continue

        if not content.strip():
            continue

        # Call AI
        raw_response = get_enrichment(content)
        parsed_data = parse_llm_response(raw_response)

        if parsed_data:
            # Save back to file
            if update_markdown_file(file_path, parsed_data):
                success_count += 1

    print(f"Enrichment complete. Updated {success_count} files.")
    print("Please re-run build_index.py to update the parquet index with these new values.")

if __name__ == "__main__":
    import sys
    limit_arg = 10
    force_arg = False

    # Simple arg parsing
    args = sys.argv[1:]
    if args:
        if args[0].isdigit():
            limit_arg = int(args[0])
        if "force" in args:
            force_arg = True

    run_enrichment(limit=limit_arg, force_update=force_arg)


