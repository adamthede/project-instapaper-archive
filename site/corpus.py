"""The Parquet index as the deep-dive pages see it.

Week pages render from synthesis frontmatter and never touch this module.
The Phase 5 pages (year rollups, orgs facet, article detail) render from
`data/archive_index.parquet` instead - the audit's option (1): the index is
the deployable artifact, so a missing vault degrades to stale data rather
than to a site that claims Adam read fewer articles than he did.

Three rules hold everywhere in here, and every one of them is an audit
finding rather than a preference:

- `content_corrupted` rows are excluded from every count on every page.
- `date_read` is era-aware, mirroring scripts/core/weekly_synthesis.py:
  Matter rows are dated only by a real archive event; legacy and Instapaper
  rows fall back to `date_saved`.
- 36 rows carry a `date_read` before 2005 (one is 1953) and would stretch
  any year axis across seven decades. They are dropped and *counted*, so the
  pages can say so instead of quietly hiding them.
"""
import datetime as dt
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "core"))
import entity_hygiene  # noqa: E402

MIN_YEAR = 2005

# Flesch-Kincaid on scraped web text is noisy at the top end: 730 of 17,416
# rows claim a reading level above grade 20 and one is negative. Every grade
# number on the site is clipped into this band, and every page that prints one
# says so - an unclipped mean reads 11.75 where the honest figure is 11.31.
GRADE_MIN, GRADE_MAX = 0.0, 20.0
# A "densest read" ought to be something substantial. Below this, a grade of
# 19.8 is one long sentence in a stub, not a demanding article. 800 sits just
# above the corpus median length (761 words).
DENSEST_MIN_WORDS = 800
# Below this many graded articles a year's average reading level is a fact
# about three articles, not about a year. 2021 holds three and averages 14.00,
# which is the highest figure in the whole series - it must not be allowed to
# be crowned "the densest year" or to set the top of the axis.
COMPLEXITY_MIN_GRADED = 25

# Top-20 ARTICLE coverage a list-valued column must clear before the site will
# rank it on a page of its own. The two precedents the audit set: orgs at 42.9%
# earned a ranked page, topics at 25.5% (73.3% singletons) did not. The bar is
# stated here so a new column's verdict is a measurement rather than a taste.
RANKABLE_HEAD_COVERAGE = 40.0

# Where a scrubbed-away fabricated cast is kept. Quarantine rather than
# deletion: /people/ tells the story of this defect and needs the evidence to
# outlive the fix. Re-exported, never re-spelled - see the constant's own
# comment in entity_hygiene for what a second spelling costs.
PEOPLE_QUARANTINE = entity_hygiene.PEOPLE_QUARANTINE

# `source` carries eight values; readers care about three eras.
ERA_ORDER = ("legacy", "instapaper", "matter")
ERA_LABELS = {
    "legacy": "Legacy files",
    "instapaper": "Instapaper",
    "matter": "Matter",
    "unknown": "Unattributed",
}


def era_of(source):
    """Which of the three saving eras a row belongs to.

    The index splits the legacy import by file type (legacy_pdf, legacy_txt,
    legacy_doc, legacy_htm, legacy_rtf). That is a useful provenance detail and
    a terrible axis - it is one era, five scanners.
    """
    s = str(source or "").strip().lower()
    if s.startswith("legacy"):
        return "legacy"
    if s in ("instapaper", "matter"):
        return s
    return "unknown"


def derive_date_read(df):
    """Era-aware read date. Matter rows without an archive event stay NaT."""
    read = df["date_archived"].copy()
    non_matter = df["source"] != "matter"
    read[non_matter] = read[non_matter].fillna(df.loc[non_matter, "date_saved"])
    return read


def source_host(url):
    if url is None or (isinstance(url, float) and pd.isna(url)):
        return ""
    host = urlparse(str(url)).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def as_list(v):
    """Entity columns arrive as lists, numpy arrays, or NaN."""
    if v is None:
        return []
    if isinstance(v, float) and pd.isna(v):
        return []
    try:
        return [str(x).strip() for x in list(v) if str(x).strip()]
    except TypeError:
        return []


