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

def public_shape(records, shortlist_count=None, today=None):
    """Everything the record may say, built from aggregates and nothing else.

    Assembled field by field rather than filtered down from the enriched rows.
    A denylist over a record shaped like this fails silently the first time a
    field is added; an allowlist fails loudly.
    """
    today = today or dt.date.today()
    domains = analysis.by_domain(records)
    aging = analysis.aging_curve(records)
    bands = analysis.abandonment_bands(records)

    return {
        "record": "meant-to-read",
        "built": today.isoformat(),
        "items": len(records),

        # counts, years, and the shape of the queue
        "by_saved_year": analysis.by_saved_year(records),
        "by_domain": [[domain, count] for domain, count in domains[:60]],
        "distinct_domains": len(domains),
        "domain_concentration": _concentration(domains),
        "by_topic": [[topic, count]
                     for topic, count in analysis.by_topic(records, limit=40).most_common(40)],

        # how much of it still exists, which is the link rot finding
        "survival": analysis.survival(records),
        "dead_fraction_by_year": analysis.dead_fraction_by_year(records),

        # the aging curve, over what was actually judged
        "aging_curve": aging,

        # the abandonment bands: counts and lengths, no titles
        "abandonment_bands": {
            band: {"count": values["count"],
                   "median_words": values["median_words"],
                   "top_topics": values["top_topics"]}
            for band, values in bands.items()},

        # the inference, as counts only, carrying its label
        "why_saved": dict(analysis.why_saved_summary(records), kind="inference"),

        # a count, never the list
        "still_worth_your_time": shortlist_count,

        "caveat": analysis.COMPARISON_CAVEAT,
        "redaction": ("Counts, years, topics, and source hosts only. No titles, "
                      "no URLs, no article text, no people, organisations or "
                      "locations."),
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

def publishable_strings(records):
    """Every string the record publishes verbatim: topics, concepts, hosts.

    A needle identical to one of these cannot distinguish a leaked title from a
    published topic, because the published topic is there on every build.
    """
    out = set()
    for record in records:
        for field in ("ai_topics", "ai_concepts"):
            for value in (record.get(field) or []):
                text = str(value).strip()
                if text:
                    out.add(text.casefold())
        host = str(record.get("domain") or "").strip()
        if host:
            out.add(host.casefold())
    return out


def publishable_collisions(records, min_len=MIN_NEEDLE):
    """Titles dropped as needles because the record publishes that exact string.

    Reported rather than silently discarded: an exclusion nobody can see is
    indistinguishable from a scan that was quietly narrowed.
    """
    publishable = publishable_strings(records)
    return {str(r.get("title") or "").strip() for r in records
            if len(str(r.get("title") or "").strip()) >= min_len
            and str(r.get("title") or "").strip().casefold() in publishable}


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

    A title that IS a string the record publishes - a topic, a concept, a host
    - is not a needle. Found on the live corpus: one article is titled exactly
    "Productivity", which is also an extracted topic, and topics ship. Such a
    needle matches on every build and can never tell a leak from the topic.
    `publishable_collisions` reports what was dropped this way.
    """
    publishable = publishable_strings(records)
    titles, paths = set(), set()
    for record in records:
        title = str(record.get("title") or "").strip()
        if len(title) >= min_len:
            # The WHOLE title must equal a publishable string, not merely
            # contain one: "Productivity" goes, "Productivity And The Modern
            # Office" stays.
            if title.casefold() not in publishable:
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
the abandonment bands, and the survival figures.
Not published: anything item level.

Domains ship deliberately (Adam, 2026-09-15). A host is a fact about the
archive; a path identifies one article in it, and paths do not ship.

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
    dropped = sorted(publishable_collisions(records))
    if not dropped:
        return "No title collided with a string the record publishes."
    return (f"{len(dropped)} title(s) were dropped as needles because the "
            f"record publishes that exact string as a topic, concept or host, "
            f"so they could never distinguish a leak from the published value. "
            f"They are listed in the needle file rather than here.")


def build(records, out_dir, needle_file=None, payload_hook=None,
          shortlist_count=None, today=None):
    """Render, scan, and only then publish.

    The order is the whole design. Everything is written into a sibling
    directory, the scan runs over that directory whole, and a single finding
    raises before anything is moved into place. A build that leaks does not
    publish a record and then complain about it.
    """
    titles, paths = private_needles(records)
    if not titles and not paths:
        # An absent list is not an empty one. This is the one branch where a
        # silent pass would be indistinguishable from a clean corpus.
        raise LeakTestError(
            f"No leak needles could be derived from {len(records)} record(s): "
            f"no title or URL path reached {MIN_NEEDLE} characters, or every "
            f"one collided with a string the record publishes "
            f"({len(publishable_collisions(records))} dropped that way). This "
            f"check fails closed, because with nothing to look for every tree "
            f"passes. Refusing to publish.")

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
        payload = public_shape(records, shortlist_count=shortlist_count, today=today)
        if payload_hook:
            payload = payload_hook(payload)
        data_path = tmp / "public_data.json"
        data_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True),
                             encoding="utf-8")

        (tmp / "PROVENANCE.md").write_text(PROVENANCE.format(
            commit=_commit(), built=(today or dt.date.today()).isoformat(),
            record=RECORD_URL, items=f"{len(records):,}",
            titles=f"{len(titles):,}", paths=f"{len(paths):,}", floor=MIN_NEEDLE,
            findings="It found nothing.", collisions=_collision_note(records),
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
        write_needle_file(needle_file, titles, paths,
                          publishable_collisions(records))

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


def write_needle_file(path, titles, paths, collisions=()):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = NEEDLE_HEADER.format(titles=len(titles), paths=len(paths), floor=MIN_NEEDLE)
    body += "\n".join(list(titles) + list(paths)) + "\n"
    if collisions:
        body += ("\n# Dropped as needles: the record publishes these exact\n"
                 "# strings as a topic, concept or host, so they can never\n"
                 "# distinguish a leak from the published value.\n")
        body += "".join(f"# {c}\n" for c in sorted(collisions))
    path.write_text(body, encoding="utf-8")
    return path
