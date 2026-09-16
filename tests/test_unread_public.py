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
    payload = public.public_shape(corpus(), shortlist_count=12)
    assert payload["still_worth_your_time"] == 12
    assert not any(isinstance(v, list) and v and isinstance(v[0], dict)
                   and "title" in v[0] for v in payload.values())


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
