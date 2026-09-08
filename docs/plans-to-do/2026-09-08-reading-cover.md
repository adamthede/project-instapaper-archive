---
title: "The reading cover — a Feltron cover page at the root, the weeks index moved to /weeks/"
status: "QA Needed"
priority: "P2"
project: "articles"
created: 2026-09-08
linked_pr: "https://github.com/adamthede/project-instapaper-archive/pull/24"
mockup: "/Users/adam_thede/Documents/Code/command-center/docs/mockups/2026-09-08-reading-cover.html"
depends_on:
  - "PR #22 — the sticky sibling-site bar (merged 2026-09-08)"
  - "The mockup generator on branch mockup/reading-cover (site/mockup_reading_cover.py)"
---

# The reading cover

## The approved design

`docs/mockups/2026-09-08-reading-cover.html` in command-center, rendered from
`site/mockup_reading_cover.py` on branch `mockup/reading-cover`. Adam approved
it on 2026-09-08:

- The heroes per column stand as mocked. Articles is the only amber hero.
- The AI share stays as the Subjects hero.
- Books and viewing do not change.

## Adam's structural decision (overrides the mockup's Decisions strip)

The mockup's Decisions strip asked whether the cover should be the home page
with the index below it, or a separate `/cover/`. Adam settled it differently
from both options:

1. **The cover becomes the root page at `/`.**
2. **The current "The Week in Reading" index moves to `/weeks/`, unchanged.**
   Its week pages already live at `/weeks/<id>/`, so the index now sits at the
   head of its own directory.
3. **The sticky sibling bar gains a page-links row**, the same row the books
   and viewing sites carry (`.pagelinks` in the books site's `htmlkit.py` and
   `styles.py`): COVER · WEEKS · YEARS · SOURCES · SUBJECTS · ARTICLES, with
   the current page marked, on every page kind. The sibling-site row itself is
   unchanged. Below 880px the bar behaves as the books bar does now that a
   page row exists.

## What gets built

- **`site/cover.py`** — the four Feltron columns on the shared 87-quarter axis
  (2005 Q1 to 2026 Q3), the era rule beneath them, and the cover's own rules
  appended to the stylesheet the way `deepdives.EXTRA_STYLE` already is. The
  computations move out of the mockup generator rather than being duplicated;
  `site/mockup_reading_cover.py` is retired by this change.
- **`site/generate.py`** — `/` renders the cover, `/weeks/` renders what `/`
  renders today, both carry a `<link rel="canonical">`, and every page kind
  carries the page row.
- **`/years/index.html`** — a small directory of the year rollups, because the
  YEARS item in the requested page row has no page to point at otherwise.

Every number on the cover is computed at build time from
`data/archive_index.parquet` and the vault's `synthesis/` files. Nothing is
typed. The tests assert the printed figures against an independent recompute:
16,382 articles, 17.4M words, 1,216 hours, 827 weeks, 1,164 sources, a streak
of 358 weeks, the 2012 seam, and the AI share.

## Constraints

- This site deploys via a nightly launchd leg with a deploy opt-in
  (`READING_DEPLOY=1`). **Never deploy.** The nightly rebuild path
  (`export_matter_to_archive.py --rebuild-site`) needs no change beyond the new
  page.
- The repo has no CI and no review bots, so the Readiness Rule's second leg is
  an adversarial review by an agent that did not write this.

## Status 2026-09-08

PR #24 is open: https://github.com/adamthede/project-instapaper-archive/pull/24

Built and verified. `/verify` READY (819 tests pass, ruff and mypy are not
installed in this repo and were skipped). The cover mutation audit is 41 caught
and 0 escaped; the sibling-nav audit's anchors were refreshed against the moved
code and it is back to 26 caught and 0 escaped. Fidelity screenshots against the
approved mockup are in `docs/qa/2026-09-08-reading-cover/`, with the 1400 and
390 viewport measurements on the PR.

Two additions the spec implied rather than named: `/years/` gained a directory
page, because the requested row names YEARS and the rollups had no door of their
own; and `concepts_gate()` was split out of `render_deep_dives()` so the row can
be settled before the first page is written.

One mockup value the data could not reproduce: the "this week" note's ", on
energy and on AI" was typed from reading the digest. The clause is derived from
`top_topics` and omitted when that field is empty, which it is in 2026-W35.

CORRECTION 2026-09-08, from the review: an earlier version of this note said
the field is empty "in every recent week". That is wrong. It is carried by 511
of the 827 week records and by 4 of the last 12, so the clause is
**intermittent** - it will appear and disappear from the cover between nightly
rebuilds depending on what the newest week's synthesis extracted. Keeping it
derived is still right; the alternative, ranking the week's canonical entries,
returns six subjects tied at one article each on 2026-W35.

## Review round 1 — 2026-09-08

Adversarial review returned REQUEST CHANGES with two blockers. Both fixed in
commit 5a95049.

**B1. The move orphaned the index it moved.** Every week page kept a kicker
reading "The Week in Reading" and a nav link reading "All weeks", both aimed at
`../../` - the weeks index until the day the cover took the root. 1,712 links
across 856 pages whose text named one page and whose target was another, with
no 404 anywhere because the page they reached exists. And the row marked WEEKS
with a span on those pages, so there was no way back from there either: 827
week pages and 22 year pages had no route to their own index.

Fixed both ways. `htmlkit.weeks_index_href(depth)` reads the index's address
off the installed page row, so the chrome links resolve to it from any depth
and in both site shapes. And `nav()` separates `here` (this IS the page, a
span) from `under` (this page sits beneath it, a marked anchor), so a child
page marks its section and can still reach it. Measured on a build from a fresh
clone: 0 of 827 week pages and 0 of 22 year pages stranded, 1,712 of 1,712
chrome links landing on the index, 12,836 internal links crawled, 0 broken.

**B2. The fidelity test read a file the PR did not add.** The approved mockup
sat in `docs/mockups/`, which `.gitignore` covers, so the suite was green in
the authoring worktree and red on a clean checkout. It is now
`tests/fixtures/2026-09-08-reading-cover.html`, tracked, and the test does not
skip when it is missing.

Non-blocking items N1 through N4 were all folded into the same commit.

**Not merged and not deployed.** Round 2 of review is the open item.
