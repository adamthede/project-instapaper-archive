# Legacy Archive Import Guide

This guide explains how to import your existing PDF, Word, TXT, and RTF files into your unified Markdown archive.

## Overview

The `import_legacy_archive.py` script converts files from multiple formats into a consistent Markdown format with YAML frontmatter, ready for enrichment and indexing.

## Setup

### 1. Install Required Library

```bash
pip install markitdown
```

### 2. Configure Paths in `.env`

Add these lines to your `.env` file:

```bash
# Legacy import paths (adjust to your actual paths)
IMPORT_SOURCE_PATH=/path/to/your/legacy/articles
IMPORT_OUTPUT_PATH=/path/to/markdown/output
```

**Example:**
```bash
# If your legacy files are on an external drive
IMPORT_SOURCE_PATH=/Volumes/ExternalDrive/Articles
IMPORT_OUTPUT_PATH=/Users/yourname/Documents/Markdown-Archive
```

## Safety Features

⚠️ **IMPORTANT: Source files are NEVER modified, deleted, or moved!**

- ✅ Source files are only READ
- ✅ Output files are written to a separate directory
- ✅ Original files remain untouched in their original locations
- ✅ Manifest tracks what's been imported to avoid duplicates

## Usage

### Test Run (Preview Only)

```bash
# Test with 5 files, no actual conversion
python scripts/import_legacy_archive.py 5 dry-run
```

### Import Specific File Types

```bash
# Import only PDFs
python scripts/import_legacy_archive.py pdf

# Import only Word documents
python scripts/import_legacy_archive.py word

# Import only TXT/RTF files
python scripts/import_legacy_archive.py txt_rtf
```

### Import Limited Batch

```bash
# Import first 100 files (useful for testing)
python scripts/import_legacy_archive.py 100

# Import first 50 PDFs
python scripts/import_legacy_archive.py 50 pdf
```

### Import Everything

```bash
# Import all files from all source directories (~7,000 files)
python scripts/import_legacy_archive.py
```

## What Happens During Import

1. **Scans** source directories for files
2. **Checks** manifest to skip already-imported files (by hash)
3. **Parses** filename to extract:
   - Publication date (from filename patterns)
   - Article title (cleaned)
   - Content type (article, presentation, etc.)
4. **Converts** file to Markdown using markitdown
5. **Creates** YAML frontmatter with metadata
6. **Writes** markdown file to output directory
7. **Updates** manifest to track import

## Filename Patterns Recognized

The script intelligently parses these filename patterns:

- `YYYY MM-DD Title.pdf` → Full date
- `YYYY MM-DD HH-MM-SS Title.pdf` → Full date + time
- `YYYY MM Title.pdf` → Month only (defaults to day 1)
- `Title.pdf` → No date (marked for LLM inference later)

### Type Prefixes Detected:
- `ARTICLE -` → content_type: article
- `PRESENTATION -` → content_type: presentation
- `Photography -` → content_type: photography

## Output Format

Each converted file includes YAML frontmatter:

```yaml
---
title: "Article Title"
source: "legacy_pdf"
original_file: "2007 04-25 Uncertainty and Decision Making.pdf"
original_format: "pdf"
content_type: "article"
date_published: "2007-04-25"
date_source: "filename-full"
date_imported: "2025-11-22"
file_hash: "sha256:abc123..."
---

[Markdown content here...]
```

## Next Steps After Import

### 1. Review Conversions
Check the output directory to verify quality:
```bash
ls -lh /path/to/your/markdown/output/
```

### 2. Enrich with AI
Use Gemini to extract topics, entities, summaries:
```bash
python scripts/enrich_archive_gemini.py force
```

### 3. Build Index
Create searchable index:
```bash
python scripts/build_index.py
```

### 4. View in Dashboard
```bash
streamlit run dashboard/app.py
```

## Troubleshooting

### "markitdown not found"
```bash
pip install markitdown
```

### "Source directory not found"
Check your `.env` file and verify the path:
```bash
IMPORT_SOURCE_PATH=/path/to/your/legacy/articles
```

### "Conversion failed" for specific files
Some files may be corrupted or encrypted. The script logs these and continues with other files.

### Duplicate files
The script uses file hashes to detect duplicates. If you run the import again, already-imported files are automatically skipped.

## Manifest File

Import progress is tracked in `~/.legacy_import_manifest.json`:
- Contains hash of each imported file
- Prevents re-importing the same content
- Can be deleted to start fresh (will re-import everything)

## Performance

**Expected speed:**
- ~10-30 files per minute (depends on file sizes)
- ~7,000 files in 4-12 hours
- No API costs (runs locally using markitdown)

**Tips for faster imports:**
- Process one file type at a time
- Use SSD for output directory
- Run overnight for large collections

