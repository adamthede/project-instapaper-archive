"""End-to-end sync behaviour, driven by a fake Matter client."""

import pytest
from conftest import FakeClient, make_annotation, make_item

from matter import mapping
from matter.errors import MatterAuthError, VaultNotFoundError
from matter.state import SyncState
from matter.sync import SyncConfig, ensure_vault, run_sync


def config_for(vault, **overrides):
    options = dict(vault_path=vault, subdir="matter", parquet_path=None, heartbeat_path=None)
    options.update(overrides)
    return SyncConfig(**options)


def write_instapaper_article(vault, url, filename="2019-04-01 – Same Article.md", extra=""):
    """An article as the Instapaper exporter wrote it, for cross-era dedupe tests."""
    path = vault / filename
    path.write_text(
        "---\n"
        'title: "Same Article"\n'
        f'original_url: "{url}"\n'
        "instapaper_id: 12345\n"
        "date_saved: 2019-04-01\n"
        f"{extra}"
        "---\n\nThe original body.\n",
        encoding="utf-8",
    )
    return path


# ---- preconditions --------------------------------------------------------

def test_a_missing_vault_fails_loudly_and_is_never_created(tmp_path):
    """The vault lives on an external SSD; creating it would fake an empty archive."""
    missing = tmp_path / "Volumes" / "Extreme SSD" / "Instapaper-Archive"
    with pytest.raises(VaultNotFoundError) as excinfo:
        ensure_vault(missing)
    assert str(missing) in str(excinfo.value)
    assert "mounted" in str(excinfo.value)
    assert not missing.exists()


def test_a_file_where_the_vault_should_be_is_rejected(tmp_path):
    path = tmp_path / "vault"
    path.write_text("not a directory")
    with pytest.raises(VaultNotFoundError, match="not a directory"):
        ensure_vault(path)


# ---- the happy path -------------------------------------------------------

def test_a_new_item_becomes_a_markdown_file_in_the_matter_subdirectory(vault):
    client = FakeClient([make_item()], annotations={"itm_abc123": [make_annotation()]})

    result = run_sync(config_for(vault), client=client)

    assert result.new == 1
    written = list((vault / "matter").glob("*.md"))
    assert len(written) == 1
    assert written[0].name == "2026-03-30 – How to Do Great Work.md"

    metadata, body = mapping.parse_markdown(written[0].read_text(encoding="utf-8"))
    assert metadata["source"] == "matter"
    assert metadata["original_url"] == "https://paulgraham.com/greatwork.html"
    assert metadata["date_archived"] == "2026-03-30"
    assert "## Highlights" in body
    assert result.highlights == 1


def test_the_watermark_advances_only_after_a_clean_run(vault):
    client = FakeClient([make_item()])
    result = run_sync(config_for(vault), client=client)

    assert result.watermark_before is None
    assert result.watermark_after is not None

    state = SyncState.load(vault / ".matter_manifest.json")
    assert state.watermark == result.watermark_after


def test_the_second_run_asks_only_for_what_changed(vault):
    client = FakeClient([make_item()])
    run_sync(config_for(vault), client=client)

    second = FakeClient([make_item()])
    run_sync(config_for(vault), client=second)

    assert second.list_calls[0]["updated_since"] is not None


def test_full_mode_ignores_the_watermark(vault):
    run_sync(config_for(vault), client=FakeClient([make_item()]))

    second = FakeClient([make_item()])
    run_sync(config_for(vault, full=True), client=second)
    assert second.list_calls[0]["updated_since"] is None


def test_an_unchanged_item_is_skipped_without_fetching_its_body_again(vault):
    client = FakeClient([make_item()])
    run_sync(config_for(vault), client=client)

    again = FakeClient([make_item()])
    result = run_sync(config_for(vault), client=again)

    assert result.unchanged == 1
    assert result.new == 0
    assert again.detail_fetches == [], "no article body should be re-downloaded"


def test_an_item_whose_file_was_deleted_is_written_again(vault):
    client = FakeClient([make_item()])
    run_sync(config_for(vault), client=client)
    next(iter((vault / "matter").glob("*.md"))).unlink()

    result = run_sync(config_for(vault), client=FakeClient([make_item()]))
    assert result.new == 1


# ---- dedupe across the two eras -------------------------------------------

def test_an_article_already_saved_in_the_instapaper_era_is_not_written_twice(vault):
    write_instapaper_article(vault, "https://paulgraham.com/greatwork.html")

    client = FakeClient([make_item(url="https://paulgraham.com/greatwork.html")])
    result = run_sync(config_for(vault), client=client)

    assert result.duplicates == 1
    assert result.new == 0
    assert list((vault / "matter").glob("*.md")) == []
    assert client.detail_fetches == [], "a duplicate should not cost a markdown fetch"


@pytest.mark.parametrize("matter_url", [
    "http://www.paulgraham.com/greatwork.html",
    "https://paulgraham.com/greatwork.html?utm_source=newsletter",
    "https://paulgraham.com/greatwork.html#intro",
    "https://paulgraham.com/greatwork.html/",
])
def test_dedupe_survives_the_usual_url_drift(vault, matter_url):
    write_instapaper_article(vault, "https://paulgraham.com/greatwork.html")
    result = run_sync(config_for(vault), client=FakeClient([make_item(url=matter_url)]))
    assert result.duplicates == 1


def test_legacy_rows_without_urls_never_collapse_together(vault):
    """~10,560 rows have an empty url; matching on that would hide real articles."""
    write_instapaper_article(vault, "", filename="1989-10-22 – Cold Spring.md")
    write_instapaper_article(vault, "", filename="1991-01-02 – Another.md")

    result = run_sync(config_for(vault), client=FakeClient([make_item(url="")]))

    assert result.duplicates == 0
    assert result.new == 1


def test_a_duplicate_is_remembered_so_it_is_not_re_checked_forever(vault):
    write_instapaper_article(vault, "https://paulgraham.com/greatwork.html")
    run_sync(config_for(vault), client=FakeClient([make_item()]))

    state = SyncState.load(vault / ".matter_manifest.json")
    record = state.get_item("itm_abc123")
    assert record["skipped_reason"] == "duplicate_url"
    assert record["duplicate_of"].endswith("Same Article.md")


