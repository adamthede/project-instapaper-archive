---
title: "The unread corpus: what I meant to read"
status: "Queued"
priority: "P2"
project: "articles"
created: 2026-09-15
completed:
effort: "L - a fetch pipeline, an enrichment template, an analysis, and a new public record"
linked_pr: ""
depends_on:
  - "the reading cover (PR #24, merged 2026-09-08)"
  - "the public shape (PR #25, merged 2026-09-10) - site/public_shape.py is the pattern"
  - "measured inventory: docs/2026-09-15-unread-corpus-resolve-rate.md"
  - "index repo: /Code/data-adamthede, records/reading/"
---

# The unread corpus: what I meant to read

## The question

The read corpus answers what I actually read. It does not answer what I meant
to read, and those are different archives of the same person. Every article in
the unread queue is a small recorded intention: on a particular day I decided
this was worth my time, and then I never spent the time. Some of those
intentions were abandoned within a week, some have been sitting for twelve
years, and a few have outlived the page they point at. The question this
project answers is what that second archive says: what I saved, when I saved
it, what the saving says about what mattered to me at the time, and which of
those intentions I never got to. The interesting finding is not the backlog. It
is the gap between the two corpora, and the shape of the aging.

## The inventory

All figures below are measured, not estimated, on 15 September 2026. Three
sources: the May 2025 CSV export, live authenticated calls to the Instapaper API
made for this plan, and `data/archive_index.parquet` as the nightly leg last
built it.

### The pool

| Measure | Count |
|---|---|
| Live unread folder (API, 2026-09-15) | 395 |
| Live user folders, all items | 105 |
| Live user folders, never opened (progress 0.0) | 97 |
| **The meant-to-read pool (unread + never-opened folder items)** | **492** |
| May 2025 export, unread rows | 462 |
| May 2025 export, all rows | 8,778 |
| Read corpus, archive index rows | 17,340 |
| of which, read-it-later articles (Instapaper 6,491 + Matter 289) | 6,780 |
| of which, legacy documents (PDF, Word, TXT, HTML, RTF) | 10,560 |
| Read corpus, rows carrying enrichment | 17,020 |

The 17,637 figure in circulation is not what the index holds. `data/archive_index.parquet`
carries 17,340 rows as of this morning's build, and the record's own cover counts
16,376 articles. Only 6,780 of the 17,340 are read-it-later articles; the rest is
the legacy document archive. The corpus this project compares against is the
6,780, because those are the ones that went through the same save-then-read loop
the unread queue is the other half of.

The live unread listing returned 395 items against a request limit of 500, so
it is a true count and not a truncated one. This matters: Instapaper's
`/bookmarks/list` caps every folder at 500 regardless of pagination, which is
the documented reason the archive was built from the CSV in the first place
(`docs/INSTAPAPER_API_LIMITATIONS.md`). The starred folder does cap, at exactly
500. Unread does not.

The May 2025 export is therefore not the ceiling, and it is also not the floor.
It is a different snapshot of a moving queue.

### The five user folders

| Folder | Items | Never opened |
|---|---|---|
| Software Development | 36 | |
| Steve Jobs | 32 | |
| CS183 - Startup | 16 | |
| PaperTrail | 13 | |
| Photographic Preservation | 8 | |
| Total | 105 | 97 |

A folder item is neither unread nor archived in Instapaper's model, so these
never appear in the unread listing and they were invisible to the CSV export as
well. The export's header declares a `Folder` column that the data rows do not
carry: the header is 18 columns wide and every one of the 8,778 rows is 17. The
column that is missing is `Folder`. Anything filed into a folder is
unattributable from the export alone, and only the API can see it.

That off-by-one also shifts the tail of the export. Position 13 is `Archived`,
not position 15, and position 15 is `Read Progress`. This was confirmed against
the API rather than assumed: reading position 13 as `Archived` yields 462 unread
rows in May 2025 against 435 in the API's own unread listing nine days earlier,
while the competing reading yields 1,323. Any script that reads this CSV with
`csv.DictReader` inherits the shift silently.

### The queue is not a backlog, it is a turnover

