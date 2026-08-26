#!/usr/bin/env python3
"""Apply the hand-authored curation decisions to the derived clusters.

Phase B of the controlled-vocabulary plan. Phase A produced 250 named clusters;
Adam read them and accepted the list with ten exceptions. This turns those
exceptions into ``data/taxonomy/v1.yaml``.

The division of labour matters and is the same one gate.py draws: the
DECISIONS are hand-authored (``data/taxonomy/decisions.yaml``), the taxonomy is
generated from them. Generating the decisions too would defeat the curation
they came from; transcribing 250 entries by hand is how a curation pass dies.

Every decision names entries and alias strings by their exact text. A typo
therefore has a specific and nasty failure mode: the decision silently does
nothing, the taxonomy looks plausible, and nobody finds out until a query
returns the wrong articles months later. Every lookup below is consequently
strict — an unmatched name or alias raises rather than skips. The alias
conservation check at the end is the backstop for the same class of bug:
aliases may only leave the vocabulary through a decision that explicitly says
so.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DECISIONS = REPO_ROOT / "data" / "taxonomy" / "decisions.yaml"
DEFAULT_OUT = REPO_ROOT / "data" / "taxonomy" / "v1.yaml"
DEFAULT_CLUSTERS = REPO_ROOT / "data" / "vocab" / "clusters.json"
DEFAULT_NAMES = REPO_ROOT / "data" / "vocab" / "cluster-names.jsonl"


class CurationError(RuntimeError):
    """A decision does not match the data it claims to describe."""


def load_head(clusters_path: Path, names_path: Path) -> list[dict]:
    """The curated head, as ordered entries with their member strings."""
    names = [json.loads(line) for line in names_path.read_text().splitlines() if line.strip()]
    clusters = {c["id"]: c for c in json.loads(clusters_path.read_text())["clusters"]}
    head = []
    for n in names:
        c = clusters[n["id"]]
        head.append({
            "name": n["name"],
            "axis": n["axis"],
            "definition": n["definition"],
            "aliases": list(c["members"]),
            "articles": c["articles"],
        })
    return head


def _find(entries: list[dict], name: str, what: str) -> dict:
    for e in entries:
        if e["name"] == name:
            return e
    raise CurationError(f"{what}: no entry named {name!r} in the curated head")


def _take(entry: dict, aliases: list[str], what: str) -> list[str]:
    """Remove `aliases` from `entry`, insisting every one was actually there."""
    have = set(entry["aliases"])
    missing = [a for a in aliases if a not in have]
    if missing:
        raise CurationError(
            f"{what}: {entry['name']!r} does not contain {missing!r} — "
            "the decision would silently do nothing"
        )
    entry["aliases"] = [a for a in entry["aliases"] if a not in set(aliases)]
    return list(aliases)


# Everything a decisions file is allowed to say. Anything else is a typo, and
# a typo at THIS level is the worst kind: `rejects:` instead of `reject:` reads
# perfectly to a human, parses as valid YAML, and silently removes nothing.
# Every strict lookup below is defeated by a key that is never consulted.
KNOWN_KEYS = frozenset({
    "version", "source", "default",
    "reject", "merge", "split", "reassign_aliases", "drop_aliases",
})


# What each rule kind must carry. A rule missing one of these used to escape
# as a bare KeyError, which is loud but skips CurationError entirely — so it
# read as a crash rather than as "your decisions file is wrong here".
REQUIRED = {
    "reject": ("name",),
    "merge": ("into", "absorb"),
    "split": ("from", "into"),
    "reassign_aliases": ("from", "to", "aliases"),
    "drop_aliases": ("from", "aliases"),
}

# Lists that must not be empty. An empty list is the quiet cousin of a
# misspelled key: `absorb: []` or `aliases: []` parses, applies to nothing,
# and reports a clean run. Nobody writes one on purpose.
NON_EMPTY = {
    "merge": ("absorb",),
    "split": ("into",),
    "reassign_aliases": ("aliases",),
    "drop_aliases": ("aliases",),
}

# `evidence` and `why` are prose for the human reader and are never consulted,
# so they are allowed everywhere. Everything else must be spelled correctly:
# the misspelled-key failure is just as silent one level down as it is at the
# top of the file, and `remainder_defintion` would quietly leave a remainder
# describing a set that no longer exists.
PROSE = ("evidence", "why")
ALLOWED = {
    "reject": ("name",) + PROSE,
    "merge": ("into", "absorb") + PROSE,
    "split": ("from", "into", "remainder", "remainder_definition",
              "drop_aliases") + PROSE,
    "reassign_aliases": ("from", "to", "aliases") + PROSE,
    "drop_aliases": ("from", "aliases") + PROSE,
}
ALLOWED_SPLIT_INTO = ("name", "definition", "aliases")


def _validate(decisions: dict) -> None:
    unknown = sorted(set(decisions) - KNOWN_KEYS)
    if unknown:
        raise CurationError(
            f"unknown key(s) in the decisions file: {unknown} — "
            f"expected one of {sorted(KNOWN_KEYS)}. A misspelled key is not "
            "ignored here because it would apply to nothing and report success."
        )
    for kind, required in REQUIRED.items():
        for i, rule in enumerate(decisions.get(kind) or []):
            missing = [k for k in required if k not in rule]
            if missing:
                raise CurationError(
                    f"{kind}[{i}] is missing {missing} — got keys {sorted(rule)}"
                )
            for key in NON_EMPTY.get(kind, ()):
                if not rule.get(key):
                    raise CurationError(
                        f"{kind}[{i}].{key} is empty — the decision would "
                        "apply to nothing and report success"
                    )
            stray = sorted(set(rule) - set(ALLOWED[kind]))
            if stray:
                raise CurationError(
                    f"{kind}[{i}] has unknown key(s) {stray} — "
                    f"expected some of {sorted(ALLOWED[kind])}"
                )
    for i, rule in enumerate(decisions.get("split") or []):
        for j, spec in enumerate(rule["into"]):
            missing = [k for k in ("name", "definition", "aliases") if k not in spec]
            if missing:
                raise CurationError(f"split[{i}].into[{j}] is missing {missing}")
            if not spec["aliases"]:
                raise CurationError(
                    f"split[{i}].into[{j}] ({spec['name']!r}) has no aliases"
                )
            stray = sorted(set(spec) - set(ALLOWED_SPLIT_INTO))
            if stray:
                raise CurationError(
                    f"split[{i}].into[{j}] has unknown key(s) {stray}"
                )


def apply_decisions(head: list[dict], decisions: dict) -> tuple[list[dict], dict]:
    _validate(decisions)
    entries = [dict(e) for e in head]
    before = {a for e in entries for a in e["aliases"]}
    dropped: set[str] = set()
    audit = {"rejected": [], "merged": [], "split": [], "moved": 0, "dropped_aliases": []}

    for rule in decisions.get("reject") or []:
        entry = _find(entries, rule["name"], "reject")
        entries.remove(entry)
        dropped.update(entry["aliases"])
        audit["rejected"].append({"name": entry["name"], "aliases": len(entry["aliases"])})

    for rule in decisions.get("merge") or []:
        target = _find(entries, rule["into"], "merge.into")
        for other_name in rule["absorb"]:
            other = _find(entries, other_name, "merge.absorb")
            if other is target:
                raise CurationError(f"merge: {other_name!r} cannot absorb itself")
            for alias in other["aliases"]:
                if alias not in target["aliases"]:
                    target["aliases"].append(alias)
            entries.remove(other)
            audit["merged"].append({"into": target["name"], "absorbed": other_name})

    for rule in decisions.get("split") or []:
        source = _find(entries, rule["from"], "split.from")
        idx = entries.index(source)
        made = []
        for spec in rule["into"]:
            taken = _take(source, spec["aliases"], f"split.into[{spec['name']}]")
            made.append({
                "name": spec["name"],
                "axis": source["axis"],
                "definition": " ".join(str(spec["definition"]).split()),
                "aliases": taken,
                "articles": None,
            })
        for alias in _take(source, rule.get("drop_aliases") or [], "split.drop_aliases"):
            dropped.add(alias)
            audit["dropped_aliases"].append({"from": source["name"], "alias": alias})
        # The remainder survives only if something is left in it. Splitting an
        # entry down to nothing and leaving an empty husk behind would put a
        # name in the taxonomy that matches no article at all.
        if source["aliases"]:
            source["name"] = rule.get("remainder", source["name"])
            # A split changes what the remainder MEANS, so its inherited
            # definition can quietly become false — it was written to describe
            # a set that no longer exists.
            if rule.get("remainder_definition"):
                source["definition"] = " ".join(
                    str(rule["remainder_definition"]).split())
            entries[idx:idx + 1] = made + [source]
        else:
            entries[idx:idx + 1] = made
        audit["split"].append({
            "from": rule["from"],
            "into": [m["name"] for m in made],
            "remainder": source["name"] if source["aliases"] else None,
        })

    for rule in decisions.get("reassign_aliases") or []:
        source = _find(entries, rule["from"], "reassign.from")
        target = _find(entries, rule["to"], "reassign.to")
        # Otherwise the alias is taken from an entry and handed straight back
        # to it: nothing changes, but the audit reports a move, which is the
        # same lie by a different route.
        if source is target:
            raise CurationError(
                f"reassign: {rule['from']!r} cannot move aliases to itself"
            )
        for alias in _take(source, rule["aliases"], "reassign.aliases"):
            if alias not in target["aliases"]:
                target["aliases"].append(alias)
            audit["moved"] += 1

    for rule in decisions.get("drop_aliases") or []:
        source = _find(entries, rule["from"], "drop_aliases.from")
        for alias in _take(source, rule["aliases"], "drop_aliases"):
            dropped.add(alias)
            audit["dropped_aliases"].append({"from": source["name"], "alias": alias})

    _check_invariants(entries, before, dropped)
    audit["excluded_aliases"] = sorted(dropped)
    return entries, audit


def _check_invariants(entries: list[dict], before: set[str], dropped: set[str]) -> None:
    names = [e["name"] for e in entries]
    if len(names) != len(set(names)):
        dupes = sorted({n for n in names if names.count(n) > 1})
        raise CurationError(f"duplicate entry names in the taxonomy: {dupes}")

    owner: dict[str, str] = {}
    for e in entries:
        if not e["aliases"]:
            raise CurationError(f"{e['name']!r} ended up with no aliases")
        for alias in e["aliases"]:
            if alias in owner:
                raise CurationError(
                    f"alias {alias!r} claimed by both {owner[alias]!r} and {e['name']!r} — "
                    "an article tagged with it would match two entries"
                )
            owner[alias] = e["name"]

    # Alias conservation. Every string that entered the head must either still
    # be in the taxonomy or have been dropped by a decision that named it. A
    # string that vanishes any other way is a bug in this script, and it would
    # be invisible in the output.
    after = set(owner)
    lost = before - after - dropped
    if lost:
        raise CurationError(f"{len(lost)} alias(es) lost without a decision: {sorted(lost)[:10]}")
    invented = after - before
    if invented:
        raise CurationError(f"aliases appeared from nowhere: {sorted(invented)[:10]}")


def render(entries: list[dict], decisions: dict, audit: dict | None = None) -> str:
    doc = {
        "version": decisions.get("version", 1),
        "generated_by": "scripts/vocab/apply_curation.py",
        "entries": [
            {"name": e["name"], "axis": e["axis"],
             "definition": " ".join(str(e["definition"]).split()),
             "aliases": e["aliases"]}
            for e in entries
        ],
        # Strings deliberately kept OUT of the vocabulary, carried here so the
        # taxonomy file is self-describing. Phase C's miss rate is the metric
        # that says when to cut a v2, and without this it counts our own
        # rejections as gaps: "Technology" alone is 1,125 unmatched articles
        # and dominates the most-missed list, which is meant to be the v2
        # candidate list. A deliberate exclusion is not a gap.
        "excluded_aliases": (audit or {}).get("excluded_aliases", []),
    }
    header = (
        "# Controlled vocabulary v1 — the source of truth for concepts + topics.\n"
        "#\n"
        "# GENERATED from data/taxonomy/decisions.yaml by\n"
        "# scripts/vocab/apply_curation.py. Edit the decisions, not this file:\n"
        "# a hand-edit here is silently reverted by the next regeneration.\n"
        f"# {len(entries)} entries, {sum(len(e['aliases']) for e in entries)} aliases.\n"
    )
    return header + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--decisions", type=Path, default=DEFAULT_DECISIONS)
    ap.add_argument("--clusters", type=Path, default=DEFAULT_CLUSTERS)
    ap.add_argument("--names", type=Path, default=DEFAULT_NAMES)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--check", action="store_true",
                    help="verify the committed taxonomy matches the decisions; write nothing")
    args = ap.parse_args(argv)

    decisions = yaml.safe_load(args.decisions.read_text())
    head = load_head(args.clusters, args.names)
    entries, audit = apply_decisions(head, decisions)
    rendered = render(entries, decisions, audit)

    if args.check:
        if not args.out.exists():
            print(f"MISSING: {args.out}", file=sys.stderr)
            return 1
        if args.out.read_text() != rendered:
            print(f"STALE: {args.out} does not match the decisions", file=sys.stderr)
            return 1
        print(f"ok — {args.out} matches the decisions ({len(entries)} entries)")
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(rendered)
    print(f"wrote {args.out} — {len(entries)} entries "
          f"({sum(len(e['aliases']) for e in entries)} aliases)")
    print(f"  rejected: {len(audit['rejected'])}  merged: {len(audit['merged'])}  "
          f"split: {len(audit['split'])}  aliases moved: {audit['moved']}  "
          f"aliases dropped: {len(audit['dropped_aliases'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