def test_two_matter_items_with_the_same_url_only_produce_one_file(vault):
    client = FakeClient([
        make_item(item_id="itm_1", url="https://e.com/a", title="First copy"),
        make_item(item_id="itm_2", url="https://e.com/a", title="Second copy"),
    ])
    result = run_sync(config_for(vault), client=client)

    assert result.new == 1
    assert result.duplicates == 1


def test_distinct_articles_sharing_a_title_and_date_both_survive(vault):
    client = FakeClient([
        make_item(item_id="itm_1", url="https://a.com/x", title="Weekly Notes"),
        make_item(item_id="itm_2", url="https://b.com/y", title="Weekly Notes"),
    ])
    result = run_sync(config_for(vault), client=client)

    assert result.new == 2
    assert len(list((vault / "matter").glob("*.md"))) == 2


def test_a_matter_file_does_not_overwrite_an_instapaper_file_of_the_same_name(vault):
    """Both eras use `date – title.md`, so the names really can collide."""
    (vault / "matter").mkdir()
    collision = vault / "matter" / "2026-03-30 – How to Do Great Work.md"
    collision.write_text("---\ntitle: Something else\n---\n\nPre-existing.\n", encoding="utf-8")

    run_sync(config_for(vault), client=FakeClient([make_item(url="https://elsewhere.com/x")]))

    assert "Pre-existing." in collision.read_text(encoding="utf-8")
    assert len(list((vault / "matter").glob("*.md"))) == 2


# ---- updates --------------------------------------------------------------

def test_a_new_highlight_updates_the_file_without_refetching_the_article(vault):
    run_sync(config_for(vault), client=FakeClient([make_item()]))

    changed = make_item(updated_at="2026-04-02T08:00:00Z")
    client = FakeClient([changed], annotations={"itm_abc123": [make_annotation(text="A fresh highlight")]})
    result = run_sync(config_for(vault), client=client)

    assert result.updated == 1
    assert client.detail_fetches == [], "the body is already on disk"

    written = next(iter((vault / "matter").glob("*.md")))
    body = written.read_text(encoding="utf-8")
    assert "A fresh highlight" in body
    assert "Body of itm_abc123." in body, "the original article text is retained"


def test_updating_does_not_destroy_enrichment_written_by_the_gemini_pass(vault):
    run_sync(config_for(vault), client=FakeClient([make_item()]))
    written = next(iter((vault / "matter").glob("*.md")))

    metadata, body = mapping.parse_markdown(written.read_text(encoding="utf-8"))
    metadata.update({"ai_topics": ["Craft", "Essays"], "ai_summary": "Expensive to regenerate.",
                     "ai_sentiment": "Positive"})
    written.write_text(mapping.dump_markdown(metadata, body), encoding="utf-8")

    run_sync(config_for(vault), client=FakeClient([make_item(updated_at="2026-05-01T00:00:00Z")]))

    after, _ = mapping.parse_markdown(written.read_text(encoding="utf-8"))
    assert after["ai_topics"] == ["Craft", "Essays"]
    assert after["ai_summary"] == "Expensive to regenerate."


def test_a_retitled_article_keeps_its_original_filename(vault):
    """Renaming would leave the old file behind as a duplicate."""
    run_sync(config_for(vault), client=FakeClient([make_item()]))
    original = next(iter((vault / "matter").glob("*.md")))

    run_sync(config_for(vault), client=FakeClient([
        make_item(title="A Completely Different Title", updated_at="2026-05-01T00:00:00Z"),
    ]))

    assert len(list((vault / "matter").glob("*.md"))) == 1
    metadata, _ = mapping.parse_markdown(original.read_text(encoding="utf-8"))
    assert metadata["title"] == "A Completely Different Title"


def test_refetch_content_forces_a_fresh_body(vault):
    run_sync(config_for(vault), client=FakeClient([make_item()]))

    client = FakeClient([make_item(updated_at="2026-05-01T00:00:00Z")])
    run_sync(config_for(vault, refetch_content=True), client=client)
    assert client.detail_fetches == ["itm_abc123"]


# ---- failure handling -----------------------------------------------------

def test_one_bad_item_does_not_end_the_run_or_advance_the_watermark(vault):
    class ExplodingClient(FakeClient):
        def iter_annotations(self, item_id, *, page_size=100):
            if item_id == "itm_bad":
                raise RuntimeError("annotations endpoint blew up")
            return super().iter_annotations(item_id, page_size=page_size)

    client = ExplodingClient([
        make_item(item_id="itm_good", url="https://e.com/good"),
        make_item(item_id="itm_bad", url="https://e.com/bad"),
    ])
    result = run_sync(config_for(vault), client=client)

    assert result.new == 1
    assert result.errors == 1
    assert result.outcome == "fail"
    assert result.watermark_after is None, "a failed run must be retried from the same point"
    assert SyncState.load(vault / ".matter_manifest.json").watermark is None


def test_a_truncated_run_does_not_advance_the_watermark(vault):
    client = FakeClient([make_item(item_id=f"itm_{n}", url=f"https://e.com/{n}") for n in range(5)])
    result = run_sync(config_for(vault, max_items=2), client=client)

    assert result.new == 2
    assert result.watermark_after is None
    assert SyncState.load(vault / ".matter_manifest.json").watermark is None


def test_a_chunked_backfill_makes_progress_run_after_run(vault):
    """The budget counts work done, not items looked at.

    Counting every item would make run 2 spend its whole allowance re-skipping
    the items run 1 already wrote, so a chunked backfill would stall at the
    first chunk forever -- which is exactly the documented first-run procedure.
    """
    items = [make_item(item_id=f"itm_{n}", url=f"https://e.com/{n}", title=f"Article {n}")
             for n in range(6)]

    for _ in range(3):
        run_sync(config_for(vault, max_items=2, full=True), client=FakeClient(items))

    assert len(list((vault / "matter").glob("*.md"))) == 6