| Measure | Count |
|---|---|
| Export-unread items still unread today | 332 |
| Export-unread items resolved since (read, archived, or deleted) | 130 |
| Today's unread saved after the May 2025 export | 62 |

Over sixteen months 130 intentions were settled and 62 new ones were made. The
pool shrank from 462 to 395. This is a queue that moves, slowly, and the oldest
strata move first: the export carried 83 unread items saved in 2011, 2012 and
2013, and today not one item from those three years remains in the queue.

### By year saved

| Year | Live unread | Export unread | Read corpus, Instapaper era |
|---|---|---|---|
| 2010 | 0 | 0 | 269 |
| 2011 | 0 | 53 | 360 |
| 2012 | 0 | 8 | 1,502 |
| 2013 | 0 | 22 | 854 |
| 2014 | 31 | 45 | 1,096 |
| 2015 | 12 | 12 | 372 |
| 2016 | 36 | 58 | 237 |
| 2017 | 57 | 58 | 133 |
| 2018 | 101 | 101 | 237 |
| 2019 | 51 | 52 | 205 |
| 2020 | 8 | 17 | 194 |
| 2021 | 0 | 0 | 13 |
| 2022 | 1 | 1 | 122 |
| 2023 | 19 | 24 | 344 |
| 2024 | 7 | 7 | 187 |
| 2025 | 32 | 4 | 366 |
| 2026 | 40 | n/a | n/a |
| Total | 395 | 462 | 6,491 |

2018 is the peak and it has not moved at all: 101 items then, 101 items now. The
2017 to 2019 band holds 209 of the 395, more than half the live queue, and it is
the band where reading volume was at its lowest. That is the finding the
analysis has to explain, not the raw backlog number.

### By source

Top twenty domains in the live unread queue. 167 distinct domains across 395
items.

| Domain | Count |
|---|---|
| nytimes.com | 78 |
| medium.com | 50 |
| x.com | 16 |
| brainpickings.org | 12 |
| theguardian.com | 8 |
| theatlantic.com | 8 |
| newyorker.com | 7 |
| medium.freecodecamp.org | 6 |
| tennessean.com | 5 |
| en.wikipedia.org | 5 |
| businessinsider.com | 5 |
| fastcodesign.com | 5 |
| getpocket.com | 4 |
| washingtonpost.com | 4 |
| techcrunch.com | 3 |
| anthropic.com | 3 |
| wsj.com | 3 |
| nymag.com | 3 |
| nautil.us | 3 |
| nationalgeographic.com | 3 |

Two domains are a third of the queue. Both are the hardest to fetch, which is
the subject of the next section.

### By length

Word counts come from the export's `Words` column, which the API does not
return, so this is measured on the 462 export-unread rows.

| Measure | Words |
|---|---|
| Minimum | 0 |
| 25th percentile | 560 |
| Median | 1,239 |
| 75th percentile | 2,165 |
| 90th percentile | 4,223 |
| Maximum | 30,205 |
| Mean | 1,993 |
| Total | 920,807 |

| Band | Items |
|---|---|
| 0 words recorded | 34 |
| 1 to 500 | 78 |
| 501 to 1,000 | 72 |
| 1,001 to 2,000 | 143 |
| 2,001 to 5,000 | 97 |
| 5,001 to 10,000 | 27 |
| Over 10,000 | 11 |

### Metadata completeness

| Field | Export unread (of 462) | Live unread (of 395) |
|---|---|---|
| Title present | 455 | 366 |
| Title absent | 7 | 29 |
| Description present | 428 | n/a |
| Author present | 326 | n/a |
| Selection (highlighted text) present | 2 | n/a |
| Read progress above zero | 142 | 119 |

Read progress above zero is the most interesting column in the inventory. 119
of the 395 live unread items were opened and abandoned part way through. That
is a different category of intention from the 276 never opened at all, and the
enrichment schema below carries it.

## The fetch strategy

### Measured resolve rates

