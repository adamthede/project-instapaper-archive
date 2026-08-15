#!/usr/bin/env python3
"""
Enrichment script using Google Gemini 2.5 Flash-Lite API.
Much faster and cheaper than local models for bulk processing.

Cost: ~$0.40 for 10,000 articles (5x cheaper than regular Flash)
Time: 20-60 minutes with parallel processing (20 threads)
"""
import os
import frontmatter
import pandas as pd
from pathlib import Path
from tqdm import tqdm
import time
from dotenv import load_dotenv
import google.generativeai as genai
from concurrent.futures import ThreadPoolExecutor, as_completed

load_dotenv()

# Config
# Anchored to the repo root, same fix as build_index.py: this script also
# predates the move into scripts/core/, where `parent.parent` silently became
# scripts/ and pointed at a stale copy of the index in scripts/data/.
REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
INDEX_PATH = DATA_DIR / "archive_index.parquet"
FAILURE_LOG = REPO_ROOT / "scripts" / "enrichment_failures.log"
MODEL_NAME = "gemini-2.5-flash-lite"  # Best price-performance, 5x cheaper than Flash
MAX_WORKERS = 20  # Parallel processing threads

def log_failure(title: str, file_path: str, reason: str, failure_log_path: Path):
    """Append failure to log file."""
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] {title}\n  File: {file_path}\n  Reason: {reason}\n\n"

    with open(failure_log_path, "a", encoding="utf-8") as f:
        f.write(log_entry)

def build_prompt(content):
    """The one shared prompt - the local (LM Studio) variant imports this
    so the two backends can never drift apart."""
    return f"""Analyze the following article text deeply. I need structured insights for a personal knowledge base.

Provide the following output fields exactly as formatted below:

CONTENT_VALID: [Respond with ONLY one word: YES or NO.
Mark NO ONLY if you detect CLEAR corruption patterns like:
- Multiple separate article previews/summaries (e.g., "Article 1: ... Article 2: ... Article 3: ...")
- Login screens or "Please sign in" messages
- List of unrelated headlines with no connecting narrative
- Placeholder text like "Content not available"

Mark YES if:
- The content tells a coherent story, even if multi-topical
- It has advertisements or links but still contains an actual article
- Multiple topics are discussed as part of one article's narrative
- You are uncertain (default to YES when unsure)]

TOPICS: [List 3-5 high-level themes/topics, comma-separated]
PEOPLE: [List key people mentioned, comma-separated. If none, write None]
ORGANIZATIONS: [List key companies/orgs mentioned, comma-separated. If none, write None]
LOCATIONS: [List notable cities/countries/regions/landmarks mentioned, comma-separated. If none, write None]
CONCEPTS: [List 3-8 important abstract concepts or products (e.g., "machine learning", "supply chains"), comma-separated. If none, write None]
SENTIMENT: [One word: Positive, Negative, or Neutral]
EMOTION: [One word describing the emotional tone, e.g., Inspiring, Alarming, Analytical, Nostalgic, Controversial]
SUMMARY: [A 2-3 sentence TL;DR summary capturing the core argument and conclusion. Max 80 words.]

Article Text:
{content[:10000]}
"""


def get_enrichment(content, api_key):
    """
    Sends the article content to Gemini for analysis.
    """
    prompt = build_prompt(content)

    try:
        # Configure client with API key
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(MODEL_NAME)

        # Generate response
        response = model.generate_content(prompt)

        return response.text
    except Exception as e:
        print(f"Error calling Gemini: {e}")
        return None