def test_an_item_with_no_id_is_counted_as_an_error_not_written(vault):
    client = FakeClient([{"object": "item", "title": "no id", "url": "https://e.com/x"}])
    result = run_sync(config_for(vault), client=client)
    assert result.errors == 1
    assert result.new == 0


def test_a_crash_still_records_what_was_already_written(vault):
    """At the production save_every, not a test-only value.

    If the manifest does not learn about files already on disk, the next run has
    no record of them and writes a second copy of every one.
    """
    class CrashingClient(FakeClient):
        def iter_annotations(self, item_id, *, page_size=100):
            if item_id == "itm_3":
                raise RuntimeError("connection died mid-run")
            return super().iter_annotations(item_id, page_size=page_size)

    items = [make_item(item_id=f"itm_{n}", url=f"https://e.com/{n}", title=f"Article {n}")
             for n in range(1, 5)]
    # Deliberately fewer items than save_every (20), so only the finally-block
    # save can have persisted anything.
    run_sync(config_for(vault), client=CrashingClient(items))

    state = SyncState.load(vault / ".matter_manifest.json")
    assert state.get_item("itm_1") and state.get_item("itm_1")["path"]
    assert state.get_item("itm_2") and state.get_item("itm_2")["path"]
    assert state.watermark is None


def test_a_killed_run_does_not_lead_to_duplicates_on_the_next_run(vault):
    """The manifest can be lost entirely; the files on disk are the truth."""
    items = [make_item(item_id=f"itm_{n}", url=f"https://e.com/{n}", title=f"Article {n}")
             for n in range(1, 4)]
    run_sync(config_for(vault), client=FakeClient(items))
    assert len(list((vault / "matter").glob("*.md"))) == 3

    # Simulate a kill before the save, or a manifest that was corrupt and reset.
    (vault / ".matter_manifest.json").unlink()

    result = run_sync(config_for(vault, full=True), client=FakeClient(items))

    assert len(list((vault / "matter").glob("*.md"))) == 3, "no second copies"
    assert result.new == 0
    assert result.updated == 3, "the orphaned files are adopted, not duplicated"


def test_an_unreadable_existing_file_is_refused_rather_than_rewritten(vault):
    """The enrichment in a file we cannot parse must not be thrown away."""
    run_sync(config_for(vault), client=FakeClient([make_item()]))
    written = next(iter((vault / "matter").glob("*.md")))

    # Frontmatter YAML that PyYAML rejects -- an unquoted colon, the classic
    # result of a hand-edit in Obsidian.
    written.write_text(
        "---\n"
        'title: "How to Do Great Work"\n'
        "matter_id: itm_abc123\n"
        "ai_summary: Rails 8: the reckoning\n"
        "ai_topics: [Craft]\n"
        "---\n\nThe article body.\n",
        encoding="utf-8",
    )
    before = written.read_text(encoding="utf-8")

    result = run_sync(
        config_for(vault), client=FakeClient([make_item(updated_at="2026-05-01T00:00:00Z")]),
    )

    assert written.read_text(encoding="utf-8") == before, "the file is left exactly as it was"
    assert result.errors == 1
    assert "frontmatter" in result.error_examples[0]["error"]
    assert result.watermark_after is None


def test_an_indented_yaml_separator_is_not_mistaken_for_the_closing_fence(vault):
    """A folded YAML value can contain a line that strips to '---'."""
    run_sync(config_for(vault), client=FakeClient([make_item()]))
    written = next(iter((vault / "matter").glob("*.md")))

    metadata, body = mapping.parse_markdown(written.read_text(encoding="utf-8"))
    metadata["ai_summary"] = "A summary whose second line is\n  ---\nand then continues."
    written.write_text(mapping.dump_markdown(metadata, body), encoding="utf-8")

    result = run_sync(
        config_for(vault), client=FakeClient([make_item(updated_at="2026-05-01T00:00:00Z")]),
    )

    assert result.errors == 0
    after, _ = mapping.parse_markdown(written.read_text(encoding="utf-8"))
    assert after["ai_summary"] == metadata["ai_summary"]


def test_a_revoked_token_stops_the_run_instead_of_failing_every_item(vault):
    """One 401 means every remaining item will 401; do not hammer a dead token."""
    class RevokedClient(FakeClient):
        attempts = 0

        def iter_annotations(self, item_id, *, page_size=100):
            RevokedClient.attempts += 1
            raise MatterAuthError("token revoked")

    items = [make_item(item_id=f"itm_{n}", url=f"https://e.com/{n}") for n in range(10)]
    client = RevokedClient(items)

    with pytest.raises(MatterAuthError):
        run_sync(config_for(vault), client=client)

    assert RevokedClient.attempts == 1, "stopped at the first rejection, not after all 10"


# ---- dry run --------------------------------------------------------------

def test_dry_run_writes_nothing_at_all(vault):
    client = FakeClient([make_item()])
    result = run_sync(config_for(vault, dry_run=True), client=client)

    assert result.new == 1
    assert not (vault / "matter").exists()
    assert not (vault / ".matter_manifest.json").exists()
    assert client.detail_fetches == []


def test_dry_run_still_reports_cross_era_duplicates(vault):
    write_instapaper_article(vault, "https://paulgraham.com/greatwork.html")
    result = run_sync(config_for(vault, dry_run=True), client=FakeClient([make_item()]))
    assert result.duplicates == 1


# ---- configuration --------------------------------------------------------

def test_only_read_articles_are_pulled_by_default(vault):
    """The archive records what was READ.

    `queue` is saved-but-unread and `inbox` is not even saved; neither belongs
    in a corpus whose entire value is that everything in it was read.
    """
    client = FakeClient([make_item()])
    run_sync(config_for(vault), client=client)
    assert client.list_calls[0]["status"] == "archive"


def test_queue_can_still_be_pulled_deliberately(vault):
    """The flag stays for deliberate use; the dashboard defends itself separately."""
    client = FakeClient([make_item(status="queue")])
    run_sync(config_for(vault, status="archive,queue"), client=client)
    assert client.list_calls[0]["status"] == "archive,queue"


