---
title: "Weekly Reading Synthesis + Static Site — the browsable week-over-week archive"
status: "In Progress"
priority: "P1"
project: "articles"
created: 2026-08-18
linked_pr: "https://github.com/adamthede/project-instapaper-archive/pull/3"
depends_on:
  - "2026-08-12 dashboard audit (docs/2026-08-12-dashboard-audit-and-static-site-proposal.md) — this plan absorbs its Part 2"
---

# Weekly Reading Synthesis + Static Site

> **Status note (2026-08-19):** Phase 1 SHIPPED via PR #3 (merged after a
> two-round adversarial review; plist installed same day). Phases 2-5 remain;
> next gate is the Phase 2 week-page mockup for Adam's approval.
>
> **2026-08-19 (later):** Phase 2 mockup APPROVED; Matter-era backfill
> complete (127 weeks, zero failures); Phase 3 build underway - Python
> generate.py per the audit (Hugo reconsidered and re-declined by Adam).
> First production deploy is Adam's; nightly deploy wiring follows it.

## Problem

The archive now captures and enriches autonomously (Matter nightly, local Qwen
enrichment, wired 2026-08-18), but synthesis stops at the per-article TL;DR.
There is no cross-article layer — nothing that says what a week of reading
actually amounted to. Separately, the 2026-08-12 audit concluded the Streamlit
dashboard should migrate to a nightly-rebuilt static site on the Daybook
architecture; its one real blocker (vault on an unmounted SSD) was solved
2026-08-16 by the move to /Volumes/AST.

These are one initiative, not two. The weekly page is the atomic unit the
static site was missing: a browsable archive of week-over-week synthesis prose
PLUS week-over-week stats, figures, and visuals — a Felton annual report at
weekly cadence, per the retrospective genre idiom (full Felton: editorial,
typographic, contemplative).

## Why now

- The vault blocker is cleared and the nightly chain is proven end-to-end.
- The audit's data findings are fresh and measured (entity long tails, the
  two-corpora footnote, which visualizations earn their place).
- The weekly digest feeds the Big Letter monthly and rehearses the Silo
  Monthly thesis: synthesis as the payoff for daily capture (CES).

## Phases

### Phase 1 — Weekly synthesis generator (ship first, no UI)

`scripts/core/weekly_synthesis.py`:

- **Input:** index rows with `date_read` in the closing ISO week (typically
  5-20 articles) — `ai_summary`, topics, people, orgs, word_count, plus the
  articles' highlight blocks (Adam's own words; highest-signal input).
- **Synthesis:** one Qwen call via the existing `_locked_completion` plumbing
  (fleet flock, pinned exact model id, sequential). Output is WOVEN prose,
  not a list: 2-4 themes, connections between pieces, one thread-of-the-week.
  300-500 words (less-is-more default).
- **Stats block** computed in pandas, stored as YAML frontmatter on the
  output file so Phase 2+ renders without recomputing: article count, total
  words + reading time, top topics/people/orgs (capped; the vocabularies are
  long-tailed per the audit), re-reads recorded that week, source split,
  week-over-week deltas.
- **Output:** `<vault>/synthesis/2026-W34.md` — one file per ISO week,
  idempotent, regenerating overwrites.
- **Schedule:** own launchd job, Sunday 20:00 (`com.thedetech.article-sync.weekly-synthesis`),
  fleet heartbeat shape, registered in the cockpit's launchd_stats (the
  now-standard fourth-job-through-the-pattern move).

### Phase 2 — Week-page mockup (HARD GATE: Adam approves before any build)

One self-contained HTML page for a real week, Data-as-Design skin,
retrospective idiom: the synthesis prose as the editorial lead, stats as
large light numerals with small-caps labels, 2-3 visuals max. Candidate
visuals (pick at mockup review): 52-week sparkline of articles/words with
the current week marked; top-topics bar; source/era split; reading-time
distribution. No chart without a question it answers.

### Phase 3 — The static site (absorbs audit Part 2 wholesale)

- Daybook reference architecture: nightly `site/generate.py --out _site` +
  `wrangler pages deploy`, aggregate pages + a client-side article detail
  view driven by a JSON payload (audit's recommendation — keeps deploys
  small). Weekly pages become the site's spine: `/weeks/2026-W34/`, a weeks
  index with trend strip, year rollups later.
- Domain: **reading.adamthede.com** (audit's naming recommendation — name
  the place for the activity). Cloudflare Pages.
- Wiring per the audit: wrapper script rather than more plist arguments;
  `~/.volta/bin` on the plist PATH for wrangler; EXTEND the existing
  nightly heartbeat rather than adding a second (failed deploy = failed
  night); command-center registry entry.
- Carry the remaining five missing frontmatter keys into the index
  (matter_status, matter_progress, matter_highlight_count,
  date_saved_source, tags/favorite) — content_corrupted already done
  2026-08-18.

### Phase 4 — Backfill

Matter-era weeks first (cheap, weeks since ~2022 have real read-dates).
Then optionally the deep past: ~1,100 weekly digests across 22 years as an
unattended Qwen batch job — memoir raw material. Respect the audit's
footnote: legacy-era read dates are publication-date proxies; historical
digests should say so on the page.

### Phase 5 — Retire Streamlit

Per the audit verdict: migrate everything that earns its place, leave
Spaced Review behind (its review-writes need a server; park until wanted).
Keep `dashboard/` runnable until the site has feature-parity on the
surfaces that survive.

## Decisions — SETTLED 2026-08-19 (Adam confirmed all four defaults)

1. **Output home:** vault `synthesis/` subdir (part of the archive itself). 
2. **Cadence:** Sunday 20:00 local.
3. **Digest length/voice:** 300-500 words, woven themes, no listicle.
4. **Domain:** reading.adamthede.com.
5. **Week-page visual set:** chosen at the Phase 2 mockup review, not before.
6. **Big Letter hook:** monthly, the four weekly digests are ingredients —
   manual pull at first, automation later.

## Known approximations and follow-up requirements

- **Week boundaries are approximate by one day for Matter rows:** date_archived
  is day-granular UTC (0 of 234 rows carry a time), so a Sunday-evening CDT
  read can stamp Monday UTC and land in the next week. Accepted; not fixable
  in this repo.
- **Cockpit registration REQUIRES weekday-aware schedule handling first:**
  launchd_stats._plist_schedule drops Weekday and last_expected_fire assumes
  daily, so registering a Sunday job as-is false-alarms no-run six days a
  week (com.thedetech.releases.weekly shares this hazard, also unregistered).
  The command-center follow-up PR must fix that before adding this entry.

## Fleet contract notes

Reuse `_locked_completion` (flock per call, exact catalog id
`qwen3.6-35b-a3b-mtp`, thinking-off is load-time). Weekly job must tolerate
LM Studio being down: skip, log, heartbeat `fail`, next Sunday regenerates —
same non-fatal posture as the nightly enrichment leg.

## Verification

Phase 1: unit tests for week-window selection (ISO week edges, year
boundary), stats block math, and idempotent regeneration; one real-week
dry-run reviewed by Adam before the launchd install. Phases 2-3: mockup
gate, then the standard adversarial-review-by-a-different-agent path (no CI
here). Cockpit registration PR to command-center follows the PR #98/#99
template.
