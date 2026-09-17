"""The public shape of the unread corpus, and the scan that fails the build closed.

    python3 scripts/core/publish_unread_record.py --out records/meant-to-read

This emits the data for a record on data.adamthede.com, the coverage-map index
over the public shapes of the private lifelogging records. The record's own page
design is a later lane and does not start before Adam has approved a mockup;
what ships here is the data layer and the scan, which are the parts that decide
whether the page can exist at all.

This record needs more care than the Reading record's did. Reading publishes
what Adam read: a read article is a finished act. This publishes what he
intended and did not do, which is a more revealing document - an unread title is
an unexecuted intention, and the set of them reads like a diary.

The four rules, settled with Adam on 2026-09-15:

**No titles and no URLs, at all.** Counts, years, topic distributions, domain
counts and the aging curve. Nothing item level.

**Domains are publishable.** The source distribution is the most interesting cut
the record has and it ships. 78 of 395 from one newspaper is a finding, not a
disclosure.

**Topics and concepts are publishable; people, orgs and locations are not.** An
entity extracted from an article nobody read still says what he was looking
into, and the allowlist those would need does not exist yet.

**Redaction happens at the data layer.** `public_shape` builds the payload from
scratch out of aggregates; the private fields are never handed to a writer, so
nothing downstream can print what it never saw. The scan is a backstop over the
written tree, not the mechanism.

The scan fails closed, which is the property worth stating twice: an absent or
empty needle list is not a clean scan, it is no scan, and a build that
publishes on that basis has published unscanned.
"""
import datetime as dt
import hashlib
import html as html_mod
import json
import shutil
import subprocess
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

from . import analysis

PUBLIC_BASE = "https://data.adamthede.com/"
RECORD_URL = PUBLIC_BASE + "meant-to-read/"

# The names a public build of this record writes. A directory holding anything
# else is somebody else's, and clearing it would be the destructive kind of
# surprise. `index.html` and `thumb.jpg` are listed because the page lane will
# add them and the guard should not then refuse to rebuild.
RECORD_FILES = ("public_data.json", "PROVENANCE.md", "index.html", "thumb.jpg")

# The floor on a leak needle, the same one `site/public_shape.py` settled. A
# four-character title matches inside an unrelated word on any page and turns
# the scan into a permanent red, which is how a leak scan gets switched off.
MIN_NEEDLE = 12


class LeakTestError(RuntimeError):
    """The scan could not run, or it found something. Nothing is published."""


# ---------------------------------------------------------------------------
# the payload
# ---------------------------------------------------------------------------

