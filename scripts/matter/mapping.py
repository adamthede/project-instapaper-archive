"""Turning a Matter API item into an archive Markdown file.

The target shape is whatever `scripts/core/build_index.py` reads, because that
is what ends up in the Parquet index and therefore the dashboard:

    title, original_url, instapaper_id, author,
    date_saved (or date_published / date_imported), date_archived,
    word_count, and the ai_* enrichment fields

One correction to the original plan is baked in here. The plan proposed writing
`date_read`; `build_index.py` does not read that key. It reads `date_archived`,
and the dashboard then computes `date_read = date_archived.fillna(date_saved)`.
Writing `date_read` would have produced files that parse cleanly and then sit at
the wrong end of every timeline. Matter's `archive` status maps to
`date_archived`, exactly like an archived Instapaper bookmark.
"""

import re
from datetime import datetime, timezone

import yaml

# Keys this sync owns and will overwrite on update. Everything else found in an
# existing file is preserved -- above all the ai_* fields, since
# enrich_archive_gemini.py writes those back into the same file and a re-sync
# that clobbered them would silently throw away paid enrichment work.
MATTER_OWNED_KEYS = frozenset({
    "title", "original_url", "matter_id", "author", "source", "content_type",
    "date_saved", "date_saved_source", "date_archived",
    "word_count", "favorite", "tags",
    "matter_status", "matter_progress", "matter_updated_at", "matter_synced_at",
    "matter_highlight_count", "matter_content_source", "matter_site_name",
})

# Matter exposes no per-item "saved" timestamp: the Item schema's only date is
# `updated_at`. This string goes in `date_saved_source` to record that, mirroring
# the honesty convention the Instapaper exporter already uses.
DATE_SOURCE_UPDATED_AT = "fallback - matter updated_at (API v1 exposes no created_at)"
DATE_SOURCE_STICKY = "original - first matter sync"

# Written onto an article the archive ALREADY has, when Matter records that it
# was read again. Deliberately not `date_archived`: the first read is the
# historical record and a re-read does not revise it.
REREAD_DATES_KEY = "matter_reread_at"
REREAD_COUNT_KEY = "matter_reread_count"
REREAD_SOURCE_KEY = "matter_reread_source"
# The same honesty `date_saved_source` carries: these dates are Matter's
# `updated_at` at the moment it reported the article archived, not an observed
# reading timestamp. Nightly syncing keeps them within a day of the truth;
# backfilled ones can be considerably later than the read they stand for.
REREAD_SOURCE = "matter updated_at when observed archived"

_ILLEGAL_FILENAME_CHARS = r'<>:"/\|?*'
_FRONTMATTER_FENCE = "---"


# ---- frontmatter I/O ------------------------------------------------------

# Outcomes of parse_document, so callers can tell "this file has no frontmatter"
# apart from "this file has frontmatter I could not read". The difference decides
# whether it is safe to rewrite the file.
PARSE_OK = "ok"
PARSE_NO_FRONTMATTER = "no_frontmatter"
PARSE_UNREADABLE = "unreadable"


def parse_document(text: str) -> tuple[dict, str, str]:
    """Split a YAML-frontmatter Markdown file into (metadata, body, status).

    Hand-rolled rather than using python-frontmatter because the nightly job
    runs under an interpreter that does not have that package installed, and a
    sync that cannot read its own output would be unable to preserve enrichment.
    """
    # A UTF-8 BOM or a leading blank line still means the file HAS frontmatter.
    # Reporting those as "no frontmatter" would let the caller conclude there was
    # nothing to preserve and rewrite the file, destroying the ai_* enrichment --
    # which is the exact failure this status exists to prevent. Note that
    # read_text(encoding="utf-8") does not strip a BOM.
    text = text.lstrip("\ufeff")
    stripped = text.lstrip("\n\r \t")
    if stripped.startswith(_FRONTMATTER_FENCE):
        text = stripped

    if not text.startswith(_FRONTMATTER_FENCE):
        return {}, text, PARSE_NO_FRONTMATTER

    lines = text.split("\n")
    closing = None
    for index in range(1, len(lines)):
        # rstrip, not strip: a fence is flush left. An *indented* `---` is a
        # document separator inside a multi-line YAML value, and treating it as
        # the closing fence truncates the frontmatter into something unparseable.
        if lines[index].rstrip() == _FRONTMATTER_FENCE:
            closing = index
            break
    if closing is None:
        return {}, text, PARSE_UNREADABLE

    raw_yaml = "\n".join(lines[1:closing])
    body = "\n".join(lines[closing + 1:])
    if body.startswith("\n"):
        body = body[1:]

    try:
        metadata = yaml.safe_load(raw_yaml)
    except yaml.YAMLError:
        return {}, text, PARSE_UNREADABLE
    if metadata is None:
        return {}, body, PARSE_OK  # an empty frontmatter block is readable
    if not isinstance(metadata, dict):
        return {}, text, PARSE_UNREADABLE
    return metadata, body, PARSE_OK


def parse_markdown(text: str) -> tuple[dict, str]:
    """parse_document without the status, for callers that only want the data."""
    metadata, body, _ = parse_document(text)
    return metadata, body