A random sample of 100 items was drawn from the live unread pool with
`random.seed(20260915)` and each URL was put through three paths. No language
model was involved at any point; success is an HTTP outcome plus a 150-word
floor on extracted text. The full per-URL results are at
`data/2026-09-15-unread-resolve-sample.json`, which sits under a gitignored path
on purpose, and the aggregates are at
`docs/2026-09-15-unread-corpus-resolve-rate.md`. This repository is public and
an unread queue is a list of intentions rather than of finished acts, so the
URLs and titles do not get committed.

| Path | Resolved | Rate |
|---|---|---|
| (a) Instapaper `/bookmarks/get_text` | 79 | 79% |
| (b) Plain GET with a browser user agent | 51 | 51% |
| (c) Wayback Machine | 90 | 90% |
| Any of the three | 98 | 98% |
| Dead on all three | 2 | 2% |

Wayback is measured as a HEAD against `web.archive.org/web/<saved-year>/<url>`,
following redirects and counting a 200 that lands on a snapshot address. It
rescued 15 of the 17 items that neither (a) nor (b) could reach,
which is what takes the dead fraction down to 2%.

The paths overlap unevenly, which is why the order matters:

| Combination | Items |
|---|---|
| Instapaper only | 32 |
| Direct GET only | 4 |
| Both | 47 |
| Neither | 17 |

Instapaper's stored text is worth four times what a direct fetch adds. It has
the article as it was when it was saved, which for a 2016 page is a materially
different document from whatever the URL serves today.

### How each path fails

Instapaper returned HTTP 400 for 20 of the 100, meaning it holds no parsed text
for that bookmark. Those failures cluster: 4 on x.com, 2 on
social.techcrunch.com, the rest one-offs. Social posts and link shorteners are
the shape.

Direct GET returned 200 for 55, 403 for 34, 404 for 7, 401 for 1, and failed to
connect 3 times. The 403s are one story: 18 nytimes.com, 9 medium.com, 2
fastcodesign.com, 2 ft.com. Bot blocking on exactly the two domains that make
up a third of the queue.

One caveat carries weight. The 51% direct-GET rate is an upper bound. The
150-word floor cannot distinguish an article from a JavaScript shell or a
consent page. The four x.com items Instapaper refused all pass it, with 1,481 to
7,420 extracted words apiece, which on a tweet page is the JavaScript payload
and not an article. Treat (b) as a supplement to (a), never as a substitute, and
gate anything it produces behind the enrichment prompt's own `CONTENT_VALID`
check, which already exists and already catches junk scrapes.

Wayback rate limits hard, and which endpoint you pick decides whether the leg is
feasible at all. This cost an hour to learn and is the most reusable thing in
the measurement:

| Endpoint | Behaviour |
|---|---|
| `archive.org/wayback/available` | HTTP 429 on all 100 requests at 5 concurrent |
| `web.archive.org/cdx/search/cdx` | timed out on 46 of the first 71 at 4 concurrent; 60 to 90 seconds each even serially |
| `HEAD web.archive.org/web/<year>/<url>` | 0.6 to 2.2 seconds, no throttling observed |

Use the HEAD probe against the dated snapshot path, keep the leg serial, and
make it resumable. A Wayback probe that times out produces a false negative that
is indistinguishable from a dead link, which would silently inflate the most
interesting number in the analysis.

The HEAD probe has its own limit and it cuts the other way: it answers whether a
snapshot exists, not whether the snapshot holds the article. Wayback archives
soft 404s and redirect pages too. The CDX pass, before it was abandoned for
being too slow, completed on 37 of the 100 with a `statuscode:200` filter and
found a genuine 200-status snapshot for 32 of them. Roughly one Wayback hit in
seven is a snapshot of something other than the article, so the rate above is an
upper bound and every Wayback body has to clear the same word floor and
`CONTENT_VALID` check as any other path.

### The chain, in order

1. **Instapaper `get_text`**, one call per bookmark id, 0.8s apart. This is the
   path that built the read corpus and it is still the best one. Store the
   returned HTML and the extracted text.
2. **Plain GET with a browser user agent** for anything Instapaper returns 400
   on. Accept the result only if it passes the word floor and the enrichment
   prompt's `CONTENT_VALID` check.
