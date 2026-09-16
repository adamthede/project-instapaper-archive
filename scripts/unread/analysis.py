"""The analysis: what the queue says, and where it diverges from what was read.

The plan's six questions, in order: what was saved and never read and whether
it differs in kind; where the drift between the two corpora is largest; what
aged out; what was nearly finished; which sources were trusted enough to save
but not to read; and what survives.

Three rules run through every function here, and each is a test.

**A low-confidence `why_saved` is excluded from every aggregate.** It is the
only condition on which the field ships at all.

**An unknown `aged_out` is excluded from the denominator, not counted as still
current.** Folding None into False would understate the aged-out share by
exactly the number of items the model would not judge.

**A comparison between the two corpora carries its caveat with it.** The unread
summaries were built on whole articles and the read corpus's on
first-10,000-character truncations. A caveat that lives only in a document can
be separated from the numbers it qualifies.
"""
import statistics
from collections import Counter, defaultdict

from . import derive

READ_IT_LATER_SOURCES = ("instapaper", "matter")

# The unread run sends whole bodies; the read corpus was enriched under a
# 10,000-character cap. Any topic comparison between the two has to say so.
COMPARISON_CAVEAT = (
    "The unread summaries were generated from full article bodies; the read "
    "corpus's were generated under a 10,000-character cap, which 28 of 79 "
    "sampled bodies exceed. The unread side therefore saw more of each article "
    "than the read side did, and a topic present on one and absent on the "
    "other may be a difference in how much text the model read rather than a "
    "difference in what was saved."
)

# What the ranking is made of, and nothing else. The shortlist is ranked on the
# enrichment rather than on a fresh model pass, so every part traces to a field.
ABANDONMENT_WEIGHT = {derive.NEVER_OPENED: 0.0, derive.STARTED: 0.5,
                      derive.NEARLY: 1.0}
ABANDONMENT_SCALE = 0.6


def _topics(record):
    return [str(t).strip() for t in (record.get("ai_topics") or []) if str(t).strip()]


def _clean(records):
    """Rows whose text the prompt's own CONTENT_VALID guard did not reject."""
    return [r for r in records if not r.get("content_corrupted")]


def _domain(record):
    host = str(record.get("domain") or "").lower()
    return host[4:] if host.startswith("www.") else host


# ---------------------------------------------------------------------------
# the aggregates
# ---------------------------------------------------------------------------

def by_saved_year(records):
    """Every item by the year it was saved, dead links included.

    An item that resolves nowhere is still an intention with a date on it. The
    queue's shape by year is a fact about the saving, not about the fetching.
    """
    counts = Counter(r.get("saved_year") for r in records if r.get("saved_year"))
    return dict(sorted(counts.items()))


def by_domain(records, limit=None):
    """Sources, most-saved first. The www prefix is folded in."""
    counts = Counter(_domain(r) for r in records if _domain(r))
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return ranked[:limit] if limit else ranked


def by_topic(records, limit=None):
    counts = Counter()
    for record in _clean(records):
        counts.update(_topics(record))
    if limit:
        return Counter(dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]))
    return counts


def resolve_counts(records):
    return dict(Counter(r.get("resolve_path") for r in records))


def dead_fraction_by_year(records):
    """Link rot by saved year. A single corpus-wide percentage says nothing."""
    totals, dead = Counter(), Counter()
    for record in records:
        year = record.get("saved_year")
        if not year:
            continue
        totals[year] += 1
        if record.get("resolve_path") == "metadata":
            dead[year] += 1
    return {year: {"total": totals[year], "dead": dead[year],
                   "fraction": round(dead[year] / totals[year], 4)}
            for year in sorted(totals)}


def survival(records):
    """Where each item's text still lives.

    Kept apart on purpose: the finding is that about half of a twelve-year
    reading list no longer serves its article from its own URL, and it is the
    Internet Archive and Instapaper's stored copies rather than the publishers
    holding it up. Folding those together erases the finding.
    """
    counts = Counter(r.get("resolve_path") for r in records)
    return {
        "live_web": counts.get("direct", 0),
        "archived_only": counts.get("instapaper", 0) + counts.get("wayback", 0),
        "dead": counts.get("metadata", 0),
        "total": len(records),
    }


# ---------------------------------------------------------------------------
# the inference
# ---------------------------------------------------------------------------

def why_saved_summary(records):
    """What the inference is allowed to say, and what it could not.

    `counted` is the aggregate population: an inference the model offered at
    high or medium confidence. `no_inference` is a result in its own right -
    the plan requires an empty answer be treated as one.
    """
    counted = excluded = none = 0
    for record in records:
        why = (record.get("why_saved") or "").strip()
        confidence = record.get("why_saved_confidence")
        if not why:
            none += 1
        elif confidence == "low":
            excluded += 1
        else:
            counted += 1
    return {"total": len(records), "counted": counted,
            "excluded_low_confidence": excluded, "no_inference": none}


def counted_inferences(records):
    """The rows any why_saved aggregate is allowed to be built from."""
    return [r for r in records
            if (r.get("why_saved") or "").strip()
            and r.get("why_saved_confidence") in ("high", "medium")]


