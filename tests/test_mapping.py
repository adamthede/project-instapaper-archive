"""Mapping a Matter item into the archive's Markdown + YAML shape."""

from datetime import datetime, timezone

import yaml
from conftest import make_annotation, make_item

from matter import mapping


def test_frontmatter_uses_the_keys_build_index_actually_reads():
    metadata, document = mapping.render_item(make_item(markdown="# Body\n\ntext"), [])

    # These four are what build_index.py looks up by name.
    assert metadata["title"] == "How to Do Great Work"
    assert metadata["original_url"] == "https://paulgraham.com/greatwork.html"
    assert metadata["author"] == "Paul Graham"
    assert metadata["word_count"] == 11842
    assert document.startswith("---\n")


def test_archived_items_get_date_archived_not_date_read():
    """The plan proposed `date_read`; build_index.py reads `date_archived`."""
    metadata, _ = mapping.render_item(make_item(status="archive"), [])
    assert metadata["date_archived"] == "2026-03-30"
    assert "date_read" not in metadata


def test_queued_items_have_no_archive_date():
    metadata, _ = mapping.render_item(make_item(status="queue"), [])
    assert "date_archived" not in metadata
    assert metadata["date_saved"] == "2026-03-30"


def test_source_marks_the_matter_era():
    metadata, _ = mapping.render_item(make_item(), [])
    assert metadata["source"] == "matter"
    assert metadata["matter_id"] == "itm_abc123"


def test_author_object_is_flattened_and_missing_authors_match_the_archive_default():
    assert mapping.author_name(make_item()) == "Paul Graham"
    assert mapping.author_name(make_item(author=None)) == "Unknown"
    assert mapping.author_name(make_item(author={"object": "author", "id": "a", "name": ""})) == "Unknown"


def test_date_saved_records_that_it_is_a_fallback():
    """Matter's Item schema has no created_at; the archive should say so."""
    metadata, _ = mapping.render_item(make_item(), [])
    assert metadata["date_saved"] == "2026-03-30"
    assert "no created_at" in metadata["date_saved_source"]


def test_dates_are_sticky_so_articles_do_not_drift_through_the_timeline():
    """updated_at advances on every highlight; date_saved must not follow it."""
    previous = {"date_saved": "2026-01-05", "date_saved_source": "original - first matter sync",
                "date_archived": "2026-01-06"}
    metadata, _ = mapping.render_item(
        make_item(updated_at="2026-08-01T10:00:00Z"), [], previous=previous,
    )
    assert metadata["date_saved"] == "2026-01-05"
    assert metadata["date_archived"] == "2026-01-06"


def test_word_count_is_omitted_when_matter_has_none_so_build_index_counts_the_body():
    metadata, _ = mapping.render_item(make_item(word_count=None, content_type="podcast"), [])
    assert "word_count" not in metadata


def test_tags_and_favourite_only_appear_when_set():
    plain, _ = mapping.render_item(make_item(tags=[], is_favorite=False), [])
    assert "tags" not in plain
    assert "favorite" not in plain

    flagged, _ = mapping.render_item(make_item(is_favorite=True), [])
    assert flagged["tags"] == ["essays"]
    assert flagged["favorite"] is True


def test_highlights_land_in_the_body_as_content():
    annotations = [
        make_annotation(text="Second highlight", created_at="2026-03-30T19:00:00Z"),
        make_annotation(annotation_id="ann_2", text="First highlight",
                        note="why this matters", created_at="2026-03-30T18:00:00Z"),
    ]
    metadata, document = mapping.render_item(make_item(markdown="# Article\n\nProse."), annotations)

    assert "## Highlights" in document
    assert "> First highlight" in document
    assert "**Note:** why this matters" in document
    # Sorted by creation, not by the order the API returned them.
    assert document.index("First highlight") < document.index("Second highlight")
    assert metadata["matter_highlight_count"] == 2


def test_multiline_highlights_stay_inside_the_blockquote():
    annotations = [make_annotation(text="line one\nline two")]
    _, document = mapping.render_item(make_item(markdown="body"), annotations)
    assert "> line one" in document
    assert "> line two" in document


def test_body_falls_back_to_the_excerpt_and_says_so():
    metadata, document = mapping.render_item(make_item(markdown=None), [])
    assert metadata["matter_content_source"] == "excerpt"
    assert "Paul Graham explores" in document


