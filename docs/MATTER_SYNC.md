# Matter Sync

Pulls reading from [Matter](https://hq.getmatter.com) into the Article Archive,
so the Instapaper era (~2008-2025) and the Matter era read as one continuous
history rather than two disconnected corpora.

Matter items become Markdown files with YAML frontmatter in the vault, exactly
like Instapaper articles. From there the existing pipeline takes over unchanged:
`enrich_archive_gemini.py` adds the `ai_*` fields, `build_index.py` compiles the
Parquet index, and the Streamlit dashboard reads it.

---

## What belongs in the archive: read, not saved

The archive is a timestamped record of what Adam has **actually read** and when.
Not what he meant to read. Matter's statuses map onto that distinction exactly:

| Matter status | Meaning | Synced? |
|---|---|---|
| `archive` | He read it. | **Yes** - this is reading history. |
| `queue` | Saved, not yet read. | No - that is intent, not history. |
| `inbox` | Unsaved discovery feed. | No - not even intent. |

So the default is `--status archive`. Pulling the queue would not merely add
unread articles to the corpus; it would **date** them, because the dashboard
derives `date_read` from `date_archived` and falls back to `date_saved`. An
article he saved and never opened would enter the reading timeline on the day he
saved it. Measured against the real library, the old `archive,queue` default
would have written **503 unread articles** into the read record.

Two independent locks, because one default is not a guarantee:

1. **The sync** pulls `archive` only. `--status` still accepts `queue` for
   deliberate use.
2. **The dashboard** never dates a Matter row it has no read evidence for. A
   Matter row without `date_archived` gets no `date_read` at all and is held out
   of every timeline surface - see [How `date_read` works](#how-date_read-works).

### Does `archive` really mean read?

It is the only read signal Matter offers, so it is worth checking rather than
assuming. `reading_progress` gives an independent cross-check, and the two
distributions are near mirror images:

| `reading_progress` | `archive` (n=1,230) | `queue` (n=521) |
|---|---|---|
| 1.00 (finished) | **83.1%** | 5.6% |
| 0.90-1.00 | 3.9% | 0.8% |
| below 0.90 | 4.6% | 10.7% |
| 0.00 (never opened) | 8.4% | **82.9%** |

`archive` means finished 87% of the time; `queue` means never opened 83% of the
time. The mapping is sound.

Two error bars, both small, both left alone deliberately:

- **103 archived articles sit at 0.00 progress** (8.4%). Some were probably
  filed without being read; others were read elsewhere and archived afterwards,
  or simply failed to track. Nothing distinguishes them, and dropping them on a
  heuristic would discard genuinely-read articles. They are synced, and
  `matter_progress` is written to the frontmatter so a later pass can filter on
  it.
- **29 finished articles sit in the queue** (progress 1.00, not archived).
  Archive-only misses these. That is the right trade: acting on progress instead
  of status would mean guessing at what "read" means, when Matter already asks
  Adam directly.

### The nightly job makes this better over time

When Adam finishes an article and Matter moves it to `archive`, a nightly sync
observes the transition within ~24 hours, so `date_archived` lands within a day
of the real reading date. That is a far truer record than any backfill can
manage: items pulled in the initial backfill can only carry Matter's
`updated_at`, and they say so in `date_saved_source`. **The archive gets more
accurate from the day this starts running**, which is the opposite of how
archives usually age.

---

## How `date_read` works

`date_read` is what every timeline, heatmap and trend chart is plotted against.
It is derived per era, because the two eras carry different evidence:

| Era | Rule | Why |
|---|---|---|
| Instapaper, legacy | `date_archived`, falling back to `date_saved` | Load-bearing: 11,326 of the 17,637 rows have no archive date at all - the legacy import had none to record. Dropping the fallback would empty most of the archive out of every chart. |
| Matter | `date_archived` **only**, no fallback | Matter states whether something was read. A Matter row with no archive date is positive evidence it was *not* read, so dating it by when it was saved would assert a read that never happened. |

Articles with no read date are held out of every surface rather than dated by
guesswork, and the sidebar says how many - so "held out" never quietly reads as
"lost". There is currently no saved-not-read surface in the dashboard; unread
articles live in the vault and the index, and appear in no view.

---

## Setup

### 1. Get an API token

Matter's public API needs a personal access token and an **active Matter Pro
subscription**.

1. Go to <https://web.getmatter.com/settings>
2. Click **Generate API Token**
3. Copy the token - it looks like `mat_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6q7r8`

> **One token per account.** Generating a new token immediately revokes the
> previous one. If you also use the Matter CLI, it shares that single token, so
> regenerating for one breaks the other.

### 2. Put it where the sync looks

```bash
mkdir -p ~/.secrets
printf '%s' 'mat_your_token_here' > ~/.secrets/matter.token
chmod 600 ~/.secrets/matter.token
```

`printf` rather than `echo` avoids a trailing newline, though the loader strips
one anyway. The file must not be readable by group or others; the sync refuses
to use a credential that is, and tells you the exact `chmod` to run.

The file can also hold JSON (`{"api_token": "mat_..."}`) if that is more
convenient. **The sync never writes to this file** - see [Why there is no
refresh token](#why-there-is-no-refresh-token).

### 3. Verify

```bash
python3 scripts/core/export_matter_to_archive.py --check-auth
```

Expected output:

```
Token file:   /Users/adam_thede/.secrets/matter.token
Token:        mat_a1b2...r8
Account:      Adam Thede <athede@gmail.com>
Account id:   act_...
Rate limits:  burst=5, markdown=20, read=120, save=10, search=30, write=30
Library:      reachable -- newest updated item is 'Some Article' (archive, updated 2026-08-10T...)

Authentication OK.
```

Then see what a sync would actually do, without writing anything:

```bash
python3 scripts/core/export_matter_to_archive.py --dry-run
```

---

## Usage

| Command | What it does |
|---|---|
| `--check-auth` | Verify the token, print the account and its rate limits. Two API calls, no writes. |
| `--dry-run` | Verify auth, then report what would be created, updated, or skipped. Writes nothing at all - not even the URL-index cache. |
| `--sync` | Incremental pull since the last watermark, using `updated_since`. |
| `--full` | Walk the whole library, ignoring the watermark. What the nightly job runs, and the first backfill - see [Nightly](#nightly). |
| `--max-items N` | Stop after N items have been *written, updated, or skipped as duplicates* — unchanged items do not count, so each run makes real progress. The watermark does not advance, so the next run resumes. |
| `--rebuild-index` | Run `build_index.py` afterwards so the dashboard sees the new articles. |
| `--refetch-content` | Re-download article bodies for items already on disk. |
| `--no-record-rereads` | Do not annotate an existing archive article when Matter reports reading it again; just count it. |
| `--status` | Which Matter statuses to pull (default `archive` - read articles only). |
| `--subdir` | Where in the vault to write (default `matter/`; `''` writes flat). |

### First backfill

Measured against the real library on 2026-08-11, with the `archive`-only
default: **1,230 read articles, of which 998 are already in the archive and 232
are new.** (A further 521 sit unread in the Matter queue and are correctly not
pulled.) The markdown endpoint allows 20 requests per minute and an
already-present article costs no markdown fetch, so the backfill is about
**12 minutes** of wall time - the 998 re-read annotations are local file writes,
not API calls.

Use the venv interpreter here, so dedupe reads the Parquet index directly - see
[Which interpreter to run](#which-interpreter-to-run):

```bash
.venv/bin/python scripts/core/export_matter_to_archive.py --full --max-items 200
```

That is one or two chunks. The manifest makes it resumable and interrupting is
safe. Repeat until it reports no new items; the
budget counts work done rather than items looked at, so each run gets through
another 200 articles instead of spending its allowance re-skipping the ones
already on disk.

Then enrich and rebuild. 232 new articles is a modest enrichment run, though
podcasts and PDFs arrive with full text (tens of thousands of characters each),
so they are not free:

```bash
python3 scripts/core/enrich_archive_gemini.py
python3 scripts/core/build_index.py
```

### Nightly

The nightly job runs `--full`, not `--sync`, on purpose. Incremental mode
filters on `updated_since`, so it only sees a newly-read article if Matter bumps
`updated_at` when an item moves queue -> archive. The spec says it does -
`status` is the first thing named in `updated_at`'s description - but if that
were ever wrong the failure would be silent and permanent: the article would
never appear, with no error and no count. A full run does not depend on it,
because a newly archived article has no manifest entry and is written whatever
its timestamp says.

That costs almost nothing. Measured against the real library with everything
already synced: **14 API requests, 4.9 seconds** - unchanged items are skipped
without a single per-item call. `--sync` is there for a library large enough
that listing it starts to matter.

Install the launchd job (04:45 daily):

```bash
mkdir -p ~/Library/Logs/MatterSync \
  && cp launchd/com.thedetech.article-sync.matter.plist ~/Library/LaunchAgents/ \
  && launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.thedetech.article-sync.matter.plist
```

Run it once by hand to confirm:

```bash
launchctl kickstart -k gui/$(id -u)/com.thedetech.article-sync.matter
```

Logs land in `~/Library/Logs/MatterSync/`, and a heartbeat JSON
(`nightly-heartbeat.json`) is written there in the fleet's standard shape
(`started_at` / `finished_at` / `outcome`), on failure as well as success. Note
that Command Center's launchd panel reads a hardcoded job registry, so showing
this job there needs a separate entry added in that repo - writing the heartbeat
is necessary but not sufficient.

---

## What gets written

```yaml
---
title: "How to Do Great Work"
original_url: "https://paulgraham.com/greatwork.html"
matter_id: "itm_r9f3a"
author: "Paul Graham"
source: "matter"
content_type: "article"
date_saved: "2026-03-30"
date_saved_source: "fallback - matter updated_at (API v1 exposes no created_at)"
date_archived: "2026-03-30"
word_count: 11842
matter_site_name: "paulgraham.com"
tags: ["essays"]
matter_status: "archive"
matter_progress: 0.35
matter_updated_at: "2026-03-30T19:15:00Z"
matter_synced_at: "2026-08-11T04:45:00+00:00"
matter_content_source: "markdown"
matter_highlight_count: 2
---

<the article body as Markdown>

## Highlights

> The way to figure out what to work on is by working.

**Note:** core thesis
```

Highlights go in the **body**, not the frontmatter - they are Adam's own words
about the article, so the enrichment pass and the dashboard's text surfaces
should see them like any other content.

### Field decisions worth knowing

**`date_archived`, not `date_read`.** The original plan proposed `date_read`.
`build_index.py` does not read that key - it reads `date_archived`, and the
dashboard then computes `date_read = date_archived.fillna(date_saved)`. Writing
`date_read` would have produced files that parse cleanly and then sit at the
wrong end of every chart. Matter's `archive` status maps to `date_archived`,
exactly like an archived Instapaper bookmark; `queue` items get no archive date
because they have not been read yet.

**Dates are sticky.** Once an item has been written, its `date_saved` and
`date_archived` are reused verbatim forever. Matter's `updated_at` advances on
*any* change - a new highlight, a progress update - so without stickiness an
article would migrate forward through the timeline every time Adam touched it,
and its filename would change with it.

**Read dates are estimates, and each one says how good it is.** Matter exposes
exactly one timestamp per item - verified against the live library, where the
union of every field across all 1,230 archived items contains one date. So
`date_saved_source` records which of three estimates produced the date, in
descending order of confidence:

| Source | Meaning | Accuracy |
|---|---|---|
| `observed-transition` | The sync watched the article appear in the archive between two runs. Claimed only once a `--full` run has listed the *entire* archive cleanly - a chunked backfill leaves the manifest full of items while most of the library has never been listed, and every unreached article would otherwise be labelled a transition nobody witnessed. | Within one sync interval - a day, nightly. |
| `highlight-derived` | Already archived when first seen, but carries highlights, and the newest one is more than a day older than `updated_at`. Highlights are made *while reading*, so that gap is a later touch dragging `updated_at` forward. | As good as the highlight. |
| `fallback` | Neither applies. `updated_at` is a last-modified date. | Right for recent reads, potentially well late for old ones. |

The estimate is fixed when the article first enters the archive and never
revised - stickiness outranks accuracy, because a date that moves is worse than
a date that is approximate. See [Backfill fidelity](#backfill-fidelity) for how
far off the backfill's estimates actually are.

**`word_count` is omitted when Matter has none** (podcasts, failed extraction),
so `build_index.py` falls back to counting the body's words.

---

## How incremental sync works

1. A checkpoint timestamp is captured **before** any fetching.
2. `GET /v1/items?updated_since=<watermark>&order=updated` returns everything
   changed since last time, paginated by cursor.
3. Each item is classified: unchanged, cross-era duplicate, new, or update.
4. Bodies and highlights are fetched **only** for items being written.
5. The watermark advances to the checkpoint **only after a fully clean run**.

Step 5 is the important one. A partial run leaves the watermark alone: re-reading
a few unchanged items next time costs nothing, whereas skipping past a failure
would lose those items permanently. A run that hit per-item errors, or that was
cut short by `--max-items`, does not advance it.

The watermark is also rewound five minutes on each use, to absorb clock skew
between this machine and Matter's servers.

Highlights have no delta endpoint of their own - annotations can only be listed
per item. That is affordable because a new highlight bumps its parent item's
`updated_at`, so the item delta already identifies which items to ask about.

### Not re-downloading article bodies

A nightly delta is dominated by items that reappeared because a highlight was
added, not because the article changed. When the file on disk already holds the
article text, the sync reuses it and re-fetches only the highlights. This keeps
the 20-per-minute markdown budget for articles that actually need it. Force a
refresh with `--refetch-content`.

---

## Duplicate handling, and re-reads

The same article can legitimately be in both eras: read in Instapaper in 2019,
read again in Matter last week. Before writing anything new, the sync checks the
URL against the existing archive. **It never writes a second file for an article
the archive already has.**

Measured on the real library, this is not a rare edge case: **998 of the 1,230
read articles in Matter are already in the archive.** And they are genuine
re-reading rather than a bulk import - their dates spread across 44 months and
430 distinct days, with the five busiest days accounting for only 9% of them. An
import would have clustered on one or two days.

That makes each one a real reading event, so it is recorded. What happens on a
match, in every case:

| Case | Behaviour |
|---|---|
| Match, Matter status `archive` (a re-read) | No second file. `matter_reread_at` (a sorted list of dates) and `matter_reread_count` are **added** to the existing file. |
| Match, Matter status `queue` | No second file, nothing written. Sitting unread in a second app is not a reading event. |
| Exact raw-URL match | Treated identically to a normalized match - the distinction only matters for measuring, not behaviour. |
| Drifted URL match (`http`/`https`, `www.`, trailing slash, `utm_*`) | Treated as a match. Ambiguous parameters like `ref` and `source` are *not* stripped, so those stay distinct. |
| Matched file's frontmatter will not parse | Counted and logged, file untouched. Not an error. |
| Matched file is not inside the vault | Counted and logged, nothing written. |
| Matched file is not valid UTF-8 | Counted and logged, nothing written. |
| Matched file's `original_url` disagrees with the item | Counted and logged, nothing written; the index entry is stale. |
| The write itself fails | Counted and logged. Never an error - a lost note must not pin the watermark. |
| Dry run | Counted, never written. |

Three fences guard that write, because it is the only one the sync makes to a
file it did not create:

- **Containment.** The match location comes from the Parquet index's
  `file_path` column, which holds absolute paths recorded whenever that index
  was last built. A stale one can name a different vault - and `vault /
  "/abs/path"` collapses to the absolute path in pathlib, so a naive join is no
  protection. The resolved path must sit inside the vault or nothing is written.
- **Strict decoding.** `build_index.py` and the enrichment pass both read with
  `errors="replace"` because they only produce derived data. This writes back,
  so a substituted character would permanently destroy whatever those bytes
  were - and the corpus is known to contain damaged files. Undecodable file, no
  write.
- **Identity.** The matched file's `original_url` must normalize to the same URL
  as the Matter item. A stale index entry can name a path whose file has since
  been replaced by a different article.

**Known limitation: only cross-era re-reads are recorded.** If Adam re-reads a
*Matter-era* article — one this sync itself wrote — by moving it back to the
queue and archiving it again, that second read is not recorded. The sticky
`date_archived` correctly preserves the first read, but nothing marks the
second. Detecting it would need a signal Matter does not provide: with
`--status archive` the sync never observes the article sitting in the queue in
between, and `updated_at` alone cannot distinguish a re-read from a highlight or
a progress tick. Recording it on a guess would be worse than not recording it.
Related: an article that leaves `archive` status disappears from the delta
entirely, so its `matter_status` field goes stale. The read date it carries
stays true, which is what the archive is actually for.

**What a re-read may change on a matched file: only those two keys.** It never
modifies or removes an existing key, so `date_archived` and `date_saved` keep
the original read date - the first read is the historical record, and reading
something again does not revise when it was first read. It never writes
`matter_id` onto a foreign file either: that key is what marks a file as this
sync's own, and stamping it on an Instapaper-era article would eventually invite
the sync to take ownership of it. Recording is idempotent - the same date is
stored once however often it is seen. `--no-record-rereads` turns the annotation
off and leaves only the count.

URLs are normalized first, so `http` vs `https`, `www.` vs not, trailing
slashes, `#fragments`, and `utm_*`/`fbclid` tracking parameters all compare
equal. Genuinely ambiguous parameters (`ref`, `source`) are **not** stripped -
on some sites they select real content, and a false merge would silently discard
an article, which is worse than a visible duplicate.

**Articles with no URL never match anything**, including each other. About
10,560 rows in the archive came from the legacy PDF/Word import with an empty
`url`; treating those as equal would collapse thousands of distinct articles
into one.

The URL index is built from `data/archive_index.parquet` when pyarrow is
available, and otherwise from a cached scan of the vault's frontmatter. If
neither produces anything the sync still runs, but says clearly in the log that
cross-era detection is degraded.

**Known limitation: a skipped duplicate's highlights are not captured.** If Adam
re-reads an article in Matter that he first saved to Instapaper years ago, and
highlights it, the Matter item is skipped as a duplicate and those highlights
land nowhere. The skip is recorded in the manifest, so it stays skipped. Merging
Matter highlights into an existing Instapaper-era file is the obvious fix, but it
means writing into files this sync did not create, which is a bigger promise than
this first version should make. The manifest records every such skip
(`skipped_reason: duplicate_url` plus `duplicate_of`), so the affected articles
can be found later.

### Files this sync wrote but lost track of

The manifest is saved as the run goes and again in a `finally` block, so a
crash or a `kill` still records what reached disk. If the manifest is lost
anyway - deleted, or corrupt and reset - the sync recovers by reading the
`matter_id` out of the frontmatter of the files already in `matter/` and
adopting them, rather than writing a second copy of everything.

---

## Why there is no refresh token

Matter's public API v1 authenticates with a long-lived personal access token and
has **no refresh endpoint** - the OpenAPI spec declares exactly one security
scheme (`bearerAuth`) and no token/refresh path. Because there is nothing to
refresh, the sync never rewrites `~/.secrets/matter.token`, and the hazard of a
crash mid-rewrite destroying the only credential does not exist. Removing the
failure mode beats defending against it.

Matter's older, undocumented **v11** API - the one behind their Obsidian plugin
- does work differently: QR-code device auth, access + refresh tokens, and a new
refresh token handed back on every exchange (so it must be persisted). It is
still live. It was not used here because:

- it has no `updated_since` or any other delta parameter, so a nightly job would
  have to re-walk the entire library every night and filter client-side;
- it has no documented rate limits;
- it is frozen (last commit to the plugin: November 2022).

It does have two things v1 lacks - `publication_date` and highlight position
offsets (`word_start`/`word_end`) - so it is worth remembering if either becomes
important.

---

## Verified vs assumed

Everything below marked **verified** was read from
<https://docs.getmatter.com/openapi.yaml> (OpenAPI 3.1.0), fetched directly, or
observed against the live API. Everything marked **assumed** needs a real token
to confirm.

**Verified from the spec:**

- Base URL `https://api.getmatter.com/public/v1`; `Authorization: Bearer mat_...`
- `GET /v1/items` with `status`, `order`, `updated_since`, `limit` (max 100),
  `cursor`, `content_type`, `tag`, `is_favorite`
- Cursor pagination via `has_more` / `next_cursor`
- `Item` requires `object, id, title, url, status, processing_status,
  is_favorite, content_type, reading_progress, tags, updated_at`; `author` is an
  object (nullable), `url` is top level, `word_count` is nullable
- **`Item` has no `created_at`** - `updated_at` is the only per-item timestamp
- `updated_at` advances on any change to the item or its associated data,
  including new annotations
- `GET /v1/items/{id}?include=markdown` for the body
- `GET /v1/items/{item_id}/annotations` - per item only, no global feed, no
  `updated_since`; `Annotation` is `{object, id, item_id, text, note?,
  created_at, updated_at}` with no position offsets
- Rate limits: read 120/min, markdown 20/min, burst 5/sec, write 30/min
- `GET /v1/me` returns the account and its `rate_limit` object

**Verified against the live API** (no token needed):

- The host is up; an unauthenticated call returns HTTP 401
  `authentication_required`, and an invalid token returns HTTP 401
  `invalid_token` - neither matches the `unauthorized` the prose docs promise.
  The client therefore branches on **HTTP status only**, never on error-code
  strings.

### Verified against the real account (2026-08-11)

Every assumption that previously sat here was checked with a read-only probe and
a `--dry-run` against the live library. Figures are aggregate counts and
percentages only.

| Assumption | Outcome |
|---|---|
| Account has active Matter Pro | **Verified.** Authenticates, and `/v1/me` returns the documented `rate_limit` object with exactly the documented ceilings (read 120, markdown 20, burst 5, write 30, save 10, search 30). |
| Item schema matches the spec | **Verified across the whole library:** zero fields outside the spec, zero missing required fields, and `author` is an object or `null` exactly as declared. |
| `site_name` nullable | **Refuted in practice** - present on 100% of items. Still treated as optional. |
| `excerpt` nullable | **Confirmed nullable** - present on 98.0%. |
| `author` nullable | **Confirmed nullable** - present on 90.7%. The rest are `null` and map to `"Unknown"`, which is what `build_index.py` already writes for authorless articles. |
| `word_count` null for non-text | **Refuted** - present on 100%, including every podcast and PDF. The "omit `word_count` and let `build_index.py` count the body" path is dormant, not wrong. |
| `content_type` may exceed the spec enum | **Not observed.** Only `article` (99.7%), `podcast` and `pdf` appear; no `video` or `newsletter`. Still passed through unvalidated. |
| `markdown` reliably present for podcasts/PDFs | **Verified** on a sample of every content type present. Markdown came back for all of them, and is substantial for podcasts and PDFs (tens of thousands of characters), so the `excerpt` fallback is a genuine edge case rather than the norm for non-articles. |
| Real library size | **1,751 items** total: 1,230 `archive` (70.2%) and 521 `queue` (29.8%). Only the archived ones are synced. The inbox is empty. |
| Backfill duration | **~12 minutes**, markdown-bound, for the 232 genuinely new read articles. |
| Pagination at real scale | **Verified.** 18 cursor pages, zero duplicate ids across pages, no cursor loop. |

Two findings worth knowing beyond the assumption list:

**81% of the read articles in Matter are already in the archive.** 998 of the
1,230 archived items match something read in the Instapaper era, so the
cross-era check does the majority of the work on the first run rather than
acting as a safety net. It is not an artifact of aggressive normalization:
across the whole library 976 matches are **exact raw-URL matches** and only 40
more are found by normalizing, and running the same normalization over all 6,508
archive URLs collapses **zero** of them - the strongest available evidence that
it does not over-merge. Without this check the first backfill would have put a
thousand duplicate articles into a 22-year archive; with it, each becomes a
recorded re-read on the article that was already there.

**The library currently contains no highlights.** A 250-item sample spread evenly
across the library found zero annotations (95% confidence puts the library-wide
figure under roughly 1.5%). The highlight code is correct and tested, but there
is nothing for it to sync today; it starts earning its place whenever Adam begins
highlighting in Matter. Three items also carry an empty `url` - they are written
normally and, correctly, never dedupe against each other.

### Backfill fidelity

The backfill's read dates come from `updated_at`, which advances on any later
touch. Two independent checks on how much that distorts them.

**No migration flattening.** If Matter had ever reset `updated_at` server-side,
a large share of items would share one narrow window. They do not: across the
1,230 archived items the busiest single month holds 8.5% and the busiest single
*day* 2.5%, spread over 44 months and 468 distinct days.

**A recency skew, directionally clear and deliberately not over-quantified.**
`/v1/reading_sessions` returns 1,257 dated sessions. It carries no item link, so
it cannot date individual articles, but it is an independent record of *when*
Adam was reading. The two feeds only overlap from 2024 on - the session feed has
no coverage before that, while 29% of archived items carry `updated_at` dates
from 2022-2023 - so they can only be compared inside that shared window:

| Year | share of item `updated_at` | share of reading sessions | difference |
|---|---|---|---|
| 2024 | 22.1% | 30.5% | -8.4 |
| 2025 | 34.7% | 51.9% | -17.2 |
| 2026 | 43.2% | 17.6% | **+25.6** |

Item dates pile into the current year at more than twice the rate the
independent reading record does, and 2025 is correspondingly thin. That is the
signature of later touches dragging `updated_at` forward off older reads, which
is what the fallback source has always claimed about itself.

The magnitude is *not* a clean percentage, and an earlier draft of this document
overstated it as a single figure. A reading session is not an article, the two
series have different denominators, and the comparison is blind to the 29% of
the archive the session feed never covered. Treat the direction as established
and the size as indicative.

Both effects apply only to the backfill. Everything synced from the day the
nightly job starts carries `observed-transition` dates instead.

### Still unverified

- **That `updated_at` advances when an item moves queue -> archive.** The spec
  says it does. The nightly job no longer depends on it (see
  [Nightly](#nightly)), but it is still worth settling - the one-line check is
  in the post-install runbook below.
- **That a vault scan finds the same URLs the Parquet index does.** See
  [Which interpreter to run](#which-interpreter-to-run).
- **A real write.** Everything so far is `--check-auth`, read-only probes, and
  `--dry-run`. No article has been written to the vault yet.

### Post-install runbook

Once the job is installed, two checks settle what is left:

1. **The transition test.** In Matter, archive one article that is currently in
   the queue. Then run the sync by hand and confirm it lands:

   ```bash
   .venv/bin/python scripts/core/export_matter_to_archive.py --full
   ```

   Expect `1 new`, and the new file's `date_saved_source` should read
   `observed-transition`. If instead it reports `0 new`, `updated_at` did not
   move on the status change - the full listing still caught it, which is the
   point of running `--full`, but say so in the ledger above.

2. **Dedupe under the nightly interpreter.** After the first backfill, run
   `/opt/homebrew/bin/python3 scripts/core/export_matter_to_archive.py --full
   --dry-run` and check the reported `dedupe_source` says `vault scan` with a
   URL count in the same range as the Parquet index (~6,500).

---

## Which interpreter to run

The two interpreters on this machine see different things, and it matters
exactly once:

| | `/opt/homebrew/bin/python3` (the nightly job) | `.venv/bin/python` |
|---|---|---|
| Runs the sync | yes | yes |
| Has pyarrow | **no** | yes |
| Cross-era dedupe source | vault scan of `original_url` frontmatter | `data/archive_index.parquet`, exact |

The nightly job must use the homebrew interpreter, because launchd attributes
the TCC grant for `~/Documents` to it. That interpreter cannot read the Parquet
index, so it scans the vault instead. Both read the same `original_url` field
out of the same files, so they should agree - but that was not confirmable while
the archive drive was unmounted.

**Run the first backfill with the venv interpreter**, where dedupe is exact and
measured:

```bash
.venv/bin/python scripts/core/export_matter_to_archive.py --full --max-items 200
```

That is the run where dedupe carries real stakes: it is deciding about 1,016
articles at once. Afterwards every Matter item is in the manifest, so the nightly
delta only consults cross-era dedupe for genuinely new saves - a handful a night,
where a mistake is visible and cheap. The `dedupe_source` field in the run
summary and the heartbeat always records which path was taken, and the sync warns
loudly if a scan of a non-empty vault turns up no URLs at all.

---

## Troubleshooting

**`Matter token file not found`** - the path in the message is where it looks.
See [Setup](#setup).

**`HTTP 401 ... invalid_token`** - the token was revoked, almost always because
a new one was generated (web settings, or `matter login`). Generate a fresh one
and rewrite the file.

**`HTTP 403 ... Matter Pro`** - the API requires an active Pro subscription.

**`Vault directory not found`** - the external drive is not mounted. The sync
deliberately refuses to create the vault: doing so would leave an empty archive
on the mount point and make it look like 17,637 articles had vanished.

**The dashboard does not show new articles** - the Markdown files are written,
but the Parquet index has not been rebuilt. Run `build_index.py`, or use
`--rebuild-index`.

**Cross-era duplicate detection is DEGRADED** - neither the Parquet index nor a
vault scan was available. Matter-vs-Matter duplicates are still prevented, but
an article already saved in the Instapaper era may be written a second time.

---

## Tests

```bash
.venv/bin/python -m pytest tests/ -q
```

No network and no real credential: the API client is driven by a fake session
serving payloads shaped like the documented schemas, and the credential loader
is pointed at a temp file for every test. The dashboard smoke tests run the real
Streamlit app headlessly against a synthetic corpus holding all three eras.