def parse_llm_response(response_text):
    """
    Parses the structured text back into a dictionary.
    Same logic as original enrich_archive.py
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
        "ai_summary": "",
        "content_valid": "YES"  # Default to valid
    }

    current_key = None

    def _titleize_concept(text: str) -> str:
        if not isinstance(text, str):
            return text
        words = []
        for w in text.split():
            if w.upper() in {"AI", "USA", "US", "EU", "UK"}:
                words.append(w.upper())
            else:
                words.append(w.capitalize())
        return " ".join(words)

    for line in lines:
        clean_line = line.strip()
        if not clean_line:
            continue

        if clean_line.startswith("CONTENT_VALID:"):
            val = clean_line.replace("CONTENT_VALID:", "").strip().upper()
            data["content_valid"] = val
        elif clean_line.startswith("TOPICS:"):
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
            current_key = "SUMMARY"
        elif current_key == "SUMMARY":
            data["ai_summary"] += " " + clean_line

    return data

def update_markdown_file(file_path, enrichment_data):
    """
    Writes the AI fields back to the Markdown frontmatter.
    """
    try:
        path = Path(file_path)

        # Robust read: Clean control characters
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content_raw = f.read()
            clean_content = "".join(ch for ch in content_raw if (ord(ch) >= 32 or ch in "\n\r\t") and not (0x7F <= ord(ch) <= 0x9F))

        post = frontmatter.loads(clean_content)

        # Check if LLM detected invalid content
        content_valid = enrichment_data.get("content_valid", "YES")
        if content_valid == "NO":
            # Mark as corrupted instead of adding AI fields
            post.metadata["content_corrupted"] = True
            post.metadata["corruption_reason"] = "llm_detected_invalid_content"
            post.metadata["corruption_note"] = "LLM detected content doesn't match title or appears to be navigation/sidebar"
        else:
            # Update metadata with new fields (excluding content_valid flag)
            for k, v in enrichment_data.items():
                if k != "content_valid":  # Don't store the validation flag
                    post.metadata[k] = v

        # Write back
        with open(path, "wb") as f:
            frontmatter.dump(post, f)

        return True
    except Exception as e:
        print(f"Error updating file {file_path}: {e}")
        return False

def process_single_article(row, api_key):
    """
    Process a single article - used for parallel processing.
    Returns: (success: bool, file_path: str, article_title: str)
    """
    file_path = row["file_path"]
    article_title = row["title"] if "title" in row else Path(file_path).stem

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content_raw = f.read()
            clean_content = "".join(ch for ch in content_raw if (ord(ch) >= 32 or ch in "\n\r\t") and not (0x7F <= ord(ch) <= 0x9F))
            post = frontmatter.loads(clean_content)
            content = post.content
    except Exception as e:
        return (False, file_path, article_title, f"Read error: {e}")

    if not content.strip():
        return (False, file_path, article_title, "Empty content")

    # Call Gemini API
    raw_response = get_enrichment(content, api_key)
    parsed_data = parse_llm_response(raw_response)

    if parsed_data:
        # Save back to file
        if update_markdown_file(file_path, parsed_data):
            return (True, file_path, article_title, None)
        else:
            return (False, file_path, article_title, "File write error")
    else:
        return (False, file_path, article_title, "API or parsing error")

def test_gemini_connection(api_key):
    """Test if Gemini API is working."""
    print(f"\n🔍 Testing Gemini API connection with model '{MODEL_NAME}'...")
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(MODEL_NAME)
        response = model.generate_content("Say 'OK'")
        print(f"✅ Gemini API is working! Model '{MODEL_NAME}' is responsive.")
        print(f"   Response: {response.text.strip()}\n")
        return True
    except Exception as e:
        print(f"❌ Error connecting to Gemini API: {e}")
        print(f"\nTroubleshooting:")
        print(f"1. Check your API key in .env: GEMINI_API_KEY=your_key_here")
        print(f"2. Get an API key from: https://aistudio.google.com/apikey")
        print(f"3. Make sure you have google-generativeai installed: pip install google-generativeai\n")
        return False

def run_enrichment(limit=None, force_update=False, parallel=True):
    """
    Main enrichment function with parallel processing support.
    """
    # Check for API key
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("❌ GEMINI_API_KEY not found in .env file")
        print("Please add: GEMINI_API_KEY=your_api_key_here")
        print("Get your key from: https://aistudio.google.com/apikey")
        return

    if not INDEX_PATH.exists():
        print("Index not found. Run build_index.py first.")
        return

    # Test Gemini before starting
    if not test_gemini_connection(api_key):
        print("Aborting enrichment due to Gemini API connection issues.")
        return

    df = pd.read_parquet(INDEX_PATH)

    # Check for columns
    if "topics" not in df.columns:
        df["topics"] = None

    # Determine candidates (same logic as original)
    if force_update:
        candidates = df
    else:
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

            if is_empty_list(topics):
                return True
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

    # ALWAYS filter out corrupted articles (whether force mode or not)
    if "content_corrupted" in df.columns:
        corrupted_count = len(df[df["content_corrupted"] == True])
        if corrupted_count > 0:
            print(f"   Skipping {corrupted_count} corrupted articles")
            candidates = candidates[candidates.get("content_corrupted", False) != True]

    print(f"Found {len(candidates)} articles needing enrichment (New or Upgrade).")

    if limit:
        candidates = candidates.head(limit)
        print(f"Processing limited batch of {limit} articles...")

    # Calculate estimated cost (for gemini-2.5-flash-lite)
    avg_input_tokens_per_article = 1550
    avg_output_tokens_per_article = 250
    total_input_tokens = len(candidates) * avg_input_tokens_per_article
    total_output_tokens = len(candidates) * avg_output_tokens_per_article
    # Flash-Lite pricing: $0.015 input, $0.06 output per 1M tokens
    estimated_cost = (total_input_tokens * 0.015) / 1000000 + (total_output_tokens * 0.06) / 1000000
    print(f"📊 Estimated cost: ${estimated_cost:.2f} for {len(candidates)} articles (using {MODEL_NAME})")
    print(f"📊 Processing mode: {'Parallel' if parallel else 'Sequential'} ({MAX_WORKERS} workers)" if parallel else "")

    success_count = 0
    failed_articles = []

    start_time = time.time()

    # Initialize failure log
    if FAILURE_LOG.exists():
        FAILURE_LOG.unlink()
    with open(FAILURE_LOG, "w", encoding="utf-8") as f:
        from datetime import datetime
        f.write(f"Enrichment Failure Log - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Model: {MODEL_NAME}\n")
        f.write("=" * 80 + "\n\n")

    if parallel and len(candidates) > 1:
        # Parallel processing
        print(f"\n🚀 Starting parallel enrichment with {MAX_WORKERS} workers...\n")

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            # Submit all jobs
            future_to_row = {
                executor.submit(process_single_article, row, api_key): (index, row)
                for index, row in candidates.iterrows()
            }

            # Process completed jobs with progress bar
            with tqdm(total=len(candidates), desc="Processing articles") as pbar:
                for future in as_completed(future_to_row):
                    index, row = future_to_row[future]
                    try:
                        success, file_path, title, error = future.result()
                        if success:
                            success_count += 1
                            tqdm.write(f"✅ {title}")
                        else:
                            failed_articles.append((title, error))
                            tqdm.write(f"⚠️  Failed: {title} - {error}")
                            # Log failure
                            log_failure(title, file_path, error, FAILURE_LOG)
                    except Exception as e:
                        title = row.get("title", "Unknown")
                        file_path = row.get("file_path", "Unknown")
                        failed_articles.append((title, str(e)))
                        tqdm.write(f"⚠️  Exception: {e}")
                        # Log exception
                        log_failure(title, file_path, f"Exception: {e}", FAILURE_LOG)

                    pbar.update(1)
    else:
        # Sequential processing (same as original)
        print("\n🔄 Starting sequential enrichment...\n")
        for index, row in tqdm(candidates.iterrows(), total=len(candidates), desc="Processing articles"):
            success, file_path, title, error = process_single_article(row, api_key)
            if success:
                success_count += 1
                tqdm.write(f"✅ {title}")
            else:
                failed_articles.append((title, error))
                tqdm.write(f"⚠️  Failed: {title} - {error}")
                # Log failure
                log_failure(title, file_path, error, FAILURE_LOG)

    elapsed_time = time.time() - start_time

    print(f"\n✅ Enrichment complete!")
    print(f"   Processed: {success_count}/{len(candidates)} articles")
    print(f"   Failed: {len(failed_articles)} articles")
    print(f"   Time: {elapsed_time/60:.1f} minutes ({elapsed_time:.0f} seconds)")
    print(f"   Rate: {success_count/(elapsed_time/60):.1f} articles/minute")

    if failed_articles:
        print(f"\n⚠️  Failed articles:")
        for title, error in failed_articles[:10]:  # Show first 10
            print(f"   - {title}: {error}")
        if len(failed_articles) > 10:
            print(f"   ... and {len(failed_articles) - 10} more")
        print(f"\n📋 Full failure log saved: {FAILURE_LOG}")
        print(f"   Review with: cat {FAILURE_LOG}")

    print("\n📝 Please re-run build_index.py to update the parquet index with these new values.")

if __name__ == "__main__":
    import sys
    limit_arg = None
    force_arg = False
    sequential = False

    # Simple arg parsing
    args = sys.argv[1:]
    for arg in args:
        if arg.isdigit():
            limit_arg = int(arg)
        elif arg == "force":
            force_arg = True
        elif arg == "sequential":
            sequential = True

    run_enrichment(limit=limit_arg, force_update=force_arg, parallel=not sequential)

