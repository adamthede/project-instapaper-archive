"""Index-layer article dedupe: same-id collapse and matter-supersedes rules."""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "core"))
from build_index import dedupe_articles


def make_df(rows):
    base = {"instapaper_id": None, "matter_id": None, "source": "instapaper",
            "title": "T", "word_count": 100, "summary": "s",
            "date_saved": pd.NaT, "date_archived": pd.NaT}
    df = pd.DataFrame([{**base, **r} for r in rows])
    df["date_saved"] = pd.to_datetime(df["date_saved"])
    df["date_archived"] = pd.to_datetime(df["date_archived"])
    return df


def test_same_instapaper_id_keeps_the_archived_copy():
    df = make_df([
        {"instapaper_id": 1.0, "title": "Aggregation Theory",
         "date_saved": "2023-12-11"},                       # exporter copy, no archive date
        {"instapaper_id": 1.0, "title": "Aggregation Theory",
         "date_saved": "2023-12-12", "date_archived": "2023-12-17"},  # bulk-import copy
    ])
    out = dedupe_articles(df)
    assert len(out) == 1
    assert str(out.iloc[0]["date_archived"])[:10] == "2023-12-17"


def test_same_id_tiebreaks_on_enrichment_then_length():
    df = make_df([
        {"instapaper_id": 2.0, "summary": "", "word_count": 900,
         "date_archived": "2024-01-01"},
        {"instapaper_id": 2.0, "summary": "enriched", "word_count": 100,
         "date_archived": "2024-01-01"},
    ])
    out = dedupe_articles(df)
    assert len(out) == 1
    assert out.iloc[0]["summary"] == "enriched"


def test_matter_supersedes_near_dated_instapaper_copy():
    df = make_df([
        {"source": "matter", "title": "Pushed Article",
         "date_archived": "2023-12-10"},
        {"source": "instapaper", "instapaper_id": 3.0, "title": "Pushed Article",
         "date_archived": "2023-12-14"},
    ])
    out = dedupe_articles(df)
    assert len(out) == 1
    assert out.iloc[0]["source"] == "matter"


def test_same_title_far_apart_is_a_real_reread_and_both_survive():
    df = make_df([
        {"source": "matter", "title": "Classic Essay",
         "date_archived": "2025-06-01"},
        {"source": "instapaper", "instapaper_id": 4.0, "title": "Classic Essay",
         "date_archived": "2019-03-01"},
    ])
    out = dedupe_articles(df)
    assert len(out) == 2


def test_distinct_ids_and_null_ids_are_untouched():
    df = make_df([
        {"instapaper_id": 5.0, "title": "A", "date_archived": "2024-01-01"},
        {"instapaper_id": 6.0, "title": "B", "date_archived": "2024-01-02"},
        {"source": "legacy_pdf", "title": "C", "date_saved": "2005-01-01"},
        {"source": "legacy_pdf", "title": "C", "date_saved": "2006-01-01"},
    ])
    out = dedupe_articles(df)
    assert len(out) == 4  # legacy same-title rows are NOT collapsed


def test_empty_titles_never_match_across_sources():
    df = make_df([
        {"source": "matter", "title": "", "date_archived": "2023-12-10"},
        {"source": "instapaper", "instapaper_id": 7.0, "title": "",
         "date_archived": "2023-12-11"},
    ])
    out = dedupe_articles(df)
    assert len(out) == 2
