# Unread corpus: measured inventory and resolve rates

Measured 15 September 2026 for `docs/plans-to-do/2026-09-15-unread-corpus-what-i-meant-to-read.md`.
Every figure here was measured, none estimated. No language model was used at
any point in the resolve-rate test; success is an HTTP outcome plus a word floor.

This file carries aggregates only. The per-URL sample and its raw results are at
`data/2026-09-15-unread-resolve-sample.json`, which is gitignored on purpose:
this repository is public and an unread queue is a list of intentions, not of
finished acts.

## The pool

| Measure | Count |
|---|---|
| Live unread folder (Instapaper API) | 395 |
| Live user folders, all items | 105 |
| Live user folders, never opened (progress 0.0) | 97 |
| Meant-to-read pool (unread + never-opened folder items) | 492 |
| May 2025 CSV export, unread rows | 462 |
| May 2025 CSV export, all rows | 8,778 |
| Read corpus, archive index rows | 17,340 |
| Read corpus, rows carrying enrichment | 17,020 |

The live unread listing returned 395 against a request limit of 500, so it is a
true count. Instapaper caps every folder listing at 500 regardless of
pagination; the starred folder does hit that cap, unread does not.

### Queue turnover, May 2025 to September 2026

| Measure | Count |
|---|---|
| Export-unread items still unread today | 332 |
| Export-unread items resolved since | 130 |
| Today's unread saved after the export | 62 |

### User folders

| Folder | Items |
|---|---|
| Software Development | 36 |
| CS183 - Startup | 16 |
| PaperTrail | 13 |
| Photographic Preservation | 8 |
| Steve Jobs | 32 |

### Are the folder items read or unread?

The API carries **no archived flag**. A bookmark's state is which listing
returns it, and all 105 folder items are absent from the unread listing and from
the first 500 of the archive listing.

| Measure | Count |
|---|---|
| Folder items with `progress` exactly 0.0 | 97 of 105 |
| Folder items with partial progress | 8 |
| Folder items ever read to the end (`progress` 1.0) | 1 |
| Folder items with `starred` = 1 | 20 |
| Starred among the 8 partially-read | 1 |

`starred` is independent of read progress. The eight partial readings are
0.02, 0.04, 0.07, 0.10, 0.18, 0.18, 0.66 and 1.0.

**The trap:** `progress_timestamp` is populated even where progress is 0. 73 of
the 105 share the single value 1375386433, which is 1 August 2013, and that same
timestamp appears 283 times in the CSV export's read-progress column. It is a
platform-side backfill, not a reading act. Never infer "opened" from the
presence of a progress timestamp; read progress is the only signal.

The evidence supports treating the folder items as unread articles that were
organized rather than read. The CSV export cannot see them at all (see the
column note below).

### By year saved

| Year | Live unread |
|---|---|
| 2014 | 31 |
| 2015 | 12 |
| 2016 | 36 |
| 2017 | 57 |
| 2018 | 101 |
| 2019 | 51 |
| 2020 | 8 |
| 2022 | 1 |
| 2023 | 19 |
| 2024 | 7 |
| 2025 | 32 |
| 2026 | 40 |

### Top 20 domains, live unread

167 distinct domains across 395 items.

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

## The CSV export column shift

The May 2025 export declares 18 columns in its header and every one of its
8,778 data rows carries 17. The missing column is `Folder`, so anything filed
into a folder is unattributable from the export alone.

Position 13 is `Archived` and position 15 is `Read Progress`. Confirmed against
the API rather than assumed: reading position 13 as `Archived` gives 462 unread
rows on 12 May 2025 against the API's own 435 nine days earlier, while the
competing reading gives 1,323. Any script using `csv.DictReader` on this file
inherits the shift silently.

## Resolve rates

Sample of 100 drawn from the live unread pool, `random.seed(20260915)`.