def test_a_queued_item_is_written_without_an_archive_date(vault):
    """So nothing downstream can mistake it for something that was read."""
    run_sync(config_for(vault, status="queue"), client=FakeClient([make_item(status="queue")]))

    written = next(iter((vault / "matter").glob("*.md")))
    metadata, _ = mapping.parse_markdown(written.read_text(encoding="utf-8"))
    assert "date_archived" not in metadata
    assert metadata["matter_status"] == "queue"


# ---- re-reads: an article the archive already has, read again in Matter ----

def test_a_reread_is_recorded_on_the_existing_file_not_as_a_second_one(vault):
    original = write_instapaper_article(vault, "https://paulgraham.com/greatwork.html")
    before, body_before = mapping.parse_markdown(original.read_text(encoding="utf-8"))

    result = run_sync(config_for(vault), client=FakeClient([
        make_item(status="archive", updated_at="2026-05-12T09:00:00Z"),
    ]))

    assert result.duplicates == 1
    assert result.rereads_recorded == 1
    assert list((vault / "matter").glob("*.md")) == [], "never a second file"

    after, body_after = mapping.parse_markdown(original.read_text(encoding="utf-8"))
    assert after["matter_reread_at"] == ["2026-05-12"]
    assert after["matter_reread_count"] == 1
    assert body_after == body_before, "the article body is untouched"


def test_a_reread_never_revises_the_original_read_date(vault):
    """The first read is the historical record. Reading it again does not move it."""
    original = write_instapaper_article(
        vault, "https://paulgraham.com/greatwork.html", extra="date_archived: 2019-04-05\n",
    )
    before, _ = mapping.parse_markdown(original.read_text(encoding="utf-8"))

    run_sync(config_for(vault), client=FakeClient([
        make_item(status="archive", updated_at="2026-05-12T09:00:00Z"),
    ]))

    after, _ = mapping.parse_markdown(original.read_text(encoding="utf-8"))
    assert str(after["date_archived"]) == str(before["date_archived"])
    assert str(after["date_saved"]) == str(before["date_saved"])
    assert after["instapaper_id"] == before["instapaper_id"]


def test_a_reread_never_stamps_matter_id_on_a_foreign_file(vault):
    """matter_id is what marks a file as ours; stamping it on an Instapaper
    article would eventually invite the sync to take ownership of it."""
    original = write_instapaper_article(vault, "https://paulgraham.com/greatwork.html")

    run_sync(config_for(vault), client=FakeClient([make_item(status="archive")]))

    after, _ = mapping.parse_markdown(original.read_text(encoding="utf-8"))
    assert "matter_id" not in after
    assert "source" not in after, "it is still an Instapaper-era file"


def test_recording_a_reread_is_idempotent(vault):
    original = write_instapaper_article(vault, "https://paulgraham.com/greatwork.html")
    item = make_item(status="archive", updated_at="2026-05-12T09:00:00Z")

    first = run_sync(config_for(vault), client=FakeClient([item]))
    second = run_sync(config_for(vault, full=True), client=FakeClient([item]))

    after, _ = mapping.parse_markdown(original.read_text(encoding="utf-8"))
    assert after["matter_reread_at"] == ["2026-05-12"]
    assert first.rereads_recorded == 1
    assert second.rereads_recorded == 0, "the same read is not recorded twice"


def test_separate_rereads_accumulate(vault):
    original = write_instapaper_article(vault, "https://paulgraham.com/greatwork.html")

    run_sync(config_for(vault), client=FakeClient([
        make_item(status="archive", updated_at="2026-05-12T09:00:00Z")]))
    run_sync(config_for(vault, full=True), client=FakeClient([
        make_item(status="archive", updated_at="2026-07-04T09:00:00Z")]))

    after, _ = mapping.parse_markdown(original.read_text(encoding="utf-8"))
    assert after["matter_reread_at"] == ["2026-05-12", "2026-07-04"]
    assert after["matter_reread_count"] == 2


def test_a_queued_duplicate_is_not_recorded_as_a_reread(vault):
    """Sitting unread in a second app is not a reading event."""
    original = write_instapaper_article(vault, "https://paulgraham.com/greatwork.html")

    result = run_sync(config_for(vault, status="archive,queue"),
                      client=FakeClient([make_item(status="queue")]))

    after, _ = mapping.parse_markdown(original.read_text(encoding="utf-8"))
    assert result.duplicates == 1
    assert result.rereads_recorded == 0
    assert "matter_reread_at" not in after


def test_enrichment_on_the_matched_file_survives_a_reread(vault):
    original = write_instapaper_article(
        vault, "https://paulgraham.com/greatwork.html",
        extra='ai_summary: "Expensive to regenerate."\nai_topics:\n  - Craft\n',
    )

    run_sync(config_for(vault), client=FakeClient([make_item(status="archive")]))

    after, _ = mapping.parse_markdown(original.read_text(encoding="utf-8"))
    assert after["ai_summary"] == "Expensive to regenerate."
    assert after["ai_topics"] == ["Craft"]


def test_a_matched_file_with_broken_frontmatter_is_left_alone(vault):
    """Counted, logged, not touched -- the same rule as everywhere else."""
    original = vault / "2019-04-01 – Broken.md"
    original.write_text(
        '---\ntitle: "Broken"\noriginal_url: "https://paulgraham.com/greatwork.html"\n'
        "ai_summary: Rails 8: the reckoning\n---\n\nBody.\n",
        encoding="utf-8",
    )
    before = original.read_text(encoding="utf-8")

    result = run_sync(config_for(vault), client=FakeClient([make_item(status="archive")]))

    assert original.read_text(encoding="utf-8") == before
    assert result.duplicates == 1
    assert result.rereads_recorded == 0
    assert result.errors == 0, "an un-annotatable match is not a failure"


