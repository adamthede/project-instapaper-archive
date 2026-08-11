---
title: "Article Archive: dashboard audit and static-site proposal"
audited: 2026-08-11
author: dispatched audit agent
status: proposal
doc_type: audit
---

# Article Archive: dashboard audit and static-site proposal

**Audited 2026-08-11** against `data/archive_index.parquet` (17,637 rows, 19 columns,
built 2026-04-13) and `dashboard/app.py` (2,003 lines). Read-only throughout; this
document is the only change to the repo.

## How this audit was done

Both ways. The Streamlit app was launched for real (`.venv/bin/streamlit run
dashboard/app.py --server.headless true --server.port 8599`), confirmed to boot and
serve HTTP 200, and then stopped. The page-by-page inventory below comes from reading
the code, because the interesting failures are in what the charts *compute*, not in
whether they render.

Every number in this document was measured against the actual Parquet index. Nothing
is assumed from documentation. Where the data cannot support an idea, that is stated
rather than hedged.

The Matter-sync worktree at `.worktrees/matter-sync` was read (its `docs/MATTER_SYNC.md`,
`scripts/matter/mapping.py`, and its diff to `scripts/core/build_index.py`) but not
touched or run.

---

## Part 0: What is actually in the data

This section exists because three of the audit's conclusions and roughly half the
deep-dive menu turn on facts that are not in any README.

### The index schema, as built

19 columns: `instapaper_id`, `title`, `url`, `author`, `date_saved`, `date_archived`,
`word_count`, `reading_time_min`, `grade_level`, `topics[]`, `sentiment`, `summary`,
`people[]`, `orgs[]`, `locations[]`, `concepts[]`, `emotion`, `file_path`,
`content_snippet`.

Fill rates across 17,637 rows:

| Field | Populated | Note |
|---|---|---|
| `title`, `author`, `word_count`, `date_saved`, `file_path` | 100% | `author` is the literal string `Unknown` on 12,809 rows (72.6%) |
| `url` | 7,078 (40.1%) | blank on the other 10,559 |
| `instapaper_id` | 7,077 (40.1%) | tracks `url` almost exactly |
| `date_archived` | 6,311 (35.8%) | the only "when did he read it" signal |
| `grade_level` | 17,056 (96.7%) | 732 values above grade 20, 240 above 30, max 857 |
| `topics[]` | 17,307 (98.1%) | mean 4.49 per article |
| `concepts[]` | 17,345 (98.3%) | mean 6.81 |
| `orgs[]` | 16,012 (90.8%) | mean 5.85 |
| `people[]` | 14,885 (84.4%) | mean 5.10 |
| `locations[]` | 13,046 (74.0%) | mean 4.99 |
| `sentiment`, `emotion`, `summary` | ~99.7% | |

### This is two corpora wearing one schema

The 40/60 split on `url` is not missing data. It is the seam between two eras, and it
runs straight through the middle of the archive:

- **Legacy import (10,559 rows, 2005-2011 and scattered earlier).** Sourced from PDFs,
  Word docs, RTF and TXT via `import_legacy_archive.py`. No URL, no `instapaper_id`, no
  `date_archived`. Critically, its `date_saved` is **parsed from the filename**, which
  encodes the article's *publication* date, not a save date. Every row from 2005 to 2011
  has zero URL coverage.
- **Instapaper export (7,078 rows, 2010-2025).** Real URLs, real `instapaper_id`, and
  `date_archived` where the bookmark was archived.

Coverage by save year makes the seam obvious — URL coverage is 0% through 2009, 13.6% in
2010, 13.3% in 2011, then 76.6% in 2012 and effectively 100% from 2014 on.

**Consequence:** any analysis keyed on `url` (domains, link rot, publication mix) covers
40% of the archive and structurally excludes the entire pre-2012 era. Any analysis keyed
on `date_saved` is comparing publication dates to save dates across that seam. This is
the single most important thing to know before building charts on this corpus.

### The "22 years" number needs a footnote

`date_saved` spans 1953-09-07 to 2025-12-01. That is 72 years, and it is why the
dashboard's monthly timeline is broken (see below). Only **36 rows** predate 2005. Drop
them and the honest span is **2005-01-24 to 2025-12-01 — 20.9 years, 17,601 articles
(99.8% of the corpus)**.

"22 years of reading" is defensible as a round number for a 20.9-year span, but the
36 outliers must be filtered or the axis is meaningless.

### There is no time-of-day data at all

Zero of 17,637 `date_saved` values and zero of 6,311 `date_archived` values carry a
non-midnight time. **Any "when do I read — mornings or late nights?" visualization is
impossible** with the current corpus. Day-of-week and month-of-year work fine; hour-of-day
does not exist and cannot be recovered from the index.

### Entity vocabularies are extremely long-tailed

This determines which "rivers over time" charts are buildable today:

| Field | Vocabulary | Top 20 covers | Top 100 covers | Used exactly once |
|---|---|---|---|---|
| `topics` | 29,882 | 25.5% | 48.1% | 73.3% |
| `people` | 41,469 | 16.8% | 28.8% | 76.7% |
| `concepts` | 51,162 | 21.7% | 41.2% | — |
| `orgs` | 27,635 | **42.9%** | **59.2%** | — |

The enrichment pass was run without a controlled vocabulary, so `topics` is 29,882 free-text
strings of which 21,892 appear exactly once. The most common topic, "Technology", tags only
1,053 articles (6%).

**`orgs` is the only entity field with usable head coverage.** Any topic-based river chart
needs a normalization pass first; an org-based one works today.

### Two data-quality defects worth naming

**Boilerplate contamination — 2,258 articles (12.8%).** 680 distinct `content_snippet`
values are shared by more than one article. The largest clusters are site chrome, not
articles: 367 articles share a Business Insider placeholder-logo header, 285 share a
Co.Design navigation block, 120 share a Business Insider subscribe/newsletter block.

The Co.Design cluster is the clearest failure: **290 articles have a `word_count` of
exactly 642** and all carry the identical `people` list — `['Josh Earnest', 'Antonia
Iamartino', 'Deb Haaland', 'Todd Sherman', 'Todd Kaplan', 'Jony Ive']`, sometimes with
'JD Vance' appended. Those names appear on articles saved in 2012. The scrape captured
Fast Company's site furniture instead of the article, and the enrichment pass then
extracted entities from the furniture. This is why "Todd Sherman" and "Antonia Iamartino"
rank in the top 15 people of a 17,000-article reading archive.

**Author-field artifacts.** Beyond the 72.6% `Unknown`, the author column contains
`By` (19 rows), `#author.fullName}` (13 rows, an unrendered template variable), and
`http://kurzweilai.net/` (318 rows, a URL in the author field). Only **4,509 rows
(25.6%) carry a plausible human author name**, across 2,383 distinct values.

---

## Part 1: Audit of the Streamlit dashboard

`dashboard/app.py` is a single 2,003-line module: eight page renderers behind a sidebar
radio, plus an SM-2 spaced-repetition implementation. It reads exactly two files —
`data/archive_index.parquet` and `data/review_history.parquet` — with one exception noted
below. `load_data()` deliberately does not cache (`# Load data without caching - always
fresh from disk`), which is affordable: the Parquet load measures **0.11s**.

A composite `date_read = date_archived.fillna(date_saved)` is computed at load. This is
the right instinct and it is also the seam problem in one line: for 6,311 rows it means
"when he archived it", and for the other 11,326 it means "when the file was named", which
for the legacy era is a publication date.

### The eight surfaces

| # | Page | Reads | Verdict |
|---|---|---|---|
| 1 | **The Quantified Reader** | `word_count`, `reading_time_min`, `grade_level`, `date_read`, `author` | Partly earns it. 4 KPI tiles, monthly timeline, day-of-week, complexity trend, top authors, length histogram — all sound. But ~150 lines of it are achievement badges and famous-book comparisons that encode no data about his reading. |
| 2 | **Content Intelligence** | `concepts`, `emotion`, `sentiment`, `topics` | Weak. Word cloud, concept-emergence bubble chart, emotion pie, topic treemap. The word cloud and treemap are the definition of default-chart filler on a 51,162-term vocabulary. |
| 3 | **Network & Entities** | `people`, `orgs`, `locations`, `concepts` | Honest but plain. Four ranked `st.dataframe` tables. Called "Network" but draws no network. |
| 4 | **Concept Explorer** | all four entity lists | Earns it. Pick an entity, see its articles. This is the one that answers a real question. |
| 5 | **Archive Explorer** | `title`, `author`, `summary`, `emotion` + entity lists | Earns it, with a caveat — see search below. |
| 6 | **Trends Over Time** | domains from `url`, all entity lists | Earns it. Six tabs of top-N-over-time, plus custom entity tracking with selectable granularity. The most substantive page. |
| 7 | **Heatmap Analysis** | `topics`, `locations`, `sentiment` | Nearly earns it. Topic×year, location×year, sentiment×topic. Closest existing thing to a topic river, but built on the raw 29,882-term vocabulary. |
| 8 | **Spaced Review** | `review_history.parquet`, `file_path` | Different product. A working SM-2 flashcard system bolted to a reading dashboard. |

### What is broken right now

**The monthly timeline renders 868 bars, 610 of them empty (70%).** Because 36 pre-2005
rows stretch `date_read` across 72 years, `resample("ME")` emits a bar per month from
1953. The actual reading history is squeezed into the right-hand third. This is the most
visible defect in the app and it is a one-line filter to fix.