@dataclass
class Corpus:
    rows: "pd.DataFrame"
    excluded_corrupted: int = 0
    excluded_undated: int = 0
    excluded_pre_min_year: int = 0
    years: list = field(default_factory=list)
    # Rows whose `people` list was blanked as extraction boilerplate, and the
    # clusters they formed. Carried rather than discarded so /people/ can state
    # on the page what was taken out of its own ranking.
    scrubbed_people: int = 0
    # How many of those scrubbed rows survive every OTHER filter and would
    # therefore have reached a ranked page. On the 2026-08-20 index this is 0
    # - the corrupted-content filter already removes all 283 - and the page
    # says so rather than taking credit for a ranking that was already clean.
    scrubbed_people_in_corpus: int = 0
    people_clusters: list = field(default_factory=list)

    def __len__(self):
        return len(self.rows)

    def year(self, y):
        return self.rows[self.rows["year"] == int(y)]

    @property
    def year_axis(self):
        """Every year from the first to the last, INCLUDING any with no rows.

        `years` is the years that have data, and it drives which year pages get
        built - an empty year must not get a page. A time series is the
        opposite case: dropping an empty 2013 from the axis draws 2012 and 2014
        as adjacent columns and invents continuity the archive does not have.
        """
        if not self.years:
            return []
        return list(range(int(self.years[0]), int(self.years[-1]) + 1))


def prepare(df):
    df = df.copy()
    total = len(df)

    # Entity hygiene runs FIRST, on the whole population, and the order is a
    # correctness property rather than a preference: a cluster only PARTLY
    # flagged corrupted would shrink below any sane threshold once the filter
    # below had run, and the rows that survived - the unflagged ones - are
    # exactly the rows that would carry a fabricated cast onto a ranked page.
    #
    # On the 2026-08-20 index that hazard is latent rather than live: all 283
    # Co.Design rows are also flagged corrupted, so this pass changes nothing
    # the site displays. It is load-bearing for the raw index instead, which
    # dashboard/app.py reads with no corrupted filter at all - there it moves
    # five fabricated names out of the top 15 and six real ones in.
    if "people" in df.columns:
        df, _ = entity_hygiene.scrub(
            df, column="people", quarantine_column=PEOPLE_QUARANTINE,
            log=lambda msg: print(msg, file=sys.stderr))
    # Read the evidence back out of the index rather than out of what THIS run
    # happened to catch. Once build_index writes a cleaned parquet there is
    # nothing left here to detect, and a page sourced from the live detection
    # would go from "here is the archive's one fabrication" to "no fabrication
    # was ever found" the moment the fix took effect.
    people_clusters = entity_hygiene.quarantined_clusters(df, PEOPLE_QUARANTINE)

    if "content_corrupted" in df.columns:
        df = df[df["content_corrupted"] != True]  # noqa: E712
    corrupted = total - len(df)

    read = derive_date_read(df)
    df = df.assign(date_read=read)
    undated = int(df["date_read"].isna().sum())
    df = df[df["date_read"].notna()]

    early = int((df["date_read"] < pd.Timestamp(f"{MIN_YEAR}-01-01")).sum())
    df = df[df["date_read"] >= pd.Timestamp(f"{MIN_YEAR}-01-01")]

    df = df.assign(
        year=df["date_read"].dt.year.astype(int),
        domain=[source_host(u) for u in df["url"]],
        # Positive evidence of a real read event, vs. a date inferred from a
        # filename or a save. Drives the per-year provenance note.
        proxy_dated=(df["source"] != "matter") & df["date_archived"].isna(),
    )
    years = sorted(int(y) for y in df["year"].unique())
    scrubbed_ids = {rid for c in people_clusters for rid in c["row_ids"]}
    return Corpus(rows=df, excluded_corrupted=corrupted, excluded_undated=undated,
                  excluded_pre_min_year=early, years=years,
                  scrubbed_people=len(scrubbed_ids),
                  scrubbed_people_in_corpus=len(scrubbed_ids & set(df.index)),
                  people_clusters=people_clusters)


