#!/usr/bin/env python3
"""Join the curated taxonomy against each article's free-text strings.

Phase C of the controlled-vocabulary plan. No inference happens here: every
article already carries free-text ``concepts`` and ``topics`` from earlier
enrichment, and the taxonomy already enumerates the alias strings that map to
each canonical entry. This is a dictionary lookup, which is why the plan calls
it the cheap half.

Three things are deliberate.

**Routing is by SOURCE FIELD, not by the entry's axis label.** An alias found
in an article's ``topics`` lands in ``canonical_topics``; one found in
``concepts`` lands in ``canonical_concepts``. The same entry can be reached
from either, because the clustering pooled both fields. Using the entry's own
``axis`` would be worse: those labels are Qwen's and are demonstrably
unreliable (iPod, iTunes and Search Engines are all labelled "concept"), and
nothing downstream should depend on them.

**Matching folds case.** The curated aliases already enumerate the case
variants the corpus contains, so exact matching would cover today's articles
completely — but tomorrow's arrive with novel casing, and folding is provably
safe here: no two entries own aliases that collide case-insensitively. Both
figures are reported so the value of the folding stays visible.

**Nothing is dropped silently.** Strings that match nothing stay in the raw
fields untouched and are counted per article. The aggregate miss rate is the
taxonomy's health metric — the signal that says when to cut a v2 — so it is
returned rather than discarded, in the same spirit as the dedupe and
people-quarantine reporting.
"""
from __future__ import annotations

import collections
from pathlib import Path

import yaml

FIELDS = ("concepts", "topics")

# A gap string must reach at least this many ARTICLES before it is worth an
# entry. Chosen against the measured distribution, not by taste: at >=25 there
# are 388 candidates worth ~15,400 article-tags, while >=5 sweeps in 4,728
# strings most of which are near-duplicates of each other.
V2_CANDIDATE_MIN = 25
CANONICAL = {"concepts": "canonical_concepts", "topics": "canonical_topics"}


class TaxonomyError(RuntimeError):
    """The taxonomy file is unusable."""


class Taxonomy:
    """An alias -> canonical-name lookup, plus the version that produced it."""

    def __init__(self, version, by_alias, by_folded, names, excluded=()):
        self.version = version
        self._by_alias = by_alias
        self._by_folded = by_folded
        self.names = names
        # Strings curation deliberately kept out. Counted apart from real gaps
        # so the most-missed list stays a usable v2 candidate list rather than
        # being led by decisions already made.
        self.excluded = frozenset(excluded)
        self._excluded_folded = frozenset(_fold(s) for s in excluded)

    def is_excluded(self, s):
        return s in self.excluded or _fold(s) in self._excluded_folded

    def __len__(self):
        return len(self.names)

    def lookup(self, s):
        """Canonical name for a raw string, or None.

        Exact first so an alias that was curated with specific casing wins over
        a fold, even though the fold is collision-free today.
        """
        hit = self._by_alias.get(s)
        if hit is not None:
            return hit
        return self._by_folded.get(_fold(s))

    def is_exact(self, s):
        return s in self._by_alias


def _fold(s):
    return " ".join(str(s).lower().split())


def load(path: Path) -> Taxonomy:
    doc = yaml.safe_load(Path(path).read_text())
    entries = doc.get("entries") or []
    if not entries:
        raise TaxonomyError(f"{path} has no entries")

    by_alias, by_folded, folded_owner = {}, {}, {}
    for e in entries:
        name = e["name"]
        for alias in e["aliases"]:
            if alias in by_alias and by_alias[alias] != name:
                raise TaxonomyError(
                    f"alias {alias!r} maps to both {by_alias[alias]!r} and {name!r}"
                )
            by_alias[alias] = name
            folded = _fold(alias)
            # A collision here would make the fold ambiguous, so the article
            # would silently get whichever entry happened to load last. Refuse
            # rather than pick. (v1 has none; this guards v2 onward.)
            if folded in folded_owner and folded_owner[folded] != name:
                raise TaxonomyError(
                    f"aliases {alias!r} collide case-insensitively between "
                    f"{folded_owner[folded]!r} and {name!r} — the fold would be ambiguous"
                )
            folded_owner[folded] = name
            by_folded[folded] = name

    excluded = doc.get("excluded_aliases") or []
    both = sorted(set(excluded) & set(by_alias))
    if both:
        raise TaxonomyError(
            f"{len(both)} alias(es) are both excluded and owned by an entry: "
            f"{both[:5]} — the taxonomy contradicts itself"
        )
    return Taxonomy(doc.get("version", 1), by_alias, by_folded,
                    [e["name"] for e in entries], excluded)


def _as_list(v):
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    try:
        return [x for x in v]
    except TypeError:
        return []


