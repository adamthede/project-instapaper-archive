#!/usr/bin/env python3
"""Fabricated entity clusters - the archive's one measured extraction failure.

The 2026-08-12 audit named it: 290 Co.Design articles scraped Fast Company's
navigation furniture instead of the article body, and the enrichment pass then
ran entity extraction over the furniture. Every one of those rows carries the
SAME people list - Josh Earnest, Antonia Iamartino, Deb Haaland, Todd Sherman,
Todd Kaplan, Jony Ive - which is why two Fast Company staffers rank in the top
15 people of a 17,000-article reading archive. Those articles are not about
those people. Nobody read them.

The rule here is generic rather than a hardcoded blocklist, because the
signature generalises and the names do not: a set of two or more people
extracted *identically* across many articles that also share one source domain
and one exact word count is site chrome, not a subject. Real articles about
Steve Jobs do not agree on their word count to the character.

Measured on the 2026-08-20 index (17,416 rows), the threshold has a wide flat
band - every value of `min_cluster` from 6 to 50 catches the same 2 groups and
the same 283 rows:

    n>=2:   24 groups,  335 rows
    n>=3:    6 groups,  299 rows   (adds a 3-row news.yahoo.com digest)
    n>=4:    5 groups,  296 rows   (adds genuine two-author bylines: National
    n>=5:    3 groups,  288 rows    Geographic, Adventure Cycling; then pando)
    n>=6:    2 groups,  283 rows   <- the Co.Design cluster, and nothing else
    n>=50:   2 groups,  283 rows
    n>=100:  1 group,   229 rows   (band ends: the 54-row variant escapes)

MIN_CLUSTER sits at 8 - inside the flat band, with headroom on both sides.
Below 5 the rule starts eating genuine two-author bylines, which is the failure
mode worth avoiding: a false positive here erases a real person from the
archive's memory of itself.

QUARANTINE, NOT DELETION. The scrubbed cast moves to a second column instead of
being dropped. That is not tidiness; it fixes a bug. The site's /people/ page
tells the story of this defect, and it was reading that story out of whatever
the CURRENT run happened to find - so the first time an already-scrubbed index
reached the page, the page would have announced that no fabrication was ever
found. Evidence that vanishes once it is acted on is not evidence. Second
reason, plainer: a later reader should be able to check this call rather than
take it on trust.

Known limit - RECALL IS FRAGILE TO CAST DRIFT. Grouping is exact-set, so one
extra name splits a cluster. fastcodesign.com/642 is really three groups
(229 / 54 / 2); the 2-row tail escapes, and a site that reshuffled its nav
forty times would yield forty sub-threshold groups and be caught by none of
them. Precision was the thing worth buying here - a false positive erases a
real person - but if a driftier cluster ever shows up, the fix is to group on
host + word count first and then require high overlap between casts rather
than equality.

Scope note: this scrubs `people`, the defect the audit measured. The same
furniture populated `orgs`, `locations` and `concepts` on the same rows, and
those columns are NOT scrubbed here - that is a wider call than the one Adam
settled, and 283 rows is 1.6% of the index. It is recorded as an open item
rather than acted on quietly.
"""
from collections import defaultdict
from urllib.parse import urlparse

# A cluster must be at least this many articles before it reads as furniture.
MIN_CLUSTER = 8
# A single recurring name is a person someone writes about ("Steve Jobs" on 67
# rows across 14 hosts). Only an identical MULTI-name set is a fingerprint.
MIN_NAMES = 2

# The column a scrubbed cast is parked in. Defined HERE, once, and imported by
# everything that touches it. Round-2 review found three independent spellings
# of this string - build_index wrote one, this module defaulted to another,
# site/corpus.py read a third - and renaming any single one of them reproduced
# the evidence-vanishing bug with the whole suite still green. A constant only
# helps if every end of the seam uses it.
PEOPLE_QUARANTINE = "people_boilerplate"


def source_host(url):
    """The registrable-ish host, www stripped. Empty for the legacy corpus,
    which carries no URLs at all - those rows can never form a cluster here."""
    if url is None:
        return ""
    try:
        text = str(url)
    except Exception:
        return ""
    if not text or text.lower() in ("nan", "none"):
        return ""
    host = urlparse(text).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def as_names(value):
    """Entity cells arrive as lists, numpy arrays, strings, None, or NaN.

    A bare string is ONE name, not a list of characters. build_index reads this
    column straight out of YAML frontmatter, where `ai_people: Steve Jobs` is a
    string, and list("Steve Jobs") would quietly enter ten single letters into
    the vocabulary and fingerprint the row against every other one-name string.
    """
    if value is None:
        return []
    if isinstance(value, float):
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    try:
        return [str(x).strip() for x in list(value) if str(x).strip()]
    except TypeError:
        return []


def fingerprint(value):
    """The order-insensitive, duplicate-insensitive identity of an entity cell."""
    return tuple(sorted(set(as_names(value))))