def test_rereads_can_be_turned_off(vault):
    original = write_instapaper_article(vault, "https://paulgraham.com/greatwork.html")
    before = original.read_text(encoding="utf-8")

    result = run_sync(config_for(vault, annotate_rereads=False),
                      client=FakeClient([make_item(status="archive")]))

    assert original.read_text(encoding="utf-8") == before
    assert result.duplicates == 1
    assert result.rereads_recorded == 0


def test_a_dry_run_never_records_a_reread(vault):
    original = write_instapaper_article(vault, "https://paulgraham.com/greatwork.html")
    before = original.read_text(encoding="utf-8")

    result = run_sync(config_for(vault, dry_run=True),
                      client=FakeClient([make_item(status="archive")]))

    assert original.read_text(encoding="utf-8") == before
    assert result.duplicates == 1
    assert result.rereads_recorded == 0


def test_files_can_be_written_flat_alongside_the_instapaper_articles(vault):
    run_sync(config_for(vault, subdir=""), client=FakeClient([make_item()]))
    assert (vault / "2026-03-30 – How to Do Great Work.md").exists()


# ---- second-round review regressions --------------------------------------

def test_adoption_wins_over_the_duplicate_check(vault):
    """The URL index can legitimately contain our own files.

    build_index.py walks the whole vault, so the Parquet index includes the
    matter/ subdir, and --subdir '' puts our files in the scanned tree. If the
    duplicate check ran first, an item with a lost manifest record would be
    filed as a duplicate of itself, get no `path`, and be re-skipped that way
    every night -- silently frozen, never syncing another highlight.
    """
    items = [make_item(item_id=f"itm_{n}", url=f"https://e.com/{n}", title=f"A{n}") for n in range(3)]
    run_sync(config_for(vault, subdir=""), client=FakeClient(items))
    (vault / ".matter_manifest.json").unlink()

    result = run_sync(config_for(vault, subdir="", full=True), client=FakeClient(items))

    assert result.updated == 3
    assert result.duplicates == 0, "an item must never be a duplicate of itself"
    assert len(list(vault.glob("*.md"))) == 3


def test_adopted_files_keep_their_original_dates(vault):
    """The manifest is gone, so the file itself is the only record of them."""
    run_sync(config_for(vault), client=FakeClient([make_item()]))
    written = next(iter((vault / "matter").glob("*.md")))
    before, _ = mapping.parse_markdown(written.read_text(encoding="utf-8"))

    (vault / ".matter_manifest.json").unlink()
    run_sync(config_for(vault, full=True),
             client=FakeClient([make_item(updated_at="2026-08-01T00:00:00Z")]))

    after, _ = mapping.parse_markdown(written.read_text(encoding="utf-8"))
    assert after["date_saved"] == before["date_saved"]
    assert after["date_archived"] == before["date_archived"]
    assert written.name.startswith(before["date_saved"]), "filename and frontmatter agree"


def test_a_vault_that_vanishes_mid_run_is_not_recreated(vault, tmp_path):
    """The vault is on an external drive. Recreating it at the mount point
    would leave a near-empty archive that a later index rebuild would compile
    over the real 17,637-row index."""
    import shutil

    mount = tmp_path / "Volumes" / "Extreme SSD"
    vault_on_drive = mount / "Instapaper-Archive"
    vault_on_drive.mkdir(parents=True)

    class Unplugged(FakeClient):
        def iter_annotations(self, item_id, *, page_size=100):
            if item_id == "itm_1":
                shutil.rmtree(mount)
            return super().iter_annotations(item_id, page_size=page_size)

    items = [make_item(item_id=f"itm_{n}", url=f"https://e.com/{n}") for n in range(3)]
    result = run_sync(config_for(vault_on_drive), client=Unplugged(items))

    assert not mount.exists(), "the mount point must not be recreated on the boot volume"
    assert result.errors > 0
    assert result.watermark_after is None


@pytest.mark.parametrize("prefix, label", [("﻿", "utf-8 BOM"), ("\n\n", "leading blank lines")])
def test_frontmatter_is_still_found_past_a_bom_or_blank_lines(vault, prefix, label):
    """Otherwise the file reads as having no frontmatter, and the enrichment in
    it is treated as nothing to preserve."""
    run_sync(config_for(vault), client=FakeClient([make_item()]))
    written = next(iter((vault / "matter").glob("*.md")))

    metadata, body = mapping.parse_markdown(written.read_text(encoding="utf-8"))
    metadata["ai_summary"] = "expensive to regenerate"
    written.write_text(prefix + mapping.dump_markdown(metadata, body), encoding="utf-8")

    result = run_sync(config_for(vault),
                      client=FakeClient([make_item(updated_at="2026-05-01T00:00:00Z")]))

    after, _ = mapping.parse_markdown(written.read_text(encoding="utf-8"))
    assert after["ai_summary"] == "expensive to regenerate", label
    assert result.errors == 0


def test_a_file_with_no_frontmatter_at_all_is_refused_not_overwritten(vault):
    """We wrote frontmatter into it; its absence means someone else changed it."""
    run_sync(config_for(vault), client=FakeClient([make_item()]))
    written = next(iter((vault / "matter").glob("*.md")))
    written.write_text("Just prose that replaced the whole file.\n", encoding="utf-8")

    result = run_sync(config_for(vault),
                      client=FakeClient([make_item(updated_at="2026-05-01T00:00:00Z")]))

    assert written.read_text(encoding="utf-8") == "Just prose that replaced the whole file.\n"
    assert result.errors == 1


def test_new_article_files_match_the_permissions_of_the_rest_of_the_vault(vault):
    run_sync(config_for(vault), client=FakeClient([make_item()]))
    written = next(iter((vault / "matter").glob("*.md")))
    assert written.stat().st_mode & 0o077, "0600 would leave the vault with two permission regimes"