def load_corpus(index_path):
    return prepare(pd.read_parquet(index_path))


# ---------------------------------------------------------------------------
# aggregates
# ---------------------------------------------------------------------------

def safe_int(v):
    try:
        if v is None or pd.isna(v):
            return 0
        return int(v)
    except (TypeError, ValueError):
        return 0


def numeric_column(rows, name):
    """Coerce an index column to numbers, rejecting a non-numeric dtype.

    Drift is a TYPE question, not a proportion question. errors="coerce" alone
    turned a retyped word_count into a complete, deployable site reporting 0
    words; a share-of-values threshold only moved that cliff, because a
    backfill retypes rows one at a time - at the midpoint of a live retype the
    site would have deployed half the archive's word count with no warning,
    and consistently so, since payload_rows' safe_int drops the same rows.

    A correctly-typed column is int64 or float64; sparse data stays float64
    with NaN. Only a column carrying strings goes object, so the dtype check
    is exact, needs no threshold, and does not change verdict with sample
    size (stats() runs per year page, sometimes over three rows). Raising
    hands the failure to generate()'s deep-dive guard: weeks-only, loud, the
    same outcome as a missing column.
    """
    raw = rows[name]
    if int(raw.notna().sum()) and not pd.api.types.is_numeric_dtype(raw):
        raise ValueError(
            f"index column {name!r} has dtype {raw.dtype} where a number was "
            f"expected - schema drift, refusing to report zeros")
    return pd.to_numeric(raw, errors="coerce")


def stats(rows):
    words_col = numeric_column(rows, "word_count")
    minutes = numeric_column(rows, "reading_time_min").fillna(0).sum()
    domains = {d for d in rows["domain"] if d}
    median = words_col.dropna().median()
    return {
        "articles": len(rows),
        "words": int(words_col.fillna(0).sum()),
        "hours": round(float(minutes) / 60.0, 1),
        "domains": len(domains),
        # The legacy corpus carries no URLs at all, so a domain count is a
        # count over an unstated subset unless the pages say how big it is.
        "url_bearing": int(sum(1 for d in rows["domain"] if d)),
        "median_words": int(median) if pd.notna(median) else 0,
        "proxy_dated": int(rows["proxy_dated"].sum()),
    }


def topic_vocabulary(rows):
    """(distinct topics, share used exactly once) - measured, not quoted.

    The pages explain why topics are not ranked. That explanation carries
    numbers, and numbers pasted from a months-old audit drift away from the
    corpus they claim to describe.
    """
    counts = Counter()
    if "topics" not in rows.columns:
        return 0, 0.0
    for v in rows["topics"]:
        for name in set(as_list(v)):
            counts[name] += 1
    if not counts:
        return 0, 0.0
    singletons = sum(1 for c in counts.values() if c == 1)
    return len(counts), round(singletons / len(counts) * 100, 1)


def month_series(rows, year):
    """(label, month, words, count) for Jan..Dec of the year."""
    out = []
    for m in range(1, 13):
        sel = rows[rows["date_read"].dt.month == m]
        out.append({
            "label": dt.date(int(year), m, 1).strftime("%b"),
            "month": m,
            "count": len(sel),
            "words": int(pd.to_numeric(sel["word_count"], errors="coerce").fillna(0).sum()),
        })
    return out


def top_entities(rows, column, limit=20):
    """Ranked (name, count) for a list-valued entity column.

    Counted once per article - an org named five times in one piece is one
    article's worth of attention, not five.
    """
    counts = Counter()
    if column not in rows.columns:
        return []
    for v in rows[column]:
        for name in set(as_list(v)):
            counts[name] += 1
    return [{"name": name, "count": c}
            for name, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]]


def entity_coverage(rows, column):
    """Share of articles carrying at least one value in this column.

    The orgs page states this rather than implying the ranking covers
    everything: enrichment populated `orgs` on 90.8% of the corpus.
    """
    if not len(rows) or column not in rows.columns:
        return 0.0
    tagged = sum(1 for v in rows[column] if as_list(v))
    return round(tagged / len(rows) * 100, 1)


