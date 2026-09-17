"""The unread corpus: what Adam meant to read.

The read corpus answers what he actually read. This package builds the other
half - the queue of recorded intentions - to the plan at
`docs/plans-to-do/2026-09-15-unread-corpus-what-i-meant-to-read.md`.

Four stages, each its own module and each resumable:

    instapaper  the listing: unread plus the never-opened folder items
    resolve     the fetch chain: Instapaper stored text, plain GET, Wayback
    enrich      the base schema plus the unread template, on Gemini Flash-Lite
    analysis    the aggregates, the drift, and the shortlist
    public      the public shape for data.adamthede.com, and its leak scan

`derive` holds the two fields that are arithmetic rather than inference, kept
apart from the model on purpose: asking a model for either would produce a
plausible number that nothing can check.
"""