3. **Wayback Machine**, serial, for anything still unresolved. HEAD
   `web.archive.org/web/<saved-year>/<url>` to test, then GET the same address
   for the body. Target the snapshot nearest the saved date, not the newest one,
   because the point is the article as it was when the intention was formed. Do
   not use the CDX or availability endpoints; see the table above.
4. **Metadata only** for the remainder. An item that resolves nowhere still has
   a title, a domain, a saved date and a word count, and it still counts as an
   intention. It is enriched from its title and description alone and flagged.

Expected yield on 492 items, applying the measured rates: roughly 389 from
Instapaper, 20 more from direct fetch, 74 more from Wayback, and about
9 carried on metadata alone.

## The enrichment

### Schema

The base schema is unchanged and is the portfolio standard: `topics`, `people`,
`orgs`, `locations`, `concepts`, `summary`, plus `sentiment` and `emotion` from
the articles template. The existing prompt in
`scripts/core/enrich_archive_gemini.py` already emits all of it, including the
`CONTENT_VALID` guard, and the local variant shares that prompt by import so the
two backends cannot drift. Do not fork the prompt.

An `unread` template adds, on top of the base:

| Field | Source | Notes |
|---|---|---|
| `saved_date` | API or export | Already carried as `date_saved` |
| `saved_context` | API and export | Folder name if filed, starred flag, the `Selection` field where present. Only 2 of 462 carry a selection, so this is usually empty |
| `read_progress` | API | 0.0 to 1.0. Above zero means opened and abandoned |
| `abandonment` | derived, not inferred | One of `never_opened`, `started`, `nearly_finished`. Thresholds are arithmetic on `read_progress`, not a model judgement |
| `why_saved` | model | One sentence, from the article plus the saved date. The prompt must say that the answer may be "cannot tell", and the field must be allowed to be empty |
| `aged_out` | model, constrained | Whether the piece was time bound and its moment has passed. A 2016 election preview is aged out; an essay on attention is not |
| `topic_drift` | computed, not model | Per-item distance between its topics and the read corpus's topic distribution for the same saved year. Computed after enrichment, in the analysis step, not asked of the model |
| `resolve_path` | pipeline | Which of the four paths produced the text. Needed to read every other field honestly |
| `content_corrupted` | existing prompt | Already in the base output as `CONTENT_VALID` |

Two of those fields are deliberately not asked of the model. `abandonment` is
arithmetic and `topic_drift` is a corpus computation; asking a model to do
either would produce a plausible number that nothing can check. `why_saved` and
`aged_out` are genuinely inferential and are the two the model is for.

`why_saved` is the field most likely to produce confident fiction. It must be
permitted to return nothing, and the analysis must treat an empty `why_saved`
as a result rather than a failure.

### Model and cost

Gemini 2.5 Flash-Lite, the same model the read corpus was enriched on, through
`scripts/core/enrich_archive_gemini.py`. Adam settled this on 2026-09-15: a paid
Gemini Flash-class model through the plumbing that already exists, not a new
provider.

The cost estimate is computed from the 79 bodies actually fetched in the sample,
not from an assumption:

| Measure | Value |
|---|---|
| Median body, characters | 5,997 |
| Mean body, characters | 10,279 |
| Bodies exceeding the prompt's 10,000-character cap | 28 of 79 |
| Mean body after the cap, characters | 6,112 |
| Estimated input tokens per article | 1,948 |
| Estimated output tokens per article | 180 |

At 492 articles that is about 0.96M input tokens and 0.09M output tokens. Paid
Flash-Lite is $0.10 per million input and $0.40 per million output.

| Line | Cost |
|---|---|
| Input | $0.10 |
| Output | $0.04 |
| **Total, one pass** | **$0.14** |
| With a re-run and a spare pass for prompt iteration | under $0.50 |

The cost is not a consideration at this size. The reason to care about the token
figures is the 10,000-character cap: 28 of 79 bodies exceed it, so more than a
third of the corpus is being summarised from its first 10,000 characters only.
That is the same behaviour the read corpus got and it is the right default for
comparability, but it should be written down rather than discovered later.

