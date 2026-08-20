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
from collections import Counter
from dataclasses import dataclass, field
from urllib.parse import urlparse

import pandas as pd

MIN_YEAR = 2005


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

    def __len__(self):
        return len(self.rows)

    def year(self, y):
        return self.rows[self.rows["year"] == int(y)]


def prepare(df):
    df = df.copy()
    total = len(df)
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
    return Corpus(rows=df, excluded_corrupted=corrupted, excluded_undated=undated,
                  excluded_pre_min_year=early, years=years)


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


def stats(rows):
    words_col = pd.to_numeric(rows.get("word_count"), errors="coerce")
    minutes = pd.to_numeric(rows.get("reading_time_min"), errors="coerce").fillna(0).sum()
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
              "date_read", "date_saved", "words", "reading_time"]
    data = []
    for r in rows.itertuples(index=False):
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
        ])
    return {"fields": fields, "count": len(data), "articles": data}
