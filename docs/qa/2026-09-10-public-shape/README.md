# QA: the public shape of the reading cover

10 September 2026. Branch `feat/public-shape`. Built from the live archive:
16,382 articles, 827 weekly syntheses, `data/archive_index.parquet` of
2026-09-06 and the vault's `synthesis/` staged on local disk.

```
site/generate.py --public --synthesis-dir <vault>/synthesis \
  --index data/archive_index.parquet --out <dir>
```

Every capture below is a real Playwright viewport (`newContext({viewport})`),
not `--window-size`. Headless Chrome clamps the window on this Mac, which is
how a record in the index repo first produced a 500px layout cropped to 390 and
called it a mobile render. The script that produced all of it is
`tests/qa_public_shape.js`, committed beside the suite so a reviewer can re-run
it rather than take this file's word.

## What the build wrote

Three files, and no fourth.

```
index.html      56,769 bytes
thumb.jpg       89,574 bytes   1200x750
PROVENANCE.md    4,403 bytes
```

The record's own provenance note as built is copied here as
`2026-09-10-record-PROVENANCE.md`.

## The press, clause one: nothing is fetched at view time

Loaded from `file://` with a route handler that lets the document through and
aborts every subsequent request, whatever origin it names.

```js
let first = true;
await page.route('**/*', route => {
  if (first) { first = false; return route.continue(); }
  return route.abort();
});
```

| | |
|---|---|
| requests | 1 |
| the request | the document |
| console errors | 0 |
| page errors | 0 |
| rendered height at 1400 | 1701 px |
| sticky bar computed position | `sticky` |

A grep over the emitted page agrees, which matters because the route handler
proves only that nothing was fetched, not that nothing was asked for:

| pattern | occurrences |
|---|---|
| `<script` | 0 |
| `<link rel="stylesheet"` | 0 |
| `<img` | 0 |
| `@import` | 0 |
| `url(` | 0 |
| `fetch(` | 0 |
| `XMLHttpRequest` | 0 |
| `fonts.g` | 0 |

The stylesheet is the site's own `generate.STYLE` and `cover.COVER_STYLE`,
inlined into the document, so the record is styled by the rules the private
cover renders under rather than by a fork of them.

## Every URL on the page

Four, all on the public tier, and the fourth is the canonical.

```
https://data.adamthede.com/            the wordmark
https://data.adamthede.com/books/      the sibling bar
https://data.adamthede.com/viewing/    the sibling bar
https://data.adamthede.com/reading/    <link rel="canonical">
```

Nothing relative, nothing on `reading.adamthede.com`, nothing on the two
Access-walled sibling hosts. The chrome as the browser reads it back:

```
page row  : ["SPAN:Cover"]
sibling   : ["SPAN:Reading", "A:Books:.../books/", "A:Viewing:.../viewing/"]
footer    : The full record is kept for the family. Generated 2026-09-10
```

## No feed, no titles

