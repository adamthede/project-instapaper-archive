"""The dashboard must survive Matter-era rows.

Matter articles arrive without the ai_* enrichment fields (enrichment runs
later, or never, for podcasts), without an instapaper_id, and sometimes without
a word count. The risk this file covers is a dashboard that renders 17,637
Instapaper rows happily and then dies on the first Matter row.

The corpus here is synthetic and deliberately nasty: one enriched Instapaper
article, one legacy row with no URL, and two Matter rows with no enrichment at
all.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest
from conftest import FakeClient, make_annotation, make_item

from matter.sync import SyncConfig, run_sync

REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_INDEX = REPO_ROOT / "scripts" / "core" / "build_index.py"
DASHBOARD_APP = REPO_ROOT / "dashboard" / "app.py"


@pytest.fixture
def merged_vault(tmp_path):
    """A vault holding all three eras, with Matter written by the real sync."""
    vault = tmp_path / "vault"
    vault.mkdir()

    (vault / "2019-04-01 – An Enriched Instapaper Article.md").write_text(
        "---\n"
        'title: "An Enriched Instapaper Article"\n'
        'original_url: "https://example.com/instapaper-era"\n'
        "instapaper_id: 998877\n"
        "date_saved: 2019-04-01\n"
        "date_archived: 2019-04-05\n"
        "ai_topics:\n  - Technology\n  - Craft\n"
        'ai_sentiment: "Positive"\n'
        'ai_summary: "A summary from the Gemini enrichment pass."\n'
        "ai_people:\n  - Ada Lovelace\n"
        "ai_orgs:\n  - Acme Corp\n"
        "ai_locations:\n  - Nashville\n"
        "ai_concepts:\n  - Systems Thinking\n"
        'ai_emotion: "Informative"\n'
        "---\n\n" + ("word " * 400) + "\n",
        encoding="utf-8",
    )

    (vault / "1989-10-22 – A Legacy Clipping.md").write_text(
        "---\n"
        'title: "A Legacy Clipping"\n'
        'source: "legacy_pdf"\n'
        'date_published: "1989-10-22"\n'
        'date_imported: "2025-11-20"\n'
        "---\n\n" + ("word " * 200) + "\n",
        encoding="utf-8",
    )

    client = FakeClient(
        [
            make_item(item_id="itm_new1", url="https://example.com/matter-one",
                      title="A Matter Article", status="archive",
                      updated_at="2026-07-01T12:00:00Z"),
            # No word count, no author, still in the queue: the awkward shape.
            make_item(item_id="itm_new2", url="https://example.com/matter-two",
                      title="An Unread Matter Podcast", status="queue",
                      content_type="podcast", word_count=None, author=None,
                      tags=[], updated_at="2026-07-02T12:00:00Z"),
        ],
        annotations={"itm_new1": [make_annotation(item_id="itm_new1", note="a note")]},
    )
    result = run_sync(
        SyncConfig(vault_path=vault, subdir="matter", parquet_path=None, heartbeat_path=None),
        client=client,
    )
    assert result.new == 2, "fixture setup failed"
    return vault


@pytest.fixture
def merged_index(merged_vault, tmp_path, monkeypatch):
    """Run the real build_index.py over the merged vault, into a temp repo copy."""
    repo = tmp_path / "repo"
    (repo / "scripts" / "core").mkdir(parents=True)
    (repo / "dashboard").mkdir(parents=True)
    (repo / "data").mkdir(parents=True)
    shutil.copy(BUILD_INDEX, repo / "scripts" / "core" / "build_index.py")
    shutil.copy(DASHBOARD_APP, repo / "dashboard" / "app.py")

    completed = subprocess.run(
        [sys.executable, str(repo / "scripts" / "core" / "build_index.py")],
        env={"INSTAPAPER_VAULT_PATH": str(merged_vault), "PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
        capture_output=True, text=True, cwd=str(repo),
    )
    assert completed.returncode == 0, completed.stderr
    index_path = repo / "data" / "archive_index.parquet"
    assert index_path.exists(), completed.stdout
    return repo, index_path


def test_build_index_records_all_three_eras(merged_index):
    _, index_path = merged_index
    df = pd.read_parquet(index_path)

    assert len(df) == 4
    assert set(df["source"]) == {"instapaper", "legacy_pdf", "matter"}
    assert set(df.loc[df["source"] == "matter", "matter_id"]) == {"itm_new1", "itm_new2"}


def test_matter_rows_carry_the_dates_the_dashboard_charts_on(merged_index):
    _, index_path = merged_index
    df = pd.read_parquet(index_path)
    matter = df[df["source"] == "matter"].set_index("title")

    # This article carries a highlight made on 2026-03-30 while its updated_at
    # sits at 2026-07-01 -- three months of drift from some later touch. The
    # highlight is when it was actually being read, so that is the date used.
    assert str(matter.loc["A Matter Article", "date_archived"].date()) == "2026-03-30"
    assert pd.isna(matter.loc["An Unread Matter Podcast", "date_archived"]), "queued items are unread"
    assert str(matter.loc["An Unread Matter Podcast", "date_saved"].date()) == "2026-07-02"


def test_word_count_falls_back_to_counting_the_body_when_matter_has_none(merged_index):
    _, index_path = merged_index
    df = pd.read_parquet(index_path)
    podcast = df[df["matter_id"] == "itm_new2"].iloc[0]
    assert podcast["word_count"] > 0


def test_matter_rows_have_no_enrichment_and_that_is_expected(merged_index):
    _, index_path = merged_index
    df = pd.read_parquet(index_path)
    matter = df[df["source"] == "matter"].iloc[0]
    assert list(matter["topics"]) == []
    assert matter["summary"] is None


def test_era_labels_cover_every_row(merged_index):
    repo, index_path = merged_index
    sys.path.insert(0, str(repo / "dashboard"))
    try:
        import app  # noqa: PLC0415
        eras = app.derive_era(pd.read_parquet(index_path))
    finally:
        sys.path.remove(str(repo / "dashboard"))
        sys.modules.pop("app", None)

    assert set(eras) == {"Instapaper", "Legacy files", "Matter"}
    assert "Unknown" not in set(eras)


def test_era_labels_work_on_an_index_built_before_the_source_column_existed():
    """Adam's current Parquet has no `source` column until he rebuilds it."""
    repo_dashboard = str(REPO_ROOT / "dashboard")
    sys.path.insert(0, repo_dashboard)
    try:
        import app  # noqa: PLC0415
        legacy_shape = pd.DataFrame({"instapaper_id": [1.0, float("nan")], "title": ["a", "b"]})
        eras = app.derive_era(legacy_shape)
    finally:
        sys.path.remove(repo_dashboard)
        sys.modules.pop("app", None)

    assert list(eras) == ["Instapaper", "Unknown"]


