#!/usr/bin/env python3
"""Find articles where the LLM recognized 'collection' but didn't flag as corrupt."""
import pandas as pd
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
INDEX_PATH = DATA_DIR / "archive_index.parquet"

df = pd.read_parquet(INDEX_PATH)

# Find articles with "collection" in summary but NOT marked as corrupted
collection_articles = df[
    (df['summary'].notna()) &
    (df['summary'].str.contains('collection', case=False, na=False)) &
    (df.get('content_corrupted', False) != True)
]

print(f"\n🔍 Articles Mentioning 'Collection' in Summary")
print("=" * 80)
print(f"Found {len(collection_articles)} articles\n")

# Check if these are actually corrupted
print("Sample articles:\n")
for _, row in collection_articles.head(20).iterrows():
    title = row['title']
    summary = row['summary'][:150]
    url = row.get('url', 'No URL')
    corrupted = row.get('content_corrupted', False)

    print(f"Title: {title}")
    print(f"Summary: {summary}...")
    print(f"URL: {url}")
    print(f"Corrupted flag: {corrupted}")
    print()

# Also check for Lululemon specifically
print("\n" + "=" * 80)
print("🔍 Checking Lululemon mentions specifically:\n")

lulu_articles = df[
    df['orgs'].apply(lambda x: x is not None and any('lululemon' in str(org).lower() for org in x))
]

print(f"Articles mentioning Lululemon: {len(lulu_articles)}")
print("\nSample Lululemon articles:\n")

for _, row in lulu_articles.head(10).iterrows():
    title = row['title']
    summary = row.get('summary', 'No summary')[:120]
    corrupted = row.get('content_corrupted', False)
    url = row.get('url', '')

    print(f"Title: {title}")
    print(f"Summary: {summary}...")
    print(f"Corrupted: {corrupted}")

    # Check if it's a Co.design or similar domain
    if 'fastcodesign' in url or 'co.design' in url:
        print(f"⚠️  WARNING: This is from Co.design (known corruption source)")

    print()

print("\n💡 If these look like corrupted sidebar content:")
print("   1. They should have been flagged by the LLM but weren't")
print("   2. The validation prompt may need improvement")
print("   3. You can manually add to problems.txt and run cleanup")

