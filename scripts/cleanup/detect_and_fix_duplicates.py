#!/usr/bin/env python3
"""
Detect and fix duplicate entries in frontmatter list fields across entire archive.
"""
import os
import frontmatter
from pathlib import Path
from tqdm import tqdm
from collections import Counter
from dotenv import load_dotenv

load_dotenv()

VAULT_PATH = Path(
    os.getenv(
        "INSTAPAPER_VAULT_PATH",
        str(Path.home() / "Obsidian" / "Vault" / "Instapaper"),
    )
)

LIST_FIELDS = ['ai_people', 'ai_topics', 'ai_orgs', 'ai_locations', 'ai_concepts',
               'people', 'topics', 'orgs', 'locations', 'concepts']

def analyze_article(file_path: Path) -> dict:
    """
    Analyze an article for duplicate entries in list fields.
    Returns dict with duplicate info.
    """
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content_raw = f.read()
            clean_content = "".join(
                ch for ch in content_raw
                if (ord(ch) >= 32 or ch in "\n\r\t") and not (0x7F <= ord(ch) <= 0x9F)
            )

        post = frontmatter.loads(clean_content)

        result = {
            'file_path': file_path,
            'title': post.metadata.get('title', file_path.stem),
            'has_duplicates': False,
            'duplicates': {}
        }

        for field in LIST_FIELDS:
            if field in post.metadata:
                value = post.metadata[field]
                if isinstance(value, list) and len(value) > 0:
                    original_len = len(value)
                    unique_len = len(set(value))

                    if original_len != unique_len:
                        result['has_duplicates'] = True
                        # Count duplicates
                        counts = Counter(value)
                        duplicates = {item: count for item, count in counts.items() if count > 1}
                        result['duplicates'][field] = {
                            'original': original_len,
                            'unique': unique_len,
                            'duplicate_items': duplicates
                        }

        return result
    except Exception as e:
        return None

def fix_article(file_path: Path) -> bool:
    """
    Remove duplicates from all list fields in an article.
    Returns True if successful.
    """
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content_raw = f.read()
            clean_content = "".join(
                ch for ch in content_raw
                if (ord(ch) >= 32 or ch in "\n\r\t") and not (0x7F <= ord(ch) <= 0x9F)
            )

        post = frontmatter.loads(clean_content)

        # Deduplicate all list fields
        fixed_count = 0
        for field in LIST_FIELDS:
            if field in post.metadata:
                original = post.metadata[field]
                if isinstance(original, list):
                    # Preserve order while deduplicating
                    deduplicated = list(dict.fromkeys(original))
                    if len(original) != len(deduplicated):
                        post.metadata[field] = deduplicated
                        fixed_count += 1

        if fixed_count > 0:
            # Write back
            with open(file_path, "wb") as f:
                frontmatter.dump(post, f)
            return True

        return False
    except Exception as e:
        return False

def main():
    import sys

    # Parse arguments
    args = sys.argv[1:]
    dry_run = "dry-run" in args or "test" in args
    fix = "fix" in args
    verbose = "verbose" in args or "v" in args

    # Threshold for fixing (only fix articles with this many duplicates or more)
    threshold = 10
    for arg in args:
        if arg.startswith("threshold="):
            threshold = int(arg.split("=")[1])

    print("\n🔍 Duplicate Detection in Frontmatter")
    print("=" * 80)

    if dry_run:
        print("🔍 DRY RUN MODE - No changes will be made\n")

    # Scan all markdown files
    all_files = list(VAULT_PATH.rglob("*.md"))
    all_files = [f for f in all_files if not f.name.startswith("._")]

    print(f"Scanning {len(all_files)} articles...\n")

    articles_with_duplicates = []
    total_duplicates_by_field = Counter()
    worst_offenders = []

    for file_path in tqdm(all_files, desc="Analyzing articles"):
        result = analyze_article(file_path)

        if result and result['has_duplicates']:
            articles_with_duplicates.append(result)

            # Track which fields have duplicates
            for field in result['duplicates'].keys():
                total_duplicates_by_field[field] += 1

            # Track worst offenders (most duplicates in a single field)
            for field, stats in result['duplicates'].items():
                worst_offenders.append({
                    'title': result['title'],
                    'field': field,
                    'original': stats['original'],
                    'unique': stats['unique'],
                    'waste': stats['original'] - stats['unique']
                })

    # Sort worst offenders by waste
    worst_offenders.sort(key=lambda x: x['waste'], reverse=True)

    # Report
    print(f"\n" + "=" * 80)
    print(f"✅ Scan Complete!")
    print(f"   Total articles scanned: {len(all_files)}")
    print(f"   Articles with duplicates: {len(articles_with_duplicates)}")
    print(f"   Percentage: {len(articles_with_duplicates)/len(all_files)*100:.2f}%")

    if total_duplicates_by_field:
        print(f"\n📊 Duplicates by Field:")
        for field, count in total_duplicates_by_field.most_common():
            print(f"   {field}: {count} articles")

    if worst_offenders:
        print(f"\n🔥 Top 20 Worst Offenders:")
        for item in worst_offenders[:20]:
            print(f"   {item['title'][:70]}")
            print(f"      {item['field']}: {item['original']} entries → {item['unique']} unique ({item['waste']} duplicates)")

    # Filter articles by threshold
    articles_to_fix = []
    for result in articles_with_duplicates:
        max_waste = 0
        for field, stats in result['duplicates'].items():
            waste = stats['original'] - stats['unique']
            max_waste = max(max_waste, waste)

        if max_waste >= threshold:
            articles_to_fix.append((result, max_waste))

    articles_to_fix.sort(key=lambda x: x[1], reverse=True)

    print(f"\n🎯 Articles exceeding threshold (>={threshold} duplicates): {len(articles_to_fix)}")

    # Fix if requested
    if fix and not dry_run:
        print(f"\n🔧 Fixing {len(articles_to_fix)} articles with >={threshold} duplicates...")

        fixed_count = 0
        for result, waste in tqdm(articles_to_fix, desc="Fixing articles"):
            if fix_article(result['file_path']):
                fixed_count += 1

        print(f"\n✅ Fixed {fixed_count} articles!")
        print(f"📝 Next step: python scripts/build_index.py")
    elif fix and dry_run:
        print(f"\n💡 Dry run mode - run without 'dry-run' to actually fix articles")
        print(f"   Would fix: {len(articles_to_fix)} articles (threshold >={threshold})")
    else:
        print(f"\n💡 To fix articles with extreme duplication (>={threshold}), run:")
        print(f"   python scripts/detect_and_fix_duplicates.py fix")
        print(f"\n💡 To fix ALL {len(articles_with_duplicates)} articles with any duplicates:")
        print(f"   python scripts/detect_and_fix_duplicates.py fix threshold=1")

    if verbose and articles_with_duplicates:
        print(f"\n📋 Detailed List of All Articles with Duplicates:")
        for result in articles_with_duplicates[:50]:
            print(f"\n  {result['title']}")
            print(f"     File: {result['file_path'].name}")
            for field, stats in result['duplicates'].items():
                print(f"     {field}: {stats['original']} → {stats['unique']}")
                if stats['duplicate_items']:
                    for item, count in list(stats['duplicate_items'].items())[:5]:
                        print(f"       - '{item}' appears {count}x")

    print()

if __name__ == "__main__":
    main()

