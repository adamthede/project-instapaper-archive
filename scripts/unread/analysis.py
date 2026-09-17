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

# The floor on a year that may be called the peak. Adversarial review showed a
# single article taking it, and the metric also runs against how much the read
# corpus carried that year, so a thin year wins twice over. A peak one article
# can move is not a finding.
MIN_DRIFT_ITEMS = 10


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


SURVIVAL_NOTE = (
    "This is which leg of the fetch chain produced the text, NOT whether the "
    "URL is still live. The chain short-circuits: the direct fetch runs only "
    "where Instapaper held no stored text, so every item Instapaper resolved "
    "was never probed against its own URL at all. A live-web survival figure "
    "needs a separate probe of all of them and is not reported here. For "
    "scale, the plan's 100-item sample measured 51 URLs still serving their "
    "own article; this chain cannot reproduce that number and does not try."
)


def survival(records):
    """Where each item's text was recovered from, and what that does not say.

    The first version of this function called the direct-resolved count the
    live web, and adversarial review caught it. The direct leg only ever runs
    after Instapaper has failed, so on a corpus shaped like the plan's measured
    sample it reported 4 items live where the measurement says 51. Question 6
    of the plan - what survives - needs its own probe, and naming a figure the
    chain cannot establish would put a wrong number on a public record.
    """
    counts = Counter(r.get("resolve_path") for r in records)
    return {
        "instapaper_stored_copy": counts.get("instapaper", 0),
        "direct_fetch_after_instapaper_failed": counts.get("direct", 0),
        "wayback_snapshot": counts.get("wayback", 0),
        "text_recovered": (counts.get("instapaper", 0) + counts.get("direct", 0)
                           + counts.get("wayback", 0)),
        "no_text_anywhere": counts.get("metadata", 0),
        "total": len(records),
        "liveness_measured": False,
        "note": SURVIVAL_NOTE,
    }


# ---------------------------------------------------------------------------
# the cover aggregates
# ---------------------------------------------------------------------------

def word_totals(records):
    """Recovered words, and the population that figure was measured over.

    26 of the 492 resolved nowhere and carry no body at all. They are still
    intentions and they still count in every year, host and abandonment figure,
    but they have no length, so the denominator here is the rows that came back
    with text rather than the corpus. The record says that denominator out
    loud; the payload carries it so the page cannot say it wrong.
    """
    words = [int(r.get("body_words") or 0) for r in records if r.get("body_words")]
    return {
        "total": sum(words),
        # None, not 0: a median of 0 reads as "the typical article was empty",
        # which is a claim. No median is the true statement.
        "median": int(statistics.median(words)) if words else None,
        "mean": int(statistics.mean(words)) if words else None,
        "measured_over": len(words),
        "corpus": len(records),
    }


def saved_span(records):
    """The oldest and newest save, to the day.

    Not from `saved_year`. The cover's sentence is about how long one
    particular intention has been sitting there, and a year is the wrong
    precision for that.
    """
    dates = sorted(str(r.get("saved_date")).strip() for r in records
                   if str(r.get("saved_date") or "").strip())
    if not dates:
        return {"oldest": None, "newest": None, "years_spanned": None, "dated": 0}
    return {"oldest": dates[0], "newest": dates[-1],
            "years_spanned": int(dates[-1][:4]) - int(dates[0][:4]) + 1,
            "dated": len(dates)}


CONFIDENCE_GRADES = ("high", "medium", "low")


def confidence_split(records):
    """Every row by the grade the model set on its own inference.

    A different population from `why_saved_summary()`, deliberately. The grade
    and the sentence are separate fields: the model can grade a row low and
    then decline to say anything, and in the live corpus it did that ten times.
    The three grades sum to the whole corpus; `counted` and
    `excluded_low_confidence` sum only over the rows that carried a sentence. A
    bar chart built from the wrong one of those has a total that is not the
    corpus and nothing on the page saying why.
    """
    counts = Counter(str(r.get("why_saved_confidence") or "").strip().lower()
                     for r in records)
    split = {grade: counts.get(grade, 0) for grade in CONFIDENCE_GRADES}
    # A grade the model invented is a fact about the run, not something to
    # drop. Dropping it makes the split stop summing to the corpus.
    split["unset"] = len(records) - sum(split.values())
    split["total"] = len(records)
    return split


def starred_count(records):
    """How many he thought enough of to star. A count, never the list."""
    return sum(1 for r in records if r.get("starred") is True)


def pool_split(records):
    """The queue against the folders.

    Two different acts wearing the same label. The queue is where things were
    dropped; the folders are where things were sorted, named and shelved, and
    every item saved before 2014 that survived unread is in a folder. One
    number for the pool would hide the finding.
    """
    filed = sum(1 for r in records if str(r.get("folder") or "").strip())
    return {"unread_queue": len(records) - filed, "filed_in_folders": filed,
            "total": len(records)}


# ---------------------------------------------------------------------------
# the paired series against the read corpus
# ---------------------------------------------------------------------------