## The analysis

### The questions

1. **What did I save that I never read, and does it differ in kind from what I
   read?** Topic distributions of the two corpora, by year saved, not by year
   read.
2. **Where is the drift largest?** The year with the biggest gap between saved
   topics and read topics is the year the intentions diverged most from the
   behaviour. On the inventory alone, 2017 to 2019 is the candidate.
3. **What aged out?** The share of the queue whose moment has passed, by year.
   This is the number that makes the case that a backlog is not a to-do list.
4. **What did I nearly finish?** 119 items were opened and abandoned. Are they
   longer, harder, or a different subject than the ones never opened at all?
5. **Which sources did I trust enough to save but not enough to read?** Domain
   level, both corpora. A source with a high save rate and a low read rate is a
   specific kind of aspiration.
6. **What survives?** Only 2 of 100 sampled items are gone everywhere, but only
   51 still serve their article from their own URL. That gap is the link rot
   finding: about half of a twelve-year personal reading list no longer exists
   on the live web, and it is the Internet Archive and Instapaper's own stored
   copies rather than the publishers holding it up. Break it down by saved year
   and by domain.

### The first hypothesis, already visible in the read corpus

The read-it-later corpus of 6,780 is dominated by Business Strategy (463),
Innovation (269), Entrepreneurship (248), Social Media (238) and Artificial
Intelligence (218). Restricted to articles saved in 2017, 2018 and 2019, the
575 rows look nothing like that:

| Topic | Read, saved 2017-2019 |
|---|---|
| Personal Development | 37 |
| Personal and Professional Development | 32 |
| Workplace Productivity | 26 |
| Climate Change | 16 |
| Entrepreneurship | 12 |
| Romantic Relationships | 11 |

That is the same three-year band where the unread queue is heaviest, 209 of 395.
So the working hypothesis going in is that 2017 to 2019 was a period of saving
one kind of thing and reading another, and the enrichment either confirms that
or replaces it with something better. Write the hypothesis down now so the
analysis can be wrong about it later.

### The outputs

**A record for data.adamthede.com.** "What I Meant To Read", a new row in
`data/index.yaml`, built at the data layer from this repo's generator the way
the Reading record already is, following `site/public_shape.py`. It keeps its
own design; it is not restyled to match the Reading cover and the Reading cover
is not restyled to match it. Number led on the cover, prose collapsed inside.

The public shape needs more care than Reading's did, and this is the part to
get right before anything is built. Reading publishes what Adam read. This
record publishes what he intended and did not do, which is a more revealing
document. The rules:

- **No titles and no URLs on the public page, at all.** Reading's public cover
  can show sources because a read article is a finished act. An unread title is
  an unexecuted intention and the set of them reads like a diary. Publish
  counts, years, topic distributions, domain counts and the aging curve. Nothing
  item level.
- **Topics and concepts are publishable; people, orgs and locations are not**,
  unless they clear the same allowlist the Highlights pages will use. An
  entity extracted from an article nobody read still says what he was looking
  into.
- **The leak needles must come from this corpus's own titles and paths**, added
  to `data/private_strings.txt` the way `public_shape.private_strings` already
  derives them from the index. The word-list gotcha applies: the file is
  gitignored, so the needles have to be written into the main checkout, not a
  worktree, and Adam refreshes `PRIVATE_STRINGS_B64` by hand.
- Standard public copy rules: "my partner" and never a name, nothing about the
  children, people counted and not named, home venues described and not named.

**A comparison against the read corpus.** Private, on reading.adamthede.com,
where titles are allowed. This is where the item-level answers live, and it is
where the essay gets written from.

**A thedetech essay.** Link rot and aspiration measured on one person's twelve
years of saved intentions. Venue rule: thought leadership goes to thedetech.
600 to 900 words unless there is a reason.

## The pipeline

One script, `scripts/core/fetch_unread_corpus.py`, and one enrichment run
through the existing Gemini script.