def test_a_broken_file_is_still_recognised_as_ours_rather_than_duplicated(vault):
    """Manifest lost AND frontmatter broken: refuse, do not write a second copy."""
    run_sync(config_for(vault), client=FakeClient([make_item()]))
    written = next(iter((vault / "matter").glob("*.md")))
    written.write_text(
        '---\ntitle: "T"\nmatter_id: itm_abc123\nai_summary: Rails 8: the reckoning\n---\n\nBody.\n',
        encoding="utf-8",
    )
    (vault / ".matter_manifest.json").unlink()

    result = run_sync(config_for(vault, full=True), client=FakeClient([make_item()]))

    assert len(list((vault / "matter").glob("*.md"))) == 1, "no second copy"
    assert result.errors == 1


def test_dry_run_leaves_no_trace_in_the_vault_at_all(vault):
    """Not even the URL-index cache.

    Caching the vault scan is a pure win in a normal run, but --dry-run is what
    Adam runs to inspect before committing to anything, and a promise to write
    nothing has to be literal or it is not a promise he can act on.
    """
    write_instapaper_article(vault, "https://example.com/already-here")

    run_sync(config_for(vault, dry_run=True), client=FakeClient([make_item()]))

    leftovers = [p.name for p in vault.rglob("*") if p.name != "2019-04-01 – Same Article.md"]
    assert leftovers == [], f"a dry run left files behind: {leftovers}"


def test_a_dry_run_still_reports_how_much_re_reading_it_found(vault):
    """A dry run that reported zero re-reads would understate the very thing it
    is being run to measure."""
    write_instapaper_article(vault, "https://paulgraham.com/greatwork.html")

    result = run_sync(config_for(vault, dry_run=True),
                      client=FakeClient([make_item(status="archive")]))

    assert result.reread_candidates == 1
    assert result.rereads_recorded == 0, "counted, not written"


def test_re_read_candidates_are_counted_even_with_annotation_off(vault):
    write_instapaper_article(vault, "https://paulgraham.com/greatwork.html")

    result = run_sync(config_for(vault, annotate_rereads=False),
                      client=FakeClient([make_item(status="archive")]))

    assert result.reread_candidates == 1
    assert result.rereads_recorded == 0


# ---- round-3: the annotation writes to a foreign file, so it is fenced ----

def test_a_reread_never_writes_outside_the_vault(vault, tmp_path):
    """The Parquet index stores ABSOLUTE paths from whenever it was last built.

    A stale one can name a different vault, and `vault / "/abs/path"` collapses
    to the absolute path in pathlib, so a naive join is no protection.
    """
    outsider = tmp_path / "somewhere-else" / "2019-04-01 – Same Article.md"
    outsider.parent.mkdir(parents=True)
    outsider.write_text(
        '---\ntitle: "Same Article"\noriginal_url: "https://paulgraham.com/greatwork.html"\n---\n\nBody.\n',
        encoding="utf-8",
    )
    before = outsider.read_text(encoding="utf-8")

    class OutsideIndex(FakeClient):
        pass

    from matter.vaultindex import UrlIndex
    import matter.sync as sync_module

    real_build = sync_module.build_url_index
    try:
        sync_module.build_url_index = lambda *a, **k: UrlIndex(
            {"https://paulgraham.com/greatwork.html": str(outsider)}, source="test",
        )
        result = run_sync(config_for(vault), client=OutsideIndex([make_item(status="archive")]))
    finally:
        sync_module.build_url_index = real_build

    assert outsider.read_text(encoding="utf-8") == before, "a file outside the vault is untouchable"
    assert result.duplicates == 1
    assert result.rereads_recorded == 0


def test_a_reread_refuses_a_file_that_is_not_valid_utf8(vault):
    """Rewriting it would replace the undecodable bytes with substitutions.

    build_index and the enrichment pass both read with errors="replace" because
    they only produce derived data. This writes back, so it must not.
    """
    damaged = vault / "2019-04-01 – Damaged.md"
    damaged.write_bytes(
        b'---\ntitle: "Damaged"\noriginal_url: "https://paulgraham.com/greatwork.html"\n'
        b"---\n\nBody with a bad byte: \xff\xfe and more.\n"
    )
    before = damaged.read_bytes()

    result = run_sync(config_for(vault), client=FakeClient([make_item(status="archive")]))

    assert damaged.read_bytes() == before, "not one byte changed"
    assert result.duplicates == 1
    assert result.rereads_recorded == 0
    assert result.errors == 0


def test_a_reread_checks_the_matched_file_is_really_that_article(vault):
    """A stale index entry can name a path whose file is now a different article."""
    impostor = write_instapaper_article(
        vault, "https://example.com/a-completely-different-article",
        filename="2019-04-01 – Same Article.md",
    )
    before = impostor.read_text(encoding="utf-8")

    from matter.vaultindex import UrlIndex
    import matter.sync as sync_module

    real_build = sync_module.build_url_index
    try:
        # The index claims this path holds greatwork.html; the file disagrees.
        sync_module.build_url_index = lambda *a, **k: UrlIndex(
            {"https://paulgraham.com/greatwork.html": str(impostor)}, source="test",
        )
        result = run_sync(config_for(vault), client=FakeClient([make_item(status="archive")]))
    finally:
        sync_module.build_url_index = real_build

    assert impostor.read_text(encoding="utf-8") == before
    assert result.rereads_recorded == 0


def test_an_annotation_write_failure_is_not_an_item_error(vault, monkeypatch):
    """A lost note must not pin the watermark over somebody else's file."""
    write_instapaper_article(vault, "https://paulgraham.com/greatwork.html")

    import matter.sync as sync_module

    def explode(*args, **kwargs):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(sync_module, "atomic_write_text", explode)
    # The manifest save uses state.save(), which has its own guard; this only
    # affects the annotation path.
    result = run_sync(config_for(vault), client=FakeClient([make_item(status="archive")]))

    assert result.errors == 0, "an annotation failure is a lost note, not a failed sync"
    assert result.rereads_recorded == 0
    assert result.duplicates == 1


def test_reread_dates_carry_their_provenance(vault):
    """The same honesty date_saved_source gets: these are updated_at, not an
    observed reading timestamp."""
    original = write_instapaper_article(vault, "https://paulgraham.com/greatwork.html")

    run_sync(config_for(vault), client=FakeClient([make_item(status="archive")]))

    after, _ = mapping.parse_markdown(original.read_text(encoding="utf-8"))
    assert "updated_at" in after["matter_reread_source"]


