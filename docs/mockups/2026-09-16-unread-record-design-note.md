# What I meant to read - record design note

**Mockup:** `docs/mockups/2026-09-16-unread-record-mockup.html`
**Record:** "What I meant to read", a public record on data.adamthede.com
**Gate:** plan step 7 does not start until Adam approves this. Nothing is built or published.
**Numbers:** every figure computed 2026-09-16 from branch `feat/2026-09-16-unread-corpus` at `0ae5395`.

## The idiom

Retrospective genre, so full Felton: editorial, typographic, contemplative, and
numbers on the cover with the prose inside. Cover is a hero numeral, four
secondary stats on dotted hairlines, one stacked recovery bar and one signature
chart. No paragraphs above the fold except the dateline.

This record keeps its own design. Where the Reading record is a cover over a
finished act, this one is a **paired record**: almost every plate draws two
series against each other, because the whole subject is the gap between what was
saved and what was read. That pairing is the visual signature. Two other things
are deliberately unlike Reading: a rose-ruled "what this cannot show" block
appears on three plates and carries the same weight as a figure, and the
plate-scale bars use each corpus's own share rather than raw counts so that two
piles of very different size sit on one axis.

Skin is the house one: stone-900 ground, `--bg-raise` #292524, semantic palette
by data type (amber time, emerald recovered, rose lost, cyan sources, indigo the
read corpus, purple inference, gray unknown), brand orange as the single accent,
small-caps mono labels, tabular numerals, serif for titles and numerals, hairlines
not boxes. Self-contained: inline CSS, no scripts, no external requests, no web
fonts.

## The plates, one sentence each

| # | Plate | The finding |
|---|---|---|
| 01 | Backlog by year saved | Half the backlog was saved between 2016 and 2019, a band that produced 12 percent of what he finished. |
| 02 | Topics, unread against read | Ruby on Rails is ten times heavier in what he saved; Technology is the one subject he read more than he saved. |
| 03 | Why I saved it | A model answered for 461 of 492 saves and judged 82 of them past their moment. |
| 04 | Sources | 218 hosts, and one newspaper is 17 percent of the queue. |
| 05 | What survives | Text came back for 466 of 492, three quarters of it from a copy stored at the moment of saving. |
| 06 | Matter backlog velocity | The live queue grew from 431 to 510 across three years while reading ran about three a week. |

Prose is collapsed behind a "read the note" summary on screen and forced open in
print. Each plate carries a footnote naming the file and function every number
came from.

## What is published, and what is not

Published: counts, years, topic names, source hosts, the recovery split, the
aging verdict, the abandonment bands. Not published: titles, addresses, article
text, summaries, the inference sentences, and the people, organisation and
location columns. Redaction happens at the data layer, where `public_shape()`
assembles the payload field by field out of aggregates; the scan over the written
tree is a backstop, not the mechanism.

One topic was withheld from the published list by `drop_title_shaped()` because
the string is also an article title in this corpus. The title remained a leak
needle. The mockup says this in a footnote without naming the string.

## Leak test

Ran the repo's own gate, `scripts/unread/public.py` `private_needles()` plus
`leak_scan()`, over a tree holding the mockup:

```
needles from the corpus: 460 titles, 480 URL paths, floor 12
repo leak_scan: 0 findings
```

Then a stricter sweep the gate does not perform: all 463 titles at a 5-character
floor, all 492 full URLs, all URL paths at a 5-character floor, all 490
`ai_summary` and 482 `why_saved` sentences at a 20-character floor. Zero hits on
every one.

A sweep of the 4,151 extracted people, organisation and location strings returned
18 substring collisions, all inside CSS, a published domain or ordinary prose:
"Apple" inside `-apple-system`, "Georgia" inside the serif stack, "Cursor" inside
`cursor:pointer`, "Intel" inside "Intelligence", "Paris" inside "comparison",
"Guardian" inside `theguardian.com`, and so on. The page publishes no entity
column, so none of these is a disclosure. Verdict: clean.

## Three numbers the payload does not carry yet

The cover and plate 03 show figures computed from `data/unread_enriched.jsonl`
that `public_shape()` does not currently emit. The built page must read
`public_data.json` only, so these have to be added before step 7:

- **`words`** - total recovered words, 1,086,902, and the median, 1,479. From `body_words`.
- **`oldest_save`** / **`newest_save`** - 2011-01-27 and 2026-04-12. From `saved_date`.
- **`why_saved_confidence` split** - high 371, medium 90, low 31.

A fourth, `starred` (20), is on the cover and is also not a payload field. All
four are aggregates and none is item-level, so adding them does not change what
the record discloses.

## Deviations from the brief, stated plainly

**The by-year comparison line is 6,769, not 17,020.** The brief named the read
corpus's 17,020 enriched rows. The index now holds 17,320 rows and all carry
topics, but only 6,769 of them are read-it-later articles; the other 10,551 are
the legacy document archive and were never saved with an intention to read later.
`analysis.READ_IT_LATER_SOURCES` already encodes that choice for the topic
comparison, and using the full archive would have compared a reading queue
against a corpus that is 62 percent scanned documents. The footnote says so.

**"Why I saved it" is not a distribution of reasons.** There is no reason
taxonomy. The model returns one free sentence per article plus a confidence
grade, and a sentence about an unread article is item-level, so it cannot ship.
The plate shows what is publishable - how often it answered, how sure it said it
was, and the aged-out verdict - and says in the body, not a footnote, that the
reasons themselves stay private.

**The Matter velocity plate has two points, not a series.** The ledger
(`data/matter/queue_daily.csv`, `data/matter/queue_snapshots.jsonl`) does not
exist yet: the launchd job shipped on PR 27 and is not installed. The two
observations that do exist are drawn, the reads-per-week series is real and
weekly, and the saves-per-week cell is left blank rather than reconstructed from
two endpoints. The plate says when it becomes readable: first measured
inflow-outflow pair on the second night after install, a usable trend at about
eight weeks.

**The Matter reads series is Matter-only, computed rather than taken from the
bundled backfill.** `queue_snapshot.py --backfill-reads` writes an archive-wide
series. On a Matter-only plate that would have mixed the Instapaper era into the
comparison, so the plate filters `source == "matter"` (292 rows) before calling
`compute_weekly_reads()`. Archive-wide the last 52 weeks are 288 reads; Matter
only, 178.

**The recovery split differs slightly from the PR 28 body.** The body reports
351 / 89 / 27 / 25 and the mockup shows 353 / 90 / 23 / 26, because
`data/unread_enriched.jsonl` was rewritten after the analysis JSON the body
quotes. The mockup is built from the current file. The PR body is one pass behind.

## Responsive and print

One 16px gutter set on a single wrapper, `overflow-x: clip` on both `html` and
`body`, every grid collapsing to one column below 680px, bars sized in
percentages. No page-level horizontal scroll at 390px. In print the mock banner
is hidden and every collapsed note opens.

## Not decided here

The page's route, its manifest row in `data/index.yaml`, its Open Graph image and
its provenance note are step 7's build, not this mockup. The leak needles this
corpus contributes to `data/private_strings.txt` are emitted by
`publish_unread_record.py --needles` and refreshed by hand, as they already are.