def test_enrichment_fields_survive_a_resync():
    """enrich_archive_gemini.py writes ai_* back into these files; a re-sync must not eat them."""
    existing = {
        "title": "Old title", "source": "matter", "matter_id": "itm_abc123",
        "ai_topics": ["Essays", "Craft"], "ai_summary": "A summary worth money.",
        "ai_sentiment": "Positive", "content_corrupted": False,
    }
    metadata, _ = mapping.render_item(
        make_item(title="New title", markdown="body"), [], existing_metadata=existing,
    )
    assert metadata["ai_topics"] == ["Essays", "Craft"]
    assert metadata["ai_summary"] == "A summary worth money."
    assert metadata["ai_sentiment"] == "Positive"
    assert metadata["content_corrupted"] is False
    # Matter-owned keys are still refreshed.
    assert metadata["title"] == "New title"


def test_roundtrip_through_the_parser_preserves_metadata_and_body():
    metadata, document = mapping.render_item(
        make_item(markdown="# Heading\n\nSome prose with a --- inside it."), [make_annotation()],
    )
    parsed_metadata, parsed_body = mapping.parse_markdown(document)
    assert parsed_metadata == metadata
    assert "Some prose" in parsed_body
    assert "## Highlights" in parsed_body


def test_frontmatter_is_valid_yaml_for_titles_with_colons_and_quotes():
    """The hand-rolled exporters escape by hand; safe_dump removes that whole class of bug."""
    tricky = 'Rails 8: "the reckoning" - a title, with: colons'
    _, document = mapping.render_item(make_item(title=tricky, markdown="body"), [])
    parsed, _ = mapping.parse_markdown(document)
    assert parsed["title"] == tricky
    assert yaml.safe_load(document.split("---")[1])["title"] == tricky


def test_dates_survive_as_strings_build_index_can_strptime():
    _, document = mapping.render_item(make_item(), [])
    parsed, _ = mapping.parse_markdown(document)
    assert isinstance(parsed["date_saved"], str)
    datetime.strptime(parsed["date_saved"], "%Y-%m-%d")  # must not raise


def test_filenames_match_the_instapaper_naming_already_in_the_vault():
    assert mapping.build_filename("2026-03-30", "How to Do Great Work") == "2026-03-30 – How to Do Great Work.md"


def test_filenames_drop_characters_the_filesystem_rejects():
    name = mapping.build_filename("2026-03-30", 'A/B: "quoted" <tag> | pipe?')
    for char in '<>:"/\\|?*':
        assert char not in name


def test_filenames_are_bounded_and_never_empty():
    long_name = mapping.build_filename("2026-03-30", "x" * 500)
    assert len(long_name) < 120
    assert mapping.build_filename("2026-03-30", "").endswith("Untitled.md")
    assert mapping.build_filename("2026-03-30", "...").endswith("Untitled.md")


def test_parse_markdown_tolerates_files_with_no_frontmatter():
    metadata, body = mapping.parse_markdown("Just prose, no fences.\n")
    assert metadata == {}
    assert body.startswith("Just prose")


def test_parse_markdown_does_not_explode_on_broken_yaml():
    metadata, _ = mapping.parse_markdown("---\ntitle: [unclosed\n---\n\nbody\n")
    assert metadata == {}


def test_strip_highlights_leaves_the_article_and_removes_the_section():
    body = "# Article\n\nProse here.\n\n## Highlights\n\n> old highlight\n"
    assert "old highlight" not in mapping.strip_highlights(body)
    assert "Prose here." in mapping.strip_highlights(body)


def test_carried_body_avoids_refetching_the_article():
    item = make_item()  # no markdown key: nothing was downloaded
    metadata, document = mapping.render_item(
        item, [make_annotation(text="new highlight")], existing_body="# Article\n\nProse here.\n",
    )
    assert metadata["matter_content_source"] == "markdown"
    assert "Prose here." in document
    assert "> new highlight" in document


def test_synced_at_is_recorded_in_utc():
    metadata, _ = mapping.render_item(
        make_item(), [], synced_at=datetime(2026, 8, 11, 4, 45, tzinfo=timezone.utc),
    )
    assert metadata["matter_synced_at"].startswith("2026-08-11T04:45:00")


# ---- read-date estimation: three paths, in descending confidence ----------

def test_an_observed_transition_uses_updated_at_and_says_so(vault_unused=None):
    """The sync watched it enter the archive, so updated_at is a day old at most."""
    date, source = mapping.best_read_date(
        make_item(updated_at="2026-07-01T12:00:00Z"), [], observed_transition=True,
    )
    assert date == "2026-07-01"
    assert source == mapping.DATE_SOURCE_OBSERVED


