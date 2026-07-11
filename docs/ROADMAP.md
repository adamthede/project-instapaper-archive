# Article Archive — Roadmap

**DRAFT roadmap** — synthesized 2026-07-11 from existing plans + git history for Adam's visual review on Command Center; re-sequence freely.

**Last updated**: 2026-07-11
**Product**: Article Archive (a.k.a. Instapaper Archive) — a searchable, AI-enriched archive of ~two decades of reading. 17,000+ articles (7,000+ from Instapaper since ~2008 + 10,000+ legacy PDFs/Word/HTML/TXT from 2007 on) unified into Markdown + YAML, enriched with topics/people/orgs/sentiment, indexed to Parquet, and explored through a Streamlit analytics dashboard ("a personal knowledge observatory").
**Owner**: Adam Thede
**Repo**: github.com/adamthede/project-instapaper-archive

This file follows the Command Center roadmap convention (`## Now / Next / Later / Shipped`, one item per top-level bullet, optional `` `effort:` `` / `` `next:` `` markers). It ties the project's one active queued plan (`docs/plans-to-do/2026-04-13-matter-cli-integration.md`) plus its open questions and the shipped pipeline (README, `docs/SUMMARY.md`, `docs/BLOG_POST_PROJECT_NARRATIVE.md`, git history) into a single "what's next." The archive itself is built and live; the forward edge is adding a second reading source and moving the pipeline from run-by-hand to always-on.

## Now

- **Matter CLI integration — second read-it-later source** — the one active queued plan (P1). Adam reads in two apps: Instapaper (since ~2008) and Matter (recent years), and Matter content is currently stranded — no local copies, no enrichment, not in the dashboard. Matter shipped an official API + CLI in April 2026, making integration possible for the first time. Build `scripts/core/export_matter_to_archive.py` mirroring the Instapaper export (bearer-token API, `?include=markdown`, manifest idempotency), normalize to the same Markdown + YAML frontmatter, and the proven enrich → index → dashboard pipeline picks it up with no new code. `effort: ~half day (plan Phases 1-3, ~3-4h)` `next: Phase 1 explore — install the CLI, authenticate, count archived vs queued items and content types` `depends: an active Matter Pro subscription (required for API/CLI access)`
- **Instapaper catch-up** — re-run the proven CSV bulk-import for any articles saved/archived since the last export (~November 2025): fresh CSV export from the web UI → `bulk_import_instapaper_from_csv.py` → enrich → rebuild the Parquet index. `effort: ~30min` (plan Phase 5, but stands on its own)

## Next

- **Incremental sync mode** — add a `--sync` mode to the Matter export using Matter's `updated_since` parameter, so ongoing reads flow in without a full re-export (read the last-sync timestamp, pull only what changed, stamp the new timestamp). `effort: ~1h` (plan Phase 4) `next: build it once the full Matter export works by hand`
- **Content-type handling for non-articles** — Matter brings content types the Instapaper pipeline never saw (podcasts, videos, PDFs, tweets, newsletters); Instapaper was articles-only. Confirm the enrichment prompt handles non-article text gracefully and surface the `content_type` frontmatter field as a dashboard filter. `effort: ~half day`
- **Unified article-sync daemon (launchd)** — a single `com.thedetech.article-sync.plist` running nightly (~2am) that pulls from both Instapaper and Matter, enriches new articles via Gemini, and rebuilds the Parquet index — moving the archive from run-by-hand to always-on. This is the "always-on local agent" pattern (shared with the PKM enrichment daemon) applied to reading history. `next: after the Matter sync + Instapaper incremental both work by hand`

## Later

- **Matter MCP server** — wrap the Matter CLI (or API) as an MCP server for direct Claude Code queries against reading history ("what did I read about X recently?"). Low priority, from the Matter plan's open questions. `next: only after the sync pipeline is stable`
- **Shared orchestration across the local-agent trio** — the article-sync daemon, the PKM Vault enrichment daemon, and the Command Center session digest are all the same always-on launchd pattern; eventually they could share one orchestration layer rather than three parallel plists.
- **Vault organization decision** — whether Matter content lives in a `matter/` subdirectory vs the current flat vault (`build_index.py` already scans recursively). A small cleanliness call flagged in the plan's open questions; defer until volume warrants it.

## Shipped

- **Full Instapaper archive export** — defeated the Instapaper API's hard 500-item-per-folder cap via a two-step solution: CSV export for article discovery + the per-article `/bookmarks/get_text` endpoint (not subject to the 500 limit) for full content, with manifest-file idempotency. ~7,000+ Instapaper articles exported to Markdown + YAML. Documented in `docs/SUMMARY.md` and the published "Navigating the Limits of the Instapaper API" blog post.
- **Legacy multi-format import** — `import_legacy_archive.py` (via `markitdown`) unified ~10,000+ older articles saved since 2007 — PDFs, Word docs, HTML, RTF, TXT — into the same Markdown + YAML format, with careful date/format handling.
- **AI enrichment pipeline** — per-article extraction of topics, concepts, people, organizations, locations, sentiment, and TL;DR summaries, plus corrupted/sidebar-content detection — via Gemini API (fast, ~$0.50 for 10k) or local Ollama (private), normalized into a Parquet index.
- **Analytics dashboard (Streamlit) — live** — seven surfaces across 17,000+ enriched articles: The Quantified Reader, Content Intelligence, Network & Entities, Trends Over Time, Heatmap Analysis, Spaced Review, and Archive Explorer. Includes the temporal-analysis correction (`date_saved` → `date_read`) and env-var-driven configuration.
- **Narrative artifacts** — the published project blog post ("Building a Personal Knowledge Observatory: Lessons from 17,000 Articles," Nov 2025) and a reveal.js presentation on building the reading archive.