def head_coverage(rows, column, k=20):
    """Share of ARTICLES carrying at least one of the top-k values.

    Summing the top-k counts and dividing by the row count would double-count
    every article tagged with two of them and report ~83% where the audit
    measured 42.9%. Coverage is a set question, not a sum.
    """
    if not len(rows) or column not in rows.columns:
        return 0.0
    head = {o["name"] for o in top_entities(rows, column, k)}
    hit = sum(1 for v in rows[column] if head & set(as_list(v)))
    return round(hit / len(rows) * 100, 1)


def vocabulary_report(rows, column, k=20):
    """Everything needed to decide whether a column may be ranked on a page.

    One pass, four numbers, all measured on `rows` rather than quoted from the
    audit: vocabulary size, share of articles carrying any value, share of the
    vocabulary used exactly once, and top-k ARTICLE coverage (a set question -
    summing the top-k counts double-counts every article tagged with two of
    them and inflates 45% into 83%).

    `rankable` is the verdict the /concepts/ decision turned on. Measured
    2026-08-20 over 16,346 corpus rows: orgs 45.2%, locations 57.0%,
    topics 25.3%, concepts 22.0%.
    """
    empty = {"column": column, "vocabulary": 0, "tagged": 0, "tagged_share": 0.0,
             "singleton_share": 0.0, "head_k": k, "head_coverage": 0.0,
             "rankable": False, "articles": len(rows)}
    if not len(rows) or column not in rows.columns:
        return empty

    counts = Counter()
    per_row = []
    tagged = 0
    for v in rows[column]:
        names = set(as_list(v))
        per_row.append(names)
        if names:
            tagged += 1
        for name in names:
            counts[name] += 1
    if not counts:
        return empty

    head = {name for name, _ in counts.most_common(k)}
    hit = sum(1 for names in per_row if head & names)
    singles = sum(1 for c in counts.values() if c == 1)
    coverage = round(hit / len(rows) * 100, 1)
    return {
        "column": column,
        "vocabulary": len(counts),
        "tagged": tagged,
        "tagged_share": round(tagged / len(rows) * 100, 1),
        "singleton_share": round(singles / len(counts) * 100, 1),
        "head_k": k,
        "head_coverage": coverage,
        "rankable": coverage >= RANKABLE_HEAD_COVERAGE,
        "articles": len(rows),
    }


# ---------------------------------------------------------------------------
# eras
# ---------------------------------------------------------------------------

def era_split(rows):
    """Article counts and shares per saving era, in chronological order.

    Rendered on the index rather than buried: half this archive predates the
    read-it-later services entirely, and a hero stat row that says "16,346
    articles" without saying so is claiming a tracking history it does not
    have.
    """
    if not len(rows) or "source" not in rows.columns:
        return []
    counts = Counter(era_of(s) for s in rows["source"])
    total = len(rows)
    order = [k for k in ERA_ORDER if counts.get(k)]
    order += [k for k in sorted(counts) if k not in ERA_ORDER and counts[k]]
    return [{"era": k, "label": ERA_LABELS.get(k, k.title()),
             "articles": counts[k], "share": round(counts[k] / total * 100, 1)}
            for k in order]


# ---------------------------------------------------------------------------
# complexity
# ---------------------------------------------------------------------------

def grade_series(rows):
    """`grade_level` as numbers, clipped into the trustworthy band.

    Values outside GRADE_MIN..GRADE_MAX are parser noise (max in the corpus is
    857), so they are pulled to the edge rather than dropped - dropping them
    would silently re-weight the mean toward the easy end.

    An ABSENT column degrades to all-NaN rather than raising, and the pages
    then render no complexity at all. That is the opposite ruling to
    numeric_column's, and deliberately so: a retyped column produces confident
    wrong numbers that nobody can see, while a missing one produces an empty
    band and a stderr line - it announces itself.
    """
    if "grade_level" not in rows.columns:
        print("index has no 'grade_level' column: complexity omitted",
              file=sys.stderr)
        return pd.Series([float("nan")] * len(rows), index=rows.index,
                         dtype="float64")
    raw = numeric_column(rows, "grade_level")
    return raw.clip(GRADE_MIN, GRADE_MAX)