def dump_markdown(metadata: dict, body: str) -> str:
    """Render metadata + body back into a frontmatter Markdown document."""
    raw_yaml = yaml.safe_dump(
        metadata,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=10_000,  # never wrap; a wrapped URL is a broken URL
    ).rstrip("\n")
    return f"{_FRONTMATTER_FENCE}\n{raw_yaml}\n{_FRONTMATTER_FENCE}\n\n{body.lstrip(chr(10))}"


# ---- field extraction -----------------------------------------------------

def parse_timestamp(value) -> datetime | None:
    """Parse an ISO-8601 timestamp from the API into an aware UTC datetime."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _date_string(value) -> str | None:
    parsed = parse_timestamp(value)
    return parsed.date().isoformat() if parsed else None


def author_name(item: dict) -> str:
    """Matter's `author` is an object (or null), not a string."""
    author = item.get("author")
    if isinstance(author, dict):
        name = author.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    # "Unknown" is what build_index.py defaults to, so matching it keeps the
    # two eras consistent in the dashboard's author charts.
    return "Unknown"


def tag_names(item: dict) -> list[str]:
    tags = item.get("tags")
    if not isinstance(tags, list):
        return []
    names = []
    for tag in tags:
        if isinstance(tag, dict) and isinstance(tag.get("name"), str):
            names.append(tag["name"])
        elif isinstance(tag, str):
            names.append(tag)
    return names


def resolve_dates(item: dict, previous: dict | None = None) -> tuple[str | None, str, str | None]:
    """Work out (date_saved, date_saved_source, date_archived) for an item.

    Both dates are *sticky*: once an item has been written, the recorded dates
    are reused verbatim on every later sync. This matters because `updated_at`
    advances on any change -- a new highlight, a progress update -- and the
    dates drive both the filename and every temporal chart. Without stickiness
    an article would migrate forward through the timeline each time Adam touched
    it, and the file would be rewritten under a new name each time.
    """
    previous = previous or {}
    updated_date = _date_string(item.get("updated_at"))

    saved = previous.get("date_saved")
    if saved:
        saved_source = previous.get("date_saved_source") or DATE_SOURCE_STICKY
    else:
        saved = updated_date
        saved_source = DATE_SOURCE_UPDATED_AT

    archived = previous.get("date_archived")
    if not archived and item.get("status") == "archive":
        archived = updated_date

    return saved, saved_source, archived


# ---- body assembly --------------------------------------------------------

def format_highlights(annotations: list[dict]) -> str:
    """Render annotations as a `## Highlights` section.

    These are Adam's own highlights and notes, so they are content, not
    metadata: putting them in the body means the enrichment pass and the
    dashboard's full-text surfaces see them like any other text.
    """
    if not annotations:
        return ""

    def sort_key(annotation):
        parsed = parse_timestamp(annotation.get("created_at"))
        # Annotations with no parseable date sort last but keep their order.
        return (parsed is None, parsed or datetime.max.replace(tzinfo=timezone.utc))

    lines = ["## Highlights", ""]
    for annotation in sorted(annotations, key=sort_key):
        text = (annotation.get("text") or "").strip()
        if not text:
            continue
        for line in text.split("\n"):
            lines.append(f"> {line}".rstrip())
        note = (annotation.get("note") or "").strip()
        if note:
            lines.append("")
            note_lines = note.split("\n")
            lines.append(f"**Note:** {note_lines[0]}")
            lines.extend(note_lines[1:])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def annotate_reread(metadata: dict, read_date: str) -> tuple[dict, bool]:
    """Record that Matter read an article the archive already holds.

    Returns (new_metadata, changed).

    This is the only place the sync modifies a file it did not create, so the
    rules are narrow and absolute:

      * It ADDS keys and never modifies or removes an existing one. In
        particular `date_archived` and `date_saved` are untouched -- the first
        read is the historical record, and reading something again does not
        revise when it was first read.
      * It never writes `matter_id` onto a foreign file. That key is what marks
        a file as this sync's own (see sync.OrphanIndex); stamping it on an
        Instapaper-era article would eventually invite the sync to take
        ownership of it.
      * It is idempotent: one date is recorded once, however often it is seen.
    """
    existing = metadata.get(REREAD_DATES_KEY)
    if isinstance(existing, str):
        dates = [existing]
    elif isinstance(existing, list):
        dates = [d for d in existing if isinstance(d, str)]
    else:
        dates = []

    if not read_date or read_date in dates:
        return metadata, False

    updated = dict(metadata)
    updated[REREAD_DATES_KEY] = sorted(dates + [read_date])
    updated[REREAD_COUNT_KEY] = len(updated[REREAD_DATES_KEY])
    updated[REREAD_SOURCE_KEY] = REREAD_SOURCE
    return updated, True


def strip_highlights(body: str) -> str:
    """Remove a trailing `## Highlights` section we previously wrote.

    Used when refreshing an item whose article text we already have: highlights
    are regenerated from the API, the article body is not re-fetched. Splitting
    on the last occurrence is safe because this only ever runs on files this
    sync wrote, where the section is always appended last.
    """
    marker = "\n## Highlights\n"
    if body.startswith("## Highlights\n"):
        return ""
    head, separator, _ = body.rpartition(marker)
    return (head if separator else body).rstrip() + "\n" if (separator or body.strip()) else ""