def aging_curve(records):
    """The aged-out share by saved year, over what the model actually judged."""
    totals, judged, aged, unknown = Counter(), Counter(), Counter(), Counter()
    for record in records:
        year = record.get("saved_year")
        if not year:
            continue
        totals[year] += 1
        verdict = record.get("aged_out")
        if verdict is None or record.get("content_corrupted"):
            unknown[year] += 1
            continue
        judged[year] += 1
        if verdict:
            aged[year] += 1
    return {year: {"total": totals[year], "judged": judged[year],
                   "aged_out": aged[year], "unknown": unknown[year],
                   # None, not 0.0: a year nothing was judged in has no share,
                   # and 0.0 would read as "nothing aged out".
                   "fraction": (round(aged[year] / judged[year], 4)
                                if judged[year] else None)}
            for year in sorted(totals)}


# ---------------------------------------------------------------------------
# abandonment
# ---------------------------------------------------------------------------

def abandonment_bands(records):
    """Counts and lengths per band, every band present even when empty."""
    grouped = defaultdict(list)
    for record in records:
        band = record.get("abandonment") or derive.NEVER_OPENED
        grouped[band].append(record)

    out = {}
    for band in derive.BANDS:
        rows = grouped.get(band, [])
        words = [int(r.get("body_words") or 0) for r in rows if r.get("body_words")]
        topics = by_topic(rows, limit=8)
        out[band] = {
            "count": len(rows),
            "median_words": int(statistics.median(words)) if words else None,
            "mean_words": int(statistics.mean(words)) if words else None,
            "top_topics": [t for t, _ in topics.most_common(8)],
        }
    return out


# ---------------------------------------------------------------------------
# the read corpus, and the drift against it
# ---------------------------------------------------------------------------

def read_corpus_topics(frame):
    """Topics the read corpus carries, by the year the article was saved.

    Read-it-later articles only. Only 6,780 of the index's 17,340 rows went
    through the same save-then-read loop the unread queue is the other half of;
    the other 10,560 are the legacy document archive and were never saved with
    an intention to read later.
    """
    import pandas as pd

    rows = frame
    if "source" in rows.columns:
        rows = rows[rows["source"].isin(READ_IT_LATER_SOURCES)]
    if "content_corrupted" in rows.columns:
        rows = rows[rows["content_corrupted"] != True]  # noqa: E712

    out = defaultdict(Counter)
    dates = pd.to_datetime(rows["date_saved"], errors="coerce", format="mixed")
    for (_, record), saved in zip(rows.iterrows(), dates):
        if saved is None or pd.isna(saved):
            continue
        topics = record.get("topics")
        if topics is None:
            continue
        values = [str(t).strip() for t in list(topics) if str(t).strip()]
        if values:
            out[int(saved.year)].update(values)
    return dict(out)


def drift_by_year(records, read_topics):
    """Mean per-item distance from what was read the same year it was saved."""
    grouped = defaultdict(list)
    for record in _clean(records):
        year = record.get("saved_year")
        if year:
            grouped[year].append(record)

    report = {}
    for year in sorted(grouped):
        baseline = list((read_topics.get(year) or {}).keys())
        drifts = [derive.topic_drift(_topics(r), baseline) for r in grouped[year]]
        measured = [d for d in drifts if d is not None]
        report[year] = {
            "items": len(grouped[year]),
            "measured": len(measured),
            # None where the read corpus has no baseline for the year. 1.0
            # there would invent the project's own finding.
            "mean_drift": round(statistics.mean(measured), 4) if measured else None,
            "read_topics": len(baseline),
        }
    return report