| Path | Resolved | Rate |
|---|---|---|
| (a) Instapaper /bookmarks/get_text | 79 | 79% |
| (b) Plain GET, desktop Chrome user agent | 51 | 51% |
| (c) Wayback Machine | 90 | 90% |
| (a) or (b) | 83 | 83% |
| Any of the three | 98 | 98% |
| Dead on all three | 2 | 2% |

Wayback rescued 15 items that neither (a) nor (b) could reach.

The Wayback probe is a HEAD against `web.archive.org/web/<saved-year>/<url>`,
following redirects, counting a 200 that lands on a snapshot URL. It targets the
snapshot nearest the year the item was saved rather than the newest one, because
the article as it was when the intention was formed is the document this project
wants. 5 probes failed for a reason other than a missing snapshot and are
counted as failures above.

That probe answers "does a snapshot exist", not "does the snapshot contain the
article". Wayback will happily have archived a soft 404 or a redirect page. A
first pass over the same sample using the CDX endpoint with a
`filter=statuscode:200` completed on only 37 of the 100 before timing out on the
rest, and of those 37 it found a 200-status snapshot for 32. So roughly one in
seven Wayback hits is a snapshot of something other than the article, and the
90% above should be read as an upper bound. The pipeline must run the fetched
snapshot through the same word floor and `CONTENT_VALID` check as any other
path.

### Overlap

| Combination | Items |
|---|---|
| Instapaper only | 32 |
| Direct GET only | 4 |
| Both | 47 |
| Neither | 17 |

### How each path fails

Instapaper HTTP status: 200 x80, 400 x20.
A 400 means Instapaper holds no parsed text for that bookmark.

Top domains returning Instapaper 400: x.com (4), social.techcrunch.com (2), aprilzero.com (1), technologyreview.com (1), themomcreative.com (1), wsj.com (1).

Direct GET HTTP status: 200 x55, 401 x1, 403 x34, 404 x7, ERR x3.

Top domains returning 403 to a direct fetch: nytimes.com (18), medium.com (9), fastcodesign.com (2), ft.com (2), politi.co (1), amazon.com (1).

Domains dead on all three paths: tennessean.com (1), google.com (1).

### Caveat on the direct-GET rate

51% is an upper bound. The 150-word floor cannot tell an article from a
JavaScript shell or a consent page, and at least the 4 x.com items that pass it
almost certainly carry no article. Treat (b) as a supplement to (a) and gate
what it produces behind the enrichment prompt's existing `CONTENT_VALID` check.

### Caveat on Wayback

Wayback rate limits hard, and the endpoint chosen decides whether the leg is
feasible at all. The availability API returned HTTP 429 to all 100 requests at 5
concurrent. The CDX search endpoint timed out on 46 of the first 71 probes at 4
concurrent, and even serially took 60 to 90 seconds per URL. A HEAD against the
dated snapshot path answers the same question in 0.6 to 2.2 seconds. Use that
one, keep the leg serial, and make it resumable: a Wayback probe that times out
produces a false negative indistinguishable from a dead link.

## Body sizes, for the cost estimate

Measured on the 79 bodies Instapaper actually returned.

| Measure | Characters |
|---|---|
| Minimum | 1,073 |
| 25th percentile | 2,226 |
| Median | 5,997 |
| 75th percentile | 15,244 |
| 90th percentile | 24,821 |
| 95th percentile | 30,242 |
| Maximum | 66,476 |
| Mean | 10,279 |

At roughly 4 characters per token plus 420 tokens of prompt boilerplate, that is
about 2,990 input tokens at the mean, 7,980 at the 95th percentile, and 180
output tokens per article.

Adam removed the prompt's 10,000-character body cap on 2026-09-15, so these are
full-length figures. For reference, 28 of the 79 bodies exceed 10,000 characters
and the capped mean would have been 6,112. Across 492 articles the cap was worth
five cents: $0.13 capped against $0.18 uncapped.