@pytest.fixture
def dashboard_module():
    """Import dashboard/app.py directly, for the pure helpers."""
    repo_dashboard = str(REPO_ROOT / "dashboard")
    sys.path.insert(0, repo_dashboard)
    try:
        import app  # noqa: PLC0415
        yield app
    finally:
        sys.path.remove(repo_dashboard)
        sys.modules.pop("app", None)


def test_review_identity_prefers_instapaper_id_so_old_history_still_matches(dashboard_module):
    """Keys are strings, but an Instapaper row still resolves to its own id."""
    assert dashboard_module.review_id({"instapaper_id": 12345.0, "file_path": "/x.md"}) == "12345"
    # Saved history holds the float; normalizing on load makes the two meet.
    assert dashboard_module.normalize_review_key(12345.0) == "12345"


def test_review_keys_are_all_strings_so_the_history_column_stays_writable(dashboard_module):
    """Mixing floats and strings gives object dtype, which pyarrow refuses."""
    keys = [
        dashboard_module.review_id({"instapaper_id": 12345.0}),
        dashboard_module.review_id({"instapaper_id": float("nan"), "matter_id": "itm_abc"}),
        dashboard_module.review_id({"instapaper_id": None, "file_path": "/legacy/x.md"}),
    ]
    assert all(isinstance(key, str) for key in keys), keys


