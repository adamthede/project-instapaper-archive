---
title: "The reading cover — a Feltron cover page at the root, the weeks index moved to /weeks/"
status: "In Progress"
priority: "P2"
project: "articles"
created: 2026-09-08
linked_pr: ""
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