# ---- round-4: the parquet is the real source of match locations ----------

def test_an_absolute_parquet_path_outside_the_vault_is_refused(vault, tmp_path):
    """The real index stores absolute paths for all 17,637 rows.

    Point --vault at a restored copy or a second drive and those paths still
    name the ORIGINAL vault, so the containment check is the only thing
    standing between a backfill and writing into a different archive.
    """
    pq = pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq_write

    other_vault = tmp_path / "a-different-archive"
    other_vault.mkdir()
    foreign = other_vault / "2019-04-01 – Same Article.md"
    foreign.write_text(
        '---\ntitle: "Same Article"\noriginal_url: "https://paulgraham.com/greatwork.html"\n'
        "instapaper_id: 12345\ndate_saved: 2019-04-01\n---\n\nThe original body.\n",
        encoding="utf-8",
    )
    before = foreign.read_text(encoding="utf-8")

    index_path = tmp_path / "archive_index.parquet"
    pq_write.write_table(
        pq.table({"url": ["https://paulgraham.com/greatwork.html"],
                  "file_path": [str(foreign)]}),
        str(index_path),
    )

    result = run_sync(config_for(vault, parquet_path=index_path),
                      client=FakeClient([make_item(status="archive")]))

    assert foreign.read_text(encoding="utf-8") == before, "a different archive was written to"
    assert result.duplicates == 1, "still recognised as already-present"
    assert result.rereads_recorded == 0


def test_a_relative_parquet_path_inside_the_vault_is_annotated(vault, tmp_path):
    """The containment check must not break the ordinary case."""
    pq = pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq_write

    inside = write_instapaper_article(vault, "https://paulgraham.com/greatwork.html")
    index_path = tmp_path / "archive_index.parquet"
    pq_write.write_table(
        pq.table({"url": ["https://paulgraham.com/greatwork.html"],
                  "file_path": [str(inside)]}),
        str(index_path),
    )

    result = run_sync(config_for(vault, parquet_path=index_path),
                      client=FakeClient([make_item(status="archive")]))

    assert result.rereads_recorded == 1
    after, _ = mapping.parse_markdown(inside.read_text(encoding="utf-8"))
    assert after["matter_reread_at"] == ["2026-03-30"]


def test_a_refusal_reason_is_recorded_in_the_manifest(vault):
    """A refusal has to be diagnosable from the data, not just a log line."""
    damaged = vault / "2019-04-01 – Damaged.md"
    damaged.write_bytes(
        b'---\ntitle: "Damaged"\noriginal_url: "https://paulgraham.com/greatwork.html"\n'
        b"---\n\nBody with a bad byte: \xff\xfe\n"
    )

    run_sync(config_for(vault), client=FakeClient([make_item(status="archive")]))

    record = SyncState.load(vault / ".matter_manifest.json").get_item("itm_abc123")
    assert record["reread_status"] == "encoding-damaged"
    assert record["reread_recorded"] is False


# ---- round-4: observed transitions ---------------------------------------

def test_a_first_run_admits_its_dates_are_a_fallback(vault):
    run_sync(config_for(vault, full=True), client=FakeClient([make_item()]))

    written = next(iter((vault / "matter").glob("*.md")))
    metadata, _ = mapping.parse_markdown(written.read_text(encoding="utf-8"))
    assert metadata["date_saved_source"].startswith("fallback")


def test_an_article_appearing_after_a_completed_run_is_an_observed_transition(vault):
    """Steady state: it was not in the archive last night, so it was read since."""
    first = make_item(item_id="itm_1", url="https://e.com/1", title="Already here")
    run_sync(config_for(vault, full=True), client=FakeClient([first]))

    fresh = make_item(item_id="itm_2", url="https://e.com/2", title="Newly read",
                      updated_at="2026-08-11T06:00:00Z")
    run_sync(config_for(vault, full=True), client=FakeClient([first, fresh]))

    written = vault / "matter" / "2026-08-11 – Newly read.md"
    metadata, _ = mapping.parse_markdown(written.read_text(encoding="utf-8"))
    assert metadata["date_saved_source"] == mapping.DATE_SOURCE_OBSERVED
    assert metadata["date_archived"] == "2026-08-11"


def test_sync_mode_does_not_claim_to_have_observed_anything(vault):
    """--sync filters the listing by updated_since, so absence proves nothing."""
    first = make_item(item_id="itm_1", url="https://e.com/1")
    run_sync(config_for(vault, full=True), client=FakeClient([first]))

    fresh = make_item(item_id="itm_2", url="https://e.com/2", title="Newly read",
                      updated_at="2026-08-11T06:00:00Z")
    run_sync(config_for(vault), client=FakeClient([fresh]))

    written = vault / "matter" / "2026-08-11 – Newly read.md"
    metadata, _ = mapping.parse_markdown(written.read_text(encoding="utf-8"))
    assert metadata["date_saved_source"].startswith("fallback")


def test_an_exception_escaping_the_loop_still_saves_what_was_written(vault):
    """The `finally` save, specifically.

    The other crash test raises inside one ITEM, which the per-item handler
    catches, so the run finishes normally and the end-of-run save covers it --
    the finally block is never exercised. This raises out of the item generator
    instead, which is what a 500-after-retries or a SIGTERM actually looks like.
    Without the finally save the files are on disk with no manifest record, and
    the next run writes a second copy of every one.
    """
    items = [make_item(item_id=f"itm_{n}", url=f"https://e.com/{n}", title=f"Article {n}")
             for n in range(1, 6)]

    class DiesMidListing(FakeClient):
        def iter_items(self, **kwargs):
            self.list_calls.append(kwargs)
            for item in items[:2]:
                yield dict(item)
            raise RuntimeError("HTTP 500 after retries, on page 2")

    with pytest.raises(RuntimeError):
        run_sync(config_for(vault), client=DiesMidListing(items))

    written = sorted(p.name for p in (vault / "matter").glob("*.md"))
    assert len(written) == 2

    state = SyncState.load(vault / ".matter_manifest.json")
    assert state.get_item("itm_1")["path"], "the manifest knows about what reached disk"
    assert state.get_item("itm_2")["path"]
    assert state.watermark is None, "and the watermark did not advance"