| grep | result |
|---|---|
| article titles from the index (16,919 of them, longest first, >= 12 chars) | 0 hits in any emitted file |
| source URL paths (6,666, >= 12 chars) | 0 hits in any emitted file |
| ISO week identifiers (`20\d\d-W\d\d`) | 1 |
| `class="wrow"` (a weeks-index row) | 0 |
| `class="row"` (a week page's article roster) | 0 |
| `class="atitle"` (a linked article title) | 0 |

The one week identifier is `2026-W35` in the Weeks column's "this week"
secondary, which reads "10 articles in 2026-W35, 21,459 words". Both figures
were read off the emitted `index.html` and then checked against the vault's own
`synthesis/2026-W35.md`, whose frontmatter carries `article_count: 10` and
`total_words: 21459`. (An earlier draft of this file quoted 58 and 84,441,
which is not what the build wrote and not what the vault holds; the numbers
here are the measured ones.) It names the newest week, it is on the private
cover in the same place, and it is a date, not a feed: there is no second week
identifier anywhere in the file.

The leak scan is not only a grep here. It runs inside the build, over every
emitted file, on both the raw text and its HTML-unescaped form, case
insensitively, and it raises rather than warns:

```
Leak scan clean over 16,919 titles and 6,666 URL paths.
```

`tests/test_public_shape.py::test_the_leak_scan_is_red_when_a_title_is_injected`
is the proof it can go red, and
`test_the_build_refuses_to_finish_when_the_scan_finds_something` the proof that
a finding costs the build rather than a line on stderr.

## The viewports

| page | width | `body.scrollWidth` | `window.innerWidth` | full height |
|---|---|---|---|---|
| public cover | 1400 | 1400 | 1400 | 1701 |
| public cover | 390 | 390 | 390 | 4617 |
| private cover | 1400 | 1400 | 1400 | 1701 |
| private cover | 390 | 390 | 390 | 4642 |

`body.scrollWidth == window.innerWidth` at 390. No horizontal scroll, no
clipped column, no overflowing hero number.

Screenshots: `2026-09-10-public-cover-1400.png`, `2026-09-10-public-cover-390.png`,
`2026-09-10-private-cover-1400.png`, `2026-09-10-private-cover-390.png`.

## The fidelity pair

Public against private, same archive, same day, `diffMask` on so a pixel is
listed only where the two actually differ.

| width | comparison | differing pixels | share | bands (y) |
|---|---|---|---|---|
| 1400 | whole page | 1,549 | 0.065% | 14-21, 118-126, 1591-1600 |
| 390 | whole page | 110,885 | 6.158% | most of the page |
| 1400 | body, sticky bar removed from both | 1,095 | 0.047% | 74-82, 1548-1557 |
| 390 | body, sticky bar removed from both | 1,095 | 0.061% | 74-82, 4445-4454 |

Diffs: `2026-09-10-cover-diff-1400.png`, `2026-09-10-cover-diff-390.png`,
`2026-09-10-body-diff-1400.png`, `2026-09-10-body-diff-390.png`.

### Every visible difference

There are three, and the table above is how they were found rather than a
list someone wrote down.

**1. The sticky bar's page row.** Public: `COVER`, marked. Private: `COVER ·
WEEKS · YEARS · SOURCES · SUBJECTS · ARTICLES`, with `COVER` marked. At 1400 the
bar is 44px tall on both pages and only the row's own text differs, which is the
`y 14-21` band. At 390 the private row wraps to a second line and the private
bar is 63px against the public 38px:

| width | page | bar height | masthead top | full height |
|---|---|---|---|---|
| 1400 | public | 44 | 108 | 1701 |
| 1400 | private | 44 | 108 | 1701 |
| 390 | public | 38 | 102 | 4617 |
| 390 | private | 63 | 127 | 4642 |

That 25px is the whole of the 390 whole-page number: everything below the bar
moves up by it, so a pixel comparison at the same y compares a line of text
against the line above it. Removing the bar from both pages puts the bodies
where they sit and the difference collapses to the footer. (Re-comparing at a
25px offset instead leaves a 1.2% residue, which is fractional line
positioning, not content - it is in the script and is the weaker measurement of
the two, so the removal is what is reported.)

**2. The masthead kicker.** Public: `data.adamthede.com/reading`. Private:
`reading.adamthede.com`. One line of small-caps label above the title, `y
118-126` at 1400 and `y 74-82` in both bar-removed bodies. The private byline
names the host the page is served from, and that host is behind Cloudflare
Access, so on a page anyone can open it points at a login wall.

**3. The footer's first line.** Public: "The full record is kept for the
family." Private: "The weekly syntheses are at /weeks/", a link. One line, in
the same span, in the same small-caps label idiom. `y 1548-1557` at 1400 and
`y 4445-4454` at 390. With the kicker, the two lines come to the same 1,095
pixels at both widths, which is what says they are two lines of text and
nothing else.

**Everything else on the page is pixel-identical at both widths.** The four
columns, all 87 quarters of every strip, every era annotation, every hero, every
secondary, the dates note and the "generated" stamp.

The other three deviations - the sibling bar's hrefs, the wordmark's href, and
the canonical - are invisible by construction. They change where a click lands,
not what is drawn, and they are asserted in the suite instead.

## The private build is not touched

The nightly path was run on the live archive before and after a public build of
the same data.

| | sha256 |
|---|---|
| `_site/index.html` before | `bfa35bcb99cf377a5391b7cb8b2c4548b3d35eab5426d4277ccffaebef130a6b` |
| `_site/index.html` after | `bfa35bcb99cf377a5391b7cb8b2c4548b3d35eab5426d4277ccffaebef130a6b` |
| whole tree before (859 files) | `646d7cce0a214000e124383ddf6d95defd20aa753db8c4f568e3310138d0fe67` |
| whole tree after (859 files) | `646d7cce0a214000e124383ddf6d95defd20aa753db8c4f568e3310138d0fe67` |

Not one byte of the private site moves. The same claim is held in the suite by
`test_the_private_build_is_unchanged_by_a_public_build`, which hashes every
file of a fixture build either side of a public one - the failure it guards is
the public build installing its own page row, sibling row and wordmark on
`htmlkit` and not restoring them.

## The thumbnail

`2026-09-10-record-thumb.jpg` is the capture the build produced: 1200x750, taken
from the `index.html` written beside it, in a 1200x750 Playwright viewport with
the network blocked. Its capture-source hash is recorded in the record's
provenance note and checked there, which closes the hole the leak scan cannot
see - that scan reads text, and a thumbnail regenerated from the private cover
would carry the private chrome into the index as pixels with every text test
still green.

## Nothing was deployed

`READING_DEPLOY` was never set and `site/deploy.sh` was never run. The nightly
leg is untouched by this branch.