def public_shape(records, shortlist_count=None, today=None,
                 read_by_year=None, read_topics=None, read_titles=()):
    """Everything the record may say, built from aggregates and nothing else.

    Assembled field by field rather than filtered down from the enriched rows.
    A denylist over a record shaped like this fails silently the first time a
    field is added; an allowlist fails loudly.

    The page built from this payload is one self-contained file served from a
    host that holds no corpus, so every figure it prints has to be in here.
    That is why the cover aggregates - words, span, stars, the queue-against-
    folders split - are payload fields rather than something the page computes:
    a figure the payload cannot supply is a figure somebody types.

    `read_by_year` and `read_topics` are the paired series this record is built
    around, and they come from the read corpus rather than from these rows. Both
    are optional: a build without them emits None rather than an empty
    container, because an empty container renders as a plate saying he read
    nothing.

    `read_titles` are the READ corpus's article titles. They are not this
    record's leak needles - the needles are this corpus's own - but this record
    publishes read-corpus topic strings, and the index repo's word list is a
    hundred hand-kept strings rather than sixteen thousand titles. So the read
    column is redacted here, at the data layer, where there is something to
    redact it against.
    """
    today = today or dt.date.today()
    titles = corpus_titles(records)
    domains = analysis.by_domain(records)
    aging = analysis.aging_curve(records)
    bands = analysis.abandonment_bands(records)
    topic_rows = [[topic, count] for topic, count
                  in analysis.by_topic(records, limit=None).most_common()
                  if topic.casefold() not in titles][:40]
    # The topic redaction runs against BOTH title sets on both columns. A
    # string is dropped if it is an article title anywhere the record can see,
    # because a topic costs one row of an aggregate and a laundered title costs
    # the record.
    all_titles = titles | {analysis.normalize(t) for t in (read_titles or ())}
    comparison = (analysis.topic_comparison(records, read_topics, titles=all_titles)
                  if read_topics else None)

    return {
        "record": "meant-to-read",
        "built": today.isoformat(),
        "items": len(records),

        # the cover: how big the pool is, and what kind of act made it
        "pool": analysis.pool_split(records),
        "starred": analysis.starred_count(records),
        "words": analysis.word_totals(records),
        "saved_span": analysis.saved_span(records),

        # counts, years, and the shape of the queue
        "by_saved_year": analysis.by_saved_year(records),
        "by_domain": [[domain, count] for domain, count in domains[:60]],
        "distinct_domains": len(domains),
        "domain_concentration": _concentration(domains),
        "by_topic": topic_rows,

        # how much of it still exists, which is the link rot finding
        "survival": analysis.survival(records),
        "dead_fraction_by_year": analysis.dead_fraction_by_year(records),

        # the aging curve, over what was actually judged
        "aging_curve": aging,

        # the abandonment bands: counts and lengths, no titles
        "abandonment_bands": {
            band: {"count": values["count"],
                   "median_words": values["median_words"],
                   "top_topics": drop_title_shaped(values["top_topics"], titles)}
            for band, values in bands.items()},

        # the inference, as counts only, carrying its label. Two populations
        # here, and the difference between them matters: `by_confidence` splits
        # every row by the grade the model set, while `counted` and
        # `excluded_low_confidence` split only the rows that carried a
        # sentence. They disagree by exactly the rows the model graded and then
        # declined to answer - ten of them - and a page that adds a bar from
        # one to a bar from the other is adding two denominators.
        "why_saved": dict(analysis.why_saved_summary(records), kind="inference",
                          by_confidence=analysis.confidence_split(records)),

        # a count, never the list
        "still_worth_your_time": shortlist_count,

        # the paired series, from the read corpus. None, never {}: an empty
        # container renders as a plate reporting that he read nothing.
        "read_comparison": ({"by_year": {str(y): int(n) for y, n
                                         in sorted(read_by_year.items())},
                             "total": int(sum(read_by_year.values())),
                             "sources": list(analysis.READ_IT_LATER_SOURCES),
                             "note": READ_COMPARISON_NOTE}
                            if read_by_year else None),
        "topic_comparison": comparison,

        # the per-day rollup and the 5Ws declaration: what makes the record
        # import-eligible for Tractor and Silo. Aggregates, like everything
        # else here - a day with one save is not an aggregate, so a per-day
        # roster would be more identifying than a title.
        "daily": analysis.daily_rollup(records, built=today.isoformat()),
        "five_ws": five_ws(),

        "caveat": analysis.COMPARISON_CAVEAT,
        "redaction": ("Counts, years, topics, and source hosts only. No titles, "
                      "no URLs, no article text, no people, organisations or "
                      "locations."),
    }


READ_COMPARISON_NOTE = (
    "Read-it-later articles only, counted by the year they were saved. The "
    "legacy document archive - scanned PDFs, Word files and text dumps, the "
    "larger half of the index - was never saved with an intention to read "
    "later, so it is not the comparison. Its denominator also differs from the "
    "topic comparison's on purpose: a row the content guard rejected still "
    "went through the save-then-read loop and counts as a read, it simply "
    "carries no usable topics."
)