def apply_to_row(row, tax: Taxonomy):
    """Canonical names per source field, plus the strings that matched nothing.

    Returns (canonical: dict[field -> sorted names], unmatched: list[str]).
    Canonical names are de-duplicated within a field: an article saying
    "Social Media" and "Social Networking" has one Social Media, not two.
    """
    canonical, unmatched = {}, []
    for field in FIELDS:
        names = []
        for s in _as_list(row.get(field)):
            hit = tax.lookup(s)
            if hit is None:
                unmatched.append(s)
            elif hit not in names:
                names.append(hit)
        canonical[field] = sorted(names)
    return canonical, unmatched


def apply_to_frame(df, tax: Taxonomy):
    """Add the Phase C columns to `df` in place and return a health report.

    New columns: canonical_concepts, canonical_topics, taxonomy_unmatched
    (a per-article count), taxonomy_version.
    """
    cols = {c: [] for c in CANONICAL.values()}
    unmatched_counts, miss_counter = [], collections.Counter()
    exact_hits = folded_only = total_strings = excluded_hits = 0

    for _, row in df.iterrows():
        canonical, unmatched = apply_to_row(row, tax)
        for field, col in CANONICAL.items():
            cols[col].append(canonical[field])
        # The per-article count is GAPS only. An article tagged "Technology"
        # is not under-covered — that entry was measured and cut on purpose.
        gaps = [s for s in unmatched if not tax.is_excluded(s)]
        unmatched_counts.append(len(gaps))
        # Deduped per article: the counter answers "how many ARTICLES would an
        # entry for this string reach", which is the question v2 curation asks.
        # Counting mentions would inflate strings an article happens to repeat.
        miss_counter.update(set(gaps))
        for field in FIELDS:
            for s in _as_list(row.get(field)):
                total_strings += 1
                if tax.lookup(s) is None:
                    if tax.is_excluded(s):
                        excluded_hits += 1
                    continue
                if tax.is_exact(s):
                    exact_hits += 1
                else:
                    folded_only += 1

    for col, values in cols.items():
        df[col] = values
    df["taxonomy_unmatched"] = unmatched_counts
    df["taxonomy_version"] = tax.version

    tagged = sum(1 for a, b in zip(cols["canonical_concepts"],
                                   cols["canonical_topics"]) if a or b)
    n = len(df)
    matched = exact_hits + folded_only
    return {
        "version": tax.version,
        "entries": len(tax),
        "articles": n,
        "articles_tagged": tagged,
        "article_coverage": round(100 * tagged / n, 1) if n else 0.0,
        "strings_total": total_strings,
        "strings_matched": matched,
        "strings_exact": exact_hits,
        "strings_folded_only": folded_only,
        "strings_excluded": excluded_hits,
        "strings_gap": total_strings - matched - excluded_hits,
        # The health metric, and the thing Phase D watches for a v2 trigger.
        # Deliberate exclusions are removed from BOTH sides: counting them as
        # misses would make every curation decision look like a regression.
        "miss_rate": round(
            100 * (total_strings - matched - excluded_hits)
            / (total_strings - excluded_hits), 1)
        if total_strings - excluded_hits else 0.0,
        "top_unmatched": miss_counter.most_common(15),
        # The actionable half of the health metric. The raw miss rate is
        # dominated by an irreducible tail — three quarters of gap strings are
        # used by exactly one article and will never deserve an entry — so it
        # barely moves and makes a poor trigger. This counts gaps common
        # enough to be worth curating, and the articles they would reach.
        "v2_candidates": sum(1 for c in miss_counter.values() if c >= V2_CANDIDATE_MIN),
        "v2_candidate_articles": sum(c for c in miss_counter.values()
                                     if c >= V2_CANDIDATE_MIN),
        "gap_strings_distinct": len(miss_counter),
        "gap_strings_singleton": sum(1 for c in miss_counter.values() if c == 1),
    }


def format_report(report: dict) -> str:
    lines = [
        f"Taxonomy v{report['version']}: {report['entries']} entries -> "
        f"{report['articles_tagged']:,}/{report['articles']:,} articles tagged "
        f"({report['article_coverage']}%)",
        f"  strings: {report['strings_matched']:,} matched "
        f"({report['strings_exact']:,} exact + {report['strings_folded_only']:,} by case-fold), "
        f"{report['strings_gap']:,} unmatched, "
        f"{report['strings_excluded']:,} excluded by curation",
        f"  miss rate {report['miss_rate']}%  "
        f"({report['gap_strings_distinct']:,} distinct gap strings, "
        f"{100 * report['gap_strings_singleton'] // max(report['gap_strings_distinct'], 1)}% "
        "used by one article)",
        f"  v2 trigger: {report['v2_candidates']} gap strings reach >={V2_CANDIDATE_MIN} "
        f"articles ({report['v2_candidate_articles']:,} article-tags on the table)",
    ]
    if report["top_unmatched"]:
        head = ", ".join(f"{s} ({n})" for s, n in report["top_unmatched"][:8])
        lines.append(f"  top v2 candidates: {head}")
    return "\n".join(lines)