def build_body(item: dict, annotations: list[dict], *, existing_body: str | None = None) -> tuple[str, str]:
    """Return (body, content_source).

    content_source records where the text came from, because "markdown" and
    "excerpt" are very different amounts of article and the difference should be
    visible later rather than inferred from a suspiciously low word count.

    `existing_body` is article text already on disk, reused when the API call
    that produced this item did not ask for markdown. That is the normal nightly
    case: an item usually reappears in the delta because a highlight was added,
    not because the article changed, and re-fetching bodies would burn the
    20-per-minute markdown budget for nothing.
    """
    markdown = item.get("markdown")
    if isinstance(markdown, str) and markdown.strip():
        body, source = markdown.strip(), "markdown"
    elif existing_body and existing_body.strip():
        body, source = existing_body.strip(), "markdown"
    else:
        excerpt = item.get("excerpt")
        if isinstance(excerpt, str) and excerpt.strip():
            body, source = excerpt.strip(), "excerpt"
        else:
            body, source = "", "none"

    highlights = format_highlights(annotations)
    if highlights:
        body = f"{body}\n\n{highlights}" if body else highlights
    return body.strip() + "\n", source


# ---- the whole file -------------------------------------------------------

def sanitize_title(title: str) -> str:
    """Filesystem-safe title, matching the Instapaper exporter's character rules."""
    cleaned = "".join(ch for ch in (title or "") if ch not in _ILLEGAL_FILENAME_CHARS)
    cleaned = "".join(ch if ch.isprintable() else " " for ch in cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = cleaned.strip(". ")
    return cleaned or "Untitled"


def build_filename(date_saved: str | None, title: str, *, suffix: str | None = None) -> str:
    """`YYYY-MM-DD – Title.md`, matching the Instapaper files already in the vault.

    The separator is an en dash with spaces, which is what the existing exporter
    wrote; keeping it means a directory listing of the merged archive stays
    visually uniform.
    """
    safe = sanitize_title(title)[:80].strip()
    stem = f"{date_saved} – {safe}" if date_saved else safe
    if suffix:
        stem = f"{stem} ({suffix})"
    return f"{stem}.md"


def build_frontmatter(item: dict, *, annotations: list[dict], content_source: str,
                      previous: dict | None = None, synced_at: datetime | None = None) -> dict:
    """The frontmatter block for one Matter item."""
    date_saved, date_saved_source, date_archived = resolve_dates(item, previous)
    synced_at = synced_at or datetime.now(timezone.utc)

    metadata: dict = {
        "title": item.get("title") or "Untitled",
        "original_url": item.get("url") or "",
        "matter_id": item.get("id"),
        "author": author_name(item),
        "source": "matter",
        "content_type": item.get("content_type") or "article",
        "date_saved": date_saved,
        "date_saved_source": date_saved_source,
    }
    if date_archived:
        metadata["date_archived"] = date_archived

    # Omitted rather than zeroed when Matter has no count (podcasts, failed
    # extraction): build_index.py falls back to counting the body's words, which
    # is a better answer than a hard 0.
    word_count = item.get("word_count")
    if isinstance(word_count, int) and word_count > 0:
        metadata["word_count"] = word_count

    site_name = item.get("site_name")
    if isinstance(site_name, str) and site_name.strip():
        metadata["matter_site_name"] = site_name.strip()

    tags = tag_names(item)
    if tags:
        metadata["tags"] = tags
    if item.get("is_favorite"):
        metadata["favorite"] = True

    progress = item.get("reading_progress")
    if isinstance(progress, (int, float)):
        metadata["matter_progress"] = round(float(progress), 4)

    metadata["matter_status"] = item.get("status")
    metadata["matter_updated_at"] = item.get("updated_at")
    metadata["matter_synced_at"] = synced_at.replace(microsecond=0).isoformat()
    metadata["matter_content_source"] = content_source
    if annotations:
        metadata["matter_highlight_count"] = len(annotations)

    return metadata


def render_item(item: dict, annotations: list[dict], *, previous: dict | None = None,
                existing_metadata: dict | None = None, existing_body: str | None = None,
                synced_at: datetime | None = None) -> tuple[dict, str]:
    """Build the (metadata, document) pair for one item.

    `existing_metadata` is the frontmatter already on disk, when updating a file
    we wrote earlier. Keys we do not own are carried across untouched.
    """
    body, content_source = build_body(item, annotations, existing_body=existing_body)
    metadata = build_frontmatter(
        item, annotations=annotations, content_source=content_source,
        previous=previous, synced_at=synced_at,
    )

    if existing_metadata:
        preserved = {k: v for k, v in existing_metadata.items() if k not in MATTER_OWNED_KEYS}
        # Preserved keys go last so the Matter-owned block stays readable at the
        # top of the file, the way the Instapaper files read.
        metadata = {**metadata, **preserved}

    return metadata, dump_markdown(metadata, body)
