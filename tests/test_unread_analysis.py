"""The unread analysis: the aggregates, the drift, and the shortlist.

Every test here names, in its docstring, the mutation it catches. Nothing here
reads the real archive; the read corpus is a small synthetic frame shaped like
`data/archive_index.parquet`.

The rule most of these defend: a low-confidence `why_saved` is excluded from
every aggregate, and an unknown `aged_out` is excluded from the aged-out
denominator rather than counted as still current. Both are the difference
between an analysis that reports what the corpus supports and one that reports
what the model was willing to say.
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from unread import analysis  # noqa: E402
from unread import derive  # noqa: E402


def record(url_sha256="a" * 64, year=2018, domain="nytimes.com",
           topics=("Attention",), aged_out=False, confidence="high",
           why="He was thinking about attention.", abandonment="never_opened",
           resolve_path="instapaper", corrupted=False, words=1200, **extra):
    row = {
        "url_sha256": url_sha256,
        "title": "An article title long enough to be a needle",
        "url": f"https://{domain}/some/article/path",
        "domain": domain,
        "saved_year": year,
        "saved_date": f"{year}-06-01",
        "ai_topics": list(topics),
        "ai_people": ["Nicholas Carr"],
        "ai_orgs": ["The Atlantic"],
        "ai_locations": ["Boston"],
        "ai_concepts": ["Deep Reading"],
        "ai_summary": "A summary.",
        "ai_sentiment": "Neutral",
        "ai_emotion": "Analytical",
        "why_saved": why,
        "why_saved_confidence": confidence,
        "why_saved_kind": "inference",
        "aged_out": aged_out,
        "abandonment": abandonment,
        "resolve_path": resolve_path,
        "content_corrupted": corrupted,
        "body_words": words,
        "read_progress": 0.0,
        "enriched_from": "body",
        "model": "gemini-2.5-flash-lite",
    }
    row.update(extra)
    return row


def many(n, **kwargs):
    return [record(url_sha256=f"{i:064d}", **kwargs) for i in range(n)]


def read_frame(rows):
    """A frame shaped like data/archive_index.parquet."""
    return pd.DataFrame(rows)


def read_row(source="instapaper", date_saved="2018-03-01", topics=("Business Strategy",),
             corrupted=False):
    return {"source": source, "date_saved": date_saved, "topics": list(topics),
            "content_corrupted": corrupted, "title": "Read article", "url": "https://x.example/y"}


# ---------------------------------------------------------------------------
# the aggregates
# ---------------------------------------------------------------------------

def test_the_year_table_counts_every_item_including_the_dead_ones():
    """Mutation: building the by-year table from resolved items only.

    An item that resolves nowhere is still an intention with a saved date. The
    queue's shape by year is a fact about the saving, not about the fetching.
    """
    rows = many(3, year=2018) + [record(url_sha256="z" * 64, year=2018,
                                        resolve_path="metadata")]
    assert analysis.by_saved_year(rows)[2018] == 4


def test_the_domain_table_folds_the_www_prefix():
    """Mutation: counting www.nytimes.com and nytimes.com as two sources.

    78 of 395 from one newspaper is the record's headline finding. Split across
    a prefix it would be two unremarkable numbers.
    """
    rows = [record(url_sha256="1" * 64, domain="www.nytimes.com"),
            record(url_sha256="2" * 64, domain="nytimes.com")]
    counts = dict(analysis.by_domain(rows))
    assert counts.get("nytimes.com") == 2
    assert "www.nytimes.com" not in counts


def test_a_corrupted_record_is_out_of_the_topic_aggregates():
    """Mutation: aggregating topics over rows CONTENT_VALID marked NO.

    The word floor cannot tell an article from a JavaScript shell; the prompt's
    own guard is the second gate and it only helps if the aggregates honour it.
    """
    rows = many(2, topics=("Attention",)) + [
        record(url_sha256="c" * 64, topics=("Consent Management",), corrupted=True)]
    topics = analysis.by_topic(rows)
    assert topics["Attention"] == 2
    assert "Consent Management" not in topics


def test_the_dead_fraction_is_measured_per_saved_year():
    """Mutation: reporting one corpus-wide dead number.

    Link rot is the finding and it is a function of age. A single percentage
    across twelve years says nothing about the shape of the decay.
    """
    rows = ([record(url_sha256=f"a{i:063d}", year=2014, resolve_path="metadata")
             for i in range(2)]
            + [record(url_sha256=f"b{i:063d}", year=2014) for i in range(2)]
            + [record(url_sha256=f"c{i:063d}", year=2025) for i in range(4)])
    dead = analysis.dead_fraction_by_year(rows)
    assert dead[2014]["dead"] == 2
    assert dead[2014]["fraction"] == pytest.approx(0.5)
    assert dead[2025]["fraction"] == 0.0


def test_the_plain_get_leg_is_reported_by_domain():
    """Mutation: folding the direct leg into the resolve counts and moving on.

    Measured on the live run and against the plan: 15 of the first 19
    direct-resolved items are x.com, and the enrichment prompt's CONTENT_VALID
    guard marked every one of them valid at high confidence. The plan expected
    that guard to catch them. Reporting the leg by domain is what makes a
    concentration on one social host visible instead of buried in a total.
    """
    rows = [record(url_sha256="1" * 64, domain="x.com", resolve_path="direct"),
            record(url_sha256="2" * 64, domain="x.com", resolve_path="direct"),
            record(url_sha256="3" * 64, domain="nytimes.com", resolve_path="direct"),
            record(url_sha256="4" * 64, domain="x.com", resolve_path="instapaper")]
    assert analysis.direct_resolved_by_domain(rows) == [("x.com", 2), ("nytimes.com", 1)]


def test_survival_does_not_claim_a_liveness_the_chain_never_measured():
    """Mutation: reading `resolve_path == "direct"` as "the URL is still live".

    Found by adversarial review. The chain short-circuits: the direct leg runs
    ONLY where Instapaper failed, so the 351 items Instapaper resolved were
    never probed against their own URL at all. Counting the direct-resolved
    items as the live web understates liveness by the whole Instapaper leg -
    the plan measured 51 of 100 sampled URLs serving their own article, and
    this arithmetic would have reported 4.

    The report says what the chain actually establishes and refuses to name a
    live-web figure it did not measure.
    """
    rows = [record(url_sha256="1" * 64, resolve_path="direct"),
            record(url_sha256="2" * 64, resolve_path="instapaper"),
            record(url_sha256="3" * 64, resolve_path="wayback"),
            record(url_sha256="4" * 64, resolve_path="metadata")]
    survival = analysis.survival(rows)
    assert "live_web" not in survival
    assert survival["text_recovered"] == 3
    assert survival["no_text_anywhere"] == 1
    assert survival["direct_fetch_after_instapaper_failed"] == 1
    assert survival["liveness_measured"] is False


def test_survival_says_why_it_cannot_report_link_rot():
    """Mutation: dropping the caveat and letting the numbers read as link rot.

    Question 6 of the plan wants what survives on the live web. Answering it
    needs a probe of every URL, which the resolve chain deliberately does not
    do because Instapaper's stored copy is the better text. The gap has to
    travel with the numbers.
    """
    note = analysis.survival(many(3))["note"]
    assert "short-circuits" in note or "short circuits" in note
    assert "not" in note.lower()


# ---------------------------------------------------------------------------
# the inference, and what it is allowed to be counted in
# ---------------------------------------------------------------------------

def test_a_low_confidence_why_saved_is_out_of_the_aggregate():
    """Mutation: counting every why_saved the model returned.

    The plan's rule, and the only reason the field ships at all: low-confidence
    rows are excluded from any aggregate. The field is the one most likely to
    produce confident fiction.
    """
    rows = (many(2, confidence="high")
            + [record(url_sha256="l" * 64, confidence="low", why="A guess.")])
    summary = analysis.why_saved_summary(rows)
    assert summary["counted"] == 2
    assert summary["excluded_low_confidence"] == 1


def test_an_empty_why_saved_is_a_result_not_a_failure():
    """Mutation: treating a blank inference as an error, or dropping the row.

    "The analysis must treat an empty why_saved as a result rather than a
    failure" is in the plan verbatim. The count of what could not be inferred
    is itself a finding about the corpus.
    """
    rows = many(2, confidence="high") + [record(url_sha256="e" * 64, why="",
                                                confidence="low")]
    summary = analysis.why_saved_summary(rows)
    assert summary["no_inference"] == 1
    assert summary["total"] == 3


def test_an_unknown_aged_out_is_not_counted_as_still_current():
    """Mutation: folding None into False in the aging curve.

    The aged-out share is the number that makes the case that a backlog is not
    a to-do list. Counting unknowns as current understates it by exactly the
    number of items the model would not judge.
    """
    rows = ([record(url_sha256=f"t{i:063d}", year=2018, aged_out=True) for i in range(2)]
            + [record(url_sha256=f"f{i:063d}", year=2018, aged_out=False) for i in range(2)]
            + [record(url_sha256=f"u{i:063d}", year=2018, aged_out=None) for i in range(4)])
    curve = analysis.aging_curve(rows)
    assert curve[2018]["judged"] == 4
    assert curve[2018]["unknown"] == 4
    assert curve[2018]["fraction"] == pytest.approx(0.5)


def test_the_aging_curve_reports_a_year_it_could_not_judge_as_none():
    """Mutation: dividing by zero, or printing 0.0 for "no data".

    A year where the model judged nothing has no aged-out share, and 0.0 there
    would read as "nothing aged out", which is the opposite claim.
    """
    rows = [record(url_sha256=f"u{i:063d}", year=2022, aged_out=None) for i in range(3)]
    assert analysis.aging_curve(rows)[2022]["fraction"] is None


# ---------------------------------------------------------------------------
# the abandonment bands
# ---------------------------------------------------------------------------

def test_the_abandonment_bands_carry_length_and_not_just_counts():
    """Mutation: a band table of counts alone.

    Question 4 is whether the items opened and abandoned are longer, harder or
    a different subject from the ones never opened. Counts alone cannot answer
    any of the three.
    """
    rows = ([record(url_sha256=f"n{i:063d}", abandonment=derive.NEVER_OPENED, words=500)
             for i in range(3)]
            + [record(url_sha256=f"s{i:063d}", abandonment=derive.STARTED, words=4000)
               for i in range(2)])
    bands = analysis.abandonment_bands(rows)
    assert bands[derive.NEVER_OPENED]["count"] == 3
    assert bands[derive.STARTED]["median_words"] == 4000
    assert bands[derive.NEVER_OPENED]["median_words"] == 500


def test_every_band_appears_even_when_empty():
    """Mutation: a table built only from bands that happen to occur.

    An empty `nearly_finished` band is a finding about the queue, and a table
    that silently omits it invites the reader to assume it was not measured.
    """
    bands = analysis.abandonment_bands(many(2, abandonment=derive.NEVER_OPENED))
    assert set(bands) == set(derive.BANDS)
    assert bands[derive.NEARLY]["count"] == 0


# ---------------------------------------------------------------------------
# the drift against the read corpus
# ---------------------------------------------------------------------------

def test_the_read_corpus_loader_takes_only_the_read_it_later_articles():
    """Mutation: comparing the unread queue against all 17,340 index rows.

    Only 6,780 of the index are read-it-later articles; the other 10,560 are
    the legacy document archive. The unread queue is the other half of the
    save-then-read loop, and the legacy scans never went through it.
    """
    frame = read_frame([read_row(source="instapaper"), read_row(source="matter"),
                        read_row(source="legacy_pdf", topics=("Tax Law",))])
    topics = analysis.read_corpus_topics(frame)
    assert "Tax Law" not in topics[2018]
    assert topics[2018]["Business Strategy"] == 2


def test_the_read_corpus_loader_drops_corrupted_rows():
    """Mutation: letting known-bad extractions set the baseline distribution."""
    frame = read_frame([read_row(), read_row(topics=("Junk",), corrupted=True)])
    assert "Junk" not in analysis.read_corpus_topics(frame)[2018]


def test_identical_topics_are_zero_drift_and_disjoint_ones_are_one():
    """Mutation: an unnormalised distance, or one that runs the wrong way."""
    assert derive.topic_drift(["A", "B"], ["A", "B"]) == 0.0
    assert derive.topic_drift(["A"], ["B"]) == 1.0


def test_an_item_with_no_topics_has_no_measurable_drift():
    """Mutation: returning 0.0 for an item nothing could be computed for.

    Zero reads as "this item matched what he was reading", which is a claim.
    None reads as "not measurable", which is the truth.
    """
    assert derive.topic_drift([], ["A"]) is None
    assert derive.topic_drift(["A"], []) is None


def test_the_drift_metric_is_not_swamped_by_the_size_of_the_read_vocabulary():
    """Mutation: scoring a year by Jaccard against its whole topic vocabulary.

    Measured on the live corpus, and it is why this test exists: every single
    year came out between 0.9967 and 0.9992. A year's read corpus carries
    several hundred distinct topics and one item carries three, so the union is
    the vocabulary, the overlap is a rounding error, and the answer is 0.998 no
    matter what was saved. A metric whose every value is the same answers the
    plan's second question with noise.

    Drift is the share of what he saved that the read corpus did not carry that
    year. A year where everything he saved is a subject he also read is zero
    drift, however large that year's vocabulary.
    """
    frame = read_frame([read_row(date_saved="2018-03-01", topics=(f"Topic {i}",))
                        for i in range(200)])
    read_topics = analysis.read_corpus_topics(frame)
    rows = [record(url_sha256=f"{i:064d}", year=2018, topics=("Topic 1", "Topic 2"))
            for i in range(30)]
    report = analysis.drift_by_year(rows, read_topics)
    assert report[2018]["unmatched_share"] == 0.0


def test_drift_counts_the_topics_the_read_corpus_never_carried():
    """Mutation: counting distinct topics rather than mentions.

    A topic saved forty times that year is forty intentions, not one. The
    denominator is what was saved, weighted the way it was saved.
    """
    frame = read_frame([read_row(date_saved="2018-03-01", topics=("Attention",))])
    read_topics = analysis.read_corpus_topics(frame)
    rows = [record(url_sha256="1" * 64, year=2018, topics=("Attention", "Obscure Hobby")),
            record(url_sha256="2" * 64, year=2018, topics=("Obscure Hobby",))]
    report = analysis.drift_by_year(rows, read_topics)
    assert report[2018]["topic_mentions"] == 3
    assert report[2018]["unmatched_share"] == pytest.approx(2 / 3, abs=1e-4)


def test_the_drift_report_finds_the_year_the_two_corpora_diverged_most():
    """Mutation: averaging drift across all years, losing the year it peaked in.

    Question 2 is where the drift is largest, and the plan's working hypothesis
    is 2017 to 2019. The analysis has to be able to contradict that.
    """
    frame = read_frame([read_row(date_saved="2018-03-01", topics=("Business Strategy",)),
                        read_row(date_saved="2015-03-01", topics=("Attention",))])
    # Both years above MIN_DRIFT_ITEMS: the peak is about the metric here, not
    # about the evidence floor, and a fixture under the floor would pass for
    # the wrong reason.
    rows = ([record(url_sha256=f"a{i:063d}", year=2018, topics=("Attention",))
             for i in range(12)]
            + [record(url_sha256=f"b{i:063d}", year=2015, topics=("Attention",))
               for i in range(12)])
    report = analysis.drift_by_year(rows, analysis.read_corpus_topics(frame))
    assert report[2018]["unmatched_share"] > report[2015]["unmatched_share"]
    assert analysis.peak_drift_year(report) == 2018


def test_a_year_the_read_corpus_never_saw_is_not_reported_as_total_drift():
    """Mutation: scoring an unmeasurable year as 1.0.

    2021 carries 13 read rows and 2026 none at all. A year with no baseline has
    no drift to report, and 1.0 there would invent the project's own finding.
    """
    frame = read_frame([read_row(date_saved="2018-03-01")])
    rows = [record(url_sha256=f"z{i:063d}", year=2026) for i in range(3)]
    report = analysis.drift_by_year(rows, analysis.read_corpus_topics(frame))
    assert report[2026]["unmatched_share"] is None


def test_the_comparison_carries_the_caveat_the_plan_requires():
    """Mutation: publishing the two topic distributions side by side, unqualified.

    The unread summaries were built on whole articles; the read corpus's were
    built on first-10,000-character truncations. The plan says any comparison
    between the two must say so, and a caveat that lives only in a document can
    be separated from the numbers.
    """
    frame = read_frame([read_row()])
    comparison = analysis.saved_versus_read(many(2), analysis.read_corpus_topics(frame))
    assert "10,000" in comparison["caveat"]
    assert "full" in comparison["caveat"].lower()


# ---------------------------------------------------------------------------
# the shortlist
# ---------------------------------------------------------------------------

def test_an_aged_out_piece_is_never_on_the_shortlist():
    """Mutation: ranking on topic overlap alone.

    "Still worth your time" is the claim the shortlist makes. A 2016 election
    preview is not, whatever its topics.
    """
    rows = [record(url_sha256="1" * 64, aged_out=True, topics=("Attention",)),
            record(url_sha256="2" * 64, aged_out=False, topics=("Attention",))]
    picks = analysis.shortlist(rows, {"Attention": 10}, limit=10)
    assert [p["url_sha256"] for p in picks] == ["2" * 64]


def test_an_unjudged_piece_is_not_on_the_shortlist_either():
    """Mutation: treating an unknown aged_out as safe to recommend.

    The shortlist is the output with the sharpest failure mode. A row the model
    would not judge is not a row to put in front of Adam as still current.
    """
    rows = [record(url_sha256="1" * 64, aged_out=None, topics=("Attention",))]
    assert analysis.shortlist(rows, {"Attention": 10}, limit=10) == []


def test_a_corrupted_row_is_never_recommended():
    """Mutation: recommending a consent page the model summarised politely."""
    rows = [record(url_sha256="1" * 64, corrupted=True, topics=("Attention",))]
    assert analysis.shortlist(rows, {"Attention": 10}, limit=10) == []


def test_the_public_shortlist_count_is_what_qualified_not_the_page_size():
    """Mutation: publishing len(shortlist) when the list was truncated.

    `shortlist(limit=25)` returns at most 25 rows, so publishing its length
    publishes the CLI's default rather than a measurement: a corpus with 200
    qualifying pieces and one with 25 would print the same number.
    """
    rows = [record(url_sha256=f"{i:064d}", aged_out=False) for i in range(40)]
    assert analysis.shortlist_eligible_count(rows) == 40
    assert len(analysis.shortlist(rows, {}, limit=25)) == 25


def test_peak_drift_ignores_a_year_with_too_little_evidence():
    """Mutation: letting one article decide the project's headline finding.

    Found by adversarial review: a single-article year can take the peak, and
    the metric correlates with how much the read corpus carried that year
    (r = -0.79 on the live data), so a thin year wins twice over. A peak that
    one article can move is not a finding.
    """
    frame = read_frame([read_row(date_saved="2018-03-01", topics=("Attention",)),
                        read_row(date_saved="2022-03-01", topics=("Attention",))])
    read_topics = analysis.read_corpus_topics(frame)
    rows = ([record(url_sha256=f"a{i:063d}", year=2018,
                    topics=("Obscure Hobby", "Attention")) for i in range(30)]
            + [record(url_sha256="b" * 64, year=2022, topics=("Another Hobby",))])
    report = analysis.drift_by_year(rows, read_topics)
    # 2022 scores higher on the raw metric and is one article.
    assert report[2022]["unmatched_share"] == 1.0
    assert report[2018]["unmatched_share"] == 0.5
    assert analysis.peak_drift_year(report) == 2018


def test_a_piece_he_nearly_finished_outranks_one_he_never_opened():
    """Mutation: ignoring the abandonment band in the ranking.

    The plan: a piece he got 66% through and abandoned is a different
    recommendation from one he never opened. That is the whole reason the band
    is in the schema.
    """
    rows = [record(url_sha256="1" * 64, abandonment=derive.NEVER_OPENED,
                   topics=("Attention",)),
            record(url_sha256="2" * 64, abandonment=derive.NEARLY,
                   topics=("Attention",))]
    picks = analysis.shortlist(rows, {"Attention": 10}, limit=2)
    assert picks[0]["url_sha256"] == "2" * 64


def test_topic_overlap_with_what_he_reads_now_moves_a_piece_up():
    """Mutation: a ranking that is the abandonment band and nothing else."""
    rows = [record(url_sha256="1" * 64, topics=("Obscure Hobby",)),
            record(url_sha256="2" * 64, topics=("Attention",))]
    picks = analysis.shortlist(rows, {"Attention": 40}, limit=2)
    assert picks[0]["url_sha256"] == "2" * 64


def test_every_pick_carries_one_line_of_reasoning():
    """Mutation: a bare ranked list.

    "Each with a one-line reason" is the plan's definition of this output, and
    the reason is what lets Adam judge whether the ranking is any good.
    """
    picks = analysis.shortlist(many(3), {"Attention": 10}, limit=3)
    assert picks
    for pick in picks:
        assert pick["reason"].strip()
        assert "\n" not in pick["reason"]


def test_the_shortlist_score_is_exactly_the_sum_of_its_published_parts():
    """Mutation: any ranking term that is not in `score_parts`.

    A fresh model pass, a recency boost, a random tiebreak - each would change
    the order while the key names stayed the same, which is why the previous
    version of this test (a check on the key names alone) could not detect the
    mutation its docstring named. Adversarial review caught that.

    The plan says rank on the enrichment. What makes that checkable is that the
    published parts add up to the published score, so a reader can recompute
    the ranking from fields that are all in the record.
    """
    picks = analysis.shortlist(
        [record(url_sha256=f"{i:064d}", abandonment=band, topics=topics)
         for i, (band, topics) in enumerate([
             (derive.NEVER_OPENED, ("Attention",)),
             (derive.STARTED, ("Obscure Hobby",)),
             (derive.NEARLY, ("Attention", "Obscure Hobby")),
             (derive.NEVER_OPENED, ("Obscure Hobby",))])],
        {"Attention": 10}, limit=4)
    assert len(picks) == 4
    for pick in picks:
        assert set(pick["score_parts"]) <= {"aged_out", "topic_overlap", "abandonment"}
        assert pick["score"] == pytest.approx(sum(pick["score_parts"].values()),
                                              abs=1e-4)
    # And the order really is that score, descending.
    assert [p["score"] for p in picks] == sorted((p["score"] for p in picks),
                                                 reverse=True)


def test_the_shortlist_is_deterministic():
    """Mutation: an unstable sort, or a tie broken by dict ordering.

    Two runs over the same corpus that disagree cannot be reviewed, and this
    output ships private precisely so Adam can review it.
    """
    rows = many(20)
    assert ([p["url_sha256"] for p in analysis.shortlist(rows, {"Attention": 10}, limit=10)]
            == [p["url_sha256"] for p in analysis.shortlist(list(reversed(rows)),
                                                            {"Attention": 10}, limit=10)])


def test_the_shortlist_is_marked_private():
    """Mutation: an output that looks publishable.

    It carries titles and URLs. It ships to reading.adamthede.com behind
    Cloudflare Access, and the public record gets a count at most.
    """
    report = analysis.build(many(5), read_topics={2018: {"Attention": 5}})
    assert report["shortlist_visibility"] == "private"


# ---------------------------------------------------------------------------
# the command line
# ---------------------------------------------------------------------------

def test_the_analysis_cli_runs_end_to_end(tmp_path, capsys):
    """Mutation: a summary line reading a key the report no longer carries.

    This is not hypothetical either. Renaming `survival()["live_web"]` left the
    CLI printing a key that no longer existed, and nothing caught it until the
    command was run by hand - every test exercised the library and none
    exercised the entry point. A crash in the last five lines of a script that
    has already done all the work is a bad way to find out.
    """
    import json as json_mod
    import sys as sys_mod

    sys_mod.path.insert(0, str(REPO / "scripts" / "core"))
    import analyze_unread_corpus as cli

    enriched = tmp_path / "enriched.jsonl"
    enriched.write_text("\n".join(
        json_mod.dumps(record(url_sha256=f"{i:064d}")) for i in range(5)),
        encoding="utf-8")
    out = tmp_path / "report.json"

    code = cli.main(["--enriched", str(enriched), "--out", str(out),
                     "--index", str(tmp_path / "absent.parquet")])
    assert code == 0
    assert out.exists()
    report = json_mod.loads(out.read_text())
    assert report["items"] == 5
    printed = capsys.readouterr().out
    assert "5 items enriched" in printed


# ---------------------------------------------------------------------------
# the cover aggregates: words, span, stars, pool, confidence
# ---------------------------------------------------------------------------

def test_the_word_total_is_measured_over_the_rows_that_have_words():
    """Mutation: dividing the total by len(records) instead of by the rows that
    carried text.

    26 of the 492 resolved nowhere and have no body at all. Counting them in
    the denominator would report a median and a mean for a population a quarter
    of which has no measurement, and the cover states the denominator out loud
    ("across the 466 whose text came back").
    """
    rows = many(3, words=1000) + [record(url_sha256="z" * 64, words=None)]
    totals = analysis.word_totals(rows)
    assert totals["total"] == 3000
    assert totals["measured_over"] == 3
    assert totals["corpus"] == 4
    assert totals["median"] == 1000


def test_a_corpus_with_no_recovered_text_reports_no_median_rather_than_zero():
    """Mutation: a median of 0 where nothing was measured.

    0 reads as "the typical article was empty". None reads as "there is no
    typical article here", which is the true statement.
    """
    totals = analysis.word_totals([record(words=None)])
    assert totals["median"] is None
    assert totals["mean"] is None
    assert totals["total"] == 0
    assert totals["measured_over"] == 0


def test_the_saved_span_is_the_oldest_and_newest_save_date():
    """Mutation: reporting the span from saved_year, which loses the day.

    The cover says "27 January 2011", not "2011". A year is the wrong
    precision for a sentence whose whole force is how long one particular
    intention has been sitting there.
    """
    rows = [record(url_sha256="1" * 64, saved_date="2011-01-27", year=2011),
            record(url_sha256="2" * 64, saved_date="2026-04-12", year=2026),
            record(url_sha256="3" * 64, saved_date="2018-06-01", year=2018)]
    span = analysis.saved_span(rows)
    assert span["oldest"] == "2011-01-27"
    assert span["newest"] == "2026-04-12"
    assert span["years_spanned"] == 16
    assert span["dated"] == 3


def test_a_corpus_with_no_dates_has_no_span_rather_than_a_fabricated_one():
    """Mutation: min() on an empty sequence, or a today() default standing in
    for a date nothing recorded."""
    span = analysis.saved_span([record(saved_date=None)])
    assert span["oldest"] is None and span["newest"] is None
    assert span["years_spanned"] is None
    assert span["dated"] == 0


def test_the_confidence_split_counts_every_row_including_the_declined_ones():
    """Mutation: splitting only the rows that carried a sentence.

    The grade and the sentence are different fields. The model can grade a row
    low and decline to say anything, and it did so ten times in the live
    corpus: the three grades sum to the whole corpus, while `counted` and
    `excluded_low_confidence` in why_saved_summary() sum only over the rows
    with a sentence. A split that quietly used the second population would
    publish a bar chart whose total is not the corpus.
    """
    rows = [record(url_sha256="1" * 64, confidence="high"),
            record(url_sha256="2" * 64, confidence="medium"),
            record(url_sha256="3" * 64, confidence="low"),
            record(url_sha256="4" * 64, confidence="low", why="")]
    split = analysis.confidence_split(rows)
    assert split == {"high": 1, "medium": 1, "low": 2, "unset": 0, "total": 4}
    assert sum(split[k] for k in ("high", "medium", "low", "unset")) == len(rows)


def test_a_row_with_an_unrecognised_confidence_is_counted_as_unset():
    """Mutation: dropping it, so the split stops summing to the corpus.

    A grade the model invents is a fact about the run. Silently discarding it
    makes the bar chart's total smaller than the corpus with nothing on the
    page saying why.
    """
    split = analysis.confidence_split([record(confidence="very high")])
    assert split["unset"] == 1
    assert split["total"] == 1


def test_the_star_count_is_a_count_and_ignores_a_missing_field():
    """Mutation: counting a row whose `starred` key is absent as starred.

    `starred` is an independent field from read progress - only one of the
    eight partially-read folder items carries it - so a truthiness bug here
    would put a number on the cover that means nothing.
    """
    rows = [record(url_sha256="1" * 64, starred=True),
            record(url_sha256="2" * 64, starred=False),
            record(url_sha256="3" * 64)]
    assert analysis.starred_count(rows) == 1


def test_the_pool_splits_the_queue_from_the_folders():
    """Mutation: reporting one number for the pool.

    These are two different acts wearing the same label. The queue is where
    things were dropped; the folders are where things were sorted, named and
    shelved, and every item saved before 2014 that survived is in a folder. The
    cover says 395 and 97, so the payload has to carry both.
    """
    rows = [record(url_sha256="1" * 64, folder=None),
            record(url_sha256="2" * 64, folder=""),
            record(url_sha256="3" * 64, folder="Steve Jobs")]
    pool = analysis.pool_split(rows)
    assert {k: v for k, v in pool.items() if k != "by_year"} == {
        "unread_queue": 2, "filed_in_folders": 1, "total": 3}


# ---------------------------------------------------------------------------
# the paired series: the read corpus, by year and by topic
# ---------------------------------------------------------------------------

def test_the_read_year_series_is_read_it_later_only():
    """Mutation: counting the legacy document archive into the comparison.

    10,551 of the index's 17,320 rows are scanned PDFs, Word files and text
    dumps that were never saved with an intention to read later. Comparing a
    reading queue against a corpus that is 62 percent scanned documents
    compares two different acts.
    """
    frame = read_frame([read_row(source="instapaper", date_saved="2018-03-01"),
                        read_row(source="matter", date_saved="2018-07-01"),
                        read_row(source="legacy_pdf", date_saved="2018-09-01")])
    assert analysis.read_corpus_by_year(frame) == {2018: 2}


def test_the_read_year_series_keeps_the_rows_the_topic_series_drops():
    """Mutation: reusing read_corpus_topics()'s clean-rows filter here.

    The two series have different denominators on purpose. A row the content
    guard rejected still went through the save-then-read loop, so it counts as
    a read; it simply carries no usable topics. Filtering it out of the year
    series would make plate 01's total 5,905 where the record says 6,769.
    """
    frame = read_frame([read_row(date_saved="2018-03-01"),
                        read_row(date_saved="2018-05-01", corrupted=True)])
    assert analysis.read_corpus_by_year(frame) == {2018: 2}


def test_a_read_row_with_no_parseable_save_date_is_not_given_a_year():
    """Mutation: coercing an unparseable date to this year, which would pile
    every undated legacy row onto 2026."""
    frame = read_frame([read_row(date_saved="not a date"),
                        read_row(date_saved="2018-03-01")])
    assert analysis.read_corpus_by_year(frame) == {2018: 1}


def test_the_topic_comparison_pairs_each_topic_with_its_read_share():
    """Mutation: publishing the two distributions separately and leaving the
    page to pair them.

    The pairing IS the record's finding, and a page that pairs two lists by
    position rather than by key will silently mismatch the moment one list
    drops a title-shaped string and the other does not.
    """
    rows = ([record(url_sha256=f"{i:064d}", topics=("Ruby on Rails",)) for i in range(3)]
            + [record(url_sha256="f" * 64, topics=("Technology",))])
    read_topics = {2018: {"Ruby on Rails": 1, "Technology": 9}}
    table = analysis.topic_comparison(rows, read_topics, limit=2, read_extra=0)
    by_topic = {r["topic"]: r for r in table["topics"]}
    assert by_topic["Ruby on Rails"]["unread"] == 3
    assert by_topic["Ruby on Rails"]["read"] == 1
    assert table["unread_mentions"] == 4
    assert table["read_mentions"] == 10
    # 0.75 of the unread mentions against 0.10 of the read ones.
    assert by_topic["Ruby on Rails"]["ratio"] == 7.5


def test_the_comparison_reaches_for_subjects_he_read_and_did_not_save():
    """Mutation: ranking on the unread side only.

    "Technology is the one subject he read more than he saved" is a finding
    that cannot appear in a table built from the top of the saved pile. The
    read-only rows are the other half of the pairing, and they are what make
    the plate a comparison rather than a ranking.
    """
    rows = [record(url_sha256=f"{i:064d}", topics=("Ruby on Rails",)) for i in range(3)]
    read_topics = {2018: {"Ruby on Rails": 1, "Social Media": 40}}
    table = analysis.topic_comparison(rows, read_topics, limit=1, read_extra=1)
    names = [r["topic"] for r in table["topics"]]
    assert names == ["Ruby on Rails", "Social Media"]
    assert dict(zip(names, (r["unread"] for r in table["topics"])))["Social Media"] == 0


def test_a_topic_that_is_an_article_title_is_dropped_from_both_sides():
    """Mutation: filtering the unread topics and trusting the read ones.

    The read corpus's topics are model-generated strings too, out of a corpus
    of 17,320 articles, and the record ships them. One of them colliding with
    an UNREAD title would launder that title onto the page through the read
    column, where nothing was looking for it.
    """
    title = "A Very Distinctive Headline About Wanting Less"
    rows = [record(url_sha256="1" * 64, title=title, topics=(title, "Attention"))]
    read_topics = {2018: {title: 99, "Attention": 5}}
    table = analysis.topic_comparison(rows, read_topics, limit=5, read_extra=5,
                                      titles={title.casefold()})
    assert title not in [r["topic"] for r in table["topics"]]
    assert "Attention" in [r["topic"] for r in table["topics"]]


def test_a_topic_the_read_corpus_never_carried_has_no_ratio():
    """Mutation: a ratio of infinity, or of 0, where the denominator is zero.

    Both are claims. "Infinitely heavier in the unread pile" is not something
    four mentions against nothing can support, and 0.0 says the opposite of
    what happened.
    """
    rows = [record(topics=("Ruby on Rails",))]
    table = analysis.topic_comparison(rows, {2018: {"Technology": 5}},
                                      limit=1, read_extra=0)
    assert table["topics"][0]["ratio"] is None


# ---------------------------------------------------------------------------
# the per-day rollup, in the shape Silo's provider daily summary takes
# ---------------------------------------------------------------------------

def test_the_daily_rollup_is_one_row_per_day_with_its_counts():
    """Mutation: emitting one row per item, which is the corpus again.

    The rollup is what makes the record import-eligible, and Silo's provider
    daily summary is unique on (provider, date_of_summary). One row per item
    would be 492 rows claiming the same key.
    """
    rows = [record(url_sha256="1" * 64, saved_date="2018-06-01"),
            record(url_sha256="2" * 64, saved_date="2018-06-01"),
            record(url_sha256="3" * 64, saved_date="2019-01-05")]
    rollup = analysis.daily_rollup(rows)
    days = {d["date_of_summary"]: d for d in rollup["days"]}
    assert len(rollup["days"]) == 2
    assert days["2018-06-01"]["raw_data"]["saves"] == 2
    assert days["2019-01-05"]["raw_data"]["saves"] == 1


def test_the_daily_rollup_is_ordered_by_day():
    """Mutation: dict ordering, which is insertion order, which is the order
    the queue happened to be fetched in. A series drawn from that is noise."""
    rows = [record(url_sha256="1" * 64, saved_date="2019-01-05"),
            record(url_sha256="2" * 64, saved_date="2011-01-27")]
    dates = [d["date_of_summary"] for d in analysis.daily_rollup(rows)["days"]]
    assert dates == sorted(dates)


def test_a_day_carries_category_counts_and_never_an_item():
    """Mutation: carrying the day's titles so the importer "has something to
    key on".

    The rollup is published. Every rule the rest of this record follows applies
    to it, and a per-day list of what was saved that day is the most
    identifying shape the data has: one title plus one date is an article.
    """
    rows = [record(url_sha256="1" * 64, saved_date="2018-06-01",
                   abandonment="nearly_finished", starred=True, words=900),
            record(url_sha256="2" * 64, saved_date="2018-06-01",
                   abandonment="never_opened", words=100)]
    day = analysis.daily_rollup(rows)["days"][0]
    stats = day["raw_data"]
    assert stats["by_abandonment"] == {"never_opened": 1, "started": 0,
                                       "nearly_finished": 1}
    assert stats["starred"] == 1
    assert stats["words"] == 1000
    assert day["word_count"] == 1000
    serialised = repr(day)
    assert "An article title long enough to be a needle" not in serialised
    assert "https://" not in serialised

    # The allowlist, asserted rather than assumed. Adversarial review walked
    # `url_sha256` into this dict - the join key `five_ws.source_id` calls the
    # one disclosure this record refuses - and every guard missed it, because
    # they all constrained the DAY's keys and then greped the inside for the
    # fixture's own strings. A key this set does not name is a key nobody chose.
    assert set(day) == analysis.DAILY_DAY_KEYS
    assert set(stats) == analysis.DAILY_RAW_KEYS


def test_a_day_reports_reads_as_unknown_rather_than_zero():
    """Mutation: `"reads": 0`.

    Every item in this corpus is unread by definition, so the record has no
    reading events at all - not zero of them on a given day, but no
    observation. A Silo import that read 0 would draw a flat line at the
    bottom of a chart and call it measurement.
    """
    day = analysis.daily_rollup([record(saved_date="2018-06-01")])["days"][0]
    assert day["raw_data"]["reads"] is None
    assert "unread" in analysis.DAILY_READS_NOTE.casefold()


def test_the_day_carries_the_inferred_reason_presence_rate_not_the_reason():
    """Mutation: rolling the why_saved sentences up "because a day is an
    aggregate".

    A day with one save is not an aggregate. Concatenating a single item's
    inference under a date publishes both the sentence and the date it belongs
    to, which is more identifying than the title would have been.
    """
    rows = [record(url_sha256="1" * 64, saved_date="2018-06-01",
                   why="He was thinking about attention.", confidence="high"),
            record(url_sha256="2" * 64, saved_date="2018-06-01", why="",
                   confidence="low")]
    stats = analysis.daily_rollup(rows)["days"][0]["raw_data"]
    assert stats["why_saved_present"] == 1
    assert stats["why_saved_rate"] == 0.5
    assert "thinking about attention" not in repr(stats)


def test_the_rollup_declares_its_provider_source_and_provenance():
    """Mutation: a bare list of days.

    Silo keys a daily summary on (provider, date_of_summary) and carries its
    provenance inside the payload rather than in a column, so a series arriving
    without those is a series nothing can file.
    """
    rollup = analysis.daily_rollup([record(saved_date="2018-06-01")],
                                   built="2026-09-16")
    assert rollup["provider"] == "record"
    assert rollup["source"] == "meant-to-read"
    assert rollup["imported_at"] == "2026-09-16T00:00:00Z"
    assert rollup["timezone"] == "UTC"
    # The precedent nests provenance inside the column Silo ingests, because
    # nothing in Silo can see a sibling of it.
    inner = rollup["days"][0]["raw_data"]
    assert inner["source"] == "meant-to-read"
    assert inner["imported_at"] == "2026-09-16T00:00:00Z"


def test_an_undated_row_is_reported_rather_than_filed_under_a_guessed_day():
    """Mutation: dropping it silently, or bucketing it under today.

    Either one makes the series disagree with `items` with nothing saying so.
    """
    rollup = analysis.daily_rollup([record(url_sha256="1" * 64, saved_date=None),
                                    record(url_sha256="2" * 64, saved_date="2018-06-01")])
    assert rollup["undated"] == 1
    assert sum(d["raw_data"]["saves"] for d in rollup["days"]) == 1


# ---------------------------------------------------------------------------
# what adversarial review found on 2026-09-16
# ---------------------------------------------------------------------------

def test_a_near_copy_of_a_title_normalizes_onto_the_title():
    """Mutation: comparing on `.strip().casefold()`.

    The hole, found by adversarial review: a model reads an article, emits the
    headline as a "topic" with one curly apostrophe turned straight, and the
    exact-match redaction publishes it. The leak scan downstream is
    exact-substring too, so nothing then looks for it - two gates, one blind
    spot, failing in the same direction. 26 percent of this corpus's titles
    change under ordinary punctuation folding.
    """
    import unicodedata
    base = "The Crane Wife, and What I Learned About Wanting Less"
    for variant in (base.replace("'", "’"),
                    base.replace(",", "—"),  # not a variant: must differ
                    unicodedata.normalize("NFD", base),
                    base.replace(" ", "  "),
                    base.replace(" ", " "),
                    base.upper()):
        same = analysis.normalize(variant) == analysis.normalize(base)
        assert same is (variant != base.replace(",", "—"))


def test_normalization_does_not_collapse_two_different_titles():
    """Mutation: a normalizer so aggressive it merges distinct titles.

    A redaction that drops everything is a record with no topics on it, which
    is the other way this fails. Two headlines that differ by a word stay two.
    """
    assert analysis.normalize("A Very Long Headline About Farming") != \
        analysis.normalize("A Very Long Headline About Fishing")


def test_the_pool_splits_by_year_as_well_as_overall():
    """Mutation: an overall split only.

    The finding is not the split, it is where it falls. The queue holds nothing
    from before 2014, so every item that survived fifteen years unread survived
    in a folder, and the record says that in prose. Prose with a typed number
    in it is what the payload exists to remove.
    """
    rows = [record(url_sha256="1" * 64, year=2011, folder="Steve Jobs"),
            record(url_sha256="2" * 64, year=2011, folder="Steve Jobs"),
            record(url_sha256="3" * 64, year=2018, folder=None)]
    by_year = analysis.pool_split(rows)["by_year"]
    assert by_year["2011"] == {"unread_queue": 0, "filed_in_folders": 2}
    assert by_year["2018"] == {"unread_queue": 1, "filed_in_folders": 0}


def test_the_read_titles_are_normalized_and_respect_the_floor():
    """Mutation: an untested producer of the read-column redaction set.

    `read_corpus_titles()` had zero references in any test file. It builds the
    set the READ column is redacted against, and that column has no leak-scan
    backstop at all - the needles are this corpus's titles, and the index
    repository's word list is a hundred hand-kept strings, not 17,320.
    """
    frame = read_frame([
        {"title": "A Read Article Title’s Long Enough", "source": "instapaper",
         "date_saved": "2018-03-01", "topics": ["X"], "content_corrupted": False,
         "url": "https://x.example/y"},
        {"title": "Short", "source": "instapaper", "date_saved": "2018-03-01",
         "topics": ["X"], "content_corrupted": False, "url": "https://x.example/z"}])
    titles = analysis.read_corpus_titles(frame)
    assert analysis.normalize("A Read Article Title's Long Enough") in titles
    assert analysis.normalize("Short") not in titles


def test_the_read_column_drops_a_near_copy_of_an_unread_title():
    """Mutation: normalizing the unread column and not the read one.

    The read corpus's topics are model-generated out of 17,320 articles and
    this record publishes them beside the saved ones. A near-copy of an unread
    title arriving through that column has no scan behind it at all.
    """
    title = "A Very Distinctive Headline About Wanting Less"
    near = title.replace("About", "About ")
    rows = [record(url_sha256="1" * 64, title=title, topics=("Attention",))]
    table = analysis.topic_comparison(rows, {2018: {near: 99, "Attention": 5}},
                                      limit=5, read_extra=5,
                                      titles={analysis.normalize(title)})
    assert near not in [r["topic"] for r in table["topics"]]


def test_the_rollup_declares_what_a_silo_import_still_needs():
    """Mutation: a shape that only LOOKS like Silo's.

    Executed against Silo by adversarial review: both "meant-to-read" and the
    house rule's own "record" fail `ProviderDailySummary#valid?`, and Silo
    recomputes `computed_stats` from `raw_data`, returning {} for a provider it
    has no method for - so numbers shipped in `computed_stats` are erased by the
    first `rake provider_analytics:compute_all` with nothing left to rebuild
    from. A payload that imports and silently self-destructs is worse than one
    that says what is missing.
    """
    rollup = analysis.daily_rollup([record(saved_date="2018-06-01")])
    assert rollup["silo_import"]["provider_accepted"] is False
    needs = " ".join(rollup["silo_import"]["needs"]).casefold()
    assert "supported_providers" in needs
    assert "compute_record_stats" in needs
    assert "word_count" in needs
    # The numbers are in the column Silo ingests, not the one it overwrites.
    assert "computed_stats" not in rollup["days"][0]
    assert rollup["days"][0]["raw_data"]["saves"] == 1


def test_the_index_row_count_is_the_whole_frame_not_the_comparison():
    """Mutation: returning None, or returning the read-it-later subset.

    The record's plate-01 footnote is a sentence about the difference between
    the two - "6,769 of the index's 17,320 rows; the other 10,551 are the
    legacy document archive" - so a producer that returns the subset publishes
    "6,769 of the index's 6,769 rows" and a producer that returns None leaves
    the page with nothing to print.

    This function had no direct test when it landed, and the mutation audit
    caught that: the payload test passes the count in by hand, so the producer
    could return anything at all and stay green.
    """
    frame = read_frame([read_row(source="instapaper"), read_row(source="matter"),
                        read_row(source="legacy_pdf"), read_row(source="legacy_txt")])
    assert analysis.read_corpus_rows(frame) == 4
    assert analysis.read_corpus_rows(frame) > sum(
        analysis.read_corpus_by_year(frame).values())
    assert analysis.read_corpus_rows(None) is None
