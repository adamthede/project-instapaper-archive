# records/reading

Published at `/reading/`.

## index.html

Built, not vendored. `site/generate.py --public --out <dir>` in
`adamthede/project-instapaper-archive` produces this directory, and re-running
it on the same index and the same day reproduces this file byte for byte.

| | |
|---|---|
| source | the private cover at `reading.adamthede.com/`, same generator |
| generator commit | `10d3b8f4830b8d3657e6b0565408ac04e9cd8626` |
| built | 2026-09-09 |
| index.html sha256 | `85cbb25ea0cc8e72091d97909616a4c55f141c9261e16c82c8830fa268ba8686` |
| articles behind it | 16,382 |
| weeks behind it | 827 |

### Redaction

At the data layer, before any template runs. The cover is handed eight columns
of the index - source, word count, reading time, canonical entries, read date,
year, host and the proxy-dated flag - and four keys per week record - the ISO
week, its article count, its word count and its subjects. Article titles, URLs,
authors, summaries and the whole per-week article roster never reach a
renderer, so no template can leak what it never saw.

Hosts are kept and paths are not. The Sources column ranks publications by
host, which is a fact about the archive; a path identifies one article in it.

The build then walks every emitted file for every article title in the index
and every source URL path, longest first, at twelve characters or more, and
refuses to publish on a single finding. On this build that is 16,919 titles and 6,666 paths.

## The deviations

The public page is the private cover with five differences and no others,
asserted as an equality in
`tests/test_public_shape.py::test_the_public_cover_is_the_private_cover_but_for_the_five_deviations`.

| # | where | what changed | why |
|---|---|---|---|
| 1 | the sticky bar's page row | reduced to its single current entry, COVER, which already renders as a marked span | the other five name pages this build does not write; they would be five dead links in the chrome of the only page there is |
| 2 | any anchor to a private page | becomes a `<span>` with the same classes | nothing on the record may 404. Today the cover's body has exactly one such anchor and deviation 5 replaces that line outright, so this pass changes nothing as the page stands; it runs against a future column growing a link |
| 3 | the sibling bar and the wordmark | BOOKS and VIEWING resolve to `https://data.adamthede.com/books/` and `https://data.adamthede.com/viewing/`, the wordmark to `https://data.adamthede.com/` | the private hosts are behind Cloudflare Access, so from a public page those links are a login wall. READING stays the marked span it is privately: the record is `/reading/` |
| 4 | canonical | `https://data.adamthede.com/reading/` | the private canonical would tell every crawler this page duplicates one it cannot reach |
| 5 | the footer's first span | "The full record is kept for the family." | it offered the weekly syntheses at `/weeks/`, which the public tier does not publish |

Not a deviation, and deliberately: the masthead kicker still reads
`reading.adamthede.com`. It is the record's own byline, and the plan of record
lists one redaction for Reading - drop the weekly article feed - of which the
hostname is not part.

Also not a deviation: the "deep dives" secondary still counts the private
site's year rollups and facet pages. It is a number, not a link, and the footer
directly beneath it says the full record is private.

### Self-contained

One directory, 3 files. The stylesheet is inlined into the document,
so the page makes exactly one network request - the document - and nothing else
is fetched at view time. No `<script>`, no `<link>`, no `<img>`, no `@import`,
no `url()`, no webfont. Proved at runtime in
`docs/qa/2026-09-10-public-shape/`.

## thumb.jpg

A 1200x750 capture of this record's own top of page, taken from the
`index.html` committed beside it in a real Playwright viewport
(`newContext({viewport})`, not `--window-size`, which headless Chrome clamps
on this Mac).

| | |
|---|---|
| captured from | `records/reading/index.html` |
| captured-from sha256 | `85cbb25ea0cc8e72091d97909616a4c55f141c9261e16c82c8830fa268ba8686` |
| thumb.jpg sha256 | `7f06263cb128d9d0782bf3d9a43700fed2af8e95279cbfbc4694f5a7214c0c4e` |

The capture-source hash is what closes the hole the leak scan cannot see: that
scan reads text, and a thumbnail regenerated from the private cover would carry
the private chrome into the index as pixels and build green.