def complexity_stats(rows, min_words=DENSEST_MIN_WORDS):
    """Average clipped grade level, how many rows carry one, how many were
    clipped, and the year's densest substantial read."""
    if not len(rows) or "grade_level" not in rows.columns:
        return {"graded": 0, "avg": None, "clipped": 0, "densest": None,
                "articles": len(rows)}
    raw = numeric_column(rows, "grade_level")
    clipped_series = raw.clip(GRADE_MIN, GRADE_MAX)
    valid = clipped_series.dropna()
    out_of_band = int(((raw > GRADE_MAX) | (raw < GRADE_MIN)).sum())

    densest = None
    words = numeric_column(rows, "word_count").fillna(0)
    # The densest read is picked from IN-BAND rows only. A row clipped down
    # from 857 would otherwise win every year on the strength of a parser bug.
    eligible = rows[(raw >= GRADE_MIN) & (raw <= GRADE_MAX) & (words >= min_words)]
    if len(eligible):
        pick = raw.loc[eligible.index].idxmax()
        row = rows.loc[pick]
        url = str(row.get("url") or "")
        densest = {
            "title": str(row.get("title") or "Untitled"),
            "grade": round(float(raw.loc[pick]), 1),
            "words": safe_int(row.get("word_count")),
            "url": url if url.lower().startswith(("http://", "https://")) else "",
        }
    raw_valid = raw.dropna()
    return {
        "graded": int(valid.count()),
        "avg": round(float(valid.mean()), 2) if len(valid) else None,
        # What the same mean would read if nobody clipped it. The pages print
        # both, because "we clipped the data" is a claim that should cost the
        # reader nothing to check.
        "raw_avg": round(float(raw_valid.mean()), 2) if len(raw_valid) else None,
        "raw_max": round(float(raw_valid.max()), 1) if len(raw_valid) else None,
        "clipped": out_of_band,
        "densest": densest,
        "articles": len(rows),
        "min_words": min_words,
    }


def complexity_by_year(corpus):
    """(year, graded, avg, delta-vs-prior-year) across the whole span.

    The delta compares against the last year that HAD a reading level, not
    against the calendar year before - across an empty year, "no change" would
    be a claim about a year with nothing in it.
    """
    out = []
    prev = None
    for y in corpus.year_axis:
        rows = corpus.year(y)
        graded, avg = 0, None
        if len(rows):
            valid = grade_series(rows).dropna()
            graded = int(valid.count())
            avg = round(float(valid.mean()), 2) if graded else None
        delta = None if (avg is None or prev is None) else round(avg - prev, 2)
        out.append({"year": int(y), "articles": len(rows),
                    "graded": graded, "avg": avg, "delta": delta,
                    "low": bool(graded) and graded < COMPLEXITY_MIN_GRADED})
        if avg is not None:
            prev = avg
    return out


# ---------------------------------------------------------------------------
# sentiment
# ---------------------------------------------------------------------------

SENTIMENTS = ("Positive", "Neutral", "Negative")
# Below this many rated articles a year's mix is a rounding artifact - 2021
# holds three articles and would otherwise render a confident 66.7% Negative.
SENTIMENT_MIN_RATED = 25


def sentiment_by_year(corpus):
    """Share of Positive/Neutral/Negative per year, and the n behind it.

    Shares are computed over RATED articles, not all articles, and both numbers
    travel together so the page can never imply a mix it did not measure. The
    three shares of a rated year sum to 100% by construction: anything the
    enrichment wrote that is not one of the three known labels is counted in
    `other` and excluded from the denominator, rather than silently folded into
    Neutral.
    """
    out = []
    for y in corpus.year_axis:
        rows = corpus.year(y)
        counts = Counter()
        other = 0
        if "sentiment" in rows.columns:
            for s in rows["sentiment"]:
                label = str(s).strip() if s is not None else ""
                if label in SENTIMENTS:
                    counts[label] += 1
                elif label and label.lower() not in ("nan", "none"):
                    other += 1
        rated = sum(counts.values())
        shares = {k: (round(counts[k] / rated * 100, 1) if rated else 0.0)
                  for k in SENTIMENTS}
        if rated:
            # Rounding three shares independently can miss 100 by a tenth. The
            # largest share absorbs the residue so the strip always fills.
            biggest = max(SENTIMENTS, key=lambda k: (shares[k], k))
            shares[biggest] = round(shares[biggest] + (100.0 - sum(shares.values())), 1)
        out.append({"year": int(y), "articles": len(rows), "rated": rated,
                    "counts": {k: counts[k] for k in SENTIMENTS},
                    "other": other, "shares": shares,
                    "low": rated < SENTIMENT_MIN_RATED})
    return out