def read_corpus_by_year(frame):
    """Read-it-later articles by the year they were saved.

    The other half of plate 01. Read-it-later sources only, for the reason
    READ_IT_LATER_SOURCES exists.

    Note the filter that is NOT here: `content_corrupted`. A row the content
    guard rejected still went through the save-then-read loop, so it is a read;
    it simply carries no usable topics. `read_corpus_topics()` drops it because
    its topics are junk, and this one keeps it because its date is not. The two
    series have different denominators on purpose, and the record states both.
    """
    import pandas as pd

    rows = frame
    if rows is None or not len(rows):
        return {}
    if "source" in rows.columns:
        rows = rows[rows["source"].isin(READ_IT_LATER_SOURCES)]
    dates = pd.to_datetime(rows["date_saved"], errors="coerce", format="mixed")
    counts = Counter(int(d.year) for d in dates if d is not None and not pd.isna(d))
    return dict(sorted(counts.items()))


def read_corpus_titles(frame, min_len=12):
    """Article titles from the read index, case-folded, at the needle floor.

    Not leak needles - this record's needles are its own corpus's titles, and
    correctly so. These exist for one job: this record publishes read-corpus
    TOPIC strings, those topics are model-generated out of 17,320 articles, and
    one of them can be a headline. `topic_comparison()` drops any topic that is
    a title on either side, and this is the other side.
    """
    if frame is None or not len(frame) or "title" not in frame.columns:
        return set()
    return {str(t).strip().casefold() for t in frame["title"].dropna()
            if len(str(t).strip()) >= min_len}


def topic_comparison(records, read_topics, limit=10, read_extra=2, titles=()):
    """The two topic distributions paired on the topic, with the ratio.

    The pairing is the finding, so it happens here rather than on the page: two
    lists paired by position mismatch silently the first time one of them drops
    a string the other keeps.

    `read_extra` reaches down the READ side for subjects that are not in the
    saved pile at all. Without it the table is a ranking of what he saved, and
    "the one subject he read more than he saved" is a sentence that cannot
    appear in it.

    `titles` is the redaction, and it runs over BOTH columns. The read corpus's
    topics are model-generated strings too, out of 17,320 articles, and this
    record publishes them; one of them colliding with an unread title would
    launder that title onto the page through a column nothing was scanning.
    """
    titles = {str(t).strip().casefold() for t in (titles or ())}
    keep = lambda name: str(name).strip().casefold() not in titles  # noqa: E731

    saved = by_topic(records)
    read = Counter()
    for counts in (read_topics or {}).values():
        read.update(counts)

    saved_total = sum(saved.values())
    read_total = sum(read.values())

    ranked_saved = [t for t, _ in sorted(saved.items(), key=lambda kv: (-kv[1], kv[0]))
                    if keep(t)]
    chosen = ranked_saved[:limit]
    seen = {t.casefold() for t in chosen}
    for topic, _ in sorted(read.items(), key=lambda kv: (-kv[1], kv[0])):
        if len(chosen) >= limit + read_extra:
            break
        if keep(topic) and topic.casefold() not in seen:
            chosen.append(topic)
            seen.add(topic.casefold())

    rows = []
    for topic in chosen:
        unread_n, read_n = saved.get(topic, 0), read.get(topic, 0)
        unread_share = round(unread_n / saved_total, 6) if saved_total else None
        read_share = round(read_n / read_total, 6) if read_total else None
        rows.append({
            "topic": topic,
            "unread": unread_n,
            "read": read_n,
            "unread_share": unread_share,
            "read_share": read_share,
            # None where the read corpus never carried the topic. Infinity is a
            # claim four mentions against nothing cannot support, and 0.0 says
            # the opposite of what happened.
            "ratio": (round(unread_share / read_share, 4)
                      if (unread_share and read_share) else None),
        })
    return {
        "topics": rows,
        "unread_mentions": saved_total,
        "read_mentions": read_total,
        "unread_articles": len(_clean(records)),
        "unread_corpus": len(records),
        "caveat": COMPARISON_CAVEAT,
    }


# ---------------------------------------------------------------------------
# the per-day rollup, in the shape Silo's provider daily summary takes
# ---------------------------------------------------------------------------

#: Silo keys a provider daily summary on (provider, date_of_summary) and
#: carries its provenance inside the payload rather than in a column, so the
#: rollup declares both. `computed_stats` is Silo's name for the derived,
#: chartable half of a summary; `raw_data` is the provider's own shape and this
#: record has none to offer that is not item level, so it is not emitted.
DAILY_PROVIDER = "meant-to-read"

DAILY_READS_NOTE = (
    "Reads are null rather than 0 on every day. Every item in this corpus is "
    "unread by definition, so the record holds no reading events at all - not "
    "zero of them on a given day, but no observation. A 0 here would draw a "
    "flat line along the bottom of a chart and call it a measurement."
)