def five_ws():
    """What this record holds per item, which halves ship, and why.

    Every record on data.adamthede.com is built import-eligible for Tractor and
    Silo, and this is the declaration a later import reads. It names the Ws
    that do NOT ship as well as the ones that do: a W the declaration is silent
    about is a W somebody assumes is coming, and on this record the who never
    ships at all.
    """
    return {
        "who": {
            "held": True, "published": False, "public_shape": None,
            "note": "People, organisations and locations are extracted per "
                    "item and none of them ships. An entity pulled out of an "
                    "article nobody read still says what he was looking into, "
                    "and the allowlist that would gate them does not exist "
                    "yet. Not even a count: the count is over articles that "
                    "are identifiable from their entities.",
        },
        "what": {
            "held": True, "published": True, "public_shape": "category counts",
            "entity": "one saved article",
            "categories": ["topic", "source host", "abandonment band",
                           "recovery leg"],
            "fields": ["by_topic", "by_domain", "topic_comparison",
                       "abandonment_bands", "survival", "daily"],
            "note": "The entity itself - the title, the address, the text - "
                    "never ships. What ships is which categories it fell into "
                    "and how many fell into each.",
        },
        "when": {
            "held": True, "published": True, "public_shape": "date",
            "precision": "day", "timezone": "UTC",
            "fields": ["saved_span", "by_saved_year", "daily"],
            "note": "The save date as Instapaper reports it: a calendar date "
                    "with no zone on it. UTC is declared so a day boundary has "
                    "one meaning rather than the importer's. There is no read "
                    "timestamp anywhere in this record, because nothing in it "
                    "was read.",
        },
        "where": {
            "held": False, "published": False, "public_shape": None,
            "note": "A saved article has no place. The source host is a "
                    "publisher, not a location, and it is filed under what "
                    "rather than where. No home flag, because there is no "
                    "place to flag.",
        },
        "why": {
            "held": True, "published": True, "kind": "inference",
            "public_shape": "the inferred-reason presence rate",
            "fields": ["why_saved", "daily"],
            "per_day": "daily.days[].raw_data.why_saved_rate",
            "note": "The user-applied annotation this slot wants does not "
                    "exist: he saved these without writing down why. What "
                    "stands in is a model's one-sentence guess, and it is "
                    "labelled inference wherever it appears. The sentences are "
                    "item level and do not ship. What ships is how often it "
                    "answered, how sure it said it was, and the per-day rate.",
        },
        "source": "instapaper",
        "source_id": {
            "held": True, "published": False, "field": "url_sha256",
            "note": "A SHA-256 of the article URL. It is a stable join key and "
                    "it is also a confirmable guess: anyone holding a URL can "
                    "hash it and test whether it is in this queue. That is the "
                    "whole disclosure this record refuses, so the id stays on "
                    "the private side and an importer is handed the aggregates. "
                    "Adversarial review walked exactly this field into the "
                    "per-day rollup on 2026-09-16 and every guard missed it, "
                    "because they all constrained the day's keys and stopped at "
                    "the door of the dict inside. Both key sets are allowlists "
                    "now, and both are asserted.",
        },
        "provenance": {
            "repo": "adamthede/project-instapaper-archive",
            "module": "scripts/unread/public.py",
            "function": "public_shape",
            "corpus": "data/unread_enriched.jsonl",
            "enrichment": "gemini-2.5-flash-lite, whole bodies, one pass",
            "note": "The public shape is derived from the same rows the "
                    "private record is built from, never assembled separately. "
                    "Two builders drift; one builder with an allowlist does not.",
        },
    }


def _concentration(domains):
    total = sum(count for _, count in domains)
    if not total:
        return {}
    top_ten = sum(count for _, count in domains[:10])
    return {"top_1_share": round(domains[0][1] / total, 4),
            "top_10_share": round(top_ten / total, 4),
            "singletons": sum(1 for _, count in domains if count == 1)}


# ---------------------------------------------------------------------------
# the leak scan
# ---------------------------------------------------------------------------

def corpus_titles(records, min_len=MIN_NEEDLE):
    """Every article title in the corpus, normalized, at the needle floor.

    Normalized rather than case-folded. Adversarial review on 2026-09-16 showed
    the exact-match version publishing a title whose only difference from the
    corpus's was a straight apostrophe for a curly one - and the leak scan
    downstream is exact-substring too, so nothing then looked for it. 26 percent
    of this corpus's titles change under ordinary punctuation folding.
    """
    return {analysis.normalize(r.get("title") or "") for r in records
            if len(str(r.get("title") or "").strip()) >= min_len}


def drop_title_shaped(values, titles):
    """Remove any model-generated string that IS an article title.

    This is the redaction that closes the laundering route, and it runs in the
    direction that fails safe. The model reads whole articles now that the body
    cap is off, so it can emit a headline as a "topic" - and a topic ships. The
    first version of this fix dropped the colliding TITLE from the leak
    needles, which is exactly backwards: it published the string and then
    stopped looking for it.

    A dropped topic costs one row of an aggregate. A laundered title costs the
    record.
    """
    return [v for v in values if analysis.normalize(v) not in titles]


def private_needles(records, min_len=MIN_NEEDLE):
    """(titles, URL paths) from this corpus, longest first, deduplicated.

    From this corpus's own rows: the read index does not contain them, so a
    scan borrowed from the Reading record would look for the wrong strings and
    pass.

    Longest first is not cosmetic. When two titles share a prefix the longer is
    the more specific finding, and a scan reporting the shorter one names the
    wrong article in the failure.

    A URL's path, not the whole URL and not the host: the record ranks
    publications by host on purpose, and a host is a fact about the archive
    while a path identifies one article in it.

    EVERY title at the floor is a needle. Nothing is excluded for colliding
    with a published value - a title that also appears as a topic is handled by
    not publishing the topic (`drop_title_shaped`), never by not looking for
    the title.
    """
    titles, paths = set(), set()
    for record in records:
        title = str(record.get("title") or "").strip()
        if len(title) >= min_len:
            titles.add(title)
        url = record.get("url")
        if url:
            path = urlsplit(str(url)).path.rstrip("/")
            if len(path) >= min_len:
                paths.add(path)
    longest = lambda values: sorted(values, key=lambda v: (-len(v), v))  # noqa: E731
    return longest(titles), longest(paths)


