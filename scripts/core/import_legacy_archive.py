#!/usr/bin/env python3
"""
Legacy Archive Importer
Converts PDF, Word, TXT, and RTF files to Markdown format.

SAFETY: This script only READS from source directories. Source files are NEVER deleted, modified, or moved.
Output files are written to a separate Markdown archive directory.
"""
import os
import re
import hashlib
from pathlib import Path
from datetime import datetime
from typing import Optional, Tuple, Dict
import json
from tqdm import tqdm
from dotenv import load_dotenv

# Try to import markitdown (unified converter)
try:
    from markitdown import MarkItDown
    HAS_MARKITDOWN = True
except ImportError:
    HAS_MARKITDOWN = False
    print("⚠️  markitdown not found. Install with: pip install markitdown")

load_dotenv()

# Config
SOURCE_BASE = Path(os.getenv("IMPORT_SOURCE_PATH", str(Path.home() / "Documents" / "Legacy-Articles")))
OUTPUT_DIR = Path(os.getenv("IMPORT_OUTPUT_PATH", str(Path.home() / "Documents" / "Markdown-Archive")))
MANIFEST_FILE = Path.home() / ".legacy_import_manifest.json"
FAILURE_LOG = Path(__file__).parent.parent / "import_failures.log"

# Source directories
SOURCE_DIRS = {
    "pdf": SOURCE_BASE / "Articles - PDF",
    "word": SOURCE_BASE / "Articles - Word",
    "txt_rtf": SOURCE_BASE / "Articles - TXT, RTF",
    "html": SOURCE_BASE / "Articles - HTML",
}

# File extensions to process
EXTENSIONS = {
    "pdf": [".pdf", ".PDF"],
    "word": [".doc", ".docx", ".DOC", ".DOCX"],
    "txt_rtf": [".txt", ".rtf", ".TXT", ".RTF"],
    "html": [".html", ".htm", ".HTML", ".HTM"],
}

def load_manifest():
    """Load manifest of already imported files."""
    if MANIFEST_FILE.exists():
        with open(MANIFEST_FILE, "r") as f:
            return json.load(f)
    return {}

def save_manifest(manifest):
    """Save manifest of imported files."""
    with open(MANIFEST_FILE, "w") as f:
        json.dump(manifest, f, indent=2)

