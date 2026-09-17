---
title: "The unread record's payload: the aggregates its page reads"
status: "In Progress"
priority: "P2"
project: "articles"
created: 2026-09-16
effort: "S - aggregates, a paired series, a per-day rollup and the tests"
linked_pr: ""
depends_on:
  - "the unread corpus (PR #28, merged 2026-09-16)"
  - "the approved mockup (PR #29, merged 2026-09-16)"
  - "data-adamthede CLAUDE.md: records are built import-eligible (2026-09-16)"
---

# The unread record's payload: the aggregates its page reads

The mockup Adam approved on 2026-09-16 prints figures `public_shape()` does not
carry, and the record's own rule is that the built page reads
`public_data.json` and nothing else. A figure the payload cannot supply is a
figure somebody types.

## What shipped

Four aggregates the design note named: `words`, `saved_span`, `starred`, and
`why_saved.by_confidence`.

Three more the built page provably cannot source otherwise: `pool` (the cover's
queue-against-folders split), `read_comparison` (plate 01's read series) and
`topic_comparison` (plate 02's pairing). Plates 01 and 02 are both halves of a
pairing and the other half was in a parquet the index repository does not hold.

The import-eligibility pair data.adamthede.com now requires of every record:
`daily`, a per-day rollup in the shape of Silo's provider daily summary, and
`five_ws`, the declaration of which halves ship.

The publish CLI now refuses to build without the read corpus, because a payload
silently missing that series publishes a record whose first two plates have no
bars in them and prints success.

## Open items

- The record's page is built in `adamthede/data-adamthede`, not here.
- `records/meant-to-read/` is committed here so the payload has a home the index
  repository can vendor from. It is the data layer, not the page.
