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
>
> **2026-08-19 (evening):** Phase 3 generator SHIPPED via PR #4 (two-round
> adversarial review). Remaining: Adam's first deploy (site/DEPLOY.md),
> nightly deploy wiring, Phase 4 deep backfill, Phase 5 Streamlit retirement.
>
> **2026-08-20:** Phase 5 deep dives in review at PR #8 - year rollups
> (`/years/YYYY/`), an orgs facet (`/orgs/`), and a client-side article
> browser (`/articles/` over one 2.5 MB payload), all rendering from the
> Parquet index rather than synthesis frontmatter. This is the port half of
> Phase 5; retiring the Streamlit app itself waits on Adam confirming the
> surfaces he still uses (Spaced Review stays local either way, per the
> audit). Phase 4 backfill was running concurrently and rewriting
> `synthesis/`, so week-page counts in that PR's evidence are a moving
> snapshot.
>
> **2026-08-20 (later):** Phase 5b — the trends layer — in review at PR #10,
> branched off PR #9 rather than main. See the Phase 5b section below.

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

### Phase 5b — The trends layer (scope settled with Adam 2026-08-20)

The second half of the Streamlit port: the surfaces that need the whole
archive at year grain rather than one week at a time. Six pieces.

1. **Index hero.** All-time totals from the Parquet index (16,346 articles,
   17.3M words, 1,209 hours, median 761 words) plus the **era split** told
   honestly — legacy files 64.1%, Instapaper 34.5%, Matter 1.4%. Roughly two
   thirds of this archive predates any read-it-later service, and a hero row
   that says "16,346 articles" without saying so claims a tracking history
   the corpus does not have.

2. **Complexity.** `grade_level` clipped to 0–20 everywhere (385 corpus rows
   parse outside that band, the highest at 857; the unclipped mean reads
   11.75 against an honest 11.31). Each year page gains an average reading
   level, a delta against the previous year with data, and that year's
   densest substantial read. `/trends/` gains a complexity band at year
   grain. The article payload gains a clipped grade.

3. **`/trends/`.** Year-grain **heatmaps**, not multi-line spaghetti: rows are
   top-15 entities, columns are the years, cell intensity is amber by count,
   single hue only. Three of them — sources (from URL host), organizations,
   places — plus the complexity band and a sentiment-mix strip. Every matrix
   scrolls inside its own container; the page never scrolls sideways.

4. **Facets.** `/locations/` modelled on `/orgs/`. **No `/concepts/` page** —
   measured and failed, see the decision below.

5. **People cleanup.** The audit's fabricated Co.Design cast, excluded at
   index-build level, and `/people/` built on the cleaned data.

6. This section, and the PR linked below.

#### Phase 5b decisions

- **Concepts are not rankable — measured, not assumed.** Top-20 *article*
  coverage of **22.0%** over a **50,601**-string vocabulary, **74.0%** of it
  used exactly once. That is worse than `topics` (25.3%), which the audit
  already ruled unrankable, against `orgs` at 45.2%. The bar is now a stated
  constant (`RANKABLE_HEAD_COVERAGE = 40%`) and the verdict is **recomputed on
  every build** rather than frozen into a comment — if the audit's
  recommendation #9 (a topic/concept normalization pass) ever lands, the
  numbers move and the page starts building itself.
- **Locations clear the bar comfortably**: 57.0% top-20 coverage over 8,658
  strings, better than organizations on both counts. Page built.
- **People do NOT clear the bar** (18.0%, the archive's thinnest field) and the
  page is built anyway, because it was explicitly in scope and because it is
  where the cleanup is visible. The page states its own coverage in the same
  measured voice and gets **no heatmap row** on `/trends/` — the bar governs
  whether a field can carry a time series, which 18% cannot. **Open for Adam:**
  if the bar should apply uniformly, `/people/` is the page that goes.
- **Grade clipping is stated wherever a grade is printed**, including the
  unclipped figure, so "we clipped the data" costs the reader nothing to check.
- **Thin years are drawn and marked, never hidden.** 2021 holds three articles
  averaging grade 14.00 — the highest number in the series. It is rendered,
  hatched, dotted, named in the note, and ineligible to be called the densest
  year. Same rule for sentiment years under 25 rated articles.

#### The people cleanup, and where it actually bites

The rule is generic rather than a name blocklist: an identical **multi-name**
cast + one **host** + one exact **word count**, at or above 8 rows. The
threshold was chosen from a measured sweep and the verdict is flat across it —
every value from 8 to 50 catches the same 2 groups and the same **283 rows**.
Below 5 it starts eating genuine two-author bylines, which is the failure worth
avoiding: a false positive erases a real person from the archive's memory.

**The two corpora disagree about the effect, and both numbers are published.**

- On the **raw index** — which `dashboard/app.py` reads with no
  `content_corrupted` filter at all — it is the audit's fix: Todd Sherman
  (286), Todd Kaplan (286), Deb Haaland (285), Antonia Iamartino (285) and
  Josh Earnest (285) all leave the top 15, Jony Ive drops 301 → 15, and Warren
  Buffett, Bill Clinton, Sarah Palin, Tim Cook, Sergey Brin and Henry Paulson
  take their places.
- On the **site corpus** it changes nothing, because all 283 rows are *also*
  flagged `content_corrupted` and were already being dropped. `/people/` says
  so on the page rather than taking credit for a ranking that was already
  clean.

The ordering (hygiene runs **before** the corrupted filter) stays regardless:
for any cluster only partly flagged, scrubbing afterwards would shrink it below
any threshold, and the unflagged survivors are exactly the rows that would leak.

**Known residue:** one uncorrupted row carries a cast mixing the furniture
names with real subjects. Its fingerprint is unique, so it forms no cluster and
keeps its people — "Todd Kaplan" survives on `/people/` with a count of 1 out
of 41,514 names. Removing it would take a name blocklist, which would also
erase Jony Ive from the articles genuinely about him. Pinned by a test so the
trade-off stays a decision.

**Open item:** the same furniture populated `orgs`, `locations` and `concepts`
on those same rows. Those columns are not scrubbed — a wider call than the one
settled here, and 283 rows is 1.6% of the index.

### Phase 5 — Retire Streamlit

Per the audit verdict: migrate everything that earns its place, leave
Spaced Review behind (its review-writes need a server; park until wanted).
Keep `dashboard/` runnable until the site has feature-parity on the
surfaces that survive.

## Decisions — SETTLED 2026-08-19 (Adam confirmed all four defaults)

1. **Output home:** vault `synthesis/` subdir (part of the archive itself). 
2. **Cadence:** Sunday 20:00 local.
3. **Digest length/voice:** 300-500 words, woven themes, no listicle.
4. **Domain:** reading.adamthede.com. **PRIVATE-FIRST (settled 2026-08-19):**
   behind Cloudflare Access like the rest of the personal portfolio; public
   is a later one-way choice Adam can make per-audience.
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