def daily_rollup(records, built=None):
    """One row per day saved, with the day's counts and nothing item level.

    This is what makes the record import-eligible: a compact daily series in
    the shape of Silo's provider daily summary, keyed on the day.

    Nothing here is a title, a URL, or an inference sentence. A day with one
    save is not an aggregate, so a per-day roster of what was saved - or one
    day's `why_saved` concatenated under its date - would be MORE identifying
    than the title would have been, not less.
    """
    grouped = defaultdict(list)
    undated = 0
    for record in records:
        day = str(record.get("saved_date") or "").strip()
        if not day:
            undated += 1
            continue
        grouped[day].append(record)

    days = []
    for day in sorted(grouped):
        rows = grouped[day]
        with_reason = sum(1 for r in rows if (r.get("why_saved") or "").strip()
                          and r.get("why_saved_confidence") in ("high", "medium"))
        bands = Counter(r.get("abandonment") or derive.NEVER_OPENED for r in rows)
        days.append({
            "date_of_summary": day,
            "computed_stats": {
                "saves": len(rows),
                "reads": None,
                "starred": sum(1 for r in rows if r.get("starred") is True),
                "words": sum(int(r.get("body_words") or 0) for r in rows),
                "by_abandonment": {band: bands.get(band, 0) for band in derive.BANDS},
                "by_recovery": dict(Counter(r.get("resolve_path") for r in rows)),
                "why_saved_present": with_reason,
                "why_saved_rate": round(with_reason / len(rows), 4),
            },
        })

    return {
        "provider": DAILY_PROVIDER,
        "source": "instapaper",
        "imported_at": built,
        # `saved_date` is a calendar date with no zone on it, as Instapaper
        # reports it. Declaring UTC is how a day boundary gets one meaning
        # instead of the importer's.
        "timezone": "UTC",
        "date_field": "date_of_summary",
        "days": days,
        "undated": undated,
        "reads_note": DAILY_READS_NOTE,
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
    """How much of what he saved each year was a subject he was not reading.

    The share of that year's saved topic MENTIONS - weighted the way they were
    saved, so a topic saved forty times counts forty intentions - whose topic
    the read corpus did not carry that year.

    Not a Jaccard against the year's vocabulary. That was the first
    implementation and the live corpus showed why it cannot work: every year
    scored between 0.9967 and 0.9992, because one item's three topics against a
    year's several hundred makes the union the vocabulary and the overlap a
    rounding error. A metric whose every value is 0.998 answers the plan's
    second question with noise.
    """
    grouped = defaultdict(list)
    for record in _clean(records):
        year = record.get("saved_year")
        if year:
            grouped[year].append(record)

    report = {}
    for year in sorted(grouped):
        baseline = read_topics.get(year) or {}
        baseline_set = {str(t).strip().casefold() for t in baseline}
        mentions = [t for r in grouped[year] for t in _topics(r)]
        unmatched = [t for t in mentions if t.casefold() not in baseline_set]
        report[year] = {
            "items": len(grouped[year]),
            "topic_mentions": len(mentions),
            "unmatched_topic_mentions": len(unmatched),
            # None where the read corpus has no baseline for the year, or the
            # unread side carries no topics. 1.0 there would invent the
            # project's own finding.
            "unmatched_share": (round(len(unmatched) / len(mentions), 4)
                                if (mentions and baseline_set) else None),
            "read_topics": len(baseline_set),
            "shared_topics": len({t.casefold() for t in mentions} & baseline_set),
        }
    return report


def peak_drift_year(report, min_items=MIN_DRIFT_ITEMS):
    """The year the intentions diverged most, over years with enough evidence."""
    measurable = {y: v["unmatched_share"] for y, v in report.items()
                  if v.get("unmatched_share") is not None
                  and v.get("items", 0) >= min_items}
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


def direct_resolved_by_domain(records):
    """Which domains the plain-GET leg resolved, and how many each.

    This exists because of a measurement that contradicts the plan. The plan
    reads the 51% direct-GET rate as an upper bound and says to gate whatever
    that leg produces behind the enrichment prompt's CONTENT_VALID check,
    "which already exists and already catches junk scrapes". On the live run it
    does not catch this one: the x.com items come back with thousands of words
    of JavaScript payload, and the prompt - which is instructed to default to
    YES when uncertain - marks them valid at high confidence.

    So the leg's output is reported by domain rather than trusted or silently
    dropped. A direct-resolved count concentrated on one social host is the
    signal that those rows are payload rather than article, and it is a
    decision for Adam rather than for a heuristic invented here.
    """
    counts = Counter(_domain(r) for r in records
                     if r.get("resolve_path") == "direct" and _domain(r))
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


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

def shortlist_eligible_count(records):
    """How many pieces qualify, before any page limit.

    `shortlist(limit=n)` truncates, so its length is the caller's page size
    rather than a measurement - a corpus with 200 qualifying pieces and one
    with 25 would publish the same number. This is the figure the public record
    carries.
    """
    return sum(1 for r in records
               if r.get("aged_out") is False and not r.get("content_corrupted"))


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
        "direct_resolved_by_domain": direct_resolved_by_domain(records),
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
        "shortlist_eligible": shortlist_eligible_count(records),
        # It carries titles and URLs. It ships to reading.adamthede.com behind
        # Cloudflare Access; the public record gets a count at most.
        "shortlist_visibility": "private",
    }
