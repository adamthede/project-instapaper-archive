---
title: "The unread corpus, private pages on reading.adamthede.com (study step 6)"
status: "In Progress"
priority: "P2"
project: "articles"
created: 2026-09-24
effort: "M - three generated pages, nav wiring, tests"
linked_pr: ""
depends_on:
  - "docs/plans-done/2026-09-15-unread-corpus-what-i-meant-to-read.md (build order step 6)"
  - "the enriched corpus (PR #28) - data/unread_enriched.jsonl, 492 rows, no new model calls"
  - "the Matter queue snapshot ledger (PR #27) - data/matter/queue_daily.csv + queue_snapshots.jsonl"
---

# The unread corpus: the private pages

Step 6 of the study's build order: "the private comparison pages on
reading.adamthede.com, one day". Three pages inside the existing reading site,
in its week-page idiom, behind the same Cloudflare Access wall as the rest of
it.

## What gets built

1. **/unread/**, the queue as a living view. The 492-item meant-to-read pool
   (by year saved, by source, by length, dead fraction) beside the Matter
   queue from the nightly snapshot ledger (size, words, started, inflow and
   outflow per week, reads per week from the index).
2. **/unread/versus/**, the read-versus-unread comparison. The study's first
   hypothesis and its questions (topic drift, aging curve, abandonment bands)
   drawn on shared axes. Numbers on the cover, prose collapsed.
3. **/unread/worth/**, the "still worth your time" shortlist (decision 3):
   ranked on the stored enrichment, one line of reasoning each, labelled as
   inference with its confidence flag, with link, saved date and source.
   Private only.
4. Wiring: one page-row entry and the weeks index's "Beyond the week" nav.
   The nightly build regenerates them because they come out of
   `site/generate.py` with its default paths.

## Rules carried

- No model calls. Everything reads the enrichment's stored output.
- Nothing from the enrichment that quotes an article body reaches a page
  beyond the one `why_saved` line. `ai_summary` is never rendered.
- Fail open: a missing enriched file or ledger drops the unread pages and the
  row entry, never the rest of the site.