def leak_scan(root, titles, paths, min_len=MIN_NEEDLE):
    """Every private string found in every file under `root`.

    Case-insensitive, over both the file's text and its HTML-unescaped text: a
    title carrying an ampersand or a curly quote reaches a page escaped, and a
    scan that only read raw bytes would miss exactly the titles most likely to
    be printed verbatim.
    """
    needles = ([(t, "title") for t in titles if len(t) >= min_len]
               + [(p, "url path") for p in paths if len(p) >= min_len])
    found = []
    for path in sorted(Path(root).rglob("*")):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # a thumbnail, or anything else that is not text
        haystack = (text + "\n" + html_mod.unescape(text)).casefold()
        for needle, kind in needles:
            if needle.casefold() in haystack:
                found.append({"file": path.relative_to(root).as_posix(),
                              "needle": needle, "kind": kind})
    return found


# ---------------------------------------------------------------------------
# the build
# ---------------------------------------------------------------------------

def _guard(out):
    """Refuse an --out this build did not make."""
    if not out.exists():
        return
    strays = [p.name for p in out.iterdir() if p.name not in RECORD_FILES]
    if strays:
        raise SystemExit(
            f"Refusing to overwrite {out}: it holds {', '.join(sorted(strays))}, "
            f"which no public build wrote. Pick another --out.")


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _commit():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True,
                              cwd=str(Path(__file__).resolve().parent)).stdout.strip() or "unknown"
    except OSError:
        return "unknown"


PROVENANCE = """# What I Meant To Read - provenance

Source repository: Article Archive (`scripts/unread/`), commit {commit}.
Built: {built}
Record: {record}

## What this is

The public shape of a private corpus: {items} items from Adam's Instapaper
unread queue and the folder items he organised but never opened. The private
record lives behind Cloudflare Access; this is the data layer of its public
coverage-map entry.

## What was redacted, and where

Redaction happens at the data layer. `public_shape()` assembles the payload
field by field from aggregates; titles, URLs, article text, summaries, the
`why_saved` inference sentences, and the people, organisation and location
columns are never handed to a writer. An allowlist, not a denylist: a denylist
over a payload like this fails silently the first time a field is added.

Published: counts, years, topic distributions, source hosts, the aging curve,
the abandonment bands, the survival figures, the cover aggregates (recovered
words, the span of save dates, the star count, the queue-against-folders split),
the confidence split on the inference, the paired series against the read
corpus, and a per-day rollup of saves.
Not published: anything item level.

Domains ship deliberately (Adam, 2026-09-15). A host is a fact about the
archive; a path identifies one article in it, and paths do not ship.

## The paired series

Two of the six plates draw the unread queue against what was actually read, so
the payload carries the read side as aggregates: counts by saved year, and a
topic table pairing each subject's share of one corpus with its share of the
other. Read-it-later sources only - the legacy document archive was never saved
with an intention to read later.

The topic redaction runs on BOTH columns against BOTH title sets. The read
corpus's topics are model-generated strings out of 17,320 articles and this
record publishes them; one of them colliding with an unread title would print
that title in a column the leak scan is not looking at, because this record's
needles are its own corpus's titles.

## The per-day rollup and the 5Ws

`daily` is a compact series, one row per day saved, in the shape of Silo's
provider daily summary: keyed on `date_of_summary`, carrying `computed_stats`
and declaring its provider, source, timezone and provenance in the payload
rather than in a column. `reads` is null on every day rather than 0, because
every item in this corpus is unread by definition - there is no observation, not
an observation of nothing.

`five_ws` declares what the record holds per item and which halves ship: who
never ships at all, where does not exist for a saved article, what ships as
category counts, when ships as a date at day precision, and why ships as the
presence rate of a model's inference rather than as the inference.

## The scan

After every file was written and before the directory was published, the whole
tree was walked for {titles} article titles and {paths} URL paths from this
corpus, case-blind, over both raw and HTML-unescaped text, at {floor}
characters or more. {findings}

The scan fails closed: an absent or empty needle list raises rather than
passing, because with nothing to look for every tree passes.

## Files

public_data.json  sha256 {digest}
"""


