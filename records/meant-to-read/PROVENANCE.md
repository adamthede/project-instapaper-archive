# What I Meant To Read - provenance

Source repository: Article Archive (`scripts/unread/`), commit abefb80.
Built: 2026-09-16
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
the abandonment bands, and the survival figures.
Not published: anything item level.

Domains ship deliberately (Adam, 2026-09-15). A host is a fact about the
archive; a path identifies one article in it, and paths do not ship.

## The scan

After every file was written and before the directory was published, the whole
tree was walked for 460 article titles and 480 URL paths from this
corpus, case-blind, over both raw and HTML-unescaped text, at 12
characters or more. It found nothing.

The scan fails closed: an absent or empty needle list raises rather than
passing, because with nothing to look for every tree passes.

## Files

public_data.json  sha256 eed1e93e2b300651270e61a030e0889442173c7b68be7b7ff28f0b01efb4ad70
