"""Tests for Phase B's decision applier.

The failure this file exists to prevent is a decision that silently does
nothing. A typo'd entry name or alias produces a taxonomy that looks entirely
plausible — right shape, right count, sensible names — and is wrong in a way
nobody discovers until a query returns the wrong articles. So most of what
follows tests the STRICTNESS of the lookups rather than the happy path.

Each guard was checked by removing it and confirming the corresponding test
goes red. Where a test could plausibly pass for a reason other than the guard
it names, the docstring says so and the assertion is built to rule it out.
"""
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from vocab.apply_curation import (  # noqa: E402
    CurationError,
    apply_decisions,
    load_head,
    render,
)

CLUSTERS = REPO_ROOT / "data" / "vocab" / "clusters.json"
NAMES = REPO_ROOT / "data" / "vocab" / "cluster-names.jsonl"
DECISIONS = REPO_ROOT / "data" / "taxonomy" / "decisions.yaml"
TAXONOMY = REPO_ROOT / "data" / "taxonomy" / "v1.yaml"

needs_derivation = pytest.mark.skipif(
    not CLUSTERS.exists(),
    reason="derivation artifacts are gitignored; run scripts/vocab/embed.py first",
)


def head(*entries):
    """A curated head, shaped as load_head returns it."""
    return [{"name": n, "axis": "topic", "definition": f"def of {n}",
             "aliases": list(a), "articles": 10} for n, a in entries]


# --- the happy paths, one per decision kind -------------------------------

def test_reject_removes_the_entry_and_its_aliases():
    out, audit = apply_decisions(
        head(("Technology", ["Technology", "Tech"]), ("Privacy", ["Privacy"])),
        {"reject": [{"name": "Technology"}]},
    )
    assert [e["name"] for e in out] == ["Privacy"]
    assert audit["rejected"] == [{"name": "Technology", "aliases": 2}]


def test_merge_folds_aliases_in_and_drops_the_absorbed_entry():
    out, _ = apply_decisions(
        head(("IPOs", ["Ipo", "Ipos"]), ("IPO", ["Initial Public Offering"])),
        {"merge": [{"into": "IPOs", "absorb": ["IPO"]}]},
    )
    assert [e["name"] for e in out] == ["IPOs"]
    assert out[0]["aliases"] == ["Ipo", "Ipos", "Initial Public Offering"]


def test_split_makes_new_entries_and_keeps_the_remainder():
    out, _ = apply_decisions(
        head(("National Economies", ["Chinese Economy", "Greek Economy", "Economy"])),
        {"split": [{
            "from": "National Economies",
            "into": [{"name": "Chinese Economy", "definition": "d",
                      "aliases": ["Chinese Economy"]}],
            "remainder": "National Economies",
            "drop_aliases": ["Economy"],
        }]},
    )
    assert [e["name"] for e in out] == ["Chinese Economy", "National Economies"]
    assert out[0]["aliases"] == ["Chinese Economy"]
    # The generic went away; the singleton country stayed. Asserting both
    # rules out a version that simply drops everything it did not promote.
    assert out[1]["aliases"] == ["Greek Economy"]


def test_a_split_that_empties_its_source_leaves_no_husk_behind():
    """An entry with zero aliases matches zero articles. Keeping it would put a
    name in the taxonomy that nothing can ever return."""
    out, _ = apply_decisions(
        head(("Culture", ["Arts and Culture", "Culture"])),
        {"split": [{
            "from": "Culture",
            "into": [{"name": "Arts and Culture", "definition": "d",
                      "aliases": ["Arts and Culture"]}],
            "remainder": "Culture",
            "drop_aliases": ["Culture"],
        }]},
    )
    assert [e["name"] for e in out] == ["Arts and Culture"]


def test_reassign_moves_aliases_rather_than_dropping_them():
    """The distinction is the point: iPad strings leaving the iPhone entry must
    LAND somewhere, or the articles carrying them lose their home. A test that
    only checked they left the source would pass on a plain drop, so the
    landing assertion is the one that matters."""
    out, audit = apply_decisions(
        head(("iPhone", ["Iphone", "Ipad App"]), ("iPad", ["Ipad"])),
        {"reassign_aliases": [{"from": "iPhone", "to": "iPad", "aliases": ["Ipad App"]}]},
    )
    by = {e["name"]: e for e in out}
    assert "Ipad App" not in by["iPhone"]["aliases"]
    assert by["iPad"]["aliases"] == ["Ipad", "Ipad App"]
    assert audit["moved"] == 1