**"Top Authors" is a chart of the word "Unknown."** `df["author"].value_counts().head(10)`
puts `Unknown` first at 12,809 — 4.3x the sum of the nine real bars behind it.

**"Avg. Grade Level" is skewed by parser noise.** Reported as 12.1; clipped to a sane
0-20 range it is 11.5. 732 rows claim a reading level above grade 20 and one is negative.

**"Read Original" renders a dead link on 60% of rows.** `st.markdown(f"[Read
Original]({row['url']})")` is emitted unconditionally in Archive Explorer, so every
legacy-era article gets a link to nowhere.

**The full-article viewer cannot open anything.** Spaced Review reads the article body
from `file_path`, and every one of the 17,637 paths points at
`/Volumes/Extreme SSD/Instapaper-Archive/`. **That volume is not mounted** (checked
2026-08-11; `/Volumes` holds AST, CIRCUITPY, Macintosh HD, Reolink, Time Machine). The
code degrades gracefully to "File not found on this machine," so this reads as a missing
feature rather than a crash — but the archive's actual text is currently unreachable from
the dashboard. **This is also the central risk for nightly automation** (see Part 2).

**Search does not search the articles.** The Archive Explorer builds its haystack from
`title`, `author`, `summary`, `emotion`, `topics`, `people`, `locations`, `concepts` —
metadata only. Bodies are never searched; `content_snippet` holds only the first 500
characters and is not in the blob. Searching "climate" returns 522 articles matched on
Gemini-written summaries and tags, not on what the authors wrote.

Performance is fine despite the naive implementation: the `df.apply(..., axis=1)` blob
scan over 17,637 rows measures **0.24s per keystroke**.

### What breaks when the Matter era lands

The Matter sync writes 19 frontmatter keys (`MATTER_OWNED_KEYS` in
`scripts/matter/mapping.py`). The branch's `build_index.py` adds only four new columns to
the Parquet index: `matter_id`, `reread_count`, `source`, `content_type`.

**The three Matter-era signals most worth visualizing never reach the dashboard:**

| Frontmatter key | In the index? | What it would unlock |
|---|---|---|
| `matter_status` | **No** | read vs. still-queued — the aspiration/consumption gap |
| `matter_progress` | **No** | partial reads, abandonment |
| `matter_highlight_count` | **No** | highlight density as an engagement signal |
| `date_saved_source` | **No** | date provenance — which dates are real vs. fallback |
| `tags` | **No** | Adam's *own* tags, the only human-authored metadata in the corpus |
| `favorite`, `matter_site_name` | No | |
| `source`, `content_type`, `matter_id`, `reread_count` | Yes | era splits, podcasts/PDFs vs. articles, re-reads |

Adding them is a small change to one `return` dict in `build_index.py` plus a re-index —
but until it happens, "read vs. saved," "highlight density," and "date provenance" are
not merely unbuilt, they are invisible.

**Highlights are structurally invisible too.** `MATTER_SYNC.md` deliberately puts
highlights in the article *body*, appended as a trailing `## Highlights` section, so the
enrichment pass sees them. But the index stores `content[:500]` — the first 500
characters. A trailing section never lands in `content_snippet`. Highlight *text* reaches
the dashboard only if the body is read off disk, which requires the unmounted volume.

**Three date semantics in one column.** After Matter, `date_saved` means: publication date
(legacy, 10,559 rows), true save date (Instapaper, 7,078 rows), and Matter's `updated_at`
used as a documented fallback because API v1 exposes no created-at. `date_saved_source`
records which — and is not in the index. The composite `date_read` silently blends all
three.

**Content types beyond articles.** Matter carries podcasts and PDFs. `word_count` and
`grade_level` on a podcast transcript are not comparable to an essay's, so the
"Words Read" tile and the complexity trend will quietly absorb a different kind of object
unless `content_type` filters them.

### Which visualizations earn their place

**Keep:** the four KPI tiles (minus the grade-level noise), the monthly timeline (once
filtered), day-of-week, the length histogram, Trends Over Time in full, the topic and
location heatmaps, Concept Explorer, Archive Explorer.

