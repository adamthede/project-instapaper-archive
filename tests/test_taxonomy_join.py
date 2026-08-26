"""Tests for Phase C's taxonomy join.

Two failure modes drive most of what follows.

The first is a join that silently matches nothing — the columns appear, every
value is empty, the build prints a cheerful report, and the rankability gate
quietly never turns on. So the tests assert on what LANDED, not just on shape.

The second is subtler and already happened once during development: routing
canonical output by source field produced columns that individually miss the
40% bar, reproducing the exact split Phase A settled by pooling. The pooled
column is therefore pinned as the vocabulary, with the per-field columns
demoted to provenance.
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "core"))

import taxonomy  # noqa: E402

TAXONOMY_PATH = REPO_ROOT / "data" / "taxonomy" / "v1.yaml"


def write_tax(tmp_path, entries, excluded=None, version=1):
    import yaml
    doc = {"version": version, "entries": entries}
    if excluded is not None:
        doc["excluded_aliases"] = excluded
    p = tmp_path / "tax.yaml"
    p.write_text(yaml.safe_dump(doc, sort_keys=False))
    return p


def simple(tmp_path):
    return write_tax(tmp_path, [
        {"name": "Social Media", "axis": "topic", "definition": "d",
         "aliases": ["Social Media", "Social Networking"]},
        {"name": "Privacy", "axis": "concept", "definition": "d",
         "aliases": ["Privacy", "Data Privacy"]},
    ])


# --- loading --------------------------------------------------------------

def test_an_alias_owned_by_two_entries_is_refused(tmp_path):
    p = write_tax(tmp_path, [
        {"name": "A", "axis": "topic", "definition": "d", "aliases": ["shared"]},
        {"name": "B", "axis": "topic", "definition": "d", "aliases": ["shared"]},
    ])
    with pytest.raises(taxonomy.TaxonomyError, match="maps to both"):
        taxonomy.load(p)


def test_aliases_that_collide_only_when_folded_are_refused(tmp_path):
    """Without this the article gets whichever entry happened to load last —
    a silent, order-dependent misclassification."""
    p = write_tax(tmp_path, [
        {"name": "A", "axis": "topic", "definition": "d", "aliases": ["Social Media"]},
        {"name": "B", "axis": "topic", "definition": "d", "aliases": ["social media"]},
    ])
    with pytest.raises(taxonomy.TaxonomyError, match="collide case-insensitively"):
        taxonomy.load(p)


def test_a_string_cannot_be_both_excluded_and_owned(tmp_path):
    p = write_tax(tmp_path, [
        {"name": "A", "axis": "topic", "definition": "d", "aliases": ["x"]},
    ], excluded=["x"])
    with pytest.raises(taxonomy.TaxonomyError, match="contradicts itself"):
        taxonomy.load(p)


def test_an_empty_taxonomy_is_refused(tmp_path):
    with pytest.raises(taxonomy.TaxonomyError, match="no entries"):
        taxonomy.load(write_tax(tmp_path, []))


# --- every malformation must be SURVIVABLE, i.e. TaxonomyError ------------
#
# build_index catches TaxonomyError and continues. Anything else escapes and
# kills the run — and because the taxonomy step sits after the vault scan and
# before to_parquet, that discards an hour of SMB walking and writes no index.
# A YAML syntax error, an entry missing `aliases`, and an empty file all did
# exactly that before this: the three most likely malformations hitting the
# worst outcome. So the type matters as much as the refusal.

@pytest.mark.parametrize("raw, why", [
    ("entries: [\n  {name: 'A'\n", "unclosed brace — a plain syntax error"),
    ("", "empty file"),
    ("just a string", "scalar document"),
    ("entries: 3", "entries is not a list"),
    ("version: 1\nentries:\n  - 'not a mapping'", "entry is a bare string"),
    ("version: 1\nentries:\n  - {name: A}", "entry missing aliases"),
    ("version: 1\nentries:\n  - {name: A, aliases: null}", "aliases is null"),
    ("version: 1\nentries:\n  - {name: A, aliases: []}", "aliases is empty"),
    ("version: 1\nentries:\n  - {name: A, aliases: [1, 2]}", "non-string aliases"),
    ("entries:\n  - {name: A, aliases: [a]}", "version missing"),
    ("version: '1'\nentries:\n  - {name: A, aliases: [a]}", "version is a string"),
    ("version: true\nentries:\n  - {name: A, aliases: [a]}",
     "version is a bool — isinstance(True, int) is True in Python, so a naive "
     "int check stamps taxonomy_version=True on every row"),
    ("version: 999999999999999999999\nentries:\n  - {name: A, aliases: [a]}",
     "version overflows the int64 column it is written to"),
    ("version: 1\nentries:\n  - {name: null, aliases: [a]}",
     "name is None — would be written into canonical_entries as the value"),
    ("version: 1\nentries:\n  - {name: {a: b}, aliases: [a]}", "name is a mapping"),
    ("version: 1\nentries:\n  - {name: '  ', aliases: [a]}", "name is blank"),
    ("version: 1\nentries:\n  - {name: A, aliases: [a]}\nexcluded_alias: [x]",
     "misspelled excluded_aliases — would load as 'no exclusions'"),
    ("version: 1\nentries:\n  - {name: A, aliases: [a]}\nexcluded_aliases: Tech",
     "scalar excluded_aliases — would iterate per CHARACTER"),
    ("version: 1\nentries:\n  - {name: A, aliases: [a]}\nexcluded_aliases: [1]",
     "non-string excluded_aliases"),
])
def test_every_malformation_raises_TaxonomyError_so_the_build_survives(
        tmp_path, raw, why):
    p = tmp_path / "bad.yaml"
    p.write_text(raw)
    with pytest.raises(taxonomy.TaxonomyError):
        taxonomy.load(p)


def test_an_unreadable_file_is_survivable_too(tmp_path):
    with pytest.raises(taxonomy.TaxonomyError, match="could not be read"):
        taxonomy.load(tmp_path / "does-not-exist.yaml")


def test_the_exclusion_contradiction_guard_folds_like_its_consumer(tmp_path):
    """is_excluded folds, so an exact-only guard would pass `excluded: [privacy]`
    beside an entry owning "Privacy" — advertising a property it lacks."""
    p = write_tax(tmp_path, [
        {"name": "Privacy", "axis": "concept", "definition": "d", "aliases": ["Privacy"]},
    ], excluded=["privacy"])
    with pytest.raises(taxonomy.TaxonomyError, match="contradicts itself"):
        taxonomy.load(p)


# --- matching -------------------------------------------------------------

def test_exact_aliases_match(tmp_path):
    tax = taxonomy.load(simple(tmp_path))
    assert tax.lookup("Social Networking") == "Social Media"
    assert tax.lookup("nothing here") is None


def test_matching_folds_case_and_whitespace(tmp_path):
    tax = taxonomy.load(simple(tmp_path))
    assert tax.lookup("social  networking") == "Social Media"
    assert not tax.is_exact("social  networking")
    assert tax.is_exact("Social Networking")


# --- the join ------------------------------------------------------------

def test_canonical_names_are_routed_by_source_field(tmp_path):
    tax = taxonomy.load(simple(tmp_path))
    canonical, unmatched = taxonomy.apply_to_row(
        {"topics": ["Social Media"], "concepts": ["Privacy"]}, tax)
    assert canonical["topics"] == ["Social Media"]
    assert canonical["concepts"] == ["Privacy"]
    assert unmatched == []


def test_two_aliases_of_one_entry_collapse_to_one_name(tmp_path):
    tax = taxonomy.load(simple(tmp_path))
    canonical, _ = taxonomy.apply_to_row(
        {"topics": ["Social Media", "Social Networking"], "concepts": []}, tax)
    assert canonical["topics"] == ["Social Media"]


def test_unmatched_strings_are_reported_not_dropped(tmp_path):
    tax = taxonomy.load(simple(tmp_path))
    canonical, unmatched = taxonomy.apply_to_row(
        {"topics": ["Social Media", "Bird Watching"], "concepts": []}, tax)
    assert canonical["topics"] == ["Social Media"]
    assert unmatched == ["Bird Watching"]


def test_the_raw_fields_are_left_untouched(tmp_path):
    """The plan is explicit: strings that match nothing stay where they are.
    A join that consumed its input would make the miss rate unrecomputable."""
    tax = taxonomy.load(simple(tmp_path))
    df = pd.DataFrame([{"topics": ["Social Media", "Bird Watching"], "concepts": []}])
    taxonomy.apply_to_frame(df, tax)
    assert list(df.iloc[0]["topics"]) == ["Social Media", "Bird Watching"]


# --- the pooled column, which is the one that matters --------------------

def test_the_pooled_column_is_the_union_of_both_fields(tmp_path):
    tax = taxonomy.load(simple(tmp_path))
    df = pd.DataFrame([{"topics": ["Social Media"], "concepts": ["Privacy"]}])
    taxonomy.apply_to_frame(df, tax)
    assert df.iloc[0][taxonomy.POOLED] == ["Privacy", "Social Media"]


def test_an_entry_reached_from_both_fields_appears_once_when_pooled(tmp_path):
    """The de-dup half. A THIRD entry reachable from only one field is present
    so the fixture can tell a real union from a single-field copy: without it,
    both fields yield the same answer and a topics-only implementation passes.
    """
    tax = taxonomy.load(simple(tmp_path))
    df = pd.DataFrame([{"topics": ["Social Media"],
                        "concepts": ["Social Networking", "Privacy"]}])
    taxonomy.apply_to_frame(df, tax)
    assert df.iloc[0][taxonomy.POOLED] == ["Privacy", "Social Media"]
    # ...and provenance still records which field reached what.
    assert df.iloc[0]["canonical_topics"] == ["Social Media"]
    assert df.iloc[0]["canonical_concepts"] == ["Privacy", "Social Media"]


def test_each_canonical_column_holds_only_its_own_field(tmp_path):
    """Pins the CANONICAL field->column map itself. The routing test below
    exercises apply_to_row, which returns a dict keyed by FIELD — so swapping
    the two column names there is invisible to it. This sees it."""
    tax = taxonomy.load(simple(tmp_path))
    df = pd.DataFrame([{"topics": ["Social Media"], "concepts": ["Privacy"]}])
    taxonomy.apply_to_frame(df, tax)
    assert df.iloc[0]["canonical_topics"] == ["Social Media"]
    assert df.iloc[0]["canonical_concepts"] == ["Privacy"]


def test_the_join_handles_numpy_arrays_as_parquet_returns_them(tmp_path):
    """Parquet round-trips list columns as ndarrays, so this is the shape the
    join actually meets on a rebuilt index — not the lists every other test
    here uses. If _as_list mishandled it, the columns would come back empty on
    the real index while the whole suite stayed green."""
    import numpy as np
    tax = taxonomy.load(simple(tmp_path))
    df = pd.DataFrame([{"topics": np.array(["Social Media"], dtype=object),
                        "concepts": np.array([], dtype=object)}])
    rep = taxonomy.apply_to_frame(df, tax)
    assert df.iloc[0][taxonomy.POOLED] == ["Social Media"]
    assert rep["articles_tagged"] == 1


def test_corrupted_rows_do_not_supply_v2_candidates(tmp_path):
    """Junk-scrape rows are in the index but never reach a page. Counting their
    strings recommends entries that would reach ~0 real articles — five of the
    top six candidates were 92-98% corrupted before this."""
    tax = taxonomy.load(simple(tmp_path))
    df = pd.DataFrame([
        {"topics": ["Ghost String"], "concepts": [], "content_corrupted": True},
        {"topics": ["Real Gap"], "concepts": [], "content_corrupted": False},
    ])
    rep = taxonomy.apply_to_frame(df, tax)
    assert [s for s, _ in rep["top_unmatched"]] == ["Real Gap"]
    assert rep["candidate_rows"] == 1
    # ...but the corrupted row still gets its columns and its own gap count.
    assert df.iloc[0]["taxonomy_unmatched"] == 1


def test_non_string_members_never_reach_the_candidate_list(tmp_path):
    """A malformed enrichment pass puts None/ints/NaN in these lists. Left
    alone they print as v2 candidates, and an unhashable one raises inside
    set(gaps) — which, before the loader was tightened, killed the build."""
    tax = taxonomy.load(simple(tmp_path))
    df = pd.DataFrame([{"topics": ["Real Gap", None, 3, ["nested"]], "concepts": []}])
    rep = taxonomy.apply_to_frame(df, tax)
    assert [s for s, _ in rep["top_unmatched"]] == ["Real Gap"]


def test_a_missing_source_column_is_refused_not_reported_as_perfect(tmp_path):
    """The failure this file's docstring names first. An absent column becomes
    None, matches nothing, and yields miss_rate 0.0 — the BEST possible value —
    so an upstream rename reads as a flawless run."""
    tax = taxonomy.load(simple(tmp_path))
    df = pd.DataFrame([{"topics": ["Social Media"]}])       # no 'concepts'
    with pytest.raises(taxonomy.TaxonomyError, match="missing from the index"):
        taxonomy.apply_to_frame(df, tax)


def test_pooling_reaches_articles_neither_field_reaches_alone(tmp_path):
    """The whole reason the pooled column exists. An article tagged only via
    concepts and another only via topics both count as covered; measuring
    either column alone undercounts, which is how the 40% bar was lost."""
    tax = taxonomy.load(simple(tmp_path))
    df = pd.DataFrame([
        {"topics": ["Social Media"], "concepts": []},
        {"topics": [], "concepts": ["Privacy"]},
    ])
    rep = taxonomy.apply_to_frame(df, tax)
    assert rep["articles_tagged"] == 2
    assert sum(1 for v in df["canonical_topics"] if v) == 1
    assert sum(1 for v in df["canonical_concepts"] if v) == 1


# --- the health report ---------------------------------------------------

def test_excluded_strings_are_not_counted_as_gaps(tmp_path):
    """Rejected entries would otherwise lead the v2 candidate list forever —
    Technology alone was 1,125 articles — making the metric that triggers a v2
    a report on decisions already made."""
    p = write_tax(tmp_path, [
        {"name": "Privacy", "axis": "concept", "definition": "d", "aliases": ["Privacy"]},
    ], excluded=["Technology"])
    tax = taxonomy.load(p)
    df = pd.DataFrame([{"topics": ["Technology", "Bird Watching"], "concepts": ["Privacy"]}])
    rep = taxonomy.apply_to_frame(df, tax)
    assert rep["strings_excluded"] == 1
    assert rep["strings_gap"] == 1
    assert [s for s, _ in rep["top_unmatched"]] == ["Bird Watching"]
    assert df.iloc[0]["taxonomy_unmatched"] == 1     # not 2


def test_the_miss_rate_ignores_excluded_strings_on_both_sides(tmp_path):
    """Counting an exclusion as a miss makes every curation decision look
    like a regression in the metric.

    The fixture must produce a NON-ZERO numerator or the denominator is
    unobservable. The original used 1 matched + 1 excluded, giving miss_rate
    0.0 — which is 0.0 whether or not the exclusion is subtracted from the
    denominator, so the test was named for a property it could not detect.
    Adding one real gap fixes that:

        1 matched, 1 gap, 1 excluded
        both sides adjusted -> 1/2 = 50.0   (correct)
        numerator only      -> 1/3 = 33.3
    """
    p = write_tax(tmp_path, [
        {"name": "Privacy", "axis": "concept", "definition": "d", "aliases": ["Privacy"]},
    ], excluded=["Technology"])
    tax = taxonomy.load(p)
    df = pd.DataFrame([{"topics": ["Technology", "Kayaking"], "concepts": ["Privacy"]}])
    rep = taxonomy.apply_to_frame(df, tax)
    assert rep["strings_excluded"] == 1
    assert rep["strings_gap"] == 1
    assert rep["miss_rate"] == 50.0
    # ...and the unadjusted figure is reported beside it, because the
    # adjustment is monotone-improvable: excluding more always looks better.
    assert rep["miss_rate_raw"] == round(100 * 2 / 3, 1)


def test_the_candidate_counter_counts_articles_not_mentions(tmp_path):
    """'How many articles would an entry for this reach' is the question v2
    curation asks. Counting mentions inflates a string an article repeats."""
    tax = taxonomy.load(simple(tmp_path))
    df = pd.DataFrame([{"topics": ["Kayaking", "Kayaking"], "concepts": ["Kayaking"]}])
    rep = taxonomy.apply_to_frame(df, tax)
    assert dict(rep["top_unmatched"])["Kayaking"] == 1


def test_version_is_carried_onto_every_row(tmp_path):
    tax = taxonomy.load(write_tax(tmp_path, [
        {"name": "A", "axis": "topic", "definition": "d", "aliases": ["a"]},
    ], version=7))
    df = pd.DataFrame([{"topics": ["a"], "concepts": []}])
    taxonomy.apply_to_frame(df, tax)
    assert df.iloc[0]["taxonomy_version"] == 7


def test_missing_and_scalar_fields_do_not_crash_the_join(tmp_path):
    tax = taxonomy.load(simple(tmp_path))
    df = pd.DataFrame([{"topics": None, "concepts": "Privacy"}])
    rep = taxonomy.apply_to_frame(df, tax)
    assert df.iloc[0]["canonical_concepts"] == ["Privacy"]
    assert rep["articles_tagged"] == 1


# --- against the committed taxonomy --------------------------------------

def test_the_shipped_taxonomy_loads_and_is_self_consistent():
    if not TAXONOMY_PATH.exists():
        pytest.skip("no committed taxonomy")
    tax = taxonomy.load(TAXONOMY_PATH)
    assert len(tax) == 248
    assert tax.version == 1
    assert tax.lookup("Social Media") == "Social Media"
    # the three rejected entries must be recognised as deliberate, not gaps
    for s in ("Technology", "Business", "Design"):
        assert tax.lookup(s) is None
        assert tax.is_excluded(s)