def test_a_review_of_a_matter_article_can_actually_be_saved(dashboard_module, tmp_path, monkeypatch):
    """The regression: saving a review used to raise ArrowInvalid on mixed dtypes."""
    monkeypatch.setattr(dashboard_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(dashboard_module, "REVIEW_HISTORY_PATH", tmp_path / "review_history.parquet")

    # History as it exists today: a float article_id from the Instapaper era.
    pd.DataFrame([{
        "article_id": 1270847868.0, "last_reviewed": pd.Timestamp("2026-01-01"),
        "next_review": pd.Timestamp("2026-02-01"), "ease_factor": 2.5,
        "interval_days": 30, "review_count": 1,
    }]).to_parquet(tmp_path / "review_history.parquet", index=False)

    history = dashboard_module.load_review_history()
    assert history["article_id"].iloc[0] == "1270847868", "existing history is migrated, not orphaned"

    matter_key = dashboard_module.review_id({"instapaper_id": float("nan"), "matter_id": "itm_abc"})
    history = dashboard_module.update_review_record(matter_key, 1, history)
    dashboard_module.save_review_history(history)  # used to raise

    reloaded = dashboard_module.load_review_history()
    assert set(reloaded["article_id"]) == {"1270847868", "itm_abc"}


def test_review_identity_falls_back_for_rows_with_no_instapaper_id(dashboard_module):
    """Matter and the ~10,560 legacy rows have none; they used to key on NaN."""
    assert dashboard_module.review_id(
        {"instapaper_id": float("nan"), "matter_id": "itm_abc", "file_path": "/x.md"}
    ) == "itm_abc"
    assert dashboard_module.review_id(
        {"instapaper_id": None, "matter_id": None, "file_path": "/legacy/x.md"}
    ) == "/legacy/x.md"


def test_review_identity_is_unique_across_a_merged_corpus(merged_index, dashboard_module):
    _, index_path = merged_index
    ids = dashboard_module.review_id_series(pd.read_parquet(index_path))

    assert ids.notna().all(), "every article needs an identity to be reviewable"
    assert ids.nunique() == len(ids), "two articles must never share a review record"


@pytest.mark.parametrize("page", [
    "The Quantified Reader",
    "Content Intelligence",
    "Network & Entities",
    "Concept Explorer",
    "Archive Explorer",
    "Trends Over Time",
    "Heatmap Analysis",
    "Spaced Review",
])
def test_every_dashboard_page_renders_on_the_merged_corpus(merged_index, page):
    """The regression this exists for: a page that works on Instapaper rows and
    dies on a Matter row with no enrichment and no instapaper_id."""
    AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
    repo, _ = merged_index

    harness = AppTest.from_file(str(repo / "dashboard" / "app.py"), default_timeout=120)
    harness.run()
    assert not harness.exception, f"app failed to start: {harness.exception}"

    harness.sidebar.radio[0].set_value(page).run()
    assert not harness.exception, f"{page} raised: {harness.exception}"


def test_the_era_filter_narrows_the_corpus(merged_index):
    AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
    repo, _ = merged_index

    harness = AppTest.from_file(str(repo / "dashboard" / "app.py"), default_timeout=120)
    harness.run()
    assert not harness.exception

    era_filter = harness.sidebar.multiselect[0]
    assert set(era_filter.value) == {"Instapaper", "Legacy files", "Matter"}

    era_filter.set_value(["Matter"]).run()
    assert not harness.exception, f"filtering to Matter alone raised: {harness.exception}"
    assert any("Articles Archived" in str(metric.label) for metric in harness.metric)


# ---- read-vs-saved semantics ----------------------------------------------

def _frame(rows):
    """A minimal index-shaped frame for date_read tests."""
    return pd.DataFrame([
        {"source": s, "date_archived": pd.Timestamp(a) if a else pd.NaT,
         "date_saved": pd.Timestamp(v) if v else pd.NaT}
        for s, a, v in rows
    ])


def test_instapaper_rows_still_fall_back_to_date_saved(dashboard_module):
    """Load-bearing: 11,326 of 17,637 rows have no archive date at all.

    The legacy import had none to record, so dropping the fallback would empty
    most of the archive out of every chart.
    """
    df = _frame([
        ("instapaper", None, "2019-04-01"),
        ("legacy_pdf", None, "1989-10-22"),
        ("instapaper", "2019-04-05", "2019-04-01"),
    ])
    read = dashboard_module.derive_date_read(df)

    assert str(read.iloc[0].date()) == "2019-04-01", "Instapaper fallback still works"
    assert str(read.iloc[1].date()) == "1989-10-22", "legacy fallback still works"
    assert str(read.iloc[2].date()) == "2019-04-05", "an archive date still wins"


def test_a_matter_row_with_no_archive_date_never_gets_a_read_date(dashboard_module):
    """Matter says explicitly whether something was read. An unread article
    dated by when it was saved would assert a read that never happened."""
    df = _frame([
        ("matter", None, "2026-07-02"),       # queued: saved, not read
        ("matter", "2026-07-01", "2026-07-01"),  # archived: read
    ])
    read = dashboard_module.derive_date_read(df)

    assert pd.isna(read.iloc[0]), "a queued Matter article has no read date"
    assert str(read.iloc[1].date()) == "2026-07-01"


def test_the_two_eras_are_handled_differently_in_one_frame(dashboard_module):
    """Both rules have to hold simultaneously, which is the whole point."""
    df = _frame([
        ("instapaper", None, "2019-04-01"),
        ("matter", None, "2026-07-02"),
    ])
    read = dashboard_module.derive_date_read(df)

    assert not pd.isna(read.iloc[0])
    assert pd.isna(read.iloc[1])


def test_date_read_survives_an_index_with_no_source_column(dashboard_module):
    """Adam's current Parquet has no `source` column until he rebuilds it."""
    df = pd.DataFrame([{"date_archived": pd.NaT, "date_saved": pd.Timestamp("2019-04-01")}])
    read = dashboard_module.derive_date_read(df)
    assert str(read.iloc[0].date()) == "2019-04-01"


def test_an_unread_matter_row_appears_in_no_dashboard_surface(merged_vault, tmp_path, monkeypatch):
    """End to end: a queue-status Matter article must not reach any page.

    Every page renders from the same date-filtered frame, so being held out of
    that frame is what "appears nowhere" means. The sidebar counts it instead,
    so held-out never reads as lost.
    """
    AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
    import shutil
    import subprocess

    # Add an unread Matter article to the merged vault.
    (merged_vault / "matter" / "2026-07-09 – An Unread Article.md").write_text(
        "---\n"
        'title: "An Unread Article"\n'
        'original_url: "https://example.com/never-read"\n'
        'matter_id: "itm_unread"\n'
        'source: "matter"\n'
        'date_saved: "2026-07-09"\n'
        'matter_status: "queue"\n'
        "---\n\n" + ("word " * 100) + "\n",
        encoding="utf-8",
    )

    repo = tmp_path / "repo2"
    (repo / "scripts" / "core").mkdir(parents=True)
    (repo / "dashboard").mkdir(parents=True)
    (repo / "data").mkdir(parents=True)
    shutil.copy(BUILD_INDEX, repo / "scripts" / "core" / "build_index.py")
    shutil.copy(DASHBOARD_APP, repo / "dashboard" / "app.py")
    completed = subprocess.run(
        [sys.executable, str(repo / "scripts" / "core" / "build_index.py")],
        env={"INSTAPAPER_VAULT_PATH": str(merged_vault), "PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
        capture_output=True, text=True, cwd=str(repo),
    )
    assert completed.returncode == 0, completed.stderr

    df = pd.read_parquet(repo / "data" / "archive_index.parquet")
    assert (df["matter_id"] == "itm_unread").sum() == 1, "it is in the index"

    harness = AppTest.from_file(str(repo / "dashboard" / "app.py"), default_timeout=120)
    harness.run()
    assert not harness.exception

    captions = " ".join(str(c.value) for c in harness.sidebar.caption)
    # Two, not one: the merged-corpus fixture already held a queue-status Matter
    # podcast, and before this change the fillna handed it a read date equal to
    # its saved date. That is exactly the corruption being fixed here.
    assert "2 saved but unread" in captions, "the exclusion is stated, not silent"
    assert "Loaded: 3 read articles" in captions, "5 in the index, 3 of them read"

    # The read timeline no longer ends on an unread article's saved date.
    assert "2026-07-09" not in captions, "the unread article's saved date is not a read date"
    assert "2026-03-30" in captions, "the newest read date is the highlight-derived one"
