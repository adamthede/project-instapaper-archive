---
title: "The public shape of reading.adamthede.com - the cover alone, redacted at the data layer, for data.adamthede.com/reading/"
status: "QA Needed"
priority: "P1"
project: "articles"
created: 2026-09-09
completed:
linked_pr: "PR_URL_PLACEHOLDER"
depends_on:
  - "the reading cover (PR #24, merged 2026-09-08)"
  - "plan of record: command-center docs/planning/2026-09-09-data-adamthede-com.md"
  - "index repo: /Code/data-adamthede, records/reading/"
---

# The public shape

`site/generate.py --public --out <dir>` emits the Reading record for the
public tier at `data.adamthede.com/reading/`. The private nightly build does
not change in any way; this is a second build shape of the same generator,
run on demand.

## What the record is

The cover page and nothing else. Adam settled the scope on 2026-09-09: covers
only in this wave. No Highlights, no weeks feed, no per-article listing, no
people page. The record keeps its own design - the index is the only shared
surface, and a record is never restyled to match another.

## The five deviations from the private cover

The public cover's DOM is the private root's DOM, with five differences and no
others:

1. **The page row is reduced to its single current entry, COVER.** Not removed.
   The sticky bar's three-part shape (wordmark, page row, sibling row) is what
   the books and viewing bars carry, and all three mark the current page; a
   bar with the `<nav>` deleted is a larger change to the DOM than a bar whose
   row holds the one page that exists here. COVER renders as the marked span it
   already renders as on the private cover, so there is no href and nothing
   404s. A row of five links to pages this build does not write would be five
   dead links on the only page there is.
2. **Every anchor that would reach a private page is a span** with the same
   classes and the same text. Today the cover's body carries exactly one such
   anchor, the footer's link to `/weeks/`, and difference 5 replaces that line
   outright - so the rewriting pass changes nothing on the cover as it stands
   today. It is in the build anyway, and asserted, because the failure it
   guards is a future column gaining a link and shipping it live.
3. **The sibling bar points at the public tier.** BOOKS and VIEWING resolve to
   `https://data.adamthede.com/books/` and `/viewing/`; READING stays the
   marked span it is on the private site, because the record is published at
   `/reading/` and a link to the page you are already on is the same category
   of dead as a 404. The wordmark points at `https://data.adamthede.com/`.
4. **Canonical is `https://data.adamthede.com/reading/`.**
5. **The footer says the record is private.** "The full record is kept for the
   family." replaces "The weekly syntheses are at /weeks/", in the same
   `<span class="label">` the footer already used.

The masthead kicker still reads `reading.adamthede.com`. It is the record's own
byline and the plan of record lists exactly one redaction for Reading, "drop
the weekly article feed"; the hostname is not one.

## Redaction at the data layer

The renderer never receives what it must not print. `site/public_shape.py`
builds a reduced view first:

- **The index rows** keep only the eight columns the cover's arithmetic reads:
  `source`, `word_count`, `reading_time_min`, `canonical_entries`, `date_read`,
  `year`, `domain`, `proxy_dated`. `title`, `url`, `author`, `summary`,
  `content_snippet`, `file_path` and every other column are dropped before the
  cover sees the frame. `domain` is a bare host, never a path.
- **The week records** keep only `week`, `article_count`, `total_words` and
  `top_topics`. The `articles` roster - every title and every URL the private
  week pages render - is dropped.

So no template can leak what it never saw, which is the press's own wording.

## The leak test

After the build, walk every emitted file and assert that no article title from
the index and no source URL path appears. Titles and paths are taken from the
live index, all of them, longest first, at a minimum of twelve characters. The
scan runs inside the build and fails it closed, and again in the test suite:
green on a real build, red when a fixture injects one title.

## Self-contained

One directory, three files: `index.html`, `thumb.jpg`, `PROVENANCE.md`. The
stylesheet is inlined into the document, so the page makes exactly one network
request - the document - and nothing else is fetched at view time. Proved in a
real Playwright context with every request after the document aborted.

## The thumbnail and the provenance note

`thumb.jpg` is a 1200x750 capture of the public cover's own top of page, taken
from the committed `index.html` in a real 1200x750 viewport. `PROVENANCE.md`
records the sha256 of `index.html`, the sha256 of the file the capture was
taken from, the generator commit, the date, and the deviations above, in the
shape `records/office/PROVENANCE.md` uses at the index repo.

## Constraints

- **Never deploy.** This repo's nightly leg carries a deploy opt-in
  (`READING_DEPLOY=1`) and it is not touched.
- The private build is unchanged, and a test proves it: the private
  `index.html` is hashed before and after the public build lands.
- No CI and no review bots in this repo, so the Readiness Rule's second leg is
  an adversarial review by an agent that did not write this.

## Status 2026-09-09

Built and verified against the live archive: 16,382 articles, 827 weekly
syntheses, `data/archive_index.parquet` of 2026-09-06.

- **Suite.** 35 tests in `tests/test_public_shape.py`. Whole repo: 864 passed
  and 5 skipped with the vault mounted, 861 passed and 8 skipped on a fresh
  clone with neither the vault nor the gitignored index present. `ruff` and
  `mypy` are not installed in this repo and were skipped, as they were for the
  cover.
- **Mutation audit.** `tests/mutation_audit_public_shape.sh`, 40 caught and 0
  escaped. The first pass was 27 and 10; eight of those escapes were real
  holes, all of them in code a working build never reaches, and two were bad
  mutations. Both are written down in the commit rather than quietly dropped.
- **The leak scan on the real corpus.** Clean over 16,919 titles and 6,666 URL
  paths. No collision between an article title and a controlled-vocabulary
  entry, which was the case worth measuring rather than assuming.
- **The private site does not move.** The nightly path was run on the live
  archive either side of a public build: 859 files, identical tree digest.
- **Browser evidence.** `docs/qa/2026-09-10-public-shape/`. One request and
  zero console errors with the network blocked after the document.
  `body.scrollWidth == window.innerWidth` at 390 and 1400. With the sticky bar
  removed from both pages, the public and private covers differ in exactly one
  band of 557 pixels at both widths - the footer line. The bar is the second
  and last visible difference.

Two things the spec implied rather than named, both stated on the PR:
`generate.main` now takes `argv` so the entry point's dispatch is testable, and
the record's document title is asserted against a literal because the index
repo's manifest row carries it.

**Not merged and not deployed.** `READING_DEPLOY` was never set. Ready for an
adversarial review by an agent that did not write this, then Adam's merge gate.
