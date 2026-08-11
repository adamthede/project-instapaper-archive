---
title: "Matter CLI Integration — Second Read-It-Later Pipeline"
status: "QA Needed"
priority: "P1"
project: "articles"
created: 2026-04-13
linked_pr: "https://github.com/adamthede/project-instapaper-archive/pull/2"
depends_on:
  - "Matter Pro subscription (required for API/CLI access)"
---

> **Status note (2026-08-11):** Phases 2-4 are built and under review. The sync
> script, incremental `updated_since` mode, and the nightly launchd job landed
> together rather than in sequence, because the delta logic is what makes a
> nightly job worth having. Phase 1 (explore) and Phase 5 (Instapaper catch-up)
> remain, and Phase 1 needs Adam's credential.
>
> Two deliberate deviations from the plan below, both documented in
> `docs/MATTER_SYNC.md`:
>
> - **The token lives at `~/.secrets/matter.token` (mode 0600), not in `.env`.**
> - **The frontmatter writes `date_archived`, not `date_read`.** `build_index.py`
>   does not read `date_read`; it reads `date_archived`, and the dashboard derives
>   `date_read` from it. The plan's spelling would have parsed fine and then put
>   every Matter article at the wrong end of the timeline.
>
> Also note the plan's mapping of `created_at` → `date_saved` is not possible:
> the v1 `Item` schema has no `created_at`. `updated_at` is the only per-item
> timestamp, and `date_saved_source` records that it is a fallback.

# Matter CLI Integration

## Problem

Adam uses two read-it-later apps: Instapaper (since ~2008) and Matter (recent years). The Article Archive project currently only ingests from Instapaper. Matter content is stranded — no local copies, no enrichment, no inclusion in the dashboard analytics. Matter just launched an official API and CLI (April 2026), making integration possible for the first time.

## Why Now

- Matter had no API or CLI until this week. Now it has both.
- The existing enrichment pipeline (normalize to Markdown + YAML frontmatter, enrich via Gemini, index to Parquet, visualize in Streamlit) is proven and ready to accept a new input source.
- The dashboard date fix is done, the blog post is published, and the project is wired into Command Center. This is the natural next step.

## Matter API/CLI Summary

**Auth:** Bearer token (`mat_xxx`), generated at web.getmatter.com/settings. One active token at a time. Requires Matter Pro.

**Key capabilities vs. Instapaper:**

| Feature | Matter | Instapaper |
|---------|--------|------------|
| Pagination | Cursor-based, no hard limit | 500-item hard cap per folder |
| Article content | `?include=markdown` (20 req/min) | `/bookmarks/get_text` (undocumented limits) |
| Filtering | status, content_type, tag, favorite, updated_since | Folder-based only |
| Incremental sync | `updated_since` + `order=updated` | Not available |
| Auth | Bearer token | OAuth 1.0a |
| CLI | Official, JSON output, `--all` flag | None |
| Highlights/Notes | Full CRUD via annotations API | Limited |
| Content types | article, podcast, video, pdf, tweet, newsletter | Articles only |

**Rate limits:** 120 reads/min, 30 writes/min, 20 markdown fetches/min, 5 req/sec burst ceiling. Well-documented with standard headers.

**CLI install:** `curl -fsSL https://cli.getmatter.com/install.sh | sh`

**CLI auth:** `matter login` (browser) or `matter login mat_yourtoken` (direct)

## Implementation Plan

### Phase 1: Explore (30 min)

Install the CLI, authenticate, and understand the data.

```bash
# Install
curl -fsSL https://cli.getmatter.com/install.sh | sh

# Authenticate
matter login

# Explore
matter account
matter items list --status archive --limit 5 --plain
matter items list --status queue --limit 5 --plain
matter tags list --plain

# Check total counts
matter items list --status archive --all | jq '.results | length'
matter items list --status queue --all | jq '.results | length'

# Get a single item with content
matter items get <id> --include markdown | jq '.title, .url, .markdown[:200]'
```

Questions to answer:
- How many archived (read) articles exist?
- How many are in queue (unread)?
- What content types are present (articles, podcasts, PDFs)?
- What tags exist?
- Do any articles have annotations/highlights?

### Phase 2: Export Script (2-3 hours)

Build `scripts/core/export_matter_to_archive.py` — mirrors the pattern of the existing Instapaper export scripts.

**Input:** Matter API via bearer token or CLI subprocess
**Output:** Markdown files with YAML frontmatter in the vault directory

