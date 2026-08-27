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
    KNOWN_KEYS,
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
    ({"split": [{"from": "Nope", "into": [
        {"name": "X", "definition": "d", "aliases": ["Tech"]}]}]}, "Nope"),
    ({"reassign_aliases": [{"from": "Nope", "to": "Privacy",
                            "aliases": ["Tech"]}]}, "Nope"),
    ({"drop_aliases": [{"from": "Nope", "aliases": ["Tech"]}]}, "Nope"),
])
def test_a_misspelled_entry_name_raises_instead_of_doing_nothing(decisions, needle):
    with pytest.raises(CurationError, match=needle):
        apply_decisions(head(("Technology", ["Tech"]), ("Privacy", ["Privacy"])), decisions)


@pytest.mark.parametrize("bad_key", ["rejects", "merges", "splits",
                                     "reassign_alias", "drop_alias", "rejcet"])
def test_a_misspelled_TOP_LEVEL_key_raises(bad_key):
    """The worst typo available, and the one that defeats every other guard in
    this file: a key that is never consulted means its rules are never looked
    up, so no strict lookup ever runs. `rejects:` for `reject:` reads correctly
    to a human and is valid YAML.

    Caught in review of this very PR — the applier accepted it and reported a
    clean run with nothing rejected."""
    with pytest.raises(CurationError, match="unknown key"):
        apply_decisions(head(("Technology", ["Tech"])),
                        {bad_key: [{"name": "Technology"}]})


def test_the_real_decisions_file_uses_only_known_keys():
    """Guards the inverse: adding a legitimate new decision kind to the schema
    without teaching KNOWN_KEYS about it would be caught here rather than by a
    confusing failure on Adam's next curation pass."""
    if not DECISIONS.exists():
        pytest.skip("decisions file absent")
    assert set(yaml.safe_load(DECISIONS.read_text())) <= KNOWN_KEYS


@pytest.mark.parametrize("decisions, needle", [
    ({"merge": [{"into": "Technology", "absorb": []}]}, "absorb is empty"),
    ({"reassign_aliases": [{"from": "Technology", "to": "Privacy",
                            "aliases": []}]}, "aliases is empty"),
    ({"drop_aliases": [{"from": "Technology", "aliases": []}]}, "aliases is empty"),
    ({"split": [{"from": "Technology", "into": []}]}, "into is empty"),
])
def test_an_empty_rule_list_raises(decisions, needle):
    """The quiet cousin of a misspelled key. `absorb: []` parses fine, applies
    to nothing, and reports a clean run — nobody writes one on purpose."""
    with pytest.raises(CurationError, match=needle):
        apply_decisions(head(("Technology", ["Tech"]), ("Privacy", ["Privacy"])), decisions)


@pytest.mark.parametrize("decisions, needle", [
    ({"reject": [{"nmae": "Technology"}]}, r"missing \['name'\]"),
    ({"merge": [{"into": "Technology"}]}, r"missing \['absorb'\]"),
    ({"reassign_aliases": [{"from": "Technology", "to": "Privacy"}]}, r"missing \['aliases'\]"),
    ({"split": [{"from": "Technology", "into": [
        {"name": "X", "aliases": ["Tech"]}]}]}, r"missing \['definition'\]"),
])
def test_a_rule_missing_a_required_key_raises_CurationError_not_KeyError(decisions, needle):
    """These were escaping as bare KeyError — loud, but it reads as a crash
    rather than as 'your decisions file is wrong, here, on this rule'."""
    with pytest.raises(CurationError, match=needle):
        apply_decisions(head(("Technology", ["Tech"]), ("Privacy", ["Privacy"])), decisions)


@pytest.mark.parametrize("decisions", [
    {"reject": [{"name": "Technology", "wyh": "typo'd prose key"}]},
    {"drop_aliases": [{"from": "Technology", "aliases": ["Tech"], "form": "x"}]},
    {"split": [{"from": "Technology", "remainder_defintion": "typo",
                "into": [{"name": "X", "definition": "d", "aliases": ["Tech"]}]}]},
])
def test_a_misspelled_key_INSIDE_a_rule_raises(decisions):
    """Same silence one level down. `remainder_defintion` would leave the
    remainder describing a set the split just destroyed."""
    with pytest.raises(CurationError, match="unknown key"):
        apply_decisions(head(("Technology", ["Tech"]), ("Privacy", ["Privacy"])), decisions)


def test_a_misspelled_key_inside_a_split_into_spec_raises():
    with pytest.raises(CurationError, match="unknown key"):
        apply_decisions(
            head(("Technology", ["Tech"])),
            {"split": [{"from": "Technology", "into": [
                {"name": "X", "definiton": "typo", "definition": "d",
                 "aliases": ["Tech"]}]}]},
        )


def test_prose_keys_are_allowed_everywhere():
    """evidence/why are for the human reader and are never consulted. The
    strictness above must not make the file undocumentable."""
    out, _ = apply_decisions(
        head(("Technology", ["Tech"]), ("Privacy", ["Privacy"])),
        {"reject": [{"name": "Technology", "evidence": "e", "why": "w"}]},
    )
    assert [e["name"] for e in out] == ["Privacy"]


def test_a_split_can_restate_the_remainders_definition():
    """A split changes what the remainder means, so its inherited definition
    can quietly become false."""
    out, _ = apply_decisions(
        head(("Econ", ["China", "Greece"])),
        {"split": [{"from": "Econ", "remainder": "Econ",
                    "remainder_definition": "everyone but China",
                    "into": [{"name": "China", "definition": "d",
                              "aliases": ["China"]}]}]},
    )
    assert [e["definition"] for e in out if e["name"] == "Econ"] == ["everyone but China"]


