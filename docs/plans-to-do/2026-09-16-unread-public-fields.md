---
title: "The unread record's payload: the aggregates its page reads"
status: "QA Needed"
priority: "P2"
project: "articles"
created: 2026-09-16
effort: "S - aggregates, a paired series, a per-day rollup and the tests"
linked_pr: "https://github.com/adamthede/project-instapaper-archive/pull/30"
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

## Adversarial review, and where it stopped

Three full rounds by a non-author reviewer. The through-line named on round 3
is the part worth carrying: rounds 1, 2 and 3 each closed the shape of the last
escape - flat key names, keys one level down, then values, the level above, and
finally the name itself - and a key-name allowlist cannot end that series,
because the payload is published verbatim and the allowlist and the producer
are edited by the same hand in the same commit.

`public.content_findings()` is the answer. It asks what the content LOOKS like
rather than what it is called - hex runs, integers too large to be counts,
corpus titles, URL paths, at any depth, as keys or as values - and it cannot be
satisfied by renaming a field. Verified with both key allowlists updated
exactly as that two-line commit would do it.

**One verification pass after this commit, and then we stop** (team lead,
2026-09-17). Anything round 4 still flags is recorded below with the reviewer's
evidence linked; the PR stays open and Adam decides at his gate.

## Open items

- The record's page is built in `adamthede/data-adamthede`, not here.
- `records/meant-to-read/` is committed here so the payload has a home the index
  repository can vendor from. It is the data layer, not the page.