# ---------------------------------------------------------------------------
# entity x year matrices (the heatmaps)
# ---------------------------------------------------------------------------

def entity_year_matrix(corpus, column, limit=15):
    """Top-`limit` values of a list-valued column, counted per year.

    Rows are ranked by archive-wide article count; columns are every year the
    corpus covers, including the empty ones - a heatmap that quietly drops 2021
    because he read three articles that year is drawing a different archive.
    Counted once per article, matching top_entities().
    """
    names = [o["name"] for o in top_entities(corpus.rows, column, limit)]
    return _matrix(corpus, names, column,
                   lambda row: set(as_list(row.get(column))))


def domain_year_matrix(corpus, limit=15):
    """The same shape for `domain`, which is single-valued rather than a list.

    Covers only the URL-bearing subset; callers must say so on the page. The
    legacy era came in as files and has no host to count.
    """
    counts = Counter(d for d in corpus.rows["domain"] if d)
    names = [name for name, _ in
             sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]]
    return _matrix(corpus, names, "domain",
                   lambda row: {row["domain"]} if row.get("domain") else set())


def _matrix(corpus, names, column, values_of):
    years = corpus.year_axis
    cells = {name: {y: 0 for y in years} for name in names}
    wanted = set(names)
    totals = {}
    for y in years:
        rows = corpus.year(y)
        totals[y] = len(rows)
        if not len(rows) or not wanted:
            continue
        for row in rows.to_dict("records"):
            for hit in values_of(row) & wanted:
                cells[hit][y] += 1
    row_totals = {name: sum(cells[name].values()) for name in names}
    peak = max((c for name in names for c in cells[name].values()), default=0)
    return {"column": column, "names": names, "years": years, "cells": cells,
            "row_totals": row_totals, "year_totals": totals, "peak": peak}


def payload_rows(corpus):
    """Compact per-article records for the client-side detail view.

    Array-of-arrays with a separate field header: the same data as objects
    costs ~40% more bytes across 17k rows, and this payload ships on every
    page load of /articles/. Bodies and summaries are deliberately absent -
    the audit's recommendation is metadata search, and a summary column would
    quadruple the download.
    """
    rows = corpus.rows.sort_values("date_read", ascending=False)
    fields = ["title", "url", "source", "domain", "author",
              "date_read", "date_saved", "words", "reading_time", "grade"]
    # Clipped, one decimal, and null where the index has none: the detail panel
    # prints this number next to a title, and grade 857 next to a headline is
    # worse than no number at all. Measured cost: +0.08 MB over 16,346 rows.
    grades = grade_series(rows).round(1)
    data = []
    for r, grade in zip(rows.itertuples(index=False), grades):
        author = str(getattr(r, "author", "") or "")
        if author in ("Unknown", "By", "nan"):
            author = ""
        saved = getattr(r, "date_saved", None)
        data.append([
            str(r.title or ""),
            str(r.url or "") if isinstance(r.url, str) else "",
            str(r.source or ""),
            r.domain,
            author,
            r.date_read.date().isoformat(),
            saved.date().isoformat() if saved is not None and not pd.isna(saved) else "",
            safe_int(r.word_count),
            round(float(getattr(r, "reading_time_min", 0) or 0), 1)
            if not pd.isna(getattr(r, "reading_time_min", 0)) else 0,
            None if pd.isna(grade) else float(grade),
        ])
    return {"fields": fields, "count": len(data), "articles": data}
