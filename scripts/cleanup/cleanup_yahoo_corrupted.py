#!/usr/bin/env python3
"""
Cleanup script for Yahoo News corrupted articles.

Finds articles with the "Prince Harry says King Charles" description pattern,
removes corrupted data, and flags them.
"""
import os
import frontmatter
from pathlib import Path
from tqdm import tqdm
from dotenv import load_dotenv

load_dotenv()

# Config
VAULT_PATH = Path(
    os.getenv(
        "INSTAPAPER_VAULT_PATH",
        str(Path.home() / "Obsidian" / "Vault" / "Instapaper"),
    )
)

YAHOO_PROBLEMS_FILE = Path(__file__).parent.parent / "yahoo_problems.txt"
CORRUPTION_PATTERN = "Prince Harry says King Charles"

def scan_for_yahoo_corruption(vault_path: Path) -> list:
    """
    Scan all markdown files for Yahoo corruption pattern.
    Returns list of file paths with the corruption.
    """
    print(f"\n🔍 Scanning {vault_path} for Yahoo corruption pattern...")
    print(f"   Pattern: '{CORRUPTION_PATTERN}'\n")

    corrupted_files = []
    all_files = list(vault_path.rglob("*.md"))

    for file_path in tqdm(all_files, desc="Scanning files"):
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content_raw = f.read()
                # Check if corruption pattern exists
                if CORRUPTION_PATTERN in content_raw:
                    corrupted_files.append(file_path)
        except Exception as e:
            pass  # Skip files that can't be read

    return corrupted_files

def cleanup_yahoo_article(file_path: Path, dry_run: bool = False) -> tuple[bool, list]:
    """
    Remove corrupted description and AI fields, add corruption flag.

    Returns: (success, list_of_removed_fields)
    """
    try:
        # Read file
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content_raw = f.read()
            clean_content = "".join(
                ch for ch in content_raw
                if (ord(ch) >= 32 or ch in "\n\r\t") and not (0x7F <= ord(ch) <= 0x9F)
            )

        post = frontmatter.loads(clean_content)

        # Remove corrupted fields
        fields_to_remove = [
            "description",  # Corrupted Yahoo sidebar
            "ai_topics", "ai_people", "ai_orgs", "ai_locations",
            "ai_concepts", "ai_sentiment", "ai_emotion", "ai_summary",
            "topics", "people", "orgs", "locations", "concepts",
            "sentiment", "emotion", "summary"
        ]

        removed_fields = []
        for field in fields_to_remove:
            if field in post.metadata:
                if not dry_run:
                    del post.metadata[field]
                removed_fields.append(field)

        if not dry_run:
            # Add corruption flags
            post.metadata["content_corrupted"] = True
            post.metadata["corruption_reason"] = "yahoo_sidebar_content"
            post.metadata["corruption_note"] = "Yahoo News sidebar/navigation content instead of article"

            # Write back
            with open(file_path, "wb") as f:
                frontmatter.dump(post, f)

        return True, removed_fields

    except Exception as e:
        return False, []

def main():
    """Main cleanup function."""
    import sys

    # Parse arguments
    args = sys.argv[1:]
    dry_run = False
    auto_scan = False

    for arg in args:
        if arg in ["dry-run", "test", "preview"]:
            dry_run = True
        elif arg == "scan":
            auto_scan = True

    print("\n📰 Yahoo News Corruption Cleanup")
    print("=" * 60)

    if auto_scan or not YAHOO_PROBLEMS_FILE.exists():
        # Scan vault for corrupted files
        corrupted_files = scan_for_yahoo_corruption(VAULT_PATH)

        if not corrupted_files:
            print("\n✅ No Yahoo corruption found!")
            return

        print(f"\n📋 Found {len(corrupted_files)} corrupted Yahoo News articles")

        # Save to file
        with open(YAHOO_PROBLEMS_FILE, "w") as f:
            for file_path in corrupted_files:
                f.write(f"{file_path}\n")

        print(f"📋 Saved to: {YAHOO_PROBLEMS_FILE}")

        file_paths = corrupted_files
    else:
        # Read from existing file
        print(f"📋 Reading from: {YAHOO_PROBLEMS_FILE}")
        with open(YAHOO_PROBLEMS_FILE, "r") as f:
            file_paths = [Path(line.strip()) for line in f if line.strip()]

        print(f"Found {len(file_paths)} articles to clean")

    if dry_run:
        print("🔍 DRY RUN MODE - No files will be modified")

    print()

    success_count = 0
    fail_count = 0

    for file_path in tqdm(file_paths, desc="Cleaning articles"):
        if not file_path.exists():
            fail_count += 1
            continue

        success, removed_fields = cleanup_yahoo_article(file_path, dry_run=dry_run)

        if success:
            success_count += 1
            if dry_run and removed_fields:
                tqdm.write(f"✓ Would clean: {file_path.name}")
        else:
            fail_count += 1

    print("\n" + "=" * 60)

    if dry_run:
        print("🔍 Dry Run Complete - No Changes Made")
        print(f"   Would clean: {success_count} files")
    else:
        print("✅ Cleanup Complete!")
        print(f"   Successfully cleaned: {success_count} files")
        print(f"   Failed: {fail_count} files")
        print(f"\n📝 Next steps:")
        print(f"   1. Rebuild index: python scripts/build_index.py")
        print(f"   2. Continue with enrichment")

    print()

if __name__ == "__main__":
    main()

