#!/usr/bin/env python3
"""Investigate the John Olson anomaly."""
import pandas as pd
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
INDEX_PATH = DATA_DIR / "archive_index.parquet"

df = pd.read_parquet(INDEX_PATH)

# Find articles with 'John Olson' in people
articles_with_jo = df[df['people'].apply(lambda x: x is not None and 'John Olson' in x)]

print(f"\n🔍 John Olson Investigation")
print("=" * 80)
print(f"Total articles in archive: {len(df)}")
print(f"Articles mentioning 'John Olson': {len(articles_with_jo)}")

if len(articles_with_jo) > 0:
    print(f"\n📄 Sample articles mentioning John Olson:\n")
    for _, row in articles_with_jo.head(20).iterrows():
        title = row['title']
        date = row['date_saved'].date() if pd.notna(row['date_saved']) else 'Unknown'
        people = row['people']
        print(f"  [{date}] {title}")
        print(f"     People: {people}\n")

# Check how the dashboard counts mentions
all_people = []
for people_list in df['people']:
    if people_list is not None:
        for person in people_list:
            all_people.append(person)

from collections import Counter
people_counts = Counter(all_people)

print(f"\n📊 Top 10 Most Mentioned People (dashboard counting method):")
for person, count in people_counts.most_common(10):
    print(f"  {person}: {count:,} mentions")

# Check for John Olson specifically
jo_count = people_counts.get('John Olson', 0)
print(f"\n🎯 John Olson total mentions: {jo_count:,}")
print(f"   (This is the number shown in dashboard)")

