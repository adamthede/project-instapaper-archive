"""End-to-end sync behaviour, driven by a fake Matter client."""

import pytest
from conftest import FakeClient, make_annotation, make_item

from matter import mapping
from matter.errors import VaultNotFoundError
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


def test_an_item_with_no_id_is_counted_as_an_error_not_written(vault):
    client = FakeClient([{"object": "item", "title": "no id", "url": "https://e.com/x"}])
    result = run_sync(config_for(vault), client=client)
    assert result.errors == 1
    assert result.new == 0


def test_progress_is_saved_as_it_goes_so_a_crash_does_not_lose_the_batch(vault):
    class CrashingClient(FakeClient):
        def iter_annotations(self, item_id, *, page_size=100):
            if item_id == "itm_3":
                raise KeyboardInterrupt("simulated interruption")
            return super().iter_annotations(item_id, page_size=page_size)

    client = CrashingClient([
        make_item(item_id=f"itm_{n}", url=f"https://e.com/{n}") for n in range(1, 5)
    ])
    with pytest.raises(KeyboardInterrupt):
        run_sync(config_for(vault, save_every=1), client=client)

    state = SyncState.load(vault / ".matter_manifest.json")
    assert state.get_item("itm_1") and state.get_item("itm_1")["path"]
    assert state.watermark is None


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
