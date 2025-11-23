# Handling Corrupted Articles

## The Problem

Some articles (particularly from Co.design in 2012-2015) were improperly saved by Instapaper - instead of capturing the article content, they captured the website's sidebar/navigation (lists of other articles). This resulted in:

- Incorrect AI enrichment (topics, people, organizations extracted from sidebars)
- Misleading summaries
- Polluted archive data

Example: ~286 articles containing "Wrightwood 659" (a museum mentioned in Co.design's sidebar in 2025)

## The Solution

### Multi-Layered Approach:

1. **Clean existing corrupted articles** - Remove bad AI data and flag them
2. **Prevent future issues** - LLM validates content during enrichment
3. **Filter in pipelines** - Skip corrupted articles in processing

---

## Step 1: Clean Existing Corrupted Articles

### Identify Corrupted Articles

You've already done this! Create `problems.txt` with file paths:

```bash
grep -l "Wrightwood 659" /path/to/vault/*.md > problems.txt
```

### Run Cleanup Script

```bash
python scripts/cleanup_corrupted_articles.py
```

**What it does:**
- Reads `problems.txt`
- Removes all `ai_*` fields from frontmatter
- Adds:
  ```yaml
  content_corrupted: true
  corruption_reason: "instapaper_sidebar_content"
  corruption_note: "Article content is website sidebar/navigation instead of actual article"
  ```
- Preserves original article metadata (title, URL, date, etc.)

**Result:** 286 articles flagged as corrupted, bad AI data removed

---

## Step 2: LLM Validation (Built Into Enrichment)

### How It Works

The enrichment scripts now include content validation:

**Gemini Prompt Addition:**
```
CONTENT_VALID: [YES if article content matches the title and appears to be a real article.
NO if content appears to be navigation/sidebar/advertisements/unrelated content]
```

**Automatic Flagging:**
- If LLM detects invalid content → Marks as corrupted instead of enriching
- Adds `content_corrupted: true` with reason `llm_detected_invalid_content`

### Example

Article title: "How To Design Great Products"
Content: List of links to other articles
LLM Response: `CONTENT_VALID: NO`
Result: Article flagged, not enriched

---

## Step 3: Filter in Pipeline

### During Enrichment

Both enrichment scripts automatically **skip corrupted articles**:

```python
# Skips during candidate selection
if row.get("content_corrupted") == True:
    return False  # Don't process
```

### In build_index.py

The index includes the `content_corrupted` field, so you can filter in the dashboard.

### In Dashboard

You can add filters to exclude corrupted articles:

```python
# Filter out corrupted
df_clean = df[df["content_corrupted"] != True]
```

Or create a "Show Corrupted" toggle to review them.

---

## Usage Workflow

### Initial Cleanup (One-Time)

```bash
# 1. You've already created problems.txt
grep -l "Wrightwood 659" /path/to/vault/*.md > problems.txt

# 2. Clean the corrupted articles
python scripts/cleanup_corrupted_articles.py

# 3. Rebuild index
python scripts/build_index.py

# 4. View results in dashboard
streamlit run dashboard/app.py
```

### Ongoing Protection (Automatic)

```bash
# Future enrichment automatically validates and flags issues
python scripts/enrich_archive_gemini.py force

# Corrupted articles are:
# - Skipped during processing
# - Flagged if newly detected
# - Excluded from stats (if filtered)
```

---

## What Happens to Corrupted Articles?

### They Are NOT Deleted

Corrupted articles remain in your archive with:
- ✅ Original metadata (title, URL, date)
- ✅ Original content (sidebar/navigation)
- ✅ Corruption flag
- ❌ No AI enrichment

### Options Going Forward

**Option 1: Keep Them (Filtered)**
- Leave in archive
- Filter out in dashboard
- Historical record preserved

**Option 2: Manual Review**
- Check if original URLs still work
- Attempt to re-fetch valid content
- Decide case-by-case

**Option 3: Delete Them**
```bash
# Backup first!
cp -r vault vault_backup

# Delete all corrupted
while read file; do rm "$file"; done < problems.txt
```

**Option 4: Re-fetch from URLs**
- Extract URLs from corrupted articles
- Attempt to scrape/save again
- Replace if successful

---

## Statistics

After cleanup, you can check:

```bash
# Count corrupted articles
grep -r "content_corrupted: true" /path/to/vault | wc -l

# View by corruption reason
grep -r "corruption_reason" /path/to/vault | cut -d: -f3 | sort | uniq -c
```

**Expected Results:**
- ~286 articles flagged as `instapaper_sidebar_content`
- Future enrichment may flag additional ones as `llm_detected_invalid_content`

---

## Dashboard Integration (Future Enhancement)

You can add a filter to the dashboard:

```python
# In sidebar
show_corrupted = st.sidebar.checkbox("Show Corrupted Articles", value=False)

if not show_corrupted:
    df = df[df.get("content_corrupted", False) != True]
```

Or create a dedicated "Corrupted Articles" tab to review and manage them.

---

## Prevention Tips

1. **Use Instapaper's "Reader View"** - Better text extraction
2. **Check previews before saving** - Verify content looks correct
3. **Re-save failed articles** - If preview shows navigation, try again
4. **Use browser extensions** - Some work better than Instapaper's bookmarklet

---

## Summary

✅ **Cleanup script** removes bad AI data and flags 286 articles
✅ **LLM validation** prevents future issues during enrichment
✅ **Pipeline filtering** automatically skips corrupted articles
✅ **Original data preserved** - nothing is deleted, just flagged
✅ **Reversible** - Can always re-process if needed

Your archive is now clean and protected from this type of corruption going forward! 🎉

