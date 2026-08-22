"""The vault holds this pipeline's own output as well as articles.

synthesis/ holds weekly digests ABOUT the archive. Indexing them made 854
digests masquerade as articles (source "unknown", ~440 words each) and
inflated every count on the site by 854 articles / 374,241 words.
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "core"))
import build_index


@pytest.fixture
def vault(tmp_path, monkeypatch):
    (tmp_path / "Archived").mkdir()
    (tmp_path / "matter").mkdir()
    (tmp_path / "synthesis").mkdir()

    def article(path, title, url):
        path.write_text(
            f'---\ntitle: "{title}"\noriginal_url: "{url}"\n'
            f'date_archived: 2026-08-01\nword_count: 900\n---\n\nBody.\n',
            encoding="utf-8")

    article(tmp_path / "Archived" / "a.md", "Real Instapaper Article", "https://x.com/a")
    article(tmp_path / "matter" / "b.md", "Real Matter Article", "https://x.com/b")
    # A weekly digest: this pipeline's own output, living in the vault.
    (tmp_path / "synthesis" / "2026-W33.md").write_text(
        '---\nweek: 2026-W33\ntitle: "2026-W33"\narticle_count: 18\n'
        'total_words: 72376\n---\n\nThis week the reading traversed...\n',
        encoding="utf-8")

    monkeypatch.setattr(build_index, "VAULT_PATH", tmp_path)
    monkeypatch.setattr(build_index, "DATA_DIR", tmp_path / "out")
    monkeypatch.setattr(build_index, "INDEX_PATH", tmp_path / "out" / "i.parquet")
    return tmp_path


def test_weekly_digests_are_not_indexed_as_articles(vault):
    build_index.build_index()
    df = pd.read_parquet(vault / "out" / "i.parquet")
    assert len(df) == 2, f"expected the 2 real articles, got {len(df)}"
    assert "2026-W33" not in set(df["title"].astype(str))
    assert not df["file_path"].astype(str).str.contains("/synthesis/").any()


def test_real_articles_in_every_other_subdir_still_index(vault):
    build_index.build_index()
    df = pd.read_parquet(vault / "out" / "i.parquet")
    assert set(df["title"]) == {"Real Instapaper Article", "Real Matter Article"}


def test_the_exclusion_is_by_directory_not_filename(vault):
    # A real article whose name merely mentions synthesis must survive.
    (vault / "Archived" / "on-synthesis.md").write_text(
        '---\ntitle: "Notes on Synthesis"\noriginal_url: "https://x.com/s"\n'
        'date_archived: 2026-08-02\nword_count: 500\n---\n\nBody.\n', encoding="utf-8")
    build_index.build_index()
    df = pd.read_parquet(vault / "out" / "i.parquet")
    assert "Notes on Synthesis" in set(df["title"].astype(str))
    assert len(df) == 3