def _collision_note(records):
    """How many model-generated strings were withheld for being article titles."""
    titles = corpus_titles(records)
    topics = set(analysis.by_topic(records, limit=None))
    for band in analysis.abandonment_bands(records).values():
        topics |= set(band["top_topics"])
    dropped = [t for t in topics if analysis.normalize(t) in titles]
    if not dropped:
        return ("No model-generated topic matched an article title, so nothing "
                "was withheld on that ground.")
    return (f"{len(dropped)} model-generated topic(s) were withheld from the "
            f"payload because each IS an article title in this corpus. Every "
            f"title remained a leak needle; the redaction runs on the "
            f"published value, never on the scan.")


def build(records, out_dir, needle_file=None, payload_hook=None,
          shortlist_count=None, today=None, read_by_year=None,
          read_topics=None, read_titles=()):
    """Render, scan, and only then publish.

    The order is the whole design. Everything is written into a sibling
    directory, the scan runs over that directory whole, and a single finding
    raises before anything is moved into place. A build that leaks does not
    publish a record and then complain about it.
    """
    titles, paths = private_needles(records)
    has_titles = any(str(r.get("title") or "").strip() for r in records)
    if has_titles and not titles:
        raise LeakTestError(
            f"{len(records)} record(s) carry titles but none reached "
            f"{MIN_NEEDLE} characters, so nothing would look for a title leak "
            f"- which is the leak this record exists to prevent. Failing "
            f"closed on the titles rather than on the union.")
    if not titles and not paths:
        # An absent list is not an empty one. This is the one branch where a
        # silent pass would be indistinguishable from a clean corpus.
        raise LeakTestError(
            f"No leak needles could be derived from {len(records)} record(s): "
            f"no title or URL path reached {MIN_NEEDLE} characters. This check "
            f"fails closed, because with nothing to look for every tree passes. "
            f"Refusing to publish.")

    out = Path(out_dir).resolve()
    _guard(out)

    tmp = out.parent / (out.name + ".building")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)

    if needle_file is not None:
        needle_path = Path(needle_file).resolve()
        if needle_path.is_relative_to(tmp) or needle_path.is_relative_to(out):
            shutil.rmtree(tmp, ignore_errors=True)
            raise LeakTestError(
                f"The needle file {needle_path} would be written inside the "
                f"published record. It holds every private string in the "
                f"corpus; it belongs beside the build, never in it.")

    try:
        payload = public_shape(records, shortlist_count=shortlist_count,
                               today=today, read_by_year=read_by_year,
                               read_topics=read_topics, read_titles=read_titles)
        if payload_hook:
            payload = payload_hook(payload)
        data_path = tmp / "public_data.json"
        data_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True),
                             encoding="utf-8")

        (tmp / "PROVENANCE.md").write_text(PROVENANCE.format(
            commit=_commit(), built=(today or dt.date.today()).isoformat(),
            record=RECORD_URL, items=f"{len(records):,}",
            titles=f"{len(titles):,}", paths=f"{len(paths):,}", floor=MIN_NEEDLE,
            findings="It found nothing.",
            collisions=_collision_note(records),
            digest=sha256(data_path)),
            encoding="utf-8")

        found = leak_scan(tmp, titles, paths)
        if found:
            lines = "\n".join(f"  {f['file']}: {f['kind']} {f['needle']!r}"
                              for f in found[:20])
            raise LeakTestError(
                f"Refusing to publish: the leak scan found {len(found)} private "
                f"string(s) in the public build.\n{lines}")

        if out.exists():
            shutil.rmtree(out)
        tmp.replace(out)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)

    if needle_file is not None:
        write_needle_file(needle_file, titles, paths)

    return {"out": out, "items": len(records), "needles": len(titles) + len(paths),
            "titles": len(titles), "paths": len(paths), "leak_findings": 0}


NEEDLE_HEADER = """# Leak needles from the unread corpus, for data.adamthede.com.
#
# data.adamthede.com's own leak test reads `data/private_strings.txt`, which is
# gitignored and hand-assembled, and Adam refreshes the PRIVATE_STRINGS_B64
# secret from it by hand. This file is that corpus's contribution, emitted by
# the build so the step is a copy rather than a transcription.
#
# This file is NOT publishable. It is every private string in the corpus, which
# is exactly what a needle list has to be.
#
# {titles} titles, {paths} URL paths, longest first, minimum {floor} characters.
"""


def write_needle_file(path, titles, paths):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = NEEDLE_HEADER.format(titles=len(titles), paths=len(paths), floor=MIN_NEEDLE)
    body += "\n".join(list(titles) + list(paths)) + "\n"
    path.write_text(body, encoding="utf-8")
    return path
