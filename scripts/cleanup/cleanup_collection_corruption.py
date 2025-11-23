#!/usr/bin/env python3
"""
Find and flag articles where LLM recognized it as a 'collection of articles'
but didn't mark as corrupted.
"""
import os
import frontmatter
import pandas as pd
from pathlib import Path
from tqdm import tqdm
from dotenv import load_dotenv

load_dotenv()

DATA_DIR = Path(__file__).parent.parent / "data"
INDEX_PATH = DATA_DIR / "archive_index.parquet"

# Known problematic domains
PROBLEMATIC_DOMAINS = [
    'yahoo.com',
    'yahoo',
    'fastcodesign.com',
    'co.design',
    'codesign.com',
]

# Patterns indicating sidebar/collection corruption
# (only check these on problematic domains)
CORRUPTION_PATTERNS = [
    "this collection of articles",
    "collection of articles covers",
    "collection of articles from",
    "diverse topics",
    "range of topics",
]

def is_likely_corrupted(summary, url):
    """
    Check if article is likely corrupted based on domain + summary patterns.
    Only flags articles from known problematic domains.
    """
    if not summary or not isinstance(summary, str):
        return False

    if not url or not isinstance(url, str):
        return False

    # Check if from problematic domain
    url_lower = url.lower()
    is_problematic_domain = any(domain in url_lower for domain in PROBLEMATIC_DOMAINS)

    if not is_problematic_domain:
        return False  # Don't flag if not from Yahoo/Co.design

    # Now check for corruption patterns in summary
    summary_lower = summary.lower()
    for pattern in CORRUPTION_PATTERNS:
        if pattern in summary_lower:
            return True

    return False

def cleanup_article(file_path):
    """Remove AI fields and flag as corrupted."""
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content_raw = f.read()
            clean_content = "".join(
                ch for ch in content_raw
                if (ord(ch) >= 32 or ch in "\n\r\t") and not (0x7F <= ord(ch) <= 0x9F)
            )

        post = frontmatter.loads(clean_content)

        # Remove AI fields
        ai_fields = [
            "ai_topics", "ai_people", "ai_orgs", "ai_locations",
            "ai_concepts", "ai_sentiment", "ai_emotion", "ai_summary",
            "topics", "people", "orgs", "locations", "concepts",
            "sentiment", "emotion", "summary", "description"
        ]

        for field in ai_fields:
            if field in post.metadata:
                del post.metadata[field]

        # Add corruption flags
        post.metadata["content_corrupted"] = True
        post.metadata["corruption_reason"] = "sidebar_collection_missed_by_llm"
        post.metadata["corruption_note"] = "Summary indicates 'collection of articles' - sidebar content"

        # Write back
        with open(file_path, "wb") as f:
            frontmatter.dump(post, f)

        return True
    except Exception as e:
        return False

def main():
    import sys

    args = sys.argv[1:]
    dry_run = "dry-run" in args or "test" in args
    fix = "fix" in args

    print("\n🔍 Collection Corruption Detection")
    print("=" * 80)

    if not INDEX_PATH.exists():
        print("Index not found. Run build_index.py first.")
        return

    df = pd.read_parquet(INDEX_PATH)

    print(f"Analyzing {len(df)} articles for 'collection' corruption patterns...\n")

    if dry_run:
        print("🔍 DRY RUN MODE - No changes will be made\n")

    # Find articles with corruption patterns
    corrupted_articles = []

    for _, row in df.iterrows():
        summary = row.get('summary', '')
        url = row.get('url', '')
        is_corrupted = row.get('content_corrupted', False)

        # Skip already flagged articles
        if is_corrupted:
            continue

        if is_likely_corrupted(summary, url):
            corrupted_articles.append(row)

    print(f"=" * 80)
    print(f"Found {len(corrupted_articles)} articles with collection corruption patterns")

    if corrupted_articles:
        print(f"\n📋 Sample articles (first 20):\n")
        for row in corrupted_articles[:20]:
            title = row['title'][:70]
            summary = row['summary'][:100] if row.get('summary') else ''
            url = row.get('url', '')

            print(f"  {title}")
            print(f"    Summary: {summary}...")

            # Check source
            if 'fastcodesign' in url or 'co.design' in url:
                print(f"    ⚠️  Co.design article")
            elif 'yahoo' in url:
                print(f"    ⚠️  Yahoo article")

            print()

    if fix and not dry_run and corrupted_articles:
        print(f"\n🔧 Flagging {len(corrupted_articles)} articles as corrupted...")

        fixed_count = 0
        for row in tqdm(corrupted_articles, desc="Cleaning articles"):
            file_path = Path(row['file_path'])
            if file_path.exists() and cleanup_article(file_path):
                fixed_count += 1

        print(f"\n✅ Flagged {fixed_count} articles as corrupted!")
        print(f"📝 Next steps:")
        print(f"   1. Rebuild index: python scripts/build_index.py")
        print(f"   2. Reload dashboard")
    elif fix and dry_run:
        print(f"\n💡 Dry run - run without 'dry-run' to actually flag these articles")
    else:
        print(f"\n💡 To flag these articles as corrupted, run:")
        print(f"   python scripts/cleanup_collection_corruption.py fix")

    print()

if __name__ == "__main__":
    main()

