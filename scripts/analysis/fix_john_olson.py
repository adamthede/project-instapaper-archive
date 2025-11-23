#!/usr/bin/env python3
"""Find and fix the John Olson duplication bug."""
import pandas as pd
import frontmatter
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
INDEX_PATH = DATA_DIR / "archive_index.parquet"

df = pd.read_parquet(INDEX_PATH)

# Find the problematic article
problem_article = df[df['title'].str.contains("101 Ways To Build Wealth", na=False)].iloc[0]

file_path = Path(problem_article['file_path'])
print(f"\n🔍 Found problematic article:")
print(f"   File: {file_path.name}")
print(f"   Title: {problem_article['title']}")

# Read the file
with open(file_path, "r", encoding="utf-8", errors="replace") as f:
    content_raw = f.read()
    clean_content = "".join(
        ch for ch in content_raw
        if (ord(ch) >= 32 or ch in "\n\r\t") and not (0x7F <= ord(ch) <= 0x9F)
    )

post = frontmatter.loads(clean_content)

# Check the people field
people = post.metadata.get('ai_people', post.metadata.get('people', []))

print(f"\n📊 People field analysis:")
print(f"   Total entries in people list: {len(people)}")
print(f"   Unique people: {len(set(people))}")

from collections import Counter
people_counts = Counter(people)

print(f"\n🔝 Top duplicates in this article:")
for person, count in people_counts.most_common(10):
    print(f"   {person}: {count} times")

# Fix: Deduplicate the people list
if len(people) != len(set(people)):
    print(f"\n🔧 Fixing duplicates...")

    # Deduplicate all list fields
    for field in ['ai_people', 'ai_topics', 'ai_orgs', 'ai_locations', 'ai_concepts']:
        if field in post.metadata:
            original = post.metadata[field]
            if isinstance(original, list):
                # Preserve order while deduplicating
                deduplicated = list(dict.fromkeys(original))
                post.metadata[field] = deduplicated
                if len(original) != len(deduplicated):
                    print(f"   {field}: {len(original)} → {len(deduplicated)}")

    # Write back
    with open(file_path, "wb") as f:
        frontmatter.dump(post, f)

    print(f"\n✅ Fixed! Rebuild index and reload dashboard.")
else:
    print(f"\n✅ No duplicates found (weird!)")