def find_clusters(records, min_cluster=MIN_CLUSTER, min_names=MIN_NAMES):
    """Boilerplate clusters in `records`, largest first.

    `records` is an iterable of (row_id, entity_value, host, word_count). The
    grouping key is all three of fingerprint, host and word count - dropping
    word count from the key would merge a publication's genuine recurring
    contributor list into the same bucket as its chrome.
    """
    buckets = defaultdict(list)
    for row_id, value, host, word_count in records:
        names = fingerprint(value)
        if len(names) < min_names or not host:
            continue
        try:
            wc = int(word_count)
        except (TypeError, ValueError):
            continue
        buckets[(names, host, wc)].append(row_id)

    clusters = [
        {"names": names, "host": host, "word_count": wc, "row_ids": ids}
        for (names, host, wc), ids in buckets.items()
        if len(ids) >= min_cluster
    ]
    clusters.sort(key=lambda c: (-len(c["row_ids"]), c["host"], c["names"]))
    return clusters


def scrub(df, column="people", hosts=None, min_cluster=MIN_CLUSTER,
          min_names=MIN_NAMES, log=None, quarantine_column=None):
    """Blank `column` on every boilerplate-cluster row. Returns (df, clusters).

    With `quarantine_column`, the removed cast is written there rather than
    lost, so the exclusion stays auditable after the index has been rebuilt -
    see the module docstring.

    Never silent: every cluster is reported through `log` (default: print) with
    its names, host, word count and row count, so a future cluster appearing in
    the archive announces itself in the build output instead of quietly
    reshaping a ranking.

    ORDERING MATTERS AT THE CALL SITE. Detection needs the whole population.
    In site/corpus.py the corrupted-row filter removes 288 of these 290 rows,
    so scrubbing *after* that filter would leave a 2-row cluster - under any
    sane threshold, undetected, and the two survivors are exactly the rows that
    leak the fabricated names into a ranked page.
    """
    emit = print if log is None else log
    if column not in df.columns:
        emit(f"entity hygiene: no {column!r} column, nothing to scrub")
        return df, []

    if hosts is None:
        hosts = [source_host(u) for u in df["url"]] if "url" in df.columns \
            else [""] * len(df)
    hosts = list(hosts)
    if len(hosts) != len(df):
        raise ValueError(
            f"hosts has {len(hosts)} entries for {len(df)} rows - a misaligned "
            f"host list would scrub the wrong articles")

    words = df["word_count"] if "word_count" in df.columns else [0] * len(df)
    records = zip(df.index, df[column], hosts, words)
    clusters = find_clusters(records, min_cluster=min_cluster, min_names=min_names)
    if not clusters:
        if quarantine_column and quarantine_column not in df.columns:
            # Materialise it even with nothing to park. A column that only
            # appears on indexes that happened to contain a defect is a schema
            # that changes shape with its contents, and it leaves the seam
            # between the writer and the reader untestable on a clean corpus.
            df = df.copy()
            df[quarantine_column] = [[] for _ in range(len(df))]
        return df, []

    victims = [rid for c in clusters for rid in c["row_ids"]]
    total = sum(len(c["row_ids"]) for c in clusters)
    emit(f"entity hygiene: quarantining {column!r} on {total} rows in "
         f"{len(clusters)} boilerplate cluster(s) "
         f"(identical {column} + host + word count, >= {min_cluster} rows):")
    for c in clusters:
        emit(f"  {len(c['row_ids']):5d} rows  {c['host']}  "
             f"word_count={c['word_count']}  {', '.join(c['names'])}")

    df = df.copy()
    # A column of lists cannot be assigned an empty list per row through .loc
    # without pandas trying to broadcast it, so rebuild both columns outright.
    dropped = set(victims)
    original = list(df[column])
    df[column] = [[] if idx in dropped else val
                  for idx, val in zip(df.index, original)]
    if quarantine_column:
        prior = list(df[quarantine_column]) if quarantine_column in df.columns \
            else [[] for _ in range(len(df))]
        # Rows this pass caught take the cast it just removed; every other row
        # KEEPS whatever an earlier pass parked there. That second half is the
        # point - on a second run over an already-cleaned index there is
        # nothing left to detect, and the earlier record is the only surviving
        # proof the defect existed. (A victim row cannot also hold prior
        # quarantine: its `people` was blanked last time, so it forms no
        # cluster this time.)
        df[quarantine_column] = [
            list(val) if idx in dropped else (as_names(was) or [])
            for idx, val, was in zip(df.index, original, prior)]
    return df, clusters


def quarantined_clusters(df, quarantine_column=PEOPLE_QUARANTINE, hosts=None):
    """Rebuild the cluster report from a quarantine column.

    The clusters a given run detects are not the clusters an index CONTAINS:
    once build_index has written a cleaned parquet, a later pass finds nothing
    to do and would report an archive with no defects in it. Reading the record
    back gives the same answer whoever did the work and whenever.
    """
    if quarantine_column not in df.columns:
        return []
    if hosts is None:
        hosts = [source_host(u) for u in df["url"]] if "url" in df.columns \
            else [""] * len(df)
    words = df["word_count"] if "word_count" in df.columns else [0] * len(df)
    records = [(idx, val, host, wc)
               for idx, val, host, wc in zip(df.index, df[quarantine_column],
                                             hosts, words)
               if as_names(val)]
    # min_cluster=1: these rows were already judged. Re-thresholding them would
    # hide a cluster that a bigger corpus once justified.
    return find_clusters(records, min_cluster=1, min_names=1)