- **The queue is a file**, `data/unread_queue.jsonl`, one row per item, written
  once from the API listing and then only ever updated in place. Every row
  carries the bookmark id, the URL, its SHA-256, the saved date, the folder, the
  read progress, the resolve path attempted, the outcome and a timestamp.
- **Idempotent by URL hash.** The hash is the key, not the bookmark id, because
  the same URL can be saved twice and because the Matter era already dedupes on
  URL across sources. A row already marked resolved is skipped on every
  subsequent run.
- **Resumable at any point.** The script writes after every item, not at the
  end. Killing it mid run costs one item.
- **A failure log**, `data/unread_fetch_failures.log`, in the same shape as the
  existing `enrichment_failures.log`, carrying the path attempted and the HTTP
  status. Failures are data here, not noise: the dead fraction is one of the
  findings.
- **Rate limits are the real constraint, not compute.** Instapaper at 0.8s
  between calls, Wayback strictly serial with backoff, direct fetches at no more
  than 4 concurrent. Extrapolating from the sample, a full first pass over 492
  items is about 25 minutes: roughly 19 for the Instapaper leg, 2 for the direct
  fetches, and 4 for the Wayback leg. Almost all of that is waiting, not work,
  which is why it belongs in a nightly slot and not in a foreground session.
- **Nightly slot: 01:00.** The fleet's occupied windows are 00:15 daybook, 02:30
  PKM enrich, 03:30 PKM promote, 04:00 Omi, 05:45 Inbox Observatory, 07:15
  cockpit, 07:30 the Matter article sync which runs about 57 minutes and
  rebuilds this repo's index. 01:00 to 02:15 is clear and it is well before the
  Matter leg touches the same parquet. Enrichment runs on Gemini, so it does not
  need the LM Studio flock; if a local fallback is ever added it does, and the
  fleet contract applies.
- **The nightly job is a refresh, not a build.** After the first full pass it
  fetches only new saves and re-checks nothing. The interesting longitudinal
  measurement is the aging of the queue, so it also appends a dated count row so
  the turnover is recorded going forward rather than reconstructed later.

## Build order

| # | Step | Effort |
|---|---|---|
| 1 | `fetch_unread_corpus.py`: API listing of unread plus all five folders, queue written to JSONL, idempotent, resumable | half a day |
| 2 | The four-path fetch chain with the failure log, run once over all 492 | half a day |
| 3 | The `unread` enrichment template on top of the existing Gemini prompt, plus the derived fields, run once | half a day |
| 4 | Index integration: unread rows into the parquet behind an explicit flag so nothing in the read corpus's numbers moves | half a day |
| 5 | The analysis: topic drift, aging curve, abandonment bands, dead fraction by year | one day |
| 6 | The private comparison pages on reading.adamthede.com | one day |
| 7 | The public record, mockup first and approved before any build, then the public shape, the leak needles and the manifest row | two days |
| 8 | The thedetech essay | half a day |

Steps 1 through 5 are the project. Steps 6 through 8 are publication and each
has its own gate. Step 7 does not start before Adam has approved a mockup.

## Open questions

1. **Are the 97 never-opened folder items in scope?** They are intentions filed
   rather than queued, which is arguably a stronger signal than leaving
   something in the inbox. Including them takes the pool from 395 to 492. The
   plan assumes yes.
2. **Does the public record show domains?** The domain distribution is the most
   interesting publishable cut and it is also the most identifying thing left
   once titles are gone. 78 nytimes.com items say something specific about a
   person.
3. **Is `why_saved` worth the fiction risk?** It is the field that makes the
   record a story rather than a table, and it is the one a model will confabulate
   most confidently. The alternative is to drop it and let the topic drift carry
   the argument.
4. **Should the queue be acted on, or only measured?** A ranked "still worth
   your time" list falls straight out of this and it changes the project from a
   record into a to-do list. Almanac was retired in June because friction killed
   it, and a 492-item queue rendered as homework is exactly that shape.
5. **Does the 10,000-character prompt cap stay?** It affects 28 of 79 sampled
   articles. Keeping it makes the unread corpus comparable to the read corpus.
   Raising it makes the summaries better and the two corpora no longer strictly
   comparable.