def peak_drift_year(report):
    """The year the intentions diverged most from the behaviour."""
    measurable = {y: v["mean_drift"] for y, v in report.items()
                  if v.get("mean_drift") is not None}
    if not measurable:
        return None
    return sorted(measurable.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def saved_versus_read(records, read_topics, limit=20):
    """The two topic distributions, side by side, carrying their caveat."""
    saved = by_topic(records)
    read = Counter()
    for counts in read_topics.values():
        read.update(counts)

    saved_only = [t for t, _ in saved.most_common() if t not in read][:limit]
    read_only = [t for t, _ in read.most_common() if t not in saved][:limit]
    return {
        "saved_top": saved.most_common(limit),
        "read_top": read.most_common(limit),
        "saved_but_not_read": saved_only,
        "read_but_not_saved": read_only,
        "caveat": COMPARISON_CAVEAT,
    }


def sources_saved_not_read(records, read_frame, limit=20):
    """Domains with a high save rate and a low read rate.

    Question 5: a source trusted enough to save but not enough to read is a
    specific kind of aspiration.
    """
    saved = Counter(_domain(r) for r in records if _domain(r))
    read = Counter()
    if read_frame is not None and "url" in read_frame.columns:
        from urllib.parse import urlsplit
        source_rows = read_frame
        if "source" in read_frame.columns:
            source_rows = read_frame[read_frame["source"].isin(READ_IT_LATER_SOURCES)]
        for url in source_rows["url"].dropna():
            host = urlsplit(str(url)).netloc.lower()
            read[host[4:] if host.startswith("www.") else host] += 1

    rows = []
    for domain, saved_count in saved.most_common():
        read_count = read.get(domain, 0)
        rows.append({
            "domain": domain,
            "unread": saved_count,
            "read": read_count,
            "unread_share": round(saved_count / (saved_count + read_count), 4)
            if (saved_count + read_count) else None,
        })
    rows.sort(key=lambda r: (-(r["unread_share"] or 0), -r["unread"], r["domain"]))
    return rows[:limit]


# ---------------------------------------------------------------------------
# the shortlist
# ---------------------------------------------------------------------------

def shortlist(records, read_topics_now, limit=25):
    """The pieces that hold up today, ranked, each with one line of reasoning.

    Ranked on the enrichment rather than on a fresh model pass, so every part
    of a score traces to a field that can be checked. Eligibility is strict:
    `aged_out` must be explicitly False - a row the model would not judge is
    not a row to put in front of anyone as still current - and a row the
    CONTENT_VALID guard rejected is never recommended.
    """
    weights = {str(k): float(v) for k, v in (read_topics_now or {}).items()}
    eligible = [r for r in records
                if r.get("aged_out") is False and not r.get("content_corrupted")]

    raw = {}
    for record in eligible:
        raw[record["url_sha256"]] = sum(weights.get(t, 0.0) for t in _topics(record))
    ceiling = max(raw.values()) if raw else 0.0

    picks = []
    for record in eligible:
        overlap = raw[record["url_sha256"]] / ceiling if ceiling else 0.0
        band = record.get("abandonment") or derive.NEVER_OPENED
        band_score = ABANDONMENT_WEIGHT.get(band, 0.0) * ABANDONMENT_SCALE
        parts = {"aged_out": 0.0, "topic_overlap": round(overlap, 4),
                 "abandonment": round(band_score, 4)}
        picks.append({
            "url_sha256": record["url_sha256"],
            "title": record.get("title", ""),
            "url": record.get("url", ""),
            "domain": _domain(record),
            "saved_year": record.get("saved_year"),
            "abandonment": band,
            "read_progress": record.get("read_progress"),
            "topics": _topics(record)[:5],
            "why_saved": record.get("why_saved", ""),
            "why_saved_confidence": record.get("why_saved_confidence"),
            "why_saved_kind": record.get("why_saved_kind", "inference"),
            "score": round(sum(parts.values()), 4),
            "score_parts": parts,
            "reason": _reason(record, overlap, weights),
        })

    # Deterministic: score, then the hash. Two runs over the same corpus that
    # disagree cannot be reviewed, and this output ships to be reviewed.
    picks.sort(key=lambda p: (-p["score"], p["url_sha256"]))
    return picks[:limit]


def _reason(record, overlap, weights):
    """One line, never more. The reason is what lets the ranking be judged."""
    bits = []
    band = record.get("abandonment")
    if band == derive.NEARLY:
        bits.append("you were nearly through it")
    elif band == derive.STARTED:
        progress = float(record.get("read_progress") or 0.0)
        bits.append(f"you got {progress:.0%} in and stopped")
    else:
        bits.append("never opened")

    shared = [t for t in _topics(record) if weights.get(t)]
    if shared:
        bits.append("on " + ", ".join(shared[:2]) + ", which you still read")
    elif overlap == 0:
        bits.append("on subjects you have since left alone")

    year = record.get("saved_year")
    if year:
        bits.append(f"saved {year}")
    return "; ".join(bits).replace("\n", " ")


# ---------------------------------------------------------------------------
# the whole report
# ---------------------------------------------------------------------------

def build(records, read_topics=None, read_frame=None, shortlist_limit=25):
    """Every private output the plan names, in one dict."""
    read_topics = read_topics or {}
    now = Counter()
    for year, counts in read_topics.items():
        # What he reads now, weighted to the recent years rather than to 2012.
        weight = 3 if int(year) >= 2023 else 1
        for topic, count in counts.items():
            now[topic] += count * weight

    drift = drift_by_year(records, read_topics)
    return {
        "items": len(records),
        "by_saved_year": by_saved_year(records),
        "by_domain": by_domain(records, limit=40),
        "by_topic": by_topic(records, limit=40).most_common(40),
        "resolve_counts": resolve_counts(records),
        "survival": survival(records),
        "dead_fraction_by_year": dead_fraction_by_year(records),
        "aging_curve": aging_curve(records),
        "abandonment_bands": abandonment_bands(records),
        "why_saved": why_saved_summary(records),
        "drift_by_year": drift,
        "peak_drift_year": peak_drift_year(drift),
        "saved_versus_read": saved_versus_read(records, read_topics),
        "sources_saved_not_read": sources_saved_not_read(records, read_frame),
        "shortlist": shortlist(records, now, limit=shortlist_limit),
        # It carries titles and URLs. It ships to reading.adamthede.com behind
        # Cloudflare Access; the public record gets a count at most.
        "shortlist_visibility": "private",
    }