def test_a_highlight_older_than_updated_at_wins():
    """Highlights are made WHILE reading; a much later updated_at is drift."""
    annotations = [
        make_annotation(created_at="2026-03-30T18:32:00Z"),
        make_annotation(annotation_id="ann_2", created_at="2026-03-30T19:05:00Z"),
    ]
    date, source = mapping.best_read_date(
        make_item(updated_at="2026-07-01T12:00:00Z"), annotations,
    )
    assert date == "2026-03-30", "the NEWEST highlight, not the oldest"
    assert source == mapping.DATE_SOURCE_HIGHLIGHT


def test_a_highlight_close_to_updated_at_does_not_displace_it():
    """Within the slack the two agree, and churn buys nothing."""
    date, source = mapping.best_read_date(
        make_item(updated_at="2026-07-01T12:00:00Z"),
        [make_annotation(created_at="2026-07-01T11:00:00Z")],
    )
    assert date == "2026-07-01"
    assert source == mapping.DATE_SOURCE_UPDATED_AT


def test_a_highlight_later_than_updated_at_is_ignored_as_noise():
    """Annotations bump updated_at, so this ordering should be impossible."""
    date, source = mapping.best_read_date(
        make_item(updated_at="2026-03-30T19:15:00Z"),
        [make_annotation(created_at="2026-07-01T12:00:00Z")],
    )
    assert date == "2026-03-30"
    assert source == mapping.DATE_SOURCE_UPDATED_AT


def test_no_annotations_falls_back_and_admits_it():
    date, source = mapping.best_read_date(make_item(updated_at="2026-07-01T12:00:00Z"), [])
    assert date == "2026-07-01"
    assert source.startswith("fallback")


def test_an_observed_transition_beats_a_stale_highlight():
    """If we watched it happen, that beats inference from an old highlight."""
    _, source = mapping.best_read_date(
        make_item(updated_at="2026-07-01T12:00:00Z"),
        [make_annotation(created_at="2026-01-01T00:00:00Z")],
        observed_transition=True,
    )
    assert source == mapping.DATE_SOURCE_OBSERVED


def test_the_estimate_is_recorded_in_the_frontmatter_source_field():
    metadata, _ = mapping.render_item(
        make_item(updated_at="2026-07-01T12:00:00Z", markdown="body"),
        [make_annotation(created_at="2026-03-30T18:32:00Z")],
    )
    assert metadata["date_saved"] == "2026-03-30"
    assert metadata["date_archived"] == "2026-03-30"
    assert metadata["date_saved_source"] == mapping.DATE_SOURCE_HIGHLIGHT


def test_a_better_estimate_arriving_later_does_not_rewrite_history():
    """Stickiness outranks accuracy: the estimate is fixed on first sight."""
    previous = {"date_saved": "2026-01-05",
                "date_saved_source": mapping.DATE_SOURCE_UPDATED_AT,
                "date_archived": "2026-01-05"}
    metadata, _ = mapping.render_item(
        make_item(updated_at="2026-08-01T10:00:00Z", markdown="body"),
        [make_annotation(created_at="2026-02-02T00:00:00Z")],
        previous=previous,
    )
    assert metadata["date_saved"] == "2026-01-05"
    assert metadata["date_archived"] == "2026-01-05"
    assert metadata["date_saved_source"] == mapping.DATE_SOURCE_UPDATED_AT


def test_annotate_reread_is_idempotent_on_its_own():
    """Directly, not through the sync.

    The manifest short-circuit now skips this function entirely on a repeat, so
    an end-to-end test passes whether or not the function itself is idempotent.
    This is the guard for the function's own contract.
    """
    first, changed = mapping.annotate_reread({"title": "x"}, "2026-05-12")
    assert changed and first[mapping.REREAD_DATES_KEY] == ["2026-05-12"]

    second, changed_again = mapping.annotate_reread(first, "2026-05-12")
    assert not changed_again
    assert second[mapping.REREAD_DATES_KEY] == ["2026-05-12"]
    assert second[mapping.REREAD_COUNT_KEY] == 1


def test_annotate_reread_accumulates_distinct_dates_in_order():
    metadata, _ = mapping.annotate_reread({"title": "x"}, "2026-07-04")
    metadata, _ = mapping.annotate_reread(metadata, "2026-05-12")
    assert metadata[mapping.REREAD_DATES_KEY] == ["2026-05-12", "2026-07-04"]
    assert metadata[mapping.REREAD_COUNT_KEY] == 2