def test_an_entry_cannot_absorb_itself():
    with pytest.raises(CurationError, match="cannot absorb itself"):
        apply_decisions(head(("A", ["a"])), {"merge": [{"into": "A", "absorb": ["A"]}]})


def test_an_alias_cannot_appear_from_nowhere(monkeypatch):
    """The mirror of the conservation check: an applier bug that INVENTS an
    alias is as wrong as one that loses it, and just as invisible."""
    import vocab.apply_curation as mod

    real_take = mod._take

    def inventive(entry, aliases, what):
        taken = real_take(entry, aliases, what)
        entry["aliases"].append("conjured-from-thin-air")
        return taken

    monkeypatch.setattr(mod, "_take", inventive)
    with pytest.raises(CurationError, match="appeared from nowhere"):
        mod.apply_decisions(
            head(("A", ["keep", "drop-me"])),
            {"drop_aliases": [{"from": "A", "aliases": ["drop-me"]}]},
        )


def test_reassigning_an_alias_to_the_same_entry_raises():
    """It would take the alias and hand it straight back, changing nothing
    while the audit counted a move."""
    with pytest.raises(CurationError, match="cannot move aliases to itself"):
        apply_decisions(
            head(("iPad", ["Ipad", "Ipad App"])),
            {"reassign_aliases": [{"from": "iPad", "to": "iPad",
                                   "aliases": ["Ipad App"]}]},
        )


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

# --- structural checks over the COMMITTED pair, needing no derivation -----
#
# The two end-to-end tests below skip on any machine without the 237MB
# derivation, and this repo has no CI — so on a fresh clone the shipped
# taxonomy would go entirely unvalidated. These run anywhere, because they
# read only the two files git actually carries.

def _committed_pair():
    if not (DECISIONS.exists() and TAXONOMY.exists()):
        pytest.skip("committed taxonomy pair absent")
    return (yaml.safe_load(DECISIONS.read_text()),
            yaml.safe_load(TAXONOMY.read_text()))


def test_the_shipped_taxonomy_is_internally_sound():
    _, tax = _committed_pair()
    entries = tax["entries"]
    seen = {}
    for e in entries:
        assert e["definition"].strip(), f"{e['name']} has an empty definition"
        assert "\n" not in e["definition"], f"{e['name']} definition has a newline"
        assert e["aliases"], f"{e['name']} has no aliases"
        for a in e["aliases"]:
            assert a not in seen, f"alias {a!r} in both {seen.get(a)!r} and {e['name']!r}"
            seen[a] = e["name"]
    names = [e["name"] for e in entries]
    assert len(names) == len(set(names)), "duplicate entry names"


def test_the_generated_header_agrees_with_the_body_it_heads():
    """The header is what a human eyeballs. If it can drift from the entries
    below it, it is worse than no header at all."""
    _, tax = _committed_pair()
    header = TAXONOMY.read_text().splitlines()
    line = next(ln for ln in header if "entries," in ln)
    n_entries = int(line.split()[1].replace(",", ""))
    n_aliases = int(line.split()[3].replace(",", ""))
    assert n_entries == len(tax["entries"])
    assert n_aliases == sum(len(e["aliases"]) for e in tax["entries"])


def test_every_decision_is_visible_in_the_shipped_taxonomy():
    """Checks the OUTCOME of each decision against the committed file, rather
    than re-running the applier. Catches a v1.yaml regenerated from a stale or
    edited decisions file even where the derivation is unavailable."""
    dec, tax = _committed_pair()
    by_name = {e["name"]: e for e in tax["entries"]}
    aliases = {a for e in tax["entries"] for a in e["aliases"]}

    for rule in dec.get("reject") or []:
        assert rule["name"] not in by_name, f"rejected {rule['name']} still present"
    for rule in dec.get("merge") or []:
        assert rule["into"] in by_name
        for absorbed in rule["absorb"]:
            assert absorbed not in by_name, f"absorbed {absorbed} still present"
    for rule in dec.get("split") or []:
        for spec in rule["into"]:
            assert spec["name"] in by_name, f"split never created {spec['name']}"
            assert set(spec["aliases"]) <= set(by_name[spec["name"]]["aliases"])
        for gone in rule.get("drop_aliases") or []:
            assert gone not in aliases, f"dropped alias {gone!r} still present"
    for rule in dec.get("reassign_aliases") or []:
        # The one the reviewer proved silently fails on a from==to typo, with
        # entry and alias counts both unchanged. Assert the aliases LANDED.
        assert set(rule["aliases"]) <= set(by_name[rule["to"]]["aliases"]), (
            f"reassigned aliases never landed in {rule['to']}")
        assert not (set(rule["aliases"]) & set(by_name[rule["from"]]["aliases"])), (
            f"reassigned aliases still in {rule['from']}")
    for rule in dec.get("drop_aliases") or []:
        for gone in rule["aliases"]:
            assert gone not in aliases, f"dropped alias {gone!r} still present"


# --- end to end against the real derivation -------------------------------

@needs_derivation
def test_the_committed_taxonomy_matches_the_committed_decisions():
    """`--check` in test form: the generated file must not drift from the
    decisions it claims to come from. A hand-edit to v1.yaml fails here."""
    decisions = yaml.safe_load(DECISIONS.read_text())
    entries, audit = apply_decisions(load_head(CLUSTERS, NAMES), decisions)
    # Derivation provenance is part of the rendered file, so the drift check
    # has to supply it the same way main() does — otherwise this fails on a
    # correct file for the wrong reason.
    from vocab.apply_curation import derivation_stats
    assert TAXONOMY.read_text() == render(entries, decisions, audit,
                                          derivation_stats(CLUSTERS))


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