def log_failure(file_path: Path, reason: str, failure_log_path: Path):
    """Append failure to log file."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] {file_path}\n  Reason: {reason}\n\n"

    with open(failure_log_path, "a", encoding="utf-8") as f:
        f.write(log_entry)

def calculate_file_hash(file_path: Path) -> str:
    """Calculate SHA256 hash of file for deduplication."""
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()

def parse_date_from_filename(filename: str) -> Optional[Tuple[datetime, str]]:
    """
    Extract date from filename using multiple patterns.

    Returns: (datetime, confidence) or (None, reason)
    """
    # Pattern 1: YYYY MM-DD (most common)
    match = re.match(r'(\d{4})\s+(\d{2})-(\d{2})\s+', filename)
    if match:
        year, month, day = match.groups()
        try:
            return datetime(int(year), int(month), int(day)), "filename-full"
        except ValueError:
            pass

    # Pattern 2: YYYY MM-DD HH-MM-SS
    match = re.match(r'(\d{4})\s+(\d{2})-(\d{2})\s+(\d{2})-(\d{2})-(\d{2})\s+', filename)
    if match:
        year, month, day, hour, minute, second = match.groups()
        try:
            return datetime(int(year), int(month), int(day), int(hour), int(minute), int(second)), "filename-full-timestamp"
        except ValueError:
            pass

    # Pattern 3: YYYY MM-DD HHMMSS (no separators in time)
    match = re.match(r'(\d{4})\s+(\d{2})-(\d{2})\s+(\d{2})(\d{2})(\d{2})\s+', filename)
    if match:
        year, month, day, hour, minute, second = match.groups()
        try:
            return datetime(int(year), int(month), int(day), int(hour), int(minute), int(second)), "filename-full-timestamp"
        except ValueError:
            pass

    # Pattern 4: YYYY MM (month only, default to first day)
    match = re.match(r'(\d{4})\s+(\d{2})\s+', filename)
    if match:
        year, month = match.groups()
        try:
            return datetime(int(year), int(month), 1), "filename-month-only"
        except ValueError:
            pass

    return None, "no-date-in-filename"

def extract_title_from_filename(filename: str) -> str:
    """
    Extract article title from filename, removing date and type prefixes.
    """
    # Remove extension
    name = Path(filename).stem

    # Remove date patterns
    name = re.sub(r'^\d{4}\s+\d{2}-\d{2}\s+\d{2}-\d{2}-\d{2}\s+', '', name)
    name = re.sub(r'^\d{4}\s+\d{2}-\d{2}\s+\d{2}\d{2}\d{2}\s+', '', name)
    name = re.sub(r'^\d{4}\s+\d{2}-\d{2}\s+', '', name)
    name = re.sub(r'^\d{4}\s+\d{2}\s+', '', name)

    # Remove common prefixes
    name = re.sub(r'^ARTICLE\s*-\s*', '', name, flags=re.IGNORECASE)
    name = re.sub(r'^PRESENTATION\s*-\s*', '', name, flags=re.IGNORECASE)
    name = re.sub(r'^Photography\s*-\s*', '', name, flags=re.IGNORECASE)

    # Remove trailing notes suffix
    name = re.sub(r'\s*\(NOTES\)\s*$', '', name, flags=re.IGNORECASE)

    # Clean up
    name = name.strip()

    return name if name else "Untitled"

def detect_content_type(filename: str) -> str:
    """Detect content type from filename."""
    filename_upper = filename.upper()
    if "ARTICLE" in filename_upper:
        return "article"
    elif "PRESENTATION" in filename_upper:
        return "presentation"
    elif "PHOTOGRAPHY" in filename_upper:
        return "photography"
    elif "NOTES" in filename_upper:
        return "notes"
    else:
        return "article"  # Default

def sanitize_filename(title: str, date: Optional[datetime]) -> str:
    """Create a safe filename for the markdown output."""
    # Use date prefix if available
    if date:
        date_str = date.strftime("%Y-%m-%d")
    else:
        date_str = "UNDATED"

    # Clean title for filename
    safe_title = re.sub(r'[<>:"/\\|?*]', '', title)
    safe_title = safe_title.strip()[:100]  # Limit length

    return f"{date_str} - {safe_title}.md"

def convert_file_to_markdown(file_path: Path) -> Optional[str]:
    """
    Convert file to Markdown using markitdown or pandoc fallback.
    Returns markdown content or None on failure.
    """
    # Try markitdown first
    if HAS_MARKITDOWN:
        try:
            md = MarkItDown()
            result = md.convert(str(file_path))
            return result.text_content
        except Exception as e:
            # If it's a .doc file and markitdown failed, try pandoc
            if file_path.suffix.lower() in ['.doc', '.DOC']:
                print(f"  ℹ️  MarkItDown failed for .doc, trying Pandoc...")
                return convert_doc_with_pandoc(file_path)
            else:
                print(f"  ⚠️  Conversion error: {e}")
                return None

    return None

def convert_doc_with_pandoc(file_path: Path) -> Optional[str]:
    """
    Fallback: Convert legacy .doc files using LibreOffice → DOCX → Markdown.
    Returns markdown content or None on failure.
    """
    import subprocess
    import tempfile

    try:
        # Step 1: Check if LibreOffice is installed
        soffice_paths = [
            "/Applications/LibreOffice.app/Contents/MacOS/soffice",
            "/usr/local/bin/soffice",
            "/opt/homebrew/bin/soffice"
        ]

        soffice = None
        for path in soffice_paths:
            if Path(path).exists():
                soffice = path
                break

        if not soffice:
            print(f"  ⚠️  LibreOffice not found. Install with: brew install --cask libreoffice")
            return None

        # Step 2: Convert .doc → .docx using LibreOffice
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # LibreOffice headless conversion
            result = subprocess.run(
                [
                    soffice,
                    '--headless',
                    '--convert-to', 'docx',
                    '--outdir', str(tmpdir_path),
                    str(file_path)
                ],
                capture_output=True,
                text=True,
                timeout=60
            )

            if result.returncode != 0:
                print(f"  ⚠️  LibreOffice conversion failed: {result.stderr[:100]}")
                return None

            # Find the generated .docx file
            docx_file = tmpdir_path / (file_path.stem + ".docx")

            if not docx_file.exists():
                print(f"  ⚠️  DOCX file not created")
                return None

            # Step 3: Convert .docx → Markdown using MarkItDown
            try:
                md = MarkItDown()
                result = md.convert(str(docx_file))
                print(f"  ✓  Converted via LibreOffice → DOCX → Markdown")
                return result.text_content
            except Exception as e:
                print(f"  ⚠️  MarkItDown failed on DOCX: {e}")
                return None

    except subprocess.TimeoutExpired:
        print(f"  ⚠️  LibreOffice conversion timed out")
        return None
    except Exception as e:
        print(f"  ⚠️  LibreOffice error: {e}")
        return None

def create_frontmatter(
    title: str,
    source_file: Path,
    date: Optional[datetime],
    date_source: str,
    file_hash: str,
    content_type: str,
    original_format: str
) -> str:
    """Create YAML frontmatter for the markdown file."""
    lines = ["---"]
    lines.append(f'title: "{title.replace(chr(34), chr(92) + chr(34))}"')  # Escape quotes
    lines.append(f'source: "legacy_{original_format}"')
    lines.append(f'original_file: "{source_file.name}"')
    lines.append(f'original_format: "{original_format}"')
    lines.append(f'content_type: "{content_type}"')

    if date:
        lines.append(f'date_published: "{date.strftime("%Y-%m-%d")}"')
        lines.append(f'date_source: "{date_source}"')
    else:
        lines.append('date_published: null')
        lines.append(f'date_source: "{date_source}"')

    lines.append(f'date_imported: "{datetime.now().strftime("%Y-%m-%d")}"')
    lines.append(f'file_hash: "{file_hash}"')
    lines.append("---")
    lines.append("")

    return "\n".join(lines)

def process_file(file_path: Path, manifest: Dict, dry_run: bool = False) -> Tuple[bool, str]:
    """
    Process a single file: convert to markdown and save.

    Returns: (success, message)
    """
    # Check if already imported
    file_hash = calculate_file_hash(file_path)
    if file_hash in manifest:
        return True, f"Already imported (skipped)"

    # Parse filename for metadata
    filename = file_path.name
    date, date_source = parse_date_from_filename(filename)
    title = extract_title_from_filename(filename)
    content_type = detect_content_type(filename)
    original_format = file_path.suffix.lower().lstrip('.')

    # Show which file is being processed
    print(f"  📄 Processing: {filename}")

    # Convert to markdown
    markdown_content = convert_file_to_markdown(file_path)
    if markdown_content is None:
        return False, "Conversion failed"

    # Create frontmatter
    frontmatter = create_frontmatter(
        title=title,
        source_file=file_path,
        date=date,
        date_source=date_source,
        file_hash=file_hash,
        content_type=content_type,
        original_format=original_format
    )

    # Combine frontmatter and content
    full_content = frontmatter + markdown_content

    # Generate output filename
    output_filename = sanitize_filename(title, date)
    output_path = OUTPUT_DIR / output_filename

    # Handle filename conflicts
    counter = 1
    while output_path.exists():
        if date:
            date_str = date.strftime("%Y-%m-%d")
        else:
            date_str = "UNDATED"
        safe_title = re.sub(r'[<>:"/\\|?*]', '', title).strip()[:100]
        output_filename = f"{date_str} - {safe_title} ({counter}).md"
        output_path = OUTPUT_DIR / output_filename
        counter += 1

    if dry_run:
        return True, f"Would create: {output_filename}"

    # Write output file
    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(full_content)

        # Update manifest
        manifest[file_hash] = {
            "source_file": str(file_path),
            "output_file": str(output_path),
            "imported_at": datetime.now().isoformat(),
            "title": title
        }

        print(f"  ✅ Saved: {output_filename}")

        return True, f"Created: {output_filename}"
    except Exception as e:
        return False, f"Write error: {e}"

def scan_files(extensions: list, source_dir: Path) -> list:
    """
    Scan directory RECURSIVELY for files with given extensions.
    Uses rglob to traverse all subdirectories.
    """
    files = []
    if not source_dir.exists():
        return files

    for ext in extensions:
        # Use rglob for recursive search (includes all subdirectories)
        files.extend(source_dir.rglob(f"*{ext}"))

    return sorted(files)

def run_import(limit: Optional[int] = None, dry_run: bool = False, file_types: list = None):
    """
    Main import function.

    Args:
        limit: Maximum number of files to process (None = all)
        dry_run: If True, don't create files, just show what would happen
        file_types: List of file types to process (e.g., ['pdf', 'word']). None = all.
    """
    print("\n📚 Legacy Archive Importer")
    print("=" * 60)

    if not HAS_MARKITDOWN:
        print("❌ markitdown library not installed.")
        print("   Install with: pip install markitdown")
        return

    # Check source directories
    print(f"\n📁 Source: {SOURCE_BASE}")
    print(f"📁 Output: {OUTPUT_DIR}")

    if dry_run:
        print("\n🔍 DRY RUN MODE - No files will be created\n")

    # Load manifest
    manifest = load_manifest()
    print(f"📋 Manifest loaded: {len(manifest)} files already imported")

    # Determine which file types to process
    if file_types is None:
        file_types = ["pdf", "word", "txt_rtf", "html"]

    # Scan for files
    all_files = []
    for file_type in file_types:
        if file_type not in SOURCE_DIRS:
            print(f"⚠️  Unknown file type: {file_type}")
            continue

        source_dir = SOURCE_DIRS[file_type]
        extensions = EXTENSIONS[file_type]
        files = scan_files(extensions, source_dir)

        print(f"   {file_type.upper()}: {len(files)} files found in {source_dir.name}")
        all_files.extend([(f, file_type) for f in files])

    print(f"\n📊 Total files to process: {len(all_files)}")

    if limit:
        all_files = all_files[:limit]
        print(f"   Limited to: {limit} files")

    if not all_files:
        print("No files to process.")
        return

    # Process files
    print("\n🔄 Processing files...\n")

    # Clear old failure log if starting fresh
    if not dry_run and all_files:
        if FAILURE_LOG.exists():
            FAILURE_LOG.unlink()
        # Write header
        with open(FAILURE_LOG, "w", encoding="utf-8") as f:
            f.write(f"Import Failure Log - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("=" * 80 + "\n\n")

    success_count = 0
    skip_count = 0
    error_count = 0

    with tqdm(total=len(all_files), desc="Converting to Markdown") as pbar:
        for file_path, file_type in all_files:
            success, message = process_file(file_path, manifest, dry_run=dry_run)

            if success:
                if "skipped" in message.lower():
                    skip_count += 1
                else:
                    success_count += 1
                    # Show successful conversions
                    tqdm.write(f"✅ {file_path.name} → {message}")
            else:
                error_count += 1
                tqdm.write(f"❌ {file_path.name}: {message}")

                # Log failure to file
                if not dry_run:
                    log_failure(file_path, message, FAILURE_LOG)

            pbar.update(1)

            # Save manifest periodically
            if not dry_run and (success_count + error_count) % 100 == 0:
                save_manifest(manifest)

    # Final save
    if not dry_run:
        save_manifest(manifest)

    # Summary
    print("\n" + "=" * 60)
    print("✅ Import Complete!")
    print(f"   Converted: {success_count} files")
    print(f"   Skipped (already imported): {skip_count} files")
    print(f"   Errors: {error_count} files")
    print(f"   Total processed: {len(all_files)} files")
    print(f"\n📁 Output directory: {OUTPUT_DIR}")

    if not dry_run:
        print(f"📋 Manifest saved: {MANIFEST_FILE}")

        if error_count > 0:
            print(f"📋 Failure log saved: {FAILURE_LOG}")
            print(f"   Review failed files with: cat {FAILURE_LOG}")

        print("\n💡 Next steps:")
        print("   1. Review the markdown files in the output directory")
        if error_count > 0:
            print(f"   2. Check failure log: {FAILURE_LOG}")
            print("   3. Run: python scripts/enrich_archive_gemini.py force")
        else:
            print("   2. Run: python scripts/enrich_archive_gemini.py force")
        print("   3. Run: python scripts/build_index.py")
        print("   4. View in dashboard: streamlit run dashboard/app.py")

if __name__ == "__main__":
    import sys

    # Parse arguments
    args = sys.argv[1:]
    limit_arg = None
    dry_run = False
    file_types = None

    for arg in args:
        if arg.isdigit():
            limit_arg = int(arg)
        elif arg == "dry-run" or arg == "test":
            dry_run = True
        elif arg in ["pdf", "word", "txt", "rtf", "txt_rtf", "html", "htm"]:
            if file_types is None:
                file_types = []
            if arg in ["txt", "rtf"]:
                arg = "txt_rtf"
            if arg in ["htm"]:
                arg = "html"
            if arg not in file_types:
                file_types.append(arg)

    run_import(limit=limit_arg, dry_run=dry_run, file_types=file_types)

