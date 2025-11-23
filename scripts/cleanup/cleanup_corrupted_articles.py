#!/usr/bin/env python3
"""
Cleanup script for corrupted articles.

Reads a list of corrupted article file paths, removes bad AI enrichment data,
and adds a corruption flag to the frontmatter.
"""
import frontmatter
from pathlib import Path
from tqdm import tqdm

def cleanup_article(file_path: Path, dry_run: bool = False) -> tuple[bool, list]:
    """
    Remove AI enrichment fields and add corruption flag.

    Returns: (success, list_of_removed_fields)
    """
    try:
        # Read file
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content_raw = f.read()
            # Sanitize control characters
            clean_content = "".join(
                ch for ch in content_raw
                if (ord(ch) >= 32 or ch in "\n\r\t") and not (0x7F <= ord(ch) <= 0x9F)
            )

        post = frontmatter.loads(clean_content)

        # Remove all AI enrichment fields AND corrupted Instapaper metadata
        ai_fields = [
            "ai_topics", "ai_people", "ai_orgs", "ai_locations",
            "ai_concepts", "ai_sentiment", "ai_emotion", "ai_summary",
            # Also remove old field names if they exist
            "topics", "people", "orgs", "locations", "concepts",
            "sentiment", "emotion", "summary"
        ]

        # Also remove corrupted Instapaper metadata
        instapaper_fields = [
            "description",  # Often contains sidebar content in corrupted articles
        ]

        removed_fields = []
        for field in ai_fields + instapaper_fields:
            if field in post.metadata:
                if not dry_run:
                    del post.metadata[field]
                removed_fields.append(field)

        if not dry_run:
            # Add corruption flags
            post.metadata["content_corrupted"] = True
            post.metadata["corruption_reason"] = "instapaper_sidebar_content"
            post.metadata["corruption_note"] = "Article content is website sidebar/navigation instead of actual article"

            # Write back
            with open(file_path, "wb") as f:
                frontmatter.dump(post, f)

        return True, removed_fields

    except Exception as e:
        print(f"Error processing {file_path}: {e}")
        return False, []

def main():
    """Main cleanup function."""
    import sys

    # Parse arguments
    args = sys.argv[1:]
    limit = None
    dry_run = False

    for arg in args:
        if arg.isdigit():
            limit = int(arg)
        elif arg in ["dry-run", "test", "preview"]:
            dry_run = True

    problems_file = Path(__file__).parent.parent / "problems.txt"

    if not problems_file.exists():
        print(f"❌ {problems_file} not found!")
        print("Please create problems.txt with list of corrupted file paths.")
        return

    # Read list of problematic files
    with open(problems_file, "r") as f:
        file_paths = [line.strip() for line in f if line.strip()]

    print(f"\n📚 Corrupted Article Cleanup")
    print("=" * 60)
    print(f"Found {len(file_paths)} articles to clean")

    if dry_run:
        print("🔍 DRY RUN MODE - No files will be modified")

    if limit:
        file_paths = file_paths[:limit]
        print(f"📊 Limited to first {limit} files")

    print()

    success_count = 0
    fail_count = 0

    for file_path_str in tqdm(file_paths, desc="Processing articles"):
        file_path = Path(file_path_str)

        if not file_path.exists():
            tqdm.write(f"⚠️  File not found: {file_path.name}")
            fail_count += 1
            continue

        success, removed_fields = cleanup_article(file_path, dry_run=dry_run)

        if success:
            success_count += 1
            if dry_run and removed_fields:
                tqdm.write(f"✓ Would remove from {file_path.name}: {', '.join(removed_fields[:3])}{'...' if len(removed_fields) > 3 else ''}")
        else:
            fail_count += 1

    print("\n" + "=" * 60)

    if dry_run:
        print("🔍 Dry Run Complete - No Changes Made")
        print(f"   Would clean: {success_count} files")
        print(f"   Would fail: {fail_count} files")
        print(f"\n💡 To actually clean these files, run without 'dry-run' argument")
    else:
        print("✅ Cleanup Complete!")
        print(f"   Successfully cleaned: {success_count} files")
        print(f"   Failed: {fail_count} files")
        print(f"\n📝 Next steps:")
        print(f"   1. Rebuild index: python scripts/build_index.py")
        print(f"   2. View in dashboard with corruption filter")
        print(f"   3. Optionally delete these files if you don't want to keep them")

    print()

if __name__ == "__main__":
    main()

