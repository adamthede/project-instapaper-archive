# What I Meant To Read - provenance

Source repository: Article Archive (`scripts/unread/`), commit 7e18474.
Built: 2026-09-17
Record: https://data.adamthede.com/meant-to-read/

## What this is

The public shape of a private corpus: 492 items from Adam's Instapaper
unread queue and the folder items he organised but never opened. The private
record lives behind Cloudflare Access; this is the data layer of its public
coverage-map entry.

## What was redacted, and where

Redaction happens at the data layer. `public_shape()` assembles the payload
field by field from aggregates; titles, URLs, article text, summaries, the
`why_saved` inference sentences, and the people, organisation and location
columns are never handed to a writer. An allowlist, not a denylist: a denylist
over a payload like this fails silently the first time a field is added.

Published: counts, years, topic distributions, source hosts, the aging curve,
the abandonment bands, the survival figures, the cover aggregates (recovered
words, the span of save dates, the star count, the queue-against-folders split),
the confidence split on the inference, the paired series against the read
corpus, and a per-day rollup of saves.
Not published: anything item level.

Domains ship deliberately (Adam, 2026-09-15). A host is a fact about the
archive; a path identifies one article in it, and paths do not ship.

## The paired series

Two of the six plates draw the unread queue against what was actually read, so
the payload carries the read side as aggregates: counts by saved year, and a
topic table pairing each subject's share of one corpus with its share of the
other. Read-it-later sources only - the legacy document archive was never saved
with an intention to read later.

The topic redaction runs on BOTH columns against BOTH title sets. The read
corpus's topics are model-generated strings out of 17,320 articles and this
record publishes them; one of them colliding with an unread title would print
that title in a column the leak scan is not looking at, because this record's
needles are its own corpus's titles.

## The per-day rollup and the 5Ws

`daily` is a compact series, one row per day saved, in the shape of Silo's
provider daily summary: keyed on `date_of_summary`, carrying `computed_stats`
and declaring its provider, source, timezone and provenance in the payload
rather than in a column. `reads` is null on every day rather than 0, because
every item in this corpus is unread by definition - there is no observation, not
an observation of nothing.

`five_ws` declares what the record holds per item and which halves ship: who
never ships at all, where does not exist for a saved article, what ships as
category counts, when ships as a date at day precision, and why ships as the
presence rate of a model's inference rather than as the inference.

## The scan

After every file was written and before the directory was published, the whole
tree was walked for 460 article titles and 480 URL paths from this
corpus, case-blind, over both raw and HTML-unescaped text, at 12
characters or more. It found nothing.

The scan fails closed: an absent or empty needle list raises rather than
passing, because with nothing to look for every tree passes.

## Files

public_data.json  sha256 351b465893e3fee0cb299a1dddf188b844ec5cb4f3ae1a2366511406ead63d33
