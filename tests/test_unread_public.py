"""The public shape of the unread corpus, and the scan that fails the build closed.

Every test here names, in its docstring, the mutation it catches.

This record needs more care than the Reading record's did, and the plan says so
plainly. Reading publishes what Adam read. This publishes what he intended and
did not do, which is a more revealing document: an unread title is an unexecuted
intention and the set of them reads like a diary.

The rules, all four tested here rather than trusted:

  * No titles and no URLs on the public page, at all.
  * Domains are publishable. 78 of 395 from one newspaper is a finding.
  * Topics and concepts are publishable; people, orgs and locations are not.
  * The scan fails the build closed. An absent needle list is not an empty one:
    with nothing to look for, every tree passes.
"""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from unread import analysis  # noqa: E402
from unread import public  # noqa: E402


def record(url_sha256="a" * 64, year=2018, domain="nytimes.com",
           title="Is Google Making Us Stupid, The Atlantic Cover Story",
           url="https://nytimes.com/2018/04/02/the-distinctive-slug-here",
           topics=("Attention", "Media Criticism"), aged_out=False,
           confidence="high", why="He was thinking about attention that spring.",
           abandonment="never_opened", resolve_path="instapaper",
           corrupted=False, words=1200, **extra):
    row = {
        "url_sha256": url_sha256,
        "title": title,
        "url": url,
        "domain": domain,
        "saved_year": year,
        "saved_date": f"{year}-06-01",
        "ai_topics": list(topics),
        "ai_people": ["Nicholas Carr", "Marshall McLuhan"],
        "ai_orgs": ["The Atlantic", "Bell Labs"],
        "ai_locations": ["Boston", "Menlo Park"],
        "ai_concepts": ["Deep Reading"],
        "ai_summary": "An argument that the web rewires how we read.",
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


def corpus(n=6):
    titles = [
        "Is Google Making Us Stupid, The Atlantic Cover Story",
        "The Machine Stops And Nobody Notices It Happening",
        "Why We Sleep And What Happens When We Do Not",
        "A Very Long And Distinctive Headline About Farming",
        "Notes On The Collapse Of The Attention Commons",
        "The Quiet Death Of The Personal Homepage Era",
    ]
    domains = ["nytimes.com", "medium.com", "nytimes.com", "theatlantic.com",
               "brainpickings.org", "nytimes.com"]
    return [record(url_sha256=f"{i:064d}", title=titles[i % len(titles)],
                   url=f"https://{domains[i % len(domains)]}/2018/the-distinctive-slug-{i}",
                   domain=domains[i % len(domains)], year=2014 + i)
            for i in range(n)]


def published_text(out):
    return "\n".join(p.read_text(encoding="utf-8")
                     for p in Path(out).rglob("*") if p.is_file())


# ---------------------------------------------------------------------------
# what the public shape carries
# ---------------------------------------------------------------------------

def test_no_title_from_the_corpus_reaches_the_public_shape():
    """Mutation: publishing a "top saved" list, or any item-level row.

    An unread title is an unexecuted intention and the set of them reads like a
    diary. This is the rule the whole record is built around.
    """
    rows = corpus()
    payload = json.dumps(public.public_shape(rows))
    for row in rows:
        assert row["title"].casefold() not in payload.casefold()


def test_no_url_from_the_corpus_reaches_the_public_shape():
    """Mutation: publishing the URL alongside the domain.

    A host is a fact about the archive; a path identifies one article in it.
    """
    rows = corpus()
    payload = json.dumps(public.public_shape(rows))
    for row in rows:
        assert row["url"] not in payload
        assert "the-distinctive-slug" not in payload


def test_domains_are_published_because_the_source_distribution_is_the_finding():
    """Mutation: redacting domains along with URLs.

    Adam settled this on 2026-09-15: the source distribution ships. 78 of 395
    from one newspaper is a finding, not a disclosure, and it is the most
    interesting cut the record has.
    """
    payload = public.public_shape(corpus())
    domains = dict(payload["by_domain"])
    assert domains["nytimes.com"] == 3
    assert payload["distinct_domains"] >= 4


def test_topics_are_published_and_people_orgs_and_locations_are_not():
    """Mutation: shipping the entity columns with the topic columns.

    An entity extracted from an article nobody read still says what he was
    looking into. The plan gates those behind the same allowlist the Highlights
    pages will use, and that allowlist does not exist yet.
    """
    payload = json.dumps(public.public_shape(corpus()))
    assert "Attention" in payload
    assert "Nicholas Carr" not in payload
    assert "Bell Labs" not in payload
    assert "Menlo Park" not in payload


def test_no_summary_or_inference_sentence_reaches_the_public_shape():
    """Mutation: publishing why_saved because it is "only one sentence".

    It is an inference about a private intention, attached to an article that
    is identifiable from it. The public record gets counts.
    """
    payload = json.dumps(public.public_shape(corpus()))
    assert "rewires how we read" not in payload
    assert "thinking about attention" not in payload


def test_the_shortlist_is_a_count_and_nothing_more():
    """Mutation: publishing the ranked list because it is "the good part".

    The plan: it ships private first, on reading.adamthede.com where titles are
    allowed and Adam can judge whether the ranking is any good. The public
    record gets a count.
    """
    rows = corpus()
    payload = public.public_shape(rows, shortlist_count=12)
    assert payload["still_worth_your_time"] == 12
    assert isinstance(payload["still_worth_your_time"], int)

    # Not a shape check on the container - the previous version asserted no
    # list-of-dicts carried a "title" key, which a list of bare title STRINGS
    # sails past, and its audit mutation had been shaped to match it.
    # Adversarial review caught the pair. This runs the real scan over the real
    # serialised payload.
    titles, paths = public.private_needles(rows)
    assert titles and paths
    serialised = json.dumps(payload, ensure_ascii=False).casefold()
    for needle in list(titles) + list(paths):
        assert needle.casefold() not in serialised


def test_a_low_confidence_inference_is_excluded_from_the_published_counts():
    """Mutation: the public aggregates using a different rule from the private ones.

    Two rules that disagree is worse than either rule, and the public one is
    the one that cannot be corrected after the fact.
    """
    rows = corpus(3) + [record(url_sha256="l" * 64, confidence="low")]
    payload = public.public_shape(rows)
    assert payload["why_saved"]["counted"] == 3
    assert payload["why_saved"]["excluded_low_confidence"] == 1


def test_the_published_inference_counts_are_labelled_as_inference():
    """Mutation: a public number about why_saved with no label on it.

    Every surface that shows the inference labels it as inference in the
    surface itself rather than in a footnote. A count derived from it is such a
    surface.
    """
    payload = public.public_shape(corpus())
    assert payload["why_saved"]["kind"] == "inference"


def test_the_public_shape_carries_the_comparison_caveat():
    """Mutation: publishing the topic comparison stripped of its qualification."""
    payload = public.public_shape(corpus())
    assert "10,000" in payload["caveat"]


# ---------------------------------------------------------------------------
# the leak scan
# ---------------------------------------------------------------------------

def test_the_needles_come_from_this_corpus_own_titles_and_paths():
    """Mutation: scanning against the read corpus's needles instead.

    The plan is specific: the leak needles must come from this corpus's own
    titles and paths. The read index does not contain them.
    """
    titles, paths = public.private_needles(corpus())
    assert "Is Google Making Us Stupid, The Atlantic Cover Story" in titles
    assert any("the-distinctive-slug" in p for p in paths)


def test_a_needle_shorter_than_the_floor_is_not_used():
    """Mutation: dropping the length floor.

    A four-character title matches inside an unrelated word on any page and
    turns the scan into a permanent red, which is how a leak scan gets switched
    off.
    """
    titles, _ = public.private_needles([record(title="Hi")])
    assert "Hi" not in titles


def test_a_topic_that_is_an_article_title_is_dropped_from_the_payload():
    """Mutation: dropping the TITLE from the needles instead of the topic from
    the payload - which is how a title gets laundered into the public record.

    Found by adversarial review of the first fix. One article is titled exactly
    "Productivity", which is also an extracted topic, and topics ship. The
    first fix dropped that title as a needle. But the model reads the whole
    article now that the body cap is off, so it can and does emit a headline as
    a topic - and then the scan is no longer looking for the one string that
    would catch it.

    The redaction goes the other way. The colliding TOPIC is not published; the
    title stays a needle. A topic costs nothing to drop and a laundered title
    costs the whole record.
    """
    rows = [record(url_sha256="1" * 64,
                   title="The Crane Wife, And What I Learned About Wanting Less",
                   topics=("The Crane Wife, And What I Learned About Wanting Less",
                           "Attention"))]
    payload = json.dumps(public.public_shape(rows))
    assert "The Crane Wife" not in payload
    assert "Attention" in payload

    titles, _ = public.private_needles(rows)
    assert "The Crane Wife, And What I Learned About Wanting Less" in titles


def test_no_title_can_be_laundered_through_the_band_topic_lists(tmp_path):
    """Mutation: filtering the topic table but not the per-band topic lists.

    `abandonment_bands[band]["top_topics"]` is a second, separate route for a
    model-generated string to reach the page, and a band can be small enough
    that a single article's headline makes its top eight.
    """
    rows = [record(url_sha256=f"{i:064d}", abandonment="nearly_finished",
                   title=f"A Distinctive Headline Number {i} About Wanting Less",
                   topics=(f"A Distinctive Headline Number {i} About Wanting Less",))
            for i in range(3)]
    out = tmp_path / "record"
    public.build(rows, out)
    text = published_text(out)
    for row in rows:
        assert row["title"] not in text


def test_a_corpus_whose_titles_all_fall_below_the_floor_fails_closed(tmp_path):
    """Mutation: publishing with zero title needles because only paths survived.

    Failing closed on the UNION lets a build run with nothing looking for a
    title at all, while PROVENANCE.md reports it walked the tree for 0 titles.
    A corpus that has titles and produced no title needle cannot detect a title
    leak, and that is the leak this record exists to prevent.
    """
    rows = [record(url_sha256="1" * 64, title="Hi",
                   url="https://x.io/a-nice-long-path-here")]
    with pytest.raises(public.LeakTestError):
        public.build(rows, tmp_path / "record")


def test_the_needles_are_longest_first():
    """Mutation: an arbitrary order.

    When two titles share a prefix the longer is the more specific finding, and
    a scan that reports the shorter one names the wrong article in the failure.
    """
    titles, _ = public.private_needles(corpus())
    assert titles == sorted(titles, key=lambda t: (-len(t), t))


def test_a_planted_title_is_found(tmp_path):
    """Mutation: a scan that runs over the data but not over the written files."""
    root = tmp_path / "out"
    root.mkdir()
    (root / "leak.json").write_text('{"note": "Is Google Making Us Stupid, The Atlantic Cover Story"}')
    titles, paths = public.private_needles(corpus())
    found = public.leak_scan(root, titles, paths)
    assert found and found[0]["kind"] == "title"


def test_a_planted_url_path_is_found(tmp_path):
    """Mutation: scanning titles only.

    A path identifies one article as precisely as a title does, and a
    machine-readable payload is likelier to carry the path.
    """
    root = tmp_path / "out"
    root.mkdir()
    (root / "leak.json").write_text('{"p": "/2018/the-distinctive-slug-3"}')
    titles, paths = public.private_needles(corpus())
    found = public.leak_scan(root, titles, paths)
    assert found and found[0]["kind"] == "url path"


def test_an_html_escaped_title_is_still_found(tmp_path):
    """Mutation: reading the raw bytes only.

    A title carrying an ampersand or a curly quote reaches a page escaped, and
    a scan that only reads raw bytes misses exactly the titles most likely to
    be printed verbatim.
    """
    rows = [record(title="Tractors & Silos And Other Long Titles")]
    root = tmp_path / "out"
    root.mkdir()
    (root / "page.html").write_text("<p>Tractors &amp; Silos And Other Long Titles</p>")
    titles, paths = public.private_needles(rows)
    assert public.leak_scan(root, titles, paths)


def test_a_clean_tree_finds_nothing(tmp_path):
    """Mutation: a scan that reports a finding on everything, which is the same
    as a scan that is switched off the first time someone gets tired of it."""
    root = tmp_path / "out"
    root.mkdir()
    (root / "clean.json").write_text('{"by_domain": [["nytimes.com", 78]]}')
    titles, paths = public.private_needles(corpus())
    assert public.leak_scan(root, titles, paths) == []


# ---------------------------------------------------------------------------
# failing closed
# ---------------------------------------------------------------------------

def test_a_build_with_no_needles_refuses_to_publish(tmp_path):
    """Mutation: an empty needle list treated as a clean scan.

    This check fails closed. With nothing to look for, every tree passes, and a
    build that publishes on that basis has published unscanned.
    """
    with pytest.raises(public.LeakTestError):
        public.build([], tmp_path / "record")


def test_a_corpus_whose_titles_are_all_too_short_refuses_to_publish(tmp_path):
    """Mutation: falling back to "no usable needles, carry on".

    The floor removing every needle is indistinguishable, to the scan, from a
    corpus with nothing private in it. Only one of those is true.
    """
    with pytest.raises(public.LeakTestError):
        public.build([record(title="Hi", url="https://x.io/a")], tmp_path / "record")


def test_a_leak_stops_the_publish_before_anything_is_moved(tmp_path):
    """Mutation: writing the record and then complaining about it.

    A build that leaks must not publish a page and then report the leak. The
    order is the whole design: write to a sibling, scan it whole, move only
    after.
    """
    out = tmp_path / "record"
    with pytest.raises(public.LeakTestError):
        public.build(corpus(), out, payload_hook=lambda p: dict(
            p, oops="Is Google Making Us Stupid, The Atlantic Cover Story"))
    assert not out.exists()
    assert not list(tmp_path.glob("*.building"))


def test_a_clean_build_publishes_and_scans_clean(tmp_path):
    """Mutation: a build that never runs the scan at all."""
    out = tmp_path / "record"
    result = public.build(corpus(), out)
    assert (out / "public_data.json").exists()
    assert (out / "PROVENANCE.md").exists()
    assert result["leak_findings"] == 0
    assert result["needles"] > 0

    titles, paths = public.private_needles(corpus())
    assert public.leak_scan(out, titles, paths) == []


def test_the_published_tree_survives_the_scan_over_the_whole_corpus(tmp_path):
    """Mutation: a redaction that holds for the fixture and not for the corpus.

    The scan the build runs is the same scan run here, over every needle the
    corpus produces, against every file that was written.
    """
    rows = corpus(6)
    out = tmp_path / "record"
    public.build(rows, out)
    text = published_text(out).casefold()
    for row in rows:
        assert row["title"].casefold() not in text
        assert row["url"].casefold() not in text


def test_the_build_refuses_an_out_directory_it_did_not_write(tmp_path):
    """Mutation: clearing whatever is in --out.

    The same posture `site/public_shape.py` takes: a directory holding names no
    public build wrote is somebody else's, and clearing it would be the
    destructive kind of surprise.
    """
    out = tmp_path / "record"
    out.mkdir()
    (out / "someones-notes.md").write_text("not mine")
    with pytest.raises(SystemExit):
        public.build(corpus(), out)
    assert (out / "someones-notes.md").exists()


def test_rebuilding_over_a_previous_record_is_allowed(tmp_path):
    """Mutation: a guard so strict the record can never be rebuilt."""
    out = tmp_path / "record"
    public.build(corpus(), out)
    public.build(corpus(4), out)
    assert json.loads((out / "public_data.json").read_text())["items"] == 4


def test_the_needle_file_is_written_for_the_index_repo(tmp_path):
    """Mutation: leaving the index repo's word list to be assembled by hand.

    data.adamthede.com's own leak test reads `data/private_strings.txt`, which
    is gitignored and hand-assembled. Emitting this corpus's needles as a build
    artifact is what makes that step a copy rather than a transcription.
    """
    out = tmp_path / "record"
    needles = tmp_path / "needles.txt"
    public.build(corpus(), out, needle_file=needles)
    lines = [l for l in needles.read_text().splitlines() if l.strip()
             and not l.startswith("#")]
    assert any("Is Google Making Us Stupid" in l for l in lines)


# ---------------------------------------------------------------------------
# the cover aggregates the page reads, and the leak scan over them
# ---------------------------------------------------------------------------

def test_the_cover_aggregates_are_in_the_payload():
    """Mutation: the built page computing them from the enriched file.

    The record ships as one self-contained page on a host that holds no corpus.
    Every figure it prints has to come out of `public_data.json`, or it comes
    out of a number somebody typed, and a typed number is the thing this whole
    pipeline exists to not have.
    """
    payload = public.public_shape(corpus())
    assert payload["words"]["total"] > 0
    assert payload["words"]["measured_over"] == 6
    assert payload["saved_span"]["oldest"] == "2014-06-01"
    assert payload["saved_span"]["newest"] == "2019-06-01"
    assert payload["saved_span"]["years_spanned"] == 6
    assert payload["starred"] == 0
    assert {k: v for k, v in payload["pool"].items() if k != "by_year"} == {
        "unread_queue": 6, "filed_in_folders": 0, "total": 6}
    assert payload["pool"]["by_year"]["2014"] == {"unread_queue": 1,
                                                  "filed_in_folders": 0}
    assert payload["why_saved"]["by_confidence"]["high"] == 6


def test_the_confidence_split_is_over_the_corpus_and_the_counts_are_over_the_answers():
    """Mutation: publishing one population under both names.

    `by_confidence` splits all 492 rows by the grade the model set.
    `counted` / `excluded_low_confidence` / `no_inference` split only the rows
    that carried a sentence. A page that adds a bar from one to a bar from the
    other is adding two different denominators, and the two disagree by exactly
    the rows the model graded and then declined to answer.
    """
    rows = corpus(3) + [record(url_sha256="l" * 64, confidence="low"),
                        record(url_sha256="d" * 64, confidence="low", why="")]
    payload = public.public_shape(rows)
    why = payload["why_saved"]
    assert why["by_confidence"]["low"] == 2
    assert why["excluded_low_confidence"] == 1
    assert why["no_inference"] == 1
    assert sum(why["by_confidence"][g] for g in ("high", "medium", "low", "unset")) == 5
    assert why["counted"] + why["excluded_low_confidence"] + why["no_inference"] == 5


def test_the_star_count_is_a_number_and_not_a_list_of_what_was_starred():
    """Mutation: publishing `starred: [...]` because "it is only twenty".

    Twenty is worse, not better. A starred unread article is the strongest
    statement of intent in the corpus, and twenty of them named is twenty
    unexecuted intentions with his name on them.
    """
    rows = [record(url_sha256=f"{i:064d}", starred=(i == 0),
                   title=f"A Distinctive Starred Headline Number {i}",
                   url=f"https://x.example/2018/the-distinctive-slug-{i}")
            for i in range(4)]
    payload = public.public_shape(rows)
    assert payload["starred"] == 1
    serialised = json.dumps(payload, ensure_ascii=False).casefold()
    for row in rows:
        assert row["title"].casefold() not in serialised


def test_no_needle_reaches_any_of_the_new_payload_fields(tmp_path):
    """Mutation: a new field added to the payload after the scan was written.

    This is the failure the allowlist exists to prevent and the one a test
    suite most easily misses: the redaction is correct for the fields it knew
    about on the day it was written. So the scan here runs over the WHOLE
    serialised payload with the whole corpus's needles, and every field added
    later is inside it by construction.
    """
    rows = corpus(6)
    read_topics = {2018: {"Attention": 40, "Technology": 9}}
    payload = public.public_shape(
        rows, read_by_year={2018: 237}, read_topics=read_topics,
        read_titles={"A Read Article Title Long Enough To Be A Needle"})
    titles, paths = public.private_needles(rows)
    assert titles and paths
    serialised = json.dumps(payload, ensure_ascii=False).casefold()
    for needle in list(titles) + list(paths):
        assert needle.casefold() not in serialised
    # and the scan the build runs, over the real written tree
    out = tmp_path / "record"
    public.build(rows, out, payload_hook=lambda p: dict(
        p, read_comparison=payload["read_comparison"],
        topic_comparison=payload["topic_comparison"]))
    assert public.leak_scan(out, titles, paths) == []


# ---------------------------------------------------------------------------
# the paired series
# ---------------------------------------------------------------------------

def test_the_read_comparison_is_counts_by_year_and_nothing_else():
    """Mutation: carrying the read corpus's rows so the page "can filter".

    The read corpus is 17,320 articles with titles, URLs, authors and
    summaries. The paired plate needs seventeen integers.
    """
    payload = public.public_shape(corpus(), read_by_year={2017: 133, 2018: 237})
    comparison = payload["read_comparison"]
    assert comparison["by_year"] == {"2017": 133, "2018": 237}
    assert comparison["total"] == 370
    assert all(isinstance(v, int) for v in comparison["by_year"].values())


def test_the_read_comparison_is_absent_rather_than_empty_when_the_index_is_not_read():
    """Mutation: emitting `{}`, which a page renders as a plate with no bars
    and no explanation.

    None says "this build did not read the index". An empty dict says "he read
    nothing", which is a claim about a corpus of 17,320 articles.
    """
    payload = public.public_shape(corpus())
    assert payload["read_comparison"] is None
    assert payload["topic_comparison"] is None


def test_an_unread_title_cannot_be_laundered_through_the_read_topic_column(tmp_path):
    """Mutation: filtering the unread topics and trusting the read ones.

    A third route onto the page, after the topic table and the band lists. The
    read corpus's topics are model-generated strings out of 17,320 articles and
    this record publishes them beside the saved ones; one colliding with an
    unread title would print that title in a column nothing was scanning.
    """
    title = "Is Google Making Us Stupid, The Atlantic Cover Story"
    rows = corpus()
    payload = public.public_shape(
        rows, read_topics={2018: {title: 900, "Technology": 9}})
    assert title not in json.dumps(payload, ensure_ascii=False)
    out = tmp_path / "record"
    public.build(rows, out, payload_hook=lambda p: dict(
        p, topic_comparison=payload["topic_comparison"]))
    assert title not in published_text(out)


def test_a_read_corpus_title_is_dropped_from_the_read_topic_column_too():
    """Mutation: scanning only against this corpus's titles.

    This record's needles are its own titles, correctly - the read index does
    not contain them. But that leaves the read corpus's own titles unguarded on
    a page that publishes read-corpus topics, and the index repo's word list is
    120 hand-kept strings, not 16,467 titles. The redaction runs at the data
    layer with both title sets, because there is no backstop for this one.
    """
    read_title = "A Read Article Title Long Enough To Be A Needle"
    payload = public.public_shape(
        corpus(), read_topics={2018: {read_title: 900, "Technology": 9}},
        read_titles={read_title})
    assert read_title not in json.dumps(payload, ensure_ascii=False)


# ---------------------------------------------------------------------------
# the per-day rollup and the 5Ws declaration
# ---------------------------------------------------------------------------

def test_the_payload_carries_a_per_day_rollup_and_it_holds_no_item(tmp_path):
    """Mutation: a rollup carrying the day's titles "so the importer has a key".

    Every record is built import-eligible, and the rollup is the shape Silo
    imports. It is published like everything else here, so one date plus one
    title - which is exactly what a one-save day would carry - is the most
    identifying pair in the corpus.
    """
    rows = corpus(6)
    payload = public.public_shape(rows)
    days = payload["daily"]["days"]
    assert days
    # Both key sets, the day's and the one inside it. Constraining only the
    # outer one is what let a per-day join key through adversarial review: the
    # top-level payload allowlist stops at the door of `raw_data`.
    assert all(set(d) == analysis.DAILY_DAY_KEYS for d in days)
    assert all(set(d["raw_data"]) == analysis.DAILY_RAW_KEYS for d in days)
    assert payload["daily"]["provider"] == "record"
    serialised = json.dumps(payload["daily"], ensure_ascii=False).casefold()
    for row in rows:
        assert row["title"].casefold() not in serialised
        assert row["url"].casefold() not in serialised
    assert "rewires how we read" not in serialised
    assert "thinking about attention" not in serialised


def test_the_rollup_reports_reads_as_unknown():
    """Mutation: `reads: 0`, which imports as a measurement of nothing read."""
    payload = public.public_shape(corpus())
    assert all(d["raw_data"]["reads"] is None
               for d in payload["daily"]["days"])


def test_the_payload_declares_its_five_ws_and_says_which_are_not_published():
    """Mutation: declaring only the Ws that ship.

    The declaration is what a later importer reads to know what it is getting.
    A who it does not mention is a who somebody assumes is coming, and the
    whole point of this record is that the who never ships.
    """
    five = public.public_shape(corpus())["five_ws"]
    assert set(five) >= {"who", "what", "when", "where", "why",
                         "source", "source_id", "provenance"}
    assert five["who"]["published"] is False
    assert five["where"]["published"] is False
    assert five["what"]["published"] is True
    assert five["when"]["published"] is True
    assert five["when"]["precision"] == "day"
    assert five["why"]["published"] is True
    assert five["why"]["kind"] == "inference"
    assert five["source_id"]["published"] is False


def test_the_five_ws_declaration_agrees_with_the_payload_it_describes():
    """Mutation: a declaration that drifts from the payload.

    A declaration nothing checks is a comment. Every field name it points at
    has to be a field the payload actually carries, or the importer written
    against it breaks on the first import and the record gets blamed.
    """
    payload = public.public_shape(corpus())
    for which in ("what", "when", "why"):
        fields = payload["five_ws"][which]["fields"]
        assert fields
        for name in fields:
            assert name.split(".")[0].split("[")[0] in payload, (which, name)


# ---------------------------------------------------------------------------
# the command line
# ---------------------------------------------------------------------------

def _cli():
    import sys as sys_mod
    sys_mod.path.insert(0, str(REPO / "scripts" / "core"))
    import publish_unread_record
    return publish_unread_record


def test_the_publish_cli_refuses_to_build_without_the_read_corpus(tmp_path, capsys):
    """Mutation: warning and carrying on.

    A payload silently missing the read series publishes a record whose first
    two plates have no bars in them, and the build that made it printed
    success. Two of six plates is not a warning-sized hole.
    """
    enriched = tmp_path / "enriched.jsonl"
    enriched.write_text("\n".join(json.dumps(r) for r in corpus()), encoding="utf-8")
    code = _cli().main(["--enriched", str(enriched), "--out", str(tmp_path / "rec"),
                        "--index", str(tmp_path / "absent.parquet")])
    assert code == 2
    assert not (tmp_path / "rec").exists()
    assert "--no-comparison" in capsys.readouterr().err


def test_the_publish_cli_builds_the_unpaired_half_when_asked(tmp_path, capsys):
    """Mutation: an escape hatch that quietly produces the same payload.

    The unpaired build is a legitimate thing to want and it has to say what it
    is, on the terminal and in the payload, or it becomes the thing that ships
    by accident.
    """
    enriched = tmp_path / "enriched.jsonl"
    enriched.write_text("\n".join(json.dumps(r) for r in corpus()), encoding="utf-8")
    out = tmp_path / "rec"
    code = _cli().main(["--enriched", str(enriched), "--out", str(out),
                        "--no-comparison"])
    assert code == 0
    payload = json.loads((out / "public_data.json").read_text())
    assert payload["read_comparison"] is None
    assert "plates 01 and 02 cannot be drawn" in capsys.readouterr().out


def test_the_paired_topic_table_carries_the_comparison_caveat():
    """Mutation: publishing the paired plate stripped of its qualification.

    The record already publishes the caveat once at the top level, and that was
    enough while the comparison lived in a private report. It is not enough on
    a plate: the two bars sit side by side, and the reason they are not quite
    comparable has to travel with the figure rather than with the document.
    """
    payload = public.public_shape(corpus(), read_topics={2018: {"Attention": 5}})
    assert "10,000" in payload["topic_comparison"]["caveat"]
