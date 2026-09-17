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

## Round 4, and where this stopped

Verification pass, and the lane stops here by decision rather than by verdict
(team lead, 2026-09-17). Evidence:
https://github.com/adamthede/project-instapaper-archive/pull/30#issuecomment-5714813406

**What holds, each attacked rather than assumed.** All four round-3 blockers
replay red on the real 492-record corpus, including the two-line
self-declaration with its promised message. The depth claim is true at 4, 5, 6,
7, 8 and 12 levels. The guard runs after `payload_hook`. No false positives on
the untouched payload. No shadowed definitions across 99 files. 142/142 and
143/143 mutation anchors resolve exactly once.

**The hex floor answered a question neither of us asked.** A 6-hex prefix is
already fully distinct across 492 items, so distinctness was never the binding
constraint: 16 is a threshold, not a floor, and everything below is open.

## Open items from round 4 - Adam's gate, not this lane's

Ordered by how much of the corpus each would publish, not by effort.

1. **The dashed digest. 492 of 492, complete and unmodified.** `_HEXISH`'s own
   word boundaries mean any non-hex character resets the run, so a dash every
   four characters turns a 64-character digest into sixteen 4-character runs.
   Recoverable with one `.replace("-","")`. The reviewer would fix this first
   and so would I.
2. **Sub-threshold prefixes.** 15-hex and 12-hex both pass, both rejoin 492 of
   492 uniquely, and a synthetic non-corpus probe returns False - a working
   confirmable-guess oracle, which is the disclosure `five_ws.source_id` names.
3. **Other encodings of the same hash:** a list of 8-character chunks, a float
   whose mantissa carries 13 hex digits, two integers of 9 hex each, base32 of
   ten digest bytes. None caught.
4. **`float` has no branch in the walk.** `bool` was correctly ordered before
   `int`, so the type lattice was thought about, and the one numeric type JSON
   round-trips losslessly has no arm. An `else` closes it and closes the next
   unknown type with it.
5. **A join key made of honest small integers.** A per-day sorted list of
   `body_words` reads as a legitimate length distribution; 492 of 492 pairs
   rejoin and 487 resolve to exactly one row. `content_findings` cannot see
   this by construction. A row-ordinal list is the same class.
6. **`content_findings` never runs on the written TREE.** Both call sites take
   the payload object. Digests added to the `PROVENANCE.md` template published
   493 full 64-hex runs with `public_data.json` clean and the suite green.
   `RECORD_FILES` already anticipates `index.html` and `thumb.jpg`, so the page
   lane lands in the same blind spot.
7. **Four single-edit neuters of the guard** that the suite does not catch.
8. **Sub-floor titles.** `MIN_NEEDLE = 12` gates both the title set and the
   needles, and 4 real corpus titles are non-empty and shorter than that, plus
   11 URL paths. The record's rule is "no titles and no URLs, at all"; the
   floor makes it "no titles over eleven characters."
9. **No content-shape backstop downstream.** The record repo's only redaction
   assertion on this payload is a key-name denylist over the rendered page,
   which is the shape three rounds have been closing, one repository over.
10. **The mutation audit is not in CI.** Stage 7 covers `content_findings`
    well and nothing invokes it; the workflow runs pytest only, so every escape
    above is invisible to the thing that actually runs.

**The 142-mutation audit is recorded as done-enough rather than skipped.** The
risk a full run addresses is a stale anchor, and that is retired by
measurement: every anchor resolves exactly once at this head, and the
20-mutation leak subset ran end to end, 20 of 20 caught, exit 0.

## Open items

- The record's page is built in `adamthede/data-adamthede`, not here.
- `records/meant-to-read/` is committed here so the payload has a home the index
  repository can vendor from. It is the data layer, not the page.