# --- the strictness guards, which are the actual point --------------------

@pytest.mark.parametrize("decisions, needle", [
    ({"reject": [{"name": "Technolgy"}]}, "Technolgy"),
    ({"merge": [{"into": "Typo", "absorb": ["Privacy"]}]}, "Typo"),
    ({"merge": [{"into": "Privacy", "absorb": ["Nope"]}]}, "Nope"),
    ({"split": [{"from": "Nope", "into": []}]}, "Nope"),
    ({"reassign_aliases": [{"from": "Nope", "to": "Privacy", "aliases": []}]}, "Nope"),
    ({"drop_aliases": [{"from": "Nope", "aliases": []}]}, "Nope"),
])
def test_a_misspelled_entry_name_raises_instead_of_doing_nothing(decisions, needle):
    with pytest.raises(CurationError, match=needle):
        apply_decisions(head(("Technology", ["Tech"]), ("Privacy", ["Privacy"])), decisions)


def test_an_alias_the_source_does_not_have_raises():
    """Without this, a decision naming 'Digital Audio Recordings' (plural, and
    wrong) removes nothing and reports success."""
    with pytest.raises(CurationError, match="does not contain"):
        apply_decisions(
            head(("Photo", ["Digital Audio Recorder"])),
            {"drop_aliases": [{"from": "Photo", "aliases": ["Digital Audio Recordings"]}]},
        )


def test_the_same_alias_cannot_be_claimed_by_two_entries():
    """An article tagged with it would match both, and each would claim it in
    any ranking."""
    with pytest.raises(CurationError, match="claimed by both"):
        apply_decisions(head(("A", ["shared", "a"]), ("B", ["shared", "b"])), {})


def test_an_entry_left_with_no_aliases_raises():
    with pytest.raises(CurationError, match="no aliases"):
        apply_decisions(
            head(("A", ["only"])),
            {"drop_aliases": [{"from": "A", "aliases": ["only"]}]},
        )


def test_an_alias_cannot_leave_without_a_decision_that_says_so(monkeypatch):
    """Conservation backstop. This catches a bug in the APPLIER rather than in
    the decisions — an alias quietly dropped by faulty list handling would be
    invisible in the output, since the file would still look well-formed."""
    import vocab.apply_curation as mod

    real_take = mod._take

    def leaky(entry, aliases, what):
        taken = real_take(entry, aliases, what)
        entry["aliases"] = entry["aliases"][:-1] if entry["aliases"] else []
        return taken

    monkeypatch.setattr(mod, "_take", leaky)
    with pytest.raises(CurationError, match="lost without a decision"):
        mod.apply_decisions(
            head(("A", ["keep-me", "drop-me", "innocent"])),
            {"drop_aliases": [{"from": "A", "aliases": ["drop-me"]}]},
        )


def test_duplicate_entry_names_raise():
    with pytest.raises(CurationError, match="duplicate entry names"):
        apply_decisions(
            head(("Culture", ["a", "Culture"]), ("Arts", ["arts"])),
            {"split": [{"from": "Culture", "remainder": "Arts",
                        "into": [{"name": "X", "definition": "d", "aliases": ["a"]}]}]},
        )


# --- end to end against the real derivation -------------------------------

@needs_derivation
def test_the_committed_taxonomy_matches_the_committed_decisions():
    """`--check` in test form: the generated file must not drift from the
    decisions it claims to come from. A hand-edit to v1.yaml fails here."""
    decisions = yaml.safe_load(DECISIONS.read_text())
    entries, _ = apply_decisions(load_head(CLUSTERS, NAMES), decisions)
    assert TAXONOMY.read_text() == render(entries, decisions)


@needs_derivation
def test_every_decision_in_the_real_file_actually_applies():
    """The strict lookups only help if they run against the REAL decisions.
    This is what turns a typo in decisions.yaml into a red suite."""
    decisions = yaml.safe_load(DECISIONS.read_text())
    entries, audit = apply_decisions(load_head(CLUSTERS, NAMES), decisions)
    assert len(audit["rejected"]) == len(decisions["reject"])
    assert len(audit["split"]) == len(decisions["split"])
    assert audit["moved"] == sum(len(r["aliases"]) for r in decisions["reassign_aliases"])
    names = {e["name"] for e in entries}
    assert {"Chinese Economy", "US Economy", "Arts and Culture", "American Culture"} <= names
    assert not ({"Technology", "Business", "Design"} & names)
