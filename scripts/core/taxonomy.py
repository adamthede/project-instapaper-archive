#!/usr/bin/env python3
"""Join the curated taxonomy against each article's free-text strings.

Phase C of the controlled-vocabulary plan. No inference happens here: every
article already carries free-text ``concepts`` and ``topics`` from earlier
enrichment, and the taxonomy already enumerates the alias strings that map to
each canonical entry. This is a dictionary lookup, which is why the plan calls
it the cheap half.

Four things are deliberate.

**``canonical_entries`` is the vocabulary; the two per-field columns are
provenance.** This one matters most and is easy to get backwards. Splitting
canonical output by source field leaves ``canonical_concepts`` at 29.7% top-20
article coverage and ``canonical_topics`` at 32.3% — both under the 40% bar,
and both within a point of the 28.9% / 33.5% Phase A measured for the RAW
axes. Pooling reaches 41.2% and clears it. Phase A already settled this ("the
axes MERGE into one vocabulary"); a split canonical output walks straight back
into the failure it settled. The rankability gate must read the pooled column.

**Routing is by SOURCE FIELD, not by the entry's axis label.** Within that,
an alias found in an article's ``topics`` lands in ``canonical_topics``; one
found in ``concepts`` lands in ``canonical_concepts``. The same entry can be
reached from either, because the clustering pooled both fields. Using the
entry's own ``axis`` would be worse: those labels are Qwen's and are
demonstrably unreliable (iPod, iTunes and Search Engines are all labelled
"concept"), and nothing downstream should depend on them.

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

import pandas as pd

import yaml

FIELDS = ("concepts", "topics")

# A gap string must reach at least this many ARTICLES before it is worth an
# entry. Chosen against the measured distribution, not by taste: at >=25 there
# are 388 candidates worth ~15,400 article-tags, while >=5 sweeps in 4,728
# strings most of which are near-duplicates of each other.
V2_CANDIDATE_MIN = 25
CANONICAL = {"concepts": "canonical_concepts", "topics": "canonical_topics"}

# The column the rankability gate must read, and the reason it exists.
#
# The per-field columns above record WHICH field reached an entry, which is
# useful provenance. They are not the vocabulary. Measured on the live index,
# canonical_concepts tops out at 29.7% top-20 article coverage and
# canonical_topics at 32.3% — both under the 40% bar, and both within a point
# of the 28.9% / 33.5% that Phase A measured for the raw axes. Pooling them
# reaches 41.2% and clears it.
#
# That is the same finding Phase A settled with ("the axes MERGE into one
# vocabulary"), and splitting the canonical output by source field walks
# straight back into it. This union is the vocabulary; the two columns above
# are notes about where each hit came from.
POOLED = "canonical_entries"


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


KNOWN_TOP_LEVEL = frozenset({"version", "generated_by", "entries", "excluded_aliases",
                             "derivation", "gate_reviewed"})


def load(path: Path) -> Taxonomy:
    """Parse and validate a taxonomy file.

    EVERY failure leaves here as TaxonomyError, without exception. The caller
    in build_index catches that one type and continues, and it has to be able
    to: the taxonomy step runs AFTER the vault scan and before ``to_parquet``,
    so anything that escapes discards an hour of SMB walking and writes no
    index at all. One stray character in a hand-edited file must not cost that.

    Before this was tightened, a YAML syntax error, an entry missing its
    ``aliases``, and an empty file escaped as ParserError / KeyError /
    AttributeError and killed the nightly — the three most likely
    malformations hitting the worst available outcome.
    """
    path = Path(path)
    try:
        doc = yaml.safe_load(path.read_text())
    except (yaml.YAMLError, OSError, UnicodeDecodeError) as exc:
        raise TaxonomyError(f"{path} could not be read: {exc}") from exc

    if not isinstance(doc, dict):
        raise TaxonomyError(
            f"{path} parsed as {type(doc).__name__}, not a mapping — "
            "expected a document with an 'entries:' key"
        )
    # A misspelled `excluded_alias:` loads clean as "no exclusions": the metric
    # shifts, the rejected entries return to leading the v2 candidate list, and
    # nothing says so. Same silent-typo class the curation applier guards.
    unknown = sorted(set(doc) - KNOWN_TOP_LEVEL)
    if unknown:
        raise TaxonomyError(
            f"{path} has unknown key(s) {unknown} — expected some of "
            f"{sorted(KNOWN_TOP_LEVEL)}"
        )

    entries = doc.get("entries") or []
    if not isinstance(entries, list):
        raise TaxonomyError(f"{path}: 'entries' is {type(entries).__name__}, not a list")
    if not entries:
        raise TaxonomyError(f"{path} has no entries")

    version = doc.get("version")
    # Defaulting a missing version to 1 would stamp taxonomy_version=1 on every
    # row of a v2 index — destroying the exact signal Phase D needs to notice a
    # re-tag, and doing it silently.
    #
    # `isinstance(True, int)` is True in Python, so a bare `version: true`
    # passes an int check and stamps taxonomy_version=True on 17,000 rows.
    # And the value goes into an int64 parquet column, so anything outside
    # that range fails at write time — after the vault scan, which is the
    # expensive thing this whole loader exists to protect.
    if isinstance(version, bool) or not isinstance(version, int):
        raise TaxonomyError(
            f"{path}: 'version' is {version!r}; it must be a plain int, because "
            "every row is stamped with it and a wrong stamp makes a re-tag invisible"
        )
    if not (-2**63 <= version < 2**63):
        raise TaxonomyError(
            f"{path}: 'version' {version} does not fit the int64 column it is "
            "written to"
        )

    by_alias, by_folded, folded_owner = {}, {}, {}
    for i, e in enumerate(entries):
        if not isinstance(e, dict):
            raise TaxonomyError(f"{path}: entry {i} is {type(e).__name__}, not a mapping")
        missing = [k for k in ("name", "aliases") if k not in e]
        if missing:
            raise TaxonomyError(f"{path}: entry {i} is missing {missing}")
        name = e["name"]
        # A None or dict name loads happily and then becomes the canonical
        # value written into canonical_entries for every article it matches —
        # garbage propagated into the index rather than caught at the door.
        if not isinstance(name, str) or not name.strip():
            raise TaxonomyError(
                f"{path}: entry {i} has name {name!r}; it must be a non-empty "
                "string, because it is written into the index as the canonical value"
            )
        if not isinstance(e["aliases"], list) or not e["aliases"]:
            raise TaxonomyError(
                f"{path}: entry {name!r} has no usable aliases "
                f"({type(e['aliases']).__name__})"
            )
        for alias in e["aliases"]:
            if not isinstance(alias, str):
                raise TaxonomyError(
                    f"{path}: entry {name!r} has a non-string alias {alias!r}"
                )
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

    excluded = doc.get("excluded_aliases")
    if excluded is None:
        excluded = []
    # A scalar here is the nastiest shape available: `excluded_aliases: Technology`
    # iterates PER CHARACTER, so is_excluded('e') becomes True and single letters
    # start silently vanishing from the gap counts.
    if not isinstance(excluded, list):
        raise TaxonomyError(
            f"{path}: 'excluded_aliases' is {type(excluded).__name__}, not a list — "
            "a bare string would be read one character at a time"
        )
    bad = [x for x in excluded if not isinstance(x, str)]
    if bad:
        raise TaxonomyError(f"{path}: non-string excluded_aliases {bad[:5]}")

    # Compared on the FOLDED form because that is what is_excluded uses. An
    # exact-only check would pass an `excluded: ["privacy"]` sitting beside an
    # entry owning "Privacy", where the exclusion is simply dead — the guard
    # would advertise a property it does not have.
    owned_folds = {_fold(a): a for a in by_alias}
    both = sorted({owned_folds[_fold(x)] for x in excluded if _fold(x) in owned_folds})
    if both:
        raise TaxonomyError(
            f"{len(both)} alias(es) are both excluded and owned by an entry: "
            f"{both[:5]} — the taxonomy contradicts itself"
        )
    return Taxonomy(version, by_alias, by_folded,
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
            # Non-strings reach here from a malformed enrichment pass. Left
            # alone they print as v2 candidates (None, 3, nan) and an unhashable
            # one raises inside set(gaps), which under the old build wiring
            # killed the whole run.
            if not isinstance(s, str):
                continue
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
    # An absent source column is indistinguishable from an empty one once
    # row.get() has turned it into None: the join matches nothing, every
    # canonical column is written empty, and miss_rate comes out 0.0 — the BEST
    # possible value. Anyone reading the nightly log sees a flawless number
    # from a join that did nothing. corpus.numeric_column refuses the same
    # class of schema drift for word_count; this refuses it here.
    absent = [f for f in FIELDS if f not in df.columns]
    if absent:
        raise TaxonomyError(
            f"source column(s) {absent} are missing from the index — the join "
            "would match nothing and report a perfect miss rate"
        )

    cols = {c: [] for c in CANONICAL.values()}
    cols[POOLED] = []
    unmatched_counts, miss_counter = [], collections.Counter()
    exact_hits = folded_only = total_strings = excluded_hits = 0

    # Junk-scrape rows are in the index but never reach a page — corpus.prepare
    # filters them. Counting their strings as v2 candidates recommends entries
    # that would reach ~0 real articles: 915 corrupted rows (5.3%) supplied
    # FIVE of the top six candidates, at 92-98% corrupted each. That is a
    # bigger contamination than the 26 curation exclusions this report already
    # corrects for.
    #
    # This is 97% of the correction, not all of it. corpus.prepare drops three
    # classes — corrupted (915), pre-min-year (29), undated (0) — and only the
    # first is visible from here. Importing site/corpus to get exact parity
    # would point a scripts/core module at the site layer, which is the wrong
    # direction; 29 rows cannot manufacture a >=25-article candidate on their
    # own, so the residual is stated rather than chased.
    corrupted = (df["content_corrupted"].fillna(False).astype(bool)
                 if "content_corrupted" in df.columns
                 else pd.Series(False, index=df.index))
    candidate_rows = 0

    for idx, row in df.iterrows():
        canonical, unmatched = apply_to_row(row, tax)
        for field, col in CANONICAL.items():
            cols[col].append(canonical[field])
        cols[POOLED].append(sorted({n for f in FIELDS for n in canonical[f]}))
        # The per-article count is GAPS only. An article tagged "Technology"
        # is not under-covered — that entry was measured and cut on purpose.
        # Note this counts OCCURRENCES, unlike miss_counter below: the column
        # answers "how much of this row went unmatched".
        gaps = [s for s in unmatched if not tax.is_excluded(s)]
        unmatched_counts.append(len(gaps))
        if not bool(corrupted.loc[idx]):
            candidate_rows += 1
            # Deduped per article, and unlike the column above this counts
            # ARTICLES: it answers "how many articles would an entry for this
            # string reach", which is the question v2 curation asks.
            miss_counter.update(set(gaps))
        for field in FIELDS:
            for s in _as_list(row.get(field)):
                if not isinstance(s, str):
                    continue
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

    tagged = sum(1 for names in cols[POOLED] if names)
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
        # Printed beside the adjusted rate because the adjustment is
        # monotone-improvable: a v2 that curates nothing and excludes 5,000
        # strings would look like progress. Seeing both makes that visible.
        # (On v1 the adjustment is worth 0.2 points; its real value is the
        # candidate LIST, not the rate.)
        "miss_rate_raw": round(100 * (total_strings - matched) / total_strings, 1)
        if total_strings else 0.0,
        "candidate_rows": candidate_rows,
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
        f"  miss rate {report['miss_rate']}% "
        f"(raw {report['miss_rate_raw']}%)  "
        f"({report['gap_strings_distinct']:,} distinct gap strings, "
        f"{round(100 * report['gap_strings_singleton'] / max(report['gap_strings_distinct'], 1))}% "
        "used by one article)",
        f"  v2 trigger: {report['v2_candidates']} gap strings reach >={V2_CANDIDATE_MIN} "
        f"articles ({report['v2_candidate_articles']:,} article-tags on the table; "
        f"counted over {report['candidate_rows']:,} non-corrupted rows)",
    ]
    if report["top_unmatched"]:
        head = ", ".join(f"{s} ({n})" for s, n in report["top_unmatched"][:8])
        lines.append(f"  top v2 candidates: {head}")
    return "\n".join(lines)
