# Matter Sync

Pulls reading from [Matter](https://hq.getmatter.com) into the Article Archive,
so the Instapaper era (~2008-2025) and the Matter era read as one continuous
history rather than two disconnected corpora.

Matter items become Markdown files with YAML frontmatter in the vault, exactly
like Instapaper articles. From there the existing pipeline takes over unchanged:
`enrich_archive_gemini.py` adds the `ai_*` fields, `build_index.py` compiles the
Parquet index, and the Streamlit dashboard reads it.

---

## Setup

### 1. Get an API token

Matter's public API needs a personal access token and an **active Matter Pro
subscription**.

1. Go to <https://web.getmatter.com/settings>
2. Click **Generate API Token**
3. Copy the token - it looks like `mat_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6q7r8`

> **One token per account.** Generating a new token immediately revokes the
> previous one. If you also use the Matter CLI, it shares that single token, so
> regenerating for one breaks the other.

### 2. Put it where the sync looks

```bash
mkdir -p ~/.secrets
printf '%s' 'mat_your_token_here' > ~/.secrets/matter.token
chmod 600 ~/.secrets/matter.token
```

`printf` rather than `echo` avoids a trailing newline, though the loader strips
one anyway. The file must not be readable by group or others; the sync refuses
to use a credential that is, and tells you the exact `chmod` to run.

The file can also hold JSON (`{"api_token": "mat_..."}`) if that is more
convenient. **The sync never writes to this file** - see [Why there is no
refresh token](#why-there-is-no-refresh-token).

### 3. Verify

```bash
python3 scripts/core/export_matter_to_archive.py --check-auth
```

Expected output:

```
Token file:   /Users/adam_thede/.secrets/matter.token
Token:        mat_a1b2...r8
Account:      Adam Thede <athede@gmail.com>
Account id:   act_...
Rate limits:  burst=5, markdown=20, read=120, save=10, search=30, write=30
Library:      reachable -- newest updated item is 'Some Article' (archive, updated 2026-08-10T...)

Authentication OK.
```

Then see what a sync would actually do, without writing anything:

```bash
python3 scripts/core/export_matter_to_archive.py --dry-run
```

---

## Usage

| Command | What it does |
|---|---|
| `--check-auth` | Verify the token, print the account and its rate limits. Two API calls, no writes. |
| `--dry-run` | Verify auth, then report what would be created, updated, or skipped. No writes. |
| `--sync` | Incremental pull since the last watermark. The default, and what the nightly job runs. |
| `--full` | Ignore the watermark and walk the whole library. For the first backfill. |
| `--max-items N` | Stop after N items. The watermark does not advance, so the next run resumes. |
| `--rebuild-index` | Run `build_index.py` afterwards so the dashboard sees the new articles. |
| `--refetch-content` | Re-download article bodies for items already on disk. |
| `--status` | Which Matter statuses to pull (default `archive,queue`). |
| `--subdir` | Where in the vault to write (default `matter/`; `''` writes flat). |

### First backfill

The markdown endpoint allows 20 requests per minute, so a large library takes a
while. Do it in chunks - the manifest makes it resumable, and interrupting is
safe:

```bash
python3 scripts/core/export_matter_to_archive.py --full --max-items 200
```

Repeat until it reports no new items. Then enrich and rebuild:

```bash
python3 scripts/core/enrich_archive_gemini.py
python3 scripts/core/build_index.py
```

### Nightly

Install the launchd job (04:45 daily):

```bash
mkdir -p ~/Library/Logs/MatterSync \
  && cp launchd/com.thedetech.article-sync.matter.plist ~/Library/LaunchAgents/ \
  && launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.thedetech.article-sync.matter.plist
```

Run it once by hand to confirm:

```bash
launchctl kickstart -k gui/$(id -u)/com.thedetech.article-sync.matter
```

Logs land in `~/Library/Logs/MatterSync/`, and a heartbeat JSON
(`nightly-heartbeat.json`) is written there in the fleet's standard shape
(`started_at` / `finished_at` / `outcome`), on failure as well as success. Note
that Command Center's launchd panel reads a hardcoded job registry, so showing
this job there needs a separate entry added in that repo - writing the heartbeat
is necessary but not sufficient.

---

## What gets written

```yaml
---
title: "How to Do Great Work"
original_url: "https://paulgraham.com/greatwork.html"
matter_id: "itm_r9f3a"
author: "Paul Graham"
source: "matter"
content_type: "article"
date_saved: "2026-03-30"
date_saved_source: "fallback - matter updated_at (API v1 exposes no created_at)"
date_archived: "2026-03-30"
word_count: 11842
matter_site_name: "paulgraham.com"
tags: ["essays"]
matter_status: "archive"
matter_progress: 0.35
matter_updated_at: "2026-03-30T19:15:00Z"
matter_synced_at: "2026-08-11T04:45:00+00:00"
matter_content_source: "markdown"
matter_highlight_count: 2
---

<the article body as Markdown>

## Highlights

> The way to figure out what to work on is by working.

**Note:** core thesis
```

Highlights go in the **body**, not the frontmatter - they are Adam's own words
about the article, so the enrichment pass and the dashboard's text surfaces
should see them like any other content.

### Field decisions worth knowing

**`date_archived`, not `date_read`.** The original plan proposed `date_read`.
`build_index.py` does not read that key - it reads `date_archived`, and the
dashboard then computes `date_read = date_archived.fillna(date_saved)`. Writing
`date_read` would have produced files that parse cleanly and then sit at the
wrong end of every chart. Matter's `archive` status maps to `date_archived`,
exactly like an archived Instapaper bookmark; `queue` items get no archive date
because they have not been read yet.

**Dates are sticky.** Once an item has been written, its `date_saved` and
`date_archived` are reused verbatim forever. Matter's `updated_at` advances on
*any* change - a new highlight, a progress update - so without stickiness an
article would migrate forward through the timeline every time Adam touched it,
and its filename would change with it.

**`date_saved` is honest about being a fallback.** Matter's API exposes no
per-item created/saved timestamp; `updated_at` is the only date on an item. For
articles synced going forward this is close to the truth, because a newly saved
item is picked up within a day. For the initial backfill of older items it is
the date of the last change, which may be much later than when it was saved.
The `date_saved_source` field records this, mirroring the convention the
Instapaper exporter already uses.

**`word_count` is omitted when Matter has none** (podcasts, failed extraction),
so `build_index.py` falls back to counting the body's words.

---

## How incremental sync works

1. A checkpoint timestamp is captured **before** any fetching.
2. `GET /v1/items?updated_since=<watermark>&order=updated` returns everything
   changed since last time, paginated by cursor.
3. Each item is classified: unchanged, cross-era duplicate, new, or update.
4. Bodies and highlights are fetched **only** for items being written.
5. The watermark advances to the checkpoint **only after a fully clean run**.

Step 5 is the important one. A partial run leaves the watermark alone: re-reading
a few unchanged items next time costs nothing, whereas skipping past a failure
would lose those items permanently. A run that hit per-item errors, or that was
cut short by `--max-items`, does not advance it.

The watermark is also rewound five minutes on each use, to absorb clock skew
between this machine and Matter's servers.

Highlights have no delta endpoint of their own - annotations can only be listed
per item. That is affordable because a new highlight bumps its parent item's
`updated_at`, so the item delta already identifies which items to ask about.

### Not re-downloading article bodies

A nightly delta is dominated by items that reappeared because a highlight was
added, not because the article changed. When the file on disk already holds the
article text, the sync reuses it and re-fetches only the highlights. This keeps
the 20-per-minute markdown budget for articles that actually need it. Force a
refresh with `--refetch-content`.

---

## Duplicate handling

The same article can legitimately be in both eras: saved to Instapaper in 2019,
saved again to Matter last week. Before writing anything new, the sync checks
the URL against the existing archive and skips a Matter item whose article is
already there, recording what it matched.

URLs are normalized first, so `http` vs `https`, `www.` vs not, trailing
slashes, `#fragments`, and `utm_*`/`fbclid` tracking parameters all compare
equal. Genuinely ambiguous parameters (`ref`, `source`) are **not** stripped -
on some sites they select real content, and a false merge would silently discard
an article, which is worse than a visible duplicate.

**Articles with no URL never match anything**, including each other. About
10,560 rows in the archive came from the legacy PDF/Word import with an empty
`url`; treating those as equal would collapse thousands of distinct articles
into one.

The URL index is built from `data/archive_index.parquet` when pyarrow is
available, and otherwise from a cached scan of the vault's frontmatter. If
neither produces anything the sync still runs, but says clearly in the log that
cross-era detection is degraded.

**Known limitation: a skipped duplicate's highlights are not captured.** If Adam
re-reads an article in Matter that he first saved to Instapaper years ago, and
highlights it, the Matter item is skipped as a duplicate and those highlights
land nowhere. The skip is recorded in the manifest, so it stays skipped. Merging
Matter highlights into an existing Instapaper-era file is the obvious fix, but it
means writing into files this sync did not create, which is a bigger promise than
this first version should make. The manifest records every such skip
(`skipped_reason: duplicate_url` plus `duplicate_of`), so the affected articles
can be found later.

### Files this sync wrote but lost track of

The manifest is saved as the run goes and again in a `finally` block, so a
crash or a `kill` still records what reached disk. If the manifest is lost
anyway - deleted, or corrupt and reset - the sync recovers by reading the
`matter_id` out of the frontmatter of the files already in `matter/` and
adopting them, rather than writing a second copy of everything.

---

## Why there is no refresh token

Matter's public API v1 authenticates with a long-lived personal access token and
has **no refresh endpoint** - the OpenAPI spec declares exactly one security
scheme (`bearerAuth`) and no token/refresh path. Because there is nothing to
refresh, the sync never rewrites `~/.secrets/matter.token`, and the hazard of a
crash mid-rewrite destroying the only credential does not exist. Removing the
failure mode beats defending against it.

Matter's older, undocumented **v11** API - the one behind their Obsidian plugin
- does work differently: QR-code device auth, access + refresh tokens, and a new
refresh token handed back on every exchange (so it must be persisted). It is
still live. It was not used here because:

- it has no `updated_since` or any other delta parameter, so a nightly job would
  have to re-walk the entire library every night and filter client-side;
- it has no documented rate limits;
- it is frozen (last commit to the plugin: November 2022).

It does have two things v1 lacks - `publication_date` and highlight position
offsets (`word_start`/`word_end`) - so it is worth remembering if either becomes
important.

---

## Verified vs assumed

Everything below marked **verified** was read from
<https://docs.getmatter.com/openapi.yaml> (OpenAPI 3.1.0), fetched directly, or
observed against the live API. Everything marked **assumed** needs a real token
to confirm.

**Verified from the spec:**

- Base URL `https://api.getmatter.com/public/v1`; `Authorization: Bearer mat_...`
- `GET /v1/items` with `status`, `order`, `updated_since`, `limit` (max 100),
  `cursor`, `content_type`, `tag`, `is_favorite`
- Cursor pagination via `has_more` / `next_cursor`
- `Item` requires `object, id, title, url, status, processing_status,
  is_favorite, content_type, reading_progress, tags, updated_at`; `author` is an
  object (nullable), `url` is top level, `word_count` is nullable
- **`Item` has no `created_at`** - `updated_at` is the only per-item timestamp
- `updated_at` advances on any change to the item or its associated data,
  including new annotations
- `GET /v1/items/{id}?include=markdown` for the body
- `GET /v1/items/{item_id}/annotations` - per item only, no global feed, no
  `updated_since`; `Annotation` is `{object, id, item_id, text, note?,
  created_at, updated_at}` with no position offsets
- Rate limits: read 120/min, markdown 20/min, burst 5/sec, write 30/min
- `GET /v1/me` returns the account and its `rate_limit` object

**Verified against the live API** (no token needed):

- The host is up; an unauthenticated call returns HTTP 401
  `authentication_required`, and an invalid token returns HTTP 401
  `invalid_token` - neither matches the `unauthorized` the prose docs promise.
  The client therefore branches on **HTTP status only**, never on error-code
  strings.

**Assumed, pending a real token:**

- That the account has active Matter Pro. Without it the API returns 403, and
  the sync says so explicitly.
- Field *presence and nullability under real data* - `site_name`, `excerpt` and
  `author` are described as nullable in the spec but have not been seen
  populated. The mapper treats all three as optional.
- That `content_type` values stay within the spec's enum (`article, podcast,
  pdf, tweet`). The prose docs also mention `video` and `newsletter`, so the
  value is passed through rather than validated.
- Whether `markdown` is reliably present for podcasts and PDFs. When it is
  absent the sync falls back to `excerpt` and records
  `matter_content_source: excerpt`.
- Real-world library size and how long the first backfill takes.

---

## Troubleshooting

**`Matter token file not found`** - the path in the message is where it looks.
See [Setup](#setup).

**`HTTP 401 ... invalid_token`** - the token was revoked, almost always because
a new one was generated (web settings, or `matter login`). Generate a fresh one
and rewrite the file.

**`HTTP 403 ... Matter Pro`** - the API requires an active Pro subscription.

**`Vault directory not found`** - the external drive is not mounted. The sync
deliberately refuses to create the vault: doing so would leave an empty archive
on the mount point and make it look like 17,637 articles had vanished.

**The dashboard does not show new articles** - the Markdown files are written,
but the Parquet index has not been rebuilt. Run `build_index.py`, or use
`--rebuild-index`.

**Cross-era duplicate detection is DEGRADED** - neither the Parquet index nor a
vault scan was available. Matter-vs-Matter duplicates are still prevented, but
an article already saved in the Instapaper era may be written a second time.

---

## Tests

```bash
.venv/bin/python -m pytest tests/ -q
```

No network and no real credential: the API client is driven by a fake session
serving payloads shaped like the documented schemas, and the credential loader
is pointed at a temp file for every test. The dashboard smoke tests run the real
Streamlit app headlessly against a synthetic corpus holding all three eras.