def test_a_chunked_backfill_never_claims_to_have_witnessed_a_transition(vault):
    """The documented backfill is `--full --max-items 200`, repeated.

    That leaves the manifest full of items while most of the library has never
    been listed. Treating a non-empty manifest as proof of a completed listing
    labelled every not-yet-reached article as a transition nobody witnessed --
    4 of 6 in the original repro, and it would have been roughly 1,030 of 1,230
    on the real library.
    """
    items = [make_item(item_id=f"itm_{n}", url=f"https://e.com/{n}", title=f"A{n}",
                       updated_at="2022-08-15T10:00:00Z") for n in range(6)]

    for _ in range(3):
        run_sync(config_for(vault, full=True, max_items=2), client=FakeClient(items))

    sources = [
        mapping.parse_markdown(f.read_text(encoding="utf-8"))[0]["date_saved_source"]
        for f in (vault / "matter").glob("*.md")
    ]
    assert len(sources) == 6
    assert all(s.startswith("fallback") for s in sources), \
        "an article the run had not reached yet was never observed entering the archive"


def test_a_completed_full_run_is_what_licenses_the_claim(vault):
    """And once one has completed, genuinely new articles are labelled honestly."""
    old = [make_item(item_id=f"itm_{n}", url=f"https://e.com/{n}", title=f"A{n}",
                     updated_at="2022-08-15T10:00:00Z") for n in range(3)]
    run_sync(config_for(vault, full=True), client=FakeClient(old))

    state = SyncState.load(vault / ".matter_manifest.json")
    assert state.full_listing_completed_at, "a clean full run records that it listed everything"

    fresh = make_item(item_id="itm_new", url="https://e.com/new", title="Newly read",
                      updated_at="2026-08-11T06:00:00Z")
    run_sync(config_for(vault, full=True), client=FakeClient(old + [fresh]))

    metadata, _ = mapping.parse_markdown(
        (vault / "matter" / "2026-08-11 – Newly read.md").read_text(encoding="utf-8"))
    assert metadata["date_saved_source"] == mapping.DATE_SOURCE_OBSERVED

    # And the backfilled ones keep their honest fallback labels.
    for n in range(3):
        older, _ = mapping.parse_markdown(
            (vault / "matter" / f"2022-08-15 – A{n}.md").read_text(encoding="utf-8"))
        assert older["date_saved_source"].startswith("fallback")


def test_a_run_with_errors_does_not_license_the_claim(vault):
    """A run that failed on an item did not cleanly list everything either."""
    class OneBadItem(FakeClient):
        def iter_annotations(self, item_id, *, page_size=100):
            if item_id == "itm_1":
                raise RuntimeError("boom")
            return super().iter_annotations(item_id, page_size=page_size)

    items = [make_item(item_id=f"itm_{n}", url=f"https://e.com/{n}") for n in range(3)]
    run_sync(config_for(vault, full=True), client=OneBadItem(items))

    assert SyncState.load(vault / ".matter_manifest.json").full_listing_completed_at is None


def test_a_queued_item_is_never_labelled_as_having_entered_the_archive(vault):
    """Under --status archive,queue a new queue item is also appearing for the
    first time -- but it has not been read, and saying it was witnessed
    entering the archive is exactly the lie the field exists to prevent."""
    seed = make_item(item_id="itm_seed", url="https://e.com/seed", status="archive")
    run_sync(config_for(vault, full=True, status="archive,queue"), client=FakeClient([seed]))

    queued = make_item(item_id="itm_q", url="https://e.com/q", title="Never read",
                       status="queue", updated_at="2026-08-11T06:00:00Z")
    run_sync(config_for(vault, full=True, status="archive,queue"),
             client=FakeClient([seed, queued]))

    metadata, _ = mapping.parse_markdown(
        (vault / "matter" / "2026-08-11 – Never read.md").read_text(encoding="utf-8"))
    assert metadata["date_saved_source"].startswith("fallback")
    assert "date_archived" not in metadata


def test_a_queue_only_listing_does_not_license_claims_about_the_archive(vault):
    """A completed --status queue run says nothing about what is in the archive."""
    run_sync(config_for(vault, full=True, status="queue"),
             client=FakeClient([make_item(item_id="itm_q", url="https://e.com/q", status="queue")]))

    state = SyncState.load(vault / ".matter_manifest.json")
    assert state.full_listing_completed_at and state.full_listing_status == "queue"

    archived = make_item(item_id="itm_a", url="https://e.com/a", title="Long archived",
                         status="archive", updated_at="2022-08-15T10:00:00Z")
    run_sync(config_for(vault, full=True), client=FakeClient([archived]))

    metadata, _ = mapping.parse_markdown(
        (vault / "matter" / "2022-08-15 – Long archived.md").read_text(encoding="utf-8"))
    assert metadata["date_saved_source"].startswith("fallback"), \
        "the archive was never listed before, so nothing about it was witnessed"


def test_an_already_recorded_reread_is_not_re_read_from_disk_every_night(vault):
    """998 matched files on an external drive, opened nightly to learn nothing."""
    original = write_instapaper_article(vault, "https://paulgraham.com/greatwork.html")
    item = make_item(status="archive", updated_at="2026-05-12T09:00:00Z")

    first = run_sync(config_for(vault, full=True), client=FakeClient([item]))
    assert first.rereads_recorded == 1

    mtime_before = original.stat().st_mtime_ns
    second = run_sync(config_for(vault, full=True), client=FakeClient([item]))

    assert second.rereads_recorded == 0
    assert original.stat().st_mtime_ns == mtime_before, "the file was not rewritten"
    record = SyncState.load(vault / ".matter_manifest.json").get_item("itm_abc123")
    assert record["reread_status"] == "already-recorded", "the manifest short-circuit ran, not a file read"