**Frontmatter schema** (aligned with existing Instapaper articles):
```yaml
---
title: "Article Title"
original_url: "https://example.com/article"
matter_id: "itm_r9f3a"
author: "Author Name"
source: "matter"
content_type: "article"
date_saved: "2026-03-15"
date_read: "2026-04-01"
word_count: 3500
favorite: true
tags: ["cybersecurity", "AI"]
matter_progress: 1.0
---
```

**Key decisions:**
- Use the API directly (requests + bearer token) rather than shelling out to the CLI. The CLI is great for exploration but the API gives more control over error handling and rate limiting.
- Map Matter `status: archive` → treat as "read" (same as Instapaper archive)
- Map Matter `created_at` → `date_saved`, `updated_at` → `date_read` (for archived items)
- Fetch markdown content via `?include=markdown` (20 req/min = ~1,200 articles/hour)
- Use manifest.json for idempotency (same pattern as Instapaper export)
- Store Matter API token in `.env` as `MATTER_API_TOKEN`

**Highlights/annotations:** For articles with annotations, append them to the markdown body as a `## Highlights` section. These are Adam's own notes and highlighted passages — high-value content.

### Phase 3: Enrichment + Index (30 min)

No new code needed. The existing pipeline handles this:
1. Run `enrich_archive_gemini.py` — it processes any markdown file without enrichment fields
2. Run `build_index.py` — it indexes all markdown files in the vault
3. The dashboard picks up everything automatically

The only consideration: Matter articles may include content types the Instapaper pipeline never saw (podcasts, videos, PDFs, tweets, newsletters). The enrichment prompt should handle these gracefully — a podcast transcript is still text. A tweet is still text. The `content_type` field in frontmatter will allow filtering in the dashboard.

### Phase 4: Incremental Sync (1 hour)

Build a sync mode into the export script using Matter's `updated_since` parameter.

```python
# Pseudocode
last_sync = read_last_sync_timestamp()
items = matter_api.list_items(order="updated", updated_since=last_sync)
for item in items:
    export_to_markdown(item)
save_last_sync_timestamp(now)
```

Options for scheduling:
- **launchd plist** — run daily at 2am (before the enrichment pipeline runs at 3am, if we set that up)
- **Manual** — run `python3 scripts/core/export_matter_to_archive.py --sync` when desired

### Phase 5: Instapaper Catch-Up (30 min)

Run the existing Instapaper export pipeline again to pull any articles saved/archived since November 2025. The CSV bulk import approach is proven — just need a fresh CSV export from the Instapaper web UI and then run `bulk_import_instapaper_from_csv.py`.

Then run enrichment and index rebuild on the new articles.

## Post-Deploy Checklist

- [ ] Install Matter CLI (`curl -fsSL https://cli.getmatter.com/install.sh | sh`)
- [ ] Authenticate (`matter login`)
- [ ] Verify Matter Pro subscription is active
- [ ] Add `MATTER_API_TOKEN` to `.env`
- [ ] Run Phase 1 exploration to understand data volume
- [ ] Build and test export script on first 10 articles
- [ ] Run full Matter export
- [ ] Run Instapaper catch-up (fresh CSV export + bulk import)
- [ ] Run enrichment on all new articles
- [ ] Rebuild Parquet index
- [ ] Verify dashboard shows Matter articles correctly
- [ ] Take fresh dashboard screenshots (numbers will change)
- [ ] Update blog post numbers if significant change

## Open Questions

1. **Should Matter content go in the same vault directory or a subdirectory?** Instapaper and legacy articles are currently mixed in one flat directory. Matter articles could go in a `matter/` subdirectory for cleanliness, but that would require `build_index.py` to scan recursively (which it already does).

2. **MCP server?** Matter's CLI could potentially be wrapped as an MCP server for direct Claude Code access. Low priority but interesting — would allow "what did I read about X recently?" queries against Matter directly.

3. **Unified sync daemon?** Eventually both Instapaper and Matter sync could run as a single launchd daemon that pulls from both sources, enriches, and indexes. This is the "always-on local agent" pattern from PKM Vault applied to reading history.

4. **Scheduling approach:** Claude Code's `/schedule` feature auto-expires after 7 days and requires an active session. For a true daily pipeline, use launchd (macOS native scheduler). A single `com.thedetech.article-sync.plist` running at 2am could orchestrate: pull from both APIs → enrich new articles via Gemini → rebuild Parquet index. Same pattern as the planned PKM enrichment daemon and CC session digest — all three could eventually share orchestration.