**Cut:** the achievement-badge ladder and the famous-works comparison bar chart (~150
lines, a full screen, and they encode nothing about *his* reading — "1.4x the Harry Potter
series" is the same chart for any corpus of the same size). The concept word cloud —
a 51,162-term vocabulary rendered as decorative typography, which is precisely
"decoration for decoration's sake." The emotion pie chart: 63.6% of articles are tagged
`Analytical`, so it is one slice and a fringe.

**Rebuild rather than port:** the topic treemap and the topic heatmap. Both are the right
*idea* on the wrong vocabulary.

**Decide separately:** Spaced Review. It is a genuinely different product — a
write-heavy, stateful flashcard app — living inside a read-only analytics dashboard, and
it is the one surface that cannot become a static page (see Part 2).

---

## Part 2: Migration to a static site

### Verdict

**Migrate — but fix the storage dependency first, and leave Spaced Review behind.**

The corpus is 17,637 rows and grows by a handful a day. Every aggregate the dashboard
computes is a group-by over a 14 MB Parquet file that loads in 0.11 seconds; the full set
of monthly entity aggregates measures **0.23 seconds**. There is no interactive computation
here that justifies a live Python server. Streamlit is being used as a rendering engine for
numbers that are identical every night until the sync runs — which is the exact shape the
Daybook already solved.

The blocker is not architectural. It is that **the source markdown lives on an unmounted
external SSD**, and a nightly unattended build cannot depend on a drive being plugged in.

### The one real blocker: `/Volumes/Extreme SSD`

Every `file_path` in the index points at `/Volumes/Extreme SSD/Instapaper-Archive/`, and
the Matter plist sets `INSTAPAPER_VAULT_PATH` to the same. The drive was not mounted
during this audit.

The Matter plist already handles this correctly for *sync* — its header documents that the
job "exits 2 with a clear message rather than creating an empty archive" when the vault is
missing. But that only protects the sync. A build-and-deploy leg needs its own answer,
because the failure mode is worse: `build_index.py` on a missing vault produces a smaller
index, and a static generator would then cheerfully deploy a site claiming Adam has read
fewer articles than he has.

Three options, in order of preference:

1. **Build from the Parquet index only, and treat the index as the deployable artifact.**
   The 14 MB Parquet is *in the repo* and committed. Every aggregate in Part 1 and nearly
   every deep dive in Part 3 needs only the index. The generator then has no external
   volume dependency at all, and a missing drive degrades to "tonight's site is yesterday's
   data," which is honest and safe. Article *bodies* stay unavailable, which is the status
   quo.
2. **Move the vault to internal storage.** ~17,600 markdown files; the index is 14 MB and
   bodies are the bulk, but this is text — plausibly 1-3 GB. Worth measuring. This is the
   only option that unlocks full-text search and highlight display.
3. **Guard and fail loudly.** Keep the vault external, have the generator refuse to build
   when the mount is absent, and let the heartbeat report the skip.

**Recommendation: (1) now, (2) when full-text search becomes the priority.** Ship the
static site against the committed Parquet, and treat "move the vault internal" as the
prerequisite for the search tier below.

### Reference architecture (the Daybook)

The Daybook at `~/Documents/Code/daybook` is the pattern to copy, and it is proven at
larger scale than this: **455 rendered pages, 41 MB**, rebuilt and deployed nightly.

```
site/generate.py  →  _site/  →  wrangler pages deploy  →  Cloudflare Pages  →  Access gate
```

Concretely, from `site/deploy.sh` and `launchd/com.thedetech.daybook.daily.plist`:

- `generate.py` takes `--out`, is idempotent, re-renders the whole site each run, and
  reports coverage honestly rather than pretending completeness.
- `deploy.sh` retries the generate step up to 3 times (a transient macOS EPERM under
  launchd was hitting it), then runs `wrangler pages deploy "$OUT" --project-name
  "$PROJECT" --branch "$BRANCH"`.
- `_site/` is gitignored. Rendered output embeds private content and never goes to GitHub.
- Every page carries `<meta name="robots" content="noindex, nofollow">`.

Three launchd gotchas are already paid for in that plist and must be copied verbatim:

- **`ProgramArguments[0]` must be `/opt/homebrew/bin/python3`,** not `/bin/bash`. macOS
  attributes the Full Disk Access grant to argv[0], and `bash` lacks access to
  `~/Documents` — this silently killed the Daybook nightly for three days in July 2026.
  The existing Matter plist already does this correctly.
- **Log paths must live outside `~/Documents`,** or the spawn dies with `EX_CONFIG` (78)
  before the job runs. The log *directory* must exist; launchd will not create it.
- **`PATH` must include `~/.volta/bin` first** if wrangler is invoked, because wrangler is
  a Volta shim. The Matter plist's current PATH does **not** include it, so the deploy leg
  must add it.

### What must survive, and how

**Search across 17,637 articles — client-side, comfortably.** The measured payloads:

| Payload | Raw | Gzipped |
|---|---|---|
| title + url + date + word count + author | 2.77 MB | **0.81 MB** |
| the above + summary + topics | 11.22 MB | **3.83 MB** |
| the full current search blob (metadata only, as Streamlit builds it) | 12.8 MB | — |

17,637 rows is small for client-side search. The honest statement of scale: a MiniSearch
or Lunr index over title + author + summary + entity tags lands in the low single-digit
megabytes gzipped and builds in the browser in well under a second on a modern machine.
That is a completely ordinary payload for a gated personal site — and it delivers *more*
than today, since the current search is a linear scan.

Full-text search over *bodies* is a different question. 17,637 articles at ~1,034 mean
words is roughly 18.2 million words; that is not a client-side index. It needs either
option (2) above plus a server-side index, or a deliberate decision to keep searching
metadata. **Recommendation: ship metadata search (matching today's behavior, faster), and
treat body search as a later tier.**

**Everything else precomputes.** The measured cost of every monthly entity aggregate in
the app is 0.23s. Facet pages (per-year, per-domain, per-topic, per-entity) are static
HTML generated at build time. The interactions worth preserving — filter by date range,
pick an entity and see its articles, switch granularity — are either URL-addressable
static pages or trivial client-side filtering over the same JSON the search index uses.

**One thing genuinely does not survive: Spaced Review.** It writes
`review_history.parquet` on every rating. A static page cannot persist state. Options:
keep Streamlit alive locally for that one page, port it to a small Worker with KV, or
retire it. Given it is a different product from an analytics site, **retiring it from the
static build and keeping the local Streamlit app as its home** is the low-cost answer —
and worth confirming Adam has actually used it, since `review_history.parquet` is 4.2 KB.

### Build duration

Measured components: Parquet load 0.11s, all monthly entity aggregates 0.23s, rendering
17,637 table rows 0.05s.

The dominant cost is writing files, not computing. Two shapes:

- **Aggregate pages only** (year pages, topic/entity/domain facets, ~500-1,500 files):
  **a few seconds**, comparable to the Daybook's 455 pages.
- **One page per article** (17,637 files): still likely **under a minute**, but it makes
  every deploy upload 17k objects. Wrangler uploads incrementally, so steady-state nightly
  deploys stay small; the first is large.

**Recommendation: aggregate pages plus a single client-side-rendered article detail view**
driven by the JSON payload. That keeps the deploy small and the build near-instant. Per-article
static pages can come later if permalinks matter.

Either way the build is negligible next to the Matter sync itself, whose first backfill is
documented at ~37 minutes of wall time and whose nightly delta is far smaller.

### Wiring it into the nightly chain

The Matter plist (`com.thedetech.article-sync.matter`, 04:45 daily) already runs
`--sync --rebuild-index`. The build+deploy leg is a third step in the same chain.

Following the Daybook's shape, the plist should invoke a wrapper script rather than
gaining more arguments, so the steps are ordered and individually logged:

1. `export_matter_to_archive.py --sync` (existing)
2. `build_index.py` via the repo venv (existing, as `--rebuild-index`)
3. **new:** `site/generate.py --out _site`
4. **new:** `wrangler pages deploy _site --project-name <project> --branch main`

Two things to get right:

- **Add `~/.volta/bin` to the plist's `PATH`.** The current Matter plist omits it, and
  wrangler will not resolve under launchd without it.
- **Extend the existing heartbeat rather than adding a second one.** `MATTER_SYNC.md`
  already writes `nightly-heartbeat.json` to `~/Library/Logs/MatterSync/` in the fleet's
  standard `started_at` / `finished_at` / `outcome` shape. The deploy leg should be part of
  the same heartbeat's outcome, so a successful sync followed by a failed deploy reports
  as a failure.

**The fleet band will not show this job until it is registered.** `MATTER_SYNC.md` already
flags that Command Center's launchd panel reads a hardcoded registry. That registry is the
job-spec list in `~/Documents/Code/command-center/scripts/cockpit/collect_fleet.py` (the
`"label": "com.thedetech.…"` entries around lines 79-170). Writing the heartbeat is
necessary but not sufficient — a Command Center change is required, and it belongs in the
same batch of work or the job runs invisibly.

### Privacy

**Cloudflare Access is mandatory, exactly as for the Daybook.** A 22-year reading history
is more revealing than a browsing history — it is a record of what he chose to think about,
including health, finances, and family. Non-negotiable specifics, mirroring
`site/DEPLOY.md`:

- Zero Trust → Access → self-hosted application scoped to **both** the `*.pages.dev`
  hostname and the custom domain. Scoping only the custom domain leaves the pages.dev URL
  world-readable.
- Allow policy on `adam@thedetech.com` and `athede@gmail.com`; One-Time PIN login.
- `<meta name="robots" content="noindex, nofollow">` on every page.
- **Configure Access before the first deploy, not after.** Until the policy exists, the
  site is public.
- `_site/` and any generated JSON payload gitignored. The search payload embeds titles,
  summaries and URLs for 17,637 articles — it must never reach GitHub.

One note specific to this project: the Daybook's generator strips full transcripts at the
byte level. This site's equivalent decision is whether Matter **highlights** — Adam's own
annotations, in his own words — belong in the deployed payload. They are the most personal
content in the corpus. Recommend including them (they are the point) but deciding
deliberately.

### Domain

The house principle from the `learn.adamthede.com` decision is to name the place for the
activity, not the artifact.

- `article-archive.adamthede.com` — names the artifact, and names it twice. 15 characters
  before the first dot. It also encodes a format ("article") that the corpus has already
  outgrown: Matter brings podcasts and PDFs.
- **`reading.adamthede.com`** — names the activity, survives the corpus growing beyond
  articles, and sits naturally beside `daybook.` and `learn.` as a verb-shaped place. It
  is what he would type from memory.

**Recommendation: `reading.adamthede.com`.**

A note on that neighborhood: `daybook.` is the record of building, `learn.` is the durable
library of lessons, `reading.` is the record of intellectual input. Three sibling
subdomains, three verbs, one person.

### Phased effort

| Phase | Scope | Estimate |
|---|---|---|
| **0. Index fixes** | Carry `matter_status`, `matter_progress`, `matter_highlight_count`, `date_saved_source`, `tags`, `favorite` into `build_index.py`'s return dict; re-index. Filter the 36 pre-2005 rows or add an `era` column. Clip `grade_level`. | **1-2 h** |
| **1. Generator skeleton** | `site/generate.py` + `render.py` modeled on the Daybook, Felton dark skin, index + year pages + the KPI band, reading from the committed Parquet only. | **1 day** |
| **2. Facets + search** | Topic/org/domain/author facet pages, MiniSearch index build, client-side search page, article detail view from JSON. | **1 day** |
| **3. Deploy + wire** | `deploy.sh`, Pages project, Access policy, custom domain, plist extension with the volta PATH fix, heartbeat extension, Command Center fleet registration. | **half day** |
| **4. Deep dives** | The Part 3 menu, incrementally — each is an added page against data that is already there. | per item, below |

**Total to a live, gated, nightly-rebuilt site: about 3 days.** Phase 0 is worth doing
immediately regardless of the migration decision, because it is also what unblocks the
Matter-era deep dives.

---

## Part 3: The deep-dive menu

This is the most Felton-shaped dataset in the portfolio: two decades of a single
person's intellectual input, timestamped and enriched. What follows is ranked by
fascination per unit of effort. Every entry names the fields it needs, and entries that
need work that does not exist yet say so.

**Legend:** ✅ buildable today from the current index · ⚙️ needs the Phase 0 index fix ·
🔬 needs a new enrichment or crawl pass that does not exist

### The ranked menu

**1. ✅ The Deferral Ledger — saved vs. actually read.**
`date_saved` + `date_archived`, 6,311 rows. Median lag **1 day**; 75th percentile **13
days**; 90th percentile **356 days**; maximum **3,561 days** — nine years and nine months
between saving "How Do You Raise a Prodigy?" in November 2012 and archiving it in August
2022. 622 articles waited more than a year. A further **767 Instapaper-era articles were
never archived at all** (369 saved in 2025, 212 in 2023, 177 in 2024). Zero new work
required, and it is the honest version of the read-vs-saved gap he already wants — 
available *now*, without waiting for Matter. Sketched below.

**2. ✅ The Attention U-Curve — median article length by year.**
`word_count` + `date_read`. Median words per article: **912 (2005) → 632 (2011) → 424
(2013) → 419 (2019) → 1,593 (2021) → 1,285 (2025)**. A near-perfect U across twenty years:
long-form, collapsing through the social-media decade, then a full recovery to *above* where
it started. Requires an honesty control for source composition (see the sketch). Sketched below.

**3. ✅ Organization Rivers — who dominated his attention, by year.**
`orgs` + `date_read`. The only entity field with head coverage strong enough to carry a
stream graph: top 20 orgs cover **42.9%** of articles (vs. 25.5% for topics, 16.8% for
people). Google (2,412), Apple (1,952), Facebook (1,625), Microsoft (1,035), Twitter (665).
Watching Facebook rise and fall against Apple's steadiness over 20 years is the "topic
river" idea, built on the one field that can actually support it today. Sketched below.

**4. ✅ Author Loyalty Arcs.**
`author` + `date_saved`, restricted to the 4,509 rows with a real name. **41 authors have
5+ articles spanning 5+ years.** Paul Graham: 13 articles across **14.6 years** (Oct 2010 →
May 2025). Maria Popova: 143 across 13.3 years. Morgan Housel: 9 across 14.5 years. A
horizontal timeline, one row per author, a dot per article — the people he kept coming back
to for a decade and a half. Needs a small author-cleanup pass first (`By`,
`#author.fullName}`, the kurzweilai.net URL).

**5. ✅ Reading Seasons.**
`date_read` month-of-year, 2005+. October is the peak at **2,209 articles**; July the
trough at **1,020** — a **2.17x** swing. Weekdays run **1.61x** weekend days per day, with
Thursday highest (3,085) and Saturday lowest (1,696). A Felton-style radial or small-multiple
year grid. Cheap, pretty, and genuinely about him. Note the hard limit: **no time-of-day
data exists**, so this stops at day-of-week.

**6. ✅ Reading Bankruptcy — the mass-archive events.**
`date_archived` daily counts. On **2015-03-08**, 216 articles were archived in a single
day. Other spikes: 70 (2025-05-03), 63 (2012-09-18), 48 (2023-11-25), 47 (2023-09-03).
The top five days account for 7.0% of all archives. These are queue-declaration-of-bankruptcy
moments, and they are both a great small chart and a **necessary caveat on #1** — an archive
date is when an article left the queue, which is not always when it was read.

**7. ⚙️ The Honesty Gap — Matter's read vs. queued.**
Needs `matter_status` and `matter_progress` in the index (Phase 0). Once there, this is #1's
live counterpart: what is in the queue right now, how long it has been there, and what share
of saves ever get read. Highest-value *new* signal the Matter era brings.

**8. ⚙️🔬 Highlight Density as an engagement signal.**
Needs `matter_highlight_count` in the index (Phase 0), and highlight *text* needs either a
larger `content_snippet` or a mounted vault. The premise is strong — highlights are the only
signal in the corpus of *active* rather than passive reading — but it applies solely to the
Matter era, so it is a going-forward instrument, not a 22-year retrospective.

**9. 🔬 Topic Rivers — the flagship, and the one that needs real work.**
This is the chart the corpus is *for*: how subjects entered and left his life across two
decades. It **cannot** be built on the current `topics` field — 29,882 distinct values,
73.3% used exactly once, top 20 covering only a quarter of the archive. It needs a
normalization pass: either embed the topic strings and cluster to ~40 stable themes, or run
a cheap local-model pass mapping each article to a controlled taxonomy. This is the single
highest-leverage enrichment investment available, and the PKM Vault enrichment schema is the
prior art. Estimate: a local pass over 17,637 articles is an overnight job, not an
afternoon's.

**10. 🔬 Link Rot as Memento Mori.**
`url`, 7,078 rows, 1,104 distinct domains, 612 of them appearing exactly once. **A caution
learned the hard way during this audit:** checking domain roots is a *misleading* method. A
26-domain sample returned 5 "failures" that were all bot-blocking (Bloomberg, Fast Company,
mobihealthnews all return 403 to a scripted HEAD), while domains that returned a clean 200
are the genuinely interesting deaths — `kurzweilai.net` (318 articles) redirects to
`thekurzweillibrary.com`, `brainpickings.org` (137) to `themarginalian.org`, and
`bits.blogs.nytimes.com` (89) to the NYT homepage, which means those 89 deep links are gone
even though the "domain" is alive. The real method is **article-level, detecting
redirect-to-homepage and 404, cross-checked against the Wayback Machine** — a polite crawl of
7,078 URLs, hours of wall time, run once and then incrementally. Worth it: the finding
"N% of what I read no longer exists at its original address" is the most quietly devastating
number in the archive. Remember it covers only the 40% with URLs.

**11. ⚙️ The Two-Corpus Portrait.**
Needs `source` in the index (already in the Matter branch). Rather than hiding the seam
described in Part 0, make it a page: what the PDF-hoarding era looked like versus the
Instapaper era versus the Matter era, and how the three modes of saving changed what he
saved. Turns the archive's biggest data-quality caveat into a story about changing
technology. Cheap once `source` lands.

**12. ✅ Publication Mix Over Time.**
Domains by year from `url`. NYT (1,038) is the constant; Business Insider (897) is almost
entirely a 2012-2014 phenomenon; `yahoonewsdigest-us.tumblr.com` (167 + 56) is an artifact
of a dead product. Straightforward, and pairs naturally with #10.

**13. ✅ Self-Portrait in Entities.**
`people` includes **"Adam Thede" 214 times** — his own documents came in through the legacy
import and the enrichment pass extracted him as a subject of his own archive. A small,
strange, very personal page.

**14. ✅ Complexity Drift.** `grade_level` by year, clipped to 0-20. Honest but thin, and
Flesch-Kincaid on scraped web text is noisy (732 rows above grade 20). Lowest fascination on
this list; include only as a supporting strip under #2.

### Explicitly not recommended

**Emotional weather.** `emotion` is 63.6% `Analytical` (11,191 of 17,577). There is not
enough variance to build on.

**Life-event correlation.** The brief raises overlaying job changes and family events. The
data supports the *timeline* precisely, but the archive contains no life-event annotations,
and nothing in the corpus derives them. This needs Adam to hand-author a small events file
(a dozen dated rows) — at which point it becomes one of the most resonant pages on the site,
because it makes reading legible as biography. Recommend: ask him for the events file, then
build it. Do not attempt to infer the events.

**Time-of-day / circadian reading.** Impossible. Zero timestamps carry a time component.

---

### The top three, sketched

#### 1. The Deferral Ledger

**Form.** A horizontal dumbbell chart, one row per article, sorted by lag descending, on a
log-scaled day axis. Left dot: saved. Right dot: archived. The connecting line *is* the
deferral. Above it, a compact distribution strip showing that the median lag is one day —
so the eye immediately understands that the long lines are the exception, not the rule.

**Interaction.** A single toggle: **"Read" / "Never read."** Flipping to "Never read"
replaces the dumbbells with 767 open circles — saves with no closing dot — anchored at their
save date and trailing a line that runs to today and keeps growing. Hovering any row gives
title, word count, and the lag in plain language ("saved 9 years 9 months before you got to
it").

**Why it is fascinating rather than merely complete.** Most reading dashboards report what
you read. This one reports the distance between intention and action, which is the actually
interesting fact about a read-it-later service — and it has a punchline the data delivers
unprompted: he did eventually read the thing he saved in 2012. The open circles are the
counterweight. It is a self-portrait of ambition and its follow-through, and it needs no
new data.

**Honesty note to render on the page:** archive dates cluster on a few bankruptcy days
(216 on 2015-03-08). Those days should be visibly marked, because on them the lag measures
capitulation rather than reading.

#### 2. The Attention U-Curve

**Form.** A ridgeline: one horizontal density curve of article length per year, stacked
2005 at top to 2025 at bottom, each year's median marked with a tick and connected down the
stack by a single thin line. That connecting line is the U. The distributions matter as much
as the medians — the collapse years are not just shorter, they are *narrower*, a decade of
uniformly brief posts.

**Interaction.** A source toggle splitting legacy / Instapaper / Matter into separate,
differently-weighted curves. This is the integrity control, and it should be on by default
rather than hidden: the early highs are partly a curation artifact (nobody prints a
200-word blog post to PDF) and the 2012-2014 collapse is partly a Business Insider artifact.
The chart is *more* interesting once it shows how much of the U is him and how much is the
medium — and it can only make that argument if the split is visible.

**Why it is fascinating.** It is a twenty-year measurement of an argument everyone makes
about themselves and almost nobody can evidence: that the internet shortened their attention,
and that they got it back. He has the receipts, and the recovery is real — 2021's median of
1,593 words is the highest in the entire series, higher than the PDF era. That is a finding,
not a chart.

#### 3. Organization Rivers

**Form.** A stream graph, 2005-2025, top ~15 organizations by total mentions, flowing
horizontally, with the stream's total thickness held to the article count so eras of heavy
reading are visibly fatter. Labels set inline within each band where it is thick enough, in
the house small-caps mono.

**Interaction.** Click a band to pin it: the stream fades to grey, the selected
organization stays lit, and a panel lists that org's articles chronologically with their
titles — so the abstraction immediately resolves into the specific things he read about
Facebook in 2012.

**Why it is fascinating rather than merely complete.** A topic river built on the raw
`topics` field would be a lie dressed as a chart — the vocabulary is too sparse to carry it
(top 20 topics: 25.5% coverage). Organizations are the one field where the head is dense
enough (42.9%) that the shape is real. And organizations *are* the story of 2005-2025: the
rise of Facebook, the constancy of Apple, the disappearance of Yahoo, the arrival of
OpenAI. It reads as a history of the technology industry that happens to be told entirely
by which articles one person chose to save.

**Upgrade path:** once #9's topic normalization exists, the same component re-renders with
themes instead of organizations, and *that* is the flagship. Building it on orgs first means
the visualization is proven before the expensive enrichment pass is commissioned.

---

## Recommended sequence

1. **Phase 0 index fixes** (1-2 h) — carry the six missing Matter fields, add `era`, clip
   `grade_level`. Do this regardless of everything else; it unblocks the Matter deep dives
   and takes an afternoon at most.
2. **Decide the vault question** — build-from-Parquet (recommended) or move the vault
   internal. Everything downstream depends on it.
3. **Static site phases 1-3** (~2.5 days) → `reading.adamthede.com` behind Access.
4. **Deep dives 1, 2, 3** — all buildable from the existing index, all Felton-shaped.
5. **Commission the topic normalization pass** (#9) once the stream-graph component is
   proven on organizations.
6. **Link-rot crawl** (#10) as a background one-off.

## Open questions for Adam

- Has Spaced Review ever been used in earnest? `review_history.parquet` is 4.2 KB. If not,
  retiring it removes the only obstacle to a fully static build.
- Is the Extreme SSD vault meant to stay external? That one answer determines whether
  full-text search is ever on the table.
- Will he hand-author the dozen-row life-events file that unlocks the reading-as-biography
  overlay? It is the highest-resonance page on this list and the only one that needs
  something from him rather than from the data.
- Should the 290 Co.Design articles and the ~2,258 boilerplate-contaminated rows be
  re-scraped, re-enriched, or simply flagged and excluded from entity charts? Flagging is
  cheap; re-scraping means going back to sites that mostly no longer serve those URLs.
