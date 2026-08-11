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

def test_the_inbox_is_excluded_by_default(vault):
    """Matter's inbox is unsaved discovery content, not reading history."""
    client = FakeClient([make_item()])
    run_sync(config_for(vault), client=client)
    assert client.list_calls[0]["status"] == "archive,queue"


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
