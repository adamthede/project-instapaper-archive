"""The unread corpus on the private site: /unread/, /unread/versus/, /unread/worth/.

Step 6 of the study (docs/plans-done/2026-09-15-unread-corpus-what-i-meant-to-read.md,
build order row 6): the private comparison pages. Three pages in the week-page
idiom, all of them behind the same Cloudflare Access wall as the rest of the
site, because two of them carry titles and URLs.

    /unread/          the queue as a living view: the 492-item Instapaper pool
                      and the Matter queue from the nightly snapshot ledger
    /unread/versus/   the read corpus and the unread pool on the same axes
    /unread/worth/    the "still worth your time" shortlist, decision 3

Everything here reads stored output. The enrichment ran once (PR #28, $0.23
across 492 items) and nothing in this module calls a model. The aggregates
are the study's own functions in `scripts/unread/analysis.py`, imported rather
than re-derived, so a page and the analysis report cannot disagree.

Two privacy rules are enforced here rather than trusted to the templates:

- **The only model sentence that reaches a page is `why_saved`.** It is the one
  line of reasoning the plan allows, it is labelled as inference with its
  confidence grade wherever it is shown, and it is flattened to one line.
  `ai_summary` is the field most likely to quote an article body and no
  renderer here reads it. `load()` drops it on the way in, so a future
  template cannot reach for it.
- **No item-level Matter data beyond counts.** The snapshot ledger holds ids,
  word counts, progress and a site name, and never a title or a URL. The
  living view reports sites and lengths as counts and nothing else.
"""
import csv
import datetime as dt
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from unread import analysis, derive  # noqa: E402

import htmlkit  # noqa: E402
from htmlkit import e, n, page, safe_url  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
DEFAULT_ENRICHED = REPO / "data" / "unread_enriched.jsonl"
DEFAULT_DAILY_CSV = REPO / "data" / "matter" / "queue_daily.csv"
DEFAULT_LEDGER = REPO / "data" / "matter" / "queue_snapshots.jsonl"

#: The fields a page is ever allowed to see. An allowlist, applied at load: a
#: field that is not here does not exist as far as the renderers can tell.
#: `ai_summary` is the one that matters by its absence.
RECORD_FIELDS = frozenset({
    "url_sha256", "title", "url", "domain", "saved_date", "saved_year",
    "folder", "starred", "read_progress", "abandonment", "resolve_path",
    "body_words", "content_corrupted", "aged_out", "ai_topics",
    "why_saved", "why_saved_confidence", "why_saved_kind",
})

#: The study's length bands (plan, "By length"), upper bounds inclusive.
LENGTH_BANDS = (
    ("1 to 500", 1, 500), ("501 to 1,000", 501, 1000),
    ("1,001 to 2,000", 1001, 2000), ("2,001 to 5,000", 2001, 5000),
    ("5,001 to 10,000", 5001, 10000), ("Over 10,000", 10001, None),
)

#: The study's hypothesis band (plan, "The first hypothesis").
HYPOTHESIS_YEARS = (2017, 2018, 2019)

#: The ledger's own source tag. The May 2023 seed row, if it is ever written,
#: is a different definition of "queue" measured once, and it is not a week.
LEDGER_SOURCE = "matter-api"

#: A week with fewer observed days than this is drawn but does not count
#: toward the drain projection. The ledger's first week started on a Thursday.
MIN_DAYS_FOR_A_WEEK = 5

#: How many measured weeks the burn-down projection needs before it will name
#: a date. With fewer, the page says how many it has.
MIN_PROJECTION_WEEKS = 4

#: How far back the reads-per-week context runs.
READS_CONTEXT_WEEKS = 12

#: The shortlist's page size. The analysis script's own default, so the page
#: and `data/unread_analysis.json` rank the same 25.
SHORTLIST_SIZE = 25

#: A source must carry at least this many unread items to be ranked as one he
#: saved and did not read. A single unread item from a host is not a pattern.
MIN_SOURCE_ITEMS = 3

#: The longest a `why_saved` line may run on the page. It is one sentence by
#: the prompt's own rule; this is the fence in case a row is not.
MAX_INFERENCE_CHARS = 280


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------

def load_records(path):
    """The enriched corpus, reduced to RECORD_FIELDS. [] when absent."""
    path = Path(path)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            out.append({k: v for k, v in row.items() if k in RECORD_FIELDS})
    return out


def load_daily(path):
    """queue_daily.csv as typed rows, oldest first. [] when absent."""
    path = Path(path) if path else None
    if not path or not path.exists():
        return []

    def num(v):
        return int(v) if str(v or "").strip() else None

    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("source") != LEDGER_SOURCE:
                continue
            rows.append({
                "date": dt.date.fromisoformat(r["date"]),
                "count": num(r.get("count")),
                "total_words": num(r.get("total_words")),
                "started": num(r.get("started_not_finished_count")),
                "inflow": num(r.get("new_ids_since_previous")),
                "outflow": num(r.get("gone_ids_since_previous")),
                "days_since_previous": num(r.get("days_since_previous")),
            })
    rows.sort(key=lambda r: r["date"])
    return rows


#: How much of the ledger is read per step, walking back from the end. Real
#: lines run 66 to 68 KB (one full item listing a night), so a line routinely
#: spans more than one block and the reader must never assume it does not.
LEDGER_BLOCK = 65536


def load_latest_snapshot(path, block=None):
    """The newest complete, parseable line of the ledger, or None.

    Read from the end, because the ledger gains a full item listing every
    night and only the newest line is ever drawn. Two rules, both from review
    of the first version:

    - A line is trusted only once its start has been read: a newline precedes
      it in the buffer, or the read has reached the start of the file. The
      first version stopped at "two non-empty fragments", which on the real
      ledger is a stop in the middle of a 67 KB line.
    - A torn last line, a nightly append killed mid-write, is skipped and the
      previous night's snapshot is drawn instead. A ledger torn all the way
      down returns None, which costs the Matter section its item detail and
      nothing else.
    """
    path = Path(path) if path else None
    if not path or not path.exists() or path.stat().st_size == 0:
        return None
    block = block or LEDGER_BLOCK
    with open(path, "rb") as f:
        f.seek(0, 2)
        pos = f.tell()
        buf = b""
        while True:
            pieces = buf.split(b"\n")
            # pieces[0] is the tail of a line whose start is not yet read,
            # unless the read has reached the start of the file.
            complete = pieces if pos == 0 else pieces[1:]
            for line in reversed(complete):
                if not line.strip():
                    continue
                try:
                    return json.loads(line)
                except ValueError:
                    continue  # torn: keep walking back
            if pos == 0:
                return None
            step = min(block, pos)
            pos -= step
            f.seek(pos)
            buf = f.read(step) + buf


READ_COLUMNS = ["source", "date_saved", "date_archived", "topics",
                "content_corrupted", "url"]


def load_read_frame(index_path):
    """The read corpus, only the columns the comparison needs, or None."""
    if not index_path or not Path(index_path).exists():
        return None
    import pandas as pd
    return pd.read_parquet(index_path, columns=READ_COLUMNS)


# ---------------------------------------------------------------------------
# the aggregates
# ---------------------------------------------------------------------------

def length_bands(words):
    """Counts per study band, plus how many carried no length at all."""
    counts = {label: 0 for label, _, _ in LENGTH_BANDS}
    empty = 0
    for w in words:
        w = int(w or 0)
        if w <= 0:
            empty += 1
            continue
        for label, lo, hi in LENGTH_BANDS:
            if w >= lo and (hi is None or w <= hi):
                counts[label] += 1
                break
    return {"bands": [(label, counts[label]) for label, _, _ in LENGTH_BANDS],
            "no_length": empty}


def iso_week(day):
    y, w, _ = day.isocalendar()
    return f"{y}-W{w:02d}"


def matter_reads_by_week(frame):
    """Matter archive events by ISO week: the reads the queue drains into.

    Matter rows only, dated only by `date_archived`, the same rule
    `corpus.derive_date_read` applies. Reads from every other source are
    history the Matter queue never held.
    """
    if frame is None or not len(frame):
        return {}
    import pandas as pd
    rows = frame[frame["source"] == "matter"]
    dates = pd.to_datetime(rows["date_archived"], errors="coerce")
    return dict(Counter(iso_week(d.date()) for d in dates if not pd.isna(d)))


def ledger_weeks(daily):
    """The daily ledger rolled up into ISO weeks: inflow, outflow, net.

    `net` is inflow minus outflow, so a positive week is a week the queue grew.
    A day whose flows are None (the ledger's first night, which had nothing to
    diff against) contributes no flow rather than a zero.
    """
    grouped = defaultdict(list)
    for row in daily:
        grouped[iso_week(row["date"])].append(row)
    out = []
    for week in sorted(grouped):
        rows = grouped[week]
        flows = [r for r in rows if r["inflow"] is not None and r["outflow"] is not None]
        inflow = sum(r["inflow"] for r in flows) if flows else None
        outflow = sum(r["outflow"] for r in flows) if flows else None
        out.append({
            "week": week, "days": len(rows), "flow_days": len(flows),
            "inflow": inflow, "outflow": outflow,
            "net": (inflow - outflow) if flows else None,
            "end_count": rows[-1]["count"],
            "measured": len(flows) >= MIN_DAYS_FOR_A_WEEK,
        })
    return out


def projection(weeks, queue_size):
    """The burn-down, with its assumption stated.

    The median weekly net over measured weeks. A median at or above zero means
    the queue is not draining, and the honest projection is never. Too few
    weeks and there is no projection at all, only a count of what is missing.
    """
    measured = [w["net"] for w in weeks if w["measured"] and w["net"] is not None]
    if len(measured) < MIN_PROJECTION_WEEKS:
        return {"state": "too_early", "measured": len(measured),
                "needed": MIN_PROJECTION_WEEKS}
    median_net = statistics.median(measured)
    if median_net >= 0 or not queue_size:
        return {"state": "never", "measured": len(measured), "median_net": median_net}
    weeks_left = queue_size / -median_net
    return {"state": "draining", "measured": len(measured), "median_net": median_net,
            "weeks": round(weeks_left, 1)}


#: How far back the queue-size delta on the cover reaches, in days.
DELTA_DAYS = 7


def change_over(daily, days=DELTA_DAYS):
    """(delta, days actually spanned) for the queue size, or (None, None).

    Measured against the newest row at least `days` before the last one, by
    DATE, not by row. The first version took the row eight back and called it
    seven days, which is true only when no night was missed. When the exact
    day is missing the comparison reaches one row further back and the page
    says how many days it actually spans.
    """
    if not daily:
        return None, None
    last = daily[-1]
    cutoff = last["date"] - dt.timedelta(days=days)
    earlier = [r for r in daily if r["date"] <= cutoff and r["count"] is not None]
    if not earlier or last["count"] is None:
        return None, None
    base = earlier[-1]
    return last["count"] - base["count"], (last["date"] - base["date"]).days


def matter_view(daily, snapshot, reads_by_week, today=None):
    """Everything the living view draws about the Matter queue, or None."""
    if not daily and not snapshot:
        return None
    items = (snapshot or {}).get("items") or []
    latest = daily[-1] if daily else None
    weeks = ledger_weeks(daily)
    for w in weeks:
        w["reads"] = reads_by_week.get(w["week"], 0) if reads_by_week else None

    anchor = (latest["date"] if latest else today) or dt.date.today()
    context = []
    for i in range(READS_CONTEXT_WEEKS - 1, -1, -1):
        wk = iso_week(anchor - dt.timedelta(weeks=i))
        context.append((wk, reads_by_week.get(wk, 0) if reads_by_week else 0))

    sites = Counter(str(it.get("site") or "").strip() for it in items)
    sites.pop("", None)
    progress = [float(it.get("reading_progress") or 0.0) for it in items]
    size = latest["count"] if latest else len(items)
    return {
        "as_of": latest["date"] if latest else None,
        "size": size,
        "words": latest["total_words"] if latest else sum(int(it.get("word_count") or 0) for it in items),
        "started": latest["started"] if latest else sum(1 for p in progress if 0 < p < 1),
        "never_opened": sum(1 for p in progress if p <= 0),
        "finished_in_queue": sum(1 for p in progress if p >= 1),
        "items_in_snapshot": len(items),
        "days": daily,
        "weeks": weeks,
        "first_day": daily[0]["date"] if daily else None,
        "change": change_over(daily),
        "reads_context": context,
        "reads_median": statistics.median([c for _, c in context]) if context else None,
        "sites": sorted(sites.items(), key=lambda kv: (-kv[1], kv[0]))[:12],
        "distinct_sites": len(sites),
        "lengths": length_bands(it.get("word_count") for it in items),
        "median_words": (int(statistics.median([int(it["word_count"]) for it in items
                                                if it.get("word_count")]))
                         if any(it.get("word_count") for it in items) else None),
        "projection": projection(weeks, size),
    }


def pool_view(records):
    """The Instapaper pool: the study's own aggregates, by year, source, length."""
    domains = analysis.by_domain(records)
    dead = analysis.dead_fraction_by_year(records)
    return {
        "items": len(records),
        "by_year": analysis.by_saved_year(records),
        "domains": domains[:20],
        "distinct_domains": len(domains),
        "top_two_share": (sum(c for _, c in domains[:2]) / len(records)) if records else 0,
        "lengths": length_bands(r.get("body_words") for r in records),
        "words": analysis.word_totals(records),
        "dead_by_year": dead,
        "dead": sum(v["dead"] for v in dead.values()),
        "survival": analysis.survival(records),
        "split": analysis.pool_split(records),
        "span": analysis.saved_span(records),
        "starred": analysis.starred_count(records),
        "bands": Counter(r.get("abandonment") or derive.NEVER_OPENED for r in records),
    }


def read_windows(read_by_year, width=3, current_year=None):
    """Read-it-later saves per `width`-year window, lowest first.

    Complete years only: a window that includes the current year is left out,
    because a year still in progress would rank low for no reason but the
    calendar. Each entry is (first year, last year, articles).
    """
    current_year = current_year or dt.date.today().year
    years = [int(y) for y in read_by_year]
    if not years:
        return []
    out = []
    for start in range(min(years), max(years) - width + 2):
        end = start + width - 1
        if end >= current_year:
            continue
        out.append((start, end, sum(read_by_year.get(y, 0) for y in range(start, end + 1))))
    return sorted(out, key=lambda w: (w[2], w[0]))


def hypothesis_window_note(read_by_year, current_year=None):
    """One sentence on where 2017-19 sits among the read corpus's windows.

    Replaces a typed claim that the unread queue peaks where reading volume
    was lowest. Adversarial review computed the windows and 2017-19 was the
    fourth lowest, so the page now says what the data says, whatever it says.
    """
    windows = read_windows(read_by_year, len(HYPOTHESIS_YEARS), current_year)
    lo, hi = HYPOTHESIS_YEARS[0], HYPOTHESIS_YEARS[-1]
    rank = next((i for i, w in enumerate(windows, 1) if w[0] == lo and w[1] == hi), None)
    if rank is None:
        return ""
    band_n = windows[rank - 1][2]
    if rank == 1:
        return (f"Read-it-later saves in {lo}-{str(hi)[2:]} were {n(band_n)}, the lowest of the "
                f"{len(windows)} complete three-year windows.")
    low = windows[0]
    return (f"Read-it-later saves in {lo}-{str(hi)[2:]} were {n(band_n)}, the "
            f"{_ordinal(rank)} lowest of the {len(windows)} complete three-year windows. The "
            f"lowest is {low[0]}-{str(low[1])[2:]} at {n(low[2])}.")


def _ordinal(k):
    return {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth",
            7: "seventh", 8: "eighth", 9: "ninth", 10: "tenth"}.get(k, f"{k}th")


def _top(counter, k):
    return sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:k]


def versus_view(records, frame):
    """The read corpus and the unread pool, paired. None without the index."""
    if frame is None:
        return None
    read_topics = analysis.read_corpus_topics(frame)
    read_by_year = analysis.read_corpus_by_year(frame)
    unread_by_year = analysis.by_saved_year(records)
    drift = analysis.drift_by_year(records, read_topics)

    band_read = Counter()
    for y in HYPOTHESIS_YEARS:
        band_read.update(read_topics.get(y, {}))
    band_unread = Counter()
    for r in records:
        if r.get("saved_year") in HYPOTHESIS_YEARS and not r.get("content_corrupted"):
            band_unread.update(str(t).strip() for t in (r.get("ai_topics") or []) if str(t).strip())

    sources = [row for row in analysis.sources_saved_not_read(records, frame, limit=None)
               if row["unread"] >= MIN_SOURCE_ITEMS]
    return {
        "read_total": sum(read_by_year.values()),
        "unread_total": len(records),
        "read_by_year": read_by_year,
        "unread_by_year": unread_by_year,
        "band_read": sum(read_by_year.get(y, 0) for y in HYPOTHESIS_YEARS),
        "band_unread": sum(unread_by_year.get(y, 0) for y in HYPOTHESIS_YEARS),
        "band_read_topics": _top(band_read, 6),
        "band_unread_topics": _top(band_unread, 6),
        "topics": analysis.topic_comparison(records, read_topics, limit=10, read_extra=2),
        "drift": drift,
        "peak_drift": analysis.peak_drift_year(drift),
        "aging": analysis.aging_curve(records),
        "abandonment": analysis.abandonment_bands(records),
        "sources": sources[:15],
        "why": analysis.why_saved_summary(records),
        "read_topics": read_topics,
    }


def now_weights(read_topics):
    """What he reads now, weighted to recent years: analysis.build()'s rule."""
    now = Counter()
    for year, counts in (read_topics or {}).items():
        weight = 3 if int(year) >= 2023 else 1
        for topic, count in counts.items():
            now[topic] += count * weight
    return now


def one_line(text, limit=MAX_INFERENCE_CHARS):
    """A model sentence flattened to one line and fenced to a length."""
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def worth_view(records, read_topics):
    """The ranked shortlist, each pick joined back to its saved date."""
    picks = analysis.shortlist(records, now_weights(read_topics), limit=SHORTLIST_SIZE)
    by_hash = {r["url_sha256"]: r for r in records if r.get("url_sha256")}
    for p in picks:
        p["saved_date"] = (by_hash.get(p["url_sha256"]) or {}).get("saved_date")
        p["why_saved"] = one_line(p.get("why_saved"))
    return {"picks": picks, "eligible": analysis.shortlist_eligible_count(records),
            "items": len(records)}


def load(enriched=DEFAULT_ENRICHED, daily_csv=DEFAULT_DAILY_CSV,
         ledger=DEFAULT_LEDGER, index_path=None, frame=None):
    """Every figure the three pages draw, or None when there is no corpus.

    None means the pages are not built and the site does not link to them.
    A missing ledger or index is not fatal: the living view drops its Matter
    half, and the comparison page is not built without the read corpus.
    """
    records = load_records(enriched)
    if not records:
        return None
    if frame is None:
        frame = load_read_frame(index_path)
    versus = versus_view(records, frame)
    read_topics = versus["read_topics"] if versus else {}
    return {
        "pool": pool_view(records),
        "matter": load_matter(daily_csv, ledger, frame),
        "versus": versus,
        "worth": worth_view(records, read_topics),
    }


def load_matter(daily_csv, ledger, frame):
    """The Matter half of the living view, in its own failure domain.

    The ledger is appended by a separate nightly job, and a torn or malformed
    file is a fact about that job, not about the Instapaper pool, the
    comparison or the shortlist. Any failure here drops the Matter section
    only, loudly on stderr, and the other three halves still build.
    """
    try:
        return matter_view(load_daily(daily_csv), load_latest_snapshot(ledger),
                           matter_reads_by_week(frame))
    except Exception as err:
        print(f"Matter queue ledger unreadable ({err!r}): the living view drops "
              f"its Matter section", file=sys.stderr)
        return None


def pages(data):
    """The pages this data can draw, as relative directories."""
    if not data:
        return []
    out = ["unread", "unread/worth"]
    if data.get("versus"):
        out.insert(1, "unread/versus")
    return out


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

UNREAD_STYLE = """
/* the unread pages */
.subnav { display:flex; gap:8px; flex-wrap:wrap; margin-top:18px; }
.subnav a, .subnav span { font-family:ui-monospace,"SF Mono",Menlo,monospace;
  font-size:11px; letter-spacing:.12em; text-transform:uppercase;
  padding:5px 9px; border-radius:3px; background:var(--bg-raise);
  color:var(--ink-3); text-decoration:none; }
.subnav a:hover { color:var(--brand); }
.subnav span { color:var(--ink); box-shadow:inset 0 -1px 0 var(--amber); }
.pair { display:flex; gap:3px; align-items:flex-end; height:150px; margin-top:10px; }
.pair .col { flex:1; display:flex; flex-direction:column; justify-content:flex-end;
  height:100%; }
.pair .bars { flex:1; display:flex; gap:1px; align-items:flex-end; }
.pair .bars i { flex:1; display:block; min-height:1px; border-radius:2px 2px 0 0; }
.pair .bars i.r { background:var(--amber-dim); }
.pair .bars i.u { border:1px solid var(--amber); border-bottom:none;
  background:rgba(251,191,36,.08); }
.pair .col.hyp i.u { background:rgba(251,191,36,.28); }
.pair .col.hyp .bl { color:var(--amber); }
.days .day.gap .dl::before { content:"\\00b7 "; color:var(--amber); }
.pair .col:hover i.r { background:var(--brand); }
.pair .col:hover i.u { border-color:var(--brand); }
.pair .bl { text-align:center; margin-top:7px; font-size:9px; color:var(--ink-3);
  letter-spacing:0; }
.key { display:flex; gap:22px; flex-wrap:wrap; margin-top:12px; }
.key span { display:flex; align-items:center; gap:7px; }
.key i { width:12px; height:10px; display:block; border-radius:2px; }
.key i.r { background:var(--amber-dim); }
.key i.u { border:1px solid var(--amber); background:rgba(251,191,36,.08); }
.twocol { display:grid; grid-template-columns:1fr 1fr; gap:28px; margin-top:8px; }
.tcrow { display:grid; grid-template-columns:minmax(0,1.3fr) 1fr 1fr 52px; gap:10px;
  align-items:center; padding:6px 0; border-bottom:1px solid #2a2523; font-size:13.5px; }
.tcrow .tn { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.tcrow .tb { height:8px; display:block; border-radius:2px; }
.tcrow .tb.r { background:var(--amber-dim); }
.tcrow .tb.u { border:1px solid var(--amber); background:rgba(251,191,36,.08); }
.tcrow .tx { text-align:right; font-size:12px; color:var(--ink-2); }
.tcrow.head { color:var(--ink-3); border-bottom:1px solid var(--rule); }
.srow { display:grid; grid-template-columns:30px 1fr; gap:12px; padding:14px 0;
  border-bottom:1px solid #2a2523; }
.srow .rk { font-family:ui-monospace,"SF Mono",Menlo,monospace; font-size:12px;
  color:var(--ink-3); text-align:right; padding-top:3px; }
.srow .st { font-size:15.5px; color:var(--ink); text-decoration:none; }
a.st:hover { color:var(--brand); }
.srow .sm { margin-top:4px; }
.srow .why { margin-top:8px; font-family:Charter,Georgia,serif; font-size:14.5px;
  color:var(--ink-2); line-height:1.55; }
.srow .why .label { color:var(--amber); margin-right:8px; }
.srow .why.low { color:var(--ink-3); }
.srow .why.low .label { color:var(--ink-3); }
.srow .rs { margin-top:6px; font-family:ui-monospace,"SF Mono",Menlo,monospace;
  font-size:11.5px; color:var(--ink-3); }
.bandstats { display:flex; gap:36px; flex-wrap:wrap; margin-top:8px; }
.bandstats .v { font-size:30px; font-weight:200; }
.bandstats .chips { margin-top:8px; max-width:200px; }
details.collapsed summary { cursor:pointer; padding:10px 0 4px; list-style:none;
  color:var(--ink-3); }
details.collapsed summary:hover { color:var(--brand); }
details.collapsed summary::before { content:"\\25b8  "; }
details.collapsed[open] summary::before { content:"\\25be  "; }
@media (max-width:560px){ .twocol{grid-template-columns:1fr;}
  .tcrow{grid-template-columns:minmax(0,1fr) 60px 60px 44px;}
  .pair .bl{display:none;} }
"""

SUBPAGES = [
    ("unread", "The queue", ""),
    ("unread/versus", "Read versus unread", "versus/"),
    ("unread/worth", "Still worth your time", "worth/"),
]

FOOTER = "Computed at build time from the stored enrichment · no model calls"


def _subnav(current, built, depth):
    up = "../" * (depth - 1)
    out = []
    for key, label, target in SUBPAGES:
        if key not in built:
            continue
        if key == current:
            out.append(f"<span>{e(label)}</span>")
        else:
            out.append(f'<a href="{e(up + target) or "./"}">{e(label)}</a>')
    return f'    <nav class="subnav" aria-label="the unread pages">{"".join(out)}</nav>\n'


def _stat(value, label, delta="", cls=""):
    d = f'<div class="delta">{e(delta)}</div>' if delta else ""
    c = f" {cls}" if cls else ""
    return (f'    <div class="stat{c}"><div class="v num">{value}</div>'
            f'<div class="l label">{e(label)}</div>{d}</div>\n')


def _collapsed(summary, paragraphs):
    body = "".join(f'<div class="note">{e(p)}</div>' for p in paragraphs if p)
    return (f'    <details class="collapsed"><summary class="label">{e(summary)}</summary>'
            f'{body}</details>\n')


def _pct(x, digits=0):
    return f"{x * 100:.{digits}f}%"


def _header(title, deck, depth):
    return (f'  <header>\n'
            f'    <a class="label kicker" href="{e(htmlkit.weeks_index_href(depth))}">'
            f'What I meant to read · private</a>\n'
            f'    <h1>{e(title)}</h1>\n'
            f'    <div class="daterange">{e(deck)}</div>\n')


def _footer(domain):
    return (f'  <footer>\n    <span class="label">{e(FOOTER)}</span>\n'
            f'    <span class="label num">Generated {dt.date.today().isoformat()} · {e(domain)}</span>\n'
            f'  </footer>')


def _orows(pairs, unit="items", lead=True):
    if not pairs:
        return '    <div class="empty">nothing to rank</div>\n'
    peak = max(c for _, c in pairs) or 1
    out = ""
    for i, (name, count) in enumerate(pairs, 1):
        cls = " lead" if (lead and i == 1) else ""
        out += (f'    <div class="orow{cls}"><span class="rk">{i}</span>'
                f'<span class="on">{e(str(name))}<span class="obar" '
                f'style="width:{max(count / peak * 100, 2):.0f}%"></span></span>'
                f'<span class="oc num">{n(count)}</span></div>\n')
    return out


def _single_band(series, fmt_tip, axis=None, hi=None, thin=()):
    """A one-series band in the trends idiom. `series` is [(label, value 0..1 or None)]."""
    out = ""
    for key, value in series:
        tip = fmt_tip(key)
        label = "’" + str(key)[2:] if len(str(key)) == 4 else str(key)
        if value is None:
            out += (f'      <div class="col none" data-tip="{e(tip)}" aria-label="{e(tip)}">'
                    f'<div class="bar" style="height:2px"></div>'
                    f'<div class="bl num">{e(label)}</div></div>\n')
            continue
        cls = " hi" if key == hi else ""
        if key in thin:
            cls += " thin"
        pct = max(min(value, 1.0), 0.02) * 100
        out += (f'      <div class="col{cls}" data-tip="{e(tip)}" aria-label="{e(tip)}"><div class="bar" '
                f'style="height:{pct:.1f}%"></div><div class="bl num">{e(label)}</div></div>\n')
    return f'    <div class="band">\n{out}    </div>\n'


def _year_axis(*series):
    years = sorted({int(y) for s in series for y in s})
    return list(range(years[0], years[-1] + 1)) if years else []


# ---- /unread/ ---------------------------------------------------------------

def render_queue(data, built, domain=""):
    pool, matter = data["pool"], data["matter"]
    depth = 1
    head = _header("The unread queue",
                   f"{n(pool['items'])} Instapaper intentions frozen in September 2026, "
                   f"and the Matter queue measured every night since "
                   f"{matter['first_day'].isoformat() if matter and matter['first_day'] else 'the ledger started'}.",
                   depth)
    head += _subnav("unread", built, depth) + "  </header>\n"

    split = pool["split"]
    stats = '  <div class="stats">\n'
    stats += _stat(n(pool["items"]), "Instapaper pool",
                   f"{n(split['unread_queue'])} queue · {n(split['filed_in_folders'])} in folders")
    if matter:
        wk, span = matter["change"]
        if wk is None:
            delta = ""
        elif wk:
            delta = f"{'+' if wk > 0 else ''}{wk} over {span} days"
        else:
            delta = f"level over {span} days"
        stats += _stat(n(matter["size"]), "Matter queue",
                       f"as of {matter['as_of'].isoformat()}" + (f" · {delta}" if delta else "")
                       if matter["as_of"] else delta, cls="time")
        stats += _stat(f'{matter["words"] / 1e6:,.2f}<em>M</em>', "Matter words waiting",
                       f"{n(matter['started'])} started, not finished")
    stats += _stat(n(pool["dead"]), "Dead on all three paths",
                   f"{_pct(pool['dead'] / pool['items'], 1)} of the pool" if pool["items"] else "")
    stats += "  </div>\n"

    sections = ""
    if matter:
        sections += _render_matter(matter)
    sections += _render_pool(pool)

    body = head + "\n" + stats + sections + "\n" + _footer(domain)
    return page("The unread queue - The Week in Reading", body, depth=depth, here="unread")


def _render_matter(m):
    # Queue size per night, on an axis that starts well above zero. The window
    # is stated beside the chart: a truncated axis is honest while labelled.
    days = m["days"]
    out = ""
    if days:
        counts = [d["count"] for d in days if d["count"] is not None]
        lo, hi = min(counts), max(counts)
        floor = max(lo - max((hi - lo), 10), 0)
        span = (hi - floor) or 1
        cols = ""
        for d in days:
            c = d["count"]
            pct = max((c - floor) / span, 0.04) * 100 if c is not None else 2
            gap = (d.get("days_since_previous") or 1) > 1
            flow = ("first night, nothing to diff against" if d["inflow"] is None
                    else f"+{d['inflow']} in, -{d['outflow']} out")
            if gap and d["inflow"] is not None:
                # A missed night: this row's flow covers every night since the
                # last snapshot, and the bar says so rather than drawing it as one.
                flow += f" across {d['days_since_previous']} nights, a missed snapshot"
            tip = f"{d['date'].strftime('%a %b %-d')} - {n(c)} in the queue, {flow}"
            cls = (" peak" if c == hi else "") + (" gap" if gap else "")
            cols += (f'      <div class="day{cls}" data-tip="{e(tip)}"><div class="dv num">{n(c)}</div>'
                     f'<div class="bar" style="height:{pct:.1f}%"></div>'
                     f'<div class="dl label">{d["date"].strftime("%-d")}</div></div>\n')
        out += (f'  <section>\n    <div class="label viz-title">The Matter queue, night by night · '
                f'axis from {n(floor)}</div>\n    <div class="days">\n{cols}    </div>\n  </section>\n')

    rows = ""
    for w in reversed(m["weeks"]):
        net = w["net"]
        net_s = "-" if net is None else (f"+{net}" if net > 0 else str(net))
        flag = "" if w["measured"] else f" · {w['flow_days']} of 7 nights"
        reads = "-" if w.get("reads") is None else n(w["reads"])
        rows += (f'    <div class="tcrow"><span class="tn num">{e(w["week"])}{e(flag)}</span>'
                 f'<span class="tx num">{"-" if w["inflow"] is None else n(w["inflow"])} in</span>'
                 f'<span class="tx num">{"-" if w["outflow"] is None else n(w["outflow"])} out</span>'
                 f'<span class="tx num">{e(net_s)}</span></div>\n')
        rows += (f'    <div class="note" style="margin-top:2px">{e(w["week"])} · '
                 f'{reads} Matter reads archived that week · queue ended at {n(w["end_count"])}</div>\n')
    p = m["projection"]
    if p["state"] == "too_early":
        proj = (f"Too early to project: {p['measured']} of the {p['needed']} full weeks "
                f"the burn-down needs.")
    elif p["state"] == "never":
        proj = (f"Never at this rate: the median week over {p['measured']} measured weeks "
                f"grew the queue by {p['median_net']:g}.")
    else:
        proj = (f"About {p['weeks']:g} weeks to empty, at the median net drain of "
                f"{-p['median_net']:g} a week over {p['measured']} measured weeks.")
    out += (f'  <section>\n    <div class="label viz-title">Build versus drain · per ISO week</div>\n'
            f'    <div class="tcrow head label"><span>Week</span><span class="tx">Inflow</span>'
            f'<span class="tx">Outflow</span><span class="tx">Net</span></div>\n{rows}'
            f'    <div class="provenance label" style="margin-top:16px">{e(proj)}</div>\n'
            + _collapsed("How these are measured", [
                "Inflow is the count of queue ids present tonight that were not present at the "
                "previous snapshot. Outflow is the count that left, whether read or removed. Both "
                "come from the nightly snapshot ledger, one row per night, and a week sums its "
                "nights. Net is inflow minus outflow, so a positive week is a week the queue grew.",
                "A night whose date carries a dot follows a missed snapshot: its flow covers every "
                "night since the last one, and its tooltip says how many.",
                f"Reads are Matter articles archived that week, from the reading index. They are "
                f"not the same thing as outflow: an article saved and read the same day never "
                f"sits in the queue at all, and an item removed unread is outflow with no read. "
                f"Over the last {READS_CONTEXT_WEEKS} weeks the median is "
                f"{m['reads_median']:g} reads a week." if m["reads_median"] is not None else "",
                f"The projection takes the median weekly net over weeks with at least "
                f"{MIN_DAYS_FOR_A_WEEK} measured nights, and it will not name a figure on fewer "
                f"than {MIN_PROJECTION_WEEKS} of them.",
            ])
            + '  </section>\n')

    ctx = m["reads_context"]
    peak = max((c for _, c in ctx), default=0) or 1
    cols = ""
    for wk, c in ctx:
        tip = f"{wk} - {n(c)} Matter reads"
        cols += (f'      <div class="mo" data-tip="{e(tip)}"><div class="mv num">{n(c)}</div>'
                 f'<div class="bar" style="height:{max(c / peak * 100, 2):.1f}%"></div>'
                 f'<div class="ml label">{e(wk[-3:])}</div></div>\n')
    out += (f'  <section>\n    <div class="label viz-title">What the queue drains into · '
            f'Matter reads per week, last {len(ctx)}</div>\n    <div class="months">\n{cols}'
            f'    </div>\n  </section>\n')

    lengths = m["lengths"]
    out += (f'  <section>\n    <div class="twocol">\n      <div>\n'
            f'    <div class="label viz-title">Matter queue by site · {n(m["distinct_sites"])} distinct</div>\n'
            f'{_orows(m["sites"])}      </div>\n      <div>\n'
            f'    <div class="label viz-title">Matter queue by length · words</div>\n'
            f'{_orows(lengths["bands"], lead=False)}'
            f'    <div class="note">Median {n(m["median_words"]) if m["median_words"] else "-"} words. '
            f'{n(lengths["no_length"])} carry no word count and sit outside the bands. '
            f'{n(m["never_opened"])} never opened, {n(m["finished_in_queue"])} finished but '
            f'never filed out of the queue. No saved date is in the ledger, so the Matter '
            f'queue has no by-year cut here.</div>\n'
            f'      </div>\n    </div>\n  </section>\n')
    return out


def _render_pool(pool):
    years = _year_axis(pool["by_year"])
    by_year = pool["by_year"]
    dead = pool["dead_by_year"]
    split = pool["split"]["by_year"]
    peak = max(by_year.values(), default=0) or 1
    cols = ""
    for y in years:
        c = by_year.get(y, 0)
        d = (dead.get(y) or {}).get("dead", 0)
        s = split.get(str(y)) or {}
        tip = (f"{y} - {n(c)} saved, {n(s.get('unread_queue', 0))} in the queue, "
               f"{n(s.get('filed_in_folders', 0))} in folders, {n(d)} dead on all three paths")
        cls = " peak" if c == peak else ""
        cols += (f'      <div class="mo{cls}" data-tip="{e(tip)}"><div class="mv num">{n(c) if c else ""}</div>'
                 f'<div class="bar" style="height:{max(c / peak * 100, 1 if c else 0):.1f}%"></div>'
                 f'<div class="ml label">’{str(y)[2:]}</div></div>\n')
    band = sum(by_year.get(y, 0) for y in HYPOTHESIS_YEARS)
    out = (f'  <section>\n    <div class="label viz-title">The Instapaper pool by year saved · '
           f'{n(pool["items"])} items</div>\n    <div class="months">\n{cols}    </div>\n'
           f'    <div class="provenance label" style="margin-top:14px">{n(band)} of '
           f'{n(pool["items"])} were saved 2017 to 2019</div>\n'
           + _collapsed("What this says", [
               f"The pool is the live unread folder plus the never-opened items filed in the "
               f"five user folders, frozen on 2026-09-15. {n(pool['split']['filed_in_folders'])} "
               f"of the {n(pool['items'])} sit in folders, and every item saved before 2014 that "
               f"survived unread survived in a folder: the queue itself holds nothing older.",
               f"The oldest save is {pool['span']['oldest']} and the newest "
               f"{pool['span']['newest']}. {n(pool['starred'])} are starred.",
           ])
           + '  </section>\n')

    dom = pool["domains"]
    lengths = pool["lengths"]
    words = pool["words"]
    out += (f'  <section>\n    <div class="twocol">\n      <div>\n'
            f'    <div class="label viz-title">By source · {n(pool["distinct_domains"])} domains</div>\n'
            f'{_orows(dom[:12])}'
            f'    <div class="note">The top two are {_pct(pool["top_two_share"])} of the pool.</div>\n'
            f'      </div>\n      <div>\n'
            f'    <div class="label viz-title">By length · recovered words</div>\n'
            f'{_orows(lengths["bands"], lead=False)}'
            f'    <div class="note">Median {n(words["median"]) if words["median"] else "-"} words over the '
            f'{n(words["measured_over"])} items that came back with text. {n(lengths["no_length"])} '
            f'carry no body and no length.</div>\n'
            f'      </div>\n    </div>\n  </section>\n')

    series = []
    for y in years:
        v = dead.get(y)
        series.append((y, v["fraction"] if v else None))
    surv = pool["survival"]
    out += (f'  <section>\n    <div class="label viz-title">Dead fraction by year saved · '
            f'no text on any of the three paths</div>\n'
            + _single_band(series, lambda y: (
                f"{y} - {dead[y]['dead']} of {dead[y]['total']} dead, "
                f"{dead[y]['fraction'] * 100:.1f}%" if y in dead else f"{y} - nothing saved"))
            + '    <div class="axis label"><span>0%</span><span>100% at full height</span></div>\n'
            + _collapsed("Where the text came from, and what that does not say", [
                f"Instapaper's stored copy {n(surv['instapaper_stored_copy'])}, a direct fetch "
                f"{n(surv['direct_fetch_after_instapaper_failed'])}, a Wayback snapshot "
                f"{n(surv['wayback_snapshot'])}, nowhere {n(surv['no_text_anywhere'])}.",
                "This is which leg of the fetch chain produced the text, not whether the URL is "
                "still live. The chain stops at the first leg that works, so an item Instapaper "
                "resolved was never requested from its own address. The live-web figure needs its "
                "own probe pass, which the study lists as a follow-up.",
            ])
            + '  </section>\n')
    return out


# ---- /unread/versus/ --------------------------------------------------------

def render_versus(data, built, domain=""):
    v = data["versus"]
    depth = 2
    head = _header("Read versus unread",
                   f"{n(v['read_total'])} read-it-later articles against the {n(v['unread_total'])} "
                   f"he saved and never read, on the same axes.", depth)
    head += _subnav("unread/versus", built, depth) + "  </header>\n"

    band_r = v["band_read"] / v["read_total"] if v["read_total"] else 0
    band_u = v["band_unread"] / v["unread_total"] if v["unread_total"] else 0
    aging = v["aging"]
    judged = sum(a["judged"] for a in aging.values())
    aged = sum(a["aged_out"] for a in aging.values())
    ab = v["abandonment"]
    peak = v["peak_drift"]
    peak_share = v["drift"][peak]["unmatched_share"] if peak else None

    stats = '  <div class="stats">\n'
    stats += _stat(_pct(band_u), "Unread saved 2017-19",
                   f"against {_pct(band_r)} of what was read", cls="time")
    stats += _stat(str(peak) if peak else "-", "Peak drift year",
                   f"{_pct(peak_share)} of its saved topics absent from that year's reading" if peak_share is not None else "")
    stats += _stat(_pct(aged / judged) if judged else "-", "Aged out",
                   f"{n(aged)} of {n(judged)} the model judged")
    stats += _stat(n(ab[derive.STARTED]["count"] + ab[derive.NEARLY]["count"]),
                   "Opened, abandoned",
                   f"{n(ab[derive.NEARLY]['count'])} nearly finished")
    stats += "  </div>\n"

    # 01 the shape of the two corpora by year saved, on shared share axes
    years = _year_axis(v["read_by_year"], v["unread_by_year"])
    rs = {y: v["read_by_year"].get(y, 0) / v["read_total"] for y in years} if v["read_total"] else {}
    us = {y: v["unread_by_year"].get(y, 0) / v["unread_total"] for y in years}
    top = max(list(rs.values()) + list(us.values()), default=0) or 1
    cols = ""
    for y in years:
        r_c, u_c = v["read_by_year"].get(y, 0), v["unread_by_year"].get(y, 0)
        tip = (f"{y} - read {n(r_c)} ({_pct(rs.get(y, 0), 1)} of read), "
               f"unread {n(u_c)} ({_pct(us.get(y, 0), 1)} of unread)")
        cls = " hyp" if y in HYPOTHESIS_YEARS else ""
        cols += (f'      <div class="col{cls}" data-tip="{e(tip)}" aria-label="{e(tip)}"><div class="bars">'
                 f'<i class="r" style="height:{rs.get(y, 0) / top * 100:.1f}%"></i>'
                 f'<i class="u" style="height:{us.get(y, 0) / top * 100:.1f}%"></i></div>'
                 f'<div class="bl num">’{str(y)[2:]}</div></div>\n')
    s1 = (f'  <section>\n    <div class="label viz-title">01 · When each was saved · '
          f'share of its own corpus per year</div>\n    <div class="pair">\n{cols}    </div>\n'
          f'    <div class="key label"><span><i class="r"></i>Read · {n(v["read_total"])}</span>'
          f'<span><i class="u"></i>Unread · {n(v["unread_total"])}</span>'
          f'<span>2017-19 shaded, years in amber</span></div>\n'
          + _collapsed("The first hypothesis", [
              "Written down before the enrichment ran: 2017 to 2019 was a period of saving one "
              "kind of thing and reading another.",
              hypothesis_window_note(v["read_by_year"]),
              f"{n(v['band_unread'])} of the {n(v['unread_total'])} unread items were saved in "
              f"those three years, {_pct(band_u, 1)}. Of the read-it-later articles, "
              f"{n(v['band_read'])} of {n(v['read_total'])}, {_pct(band_r, 1)}.",
              "Each series is drawn as a share of its own corpus, so the two can sit on one "
              "axis. The raw counts are in every tooltip.",
          ])
          + '  </section>\n')

    # 02 the hypothesis band's subjects, side by side
    s2 = (f'  <section>\n    <div class="label viz-title">02 · What 2017-19 was about · '
          f'topic mentions</div>\n    <div class="twocol">\n      <div>\n'
          f'    <div class="label" style="margin-bottom:6px">Read, saved 2017-19</div>\n'
          f'{_orows(v["band_read_topics"])}      </div>\n      <div>\n'
          f'    <div class="label" style="margin-bottom:6px">Unread, saved 2017-19</div>\n'
          f'{_orows(v["band_unread_topics"])}      </div>\n    </div>\n  </section>\n')

    # 03 topic comparison across the whole of both corpora
    tc = v["topics"]
    tpeak = max([t["unread_share"] or 0 for t in tc["topics"]]
                + [t["read_share"] or 0 for t in tc["topics"]] + [0]) or 1
    trows = ('    <div class="tcrow head label"><span>Topic</span><span>Unread share</span>'
             '<span>Read share</span><span class="tx">Ratio</span></div>\n')
    for t in tc["topics"]:
        us_, rs_ = t["unread_share"] or 0, t["read_share"] or 0
        tip = (f"{t['topic']} - {n(t['unread'])} unread mentions, {n(t['read'])} read mentions")
        ratio = "-" if t["ratio"] is None else f"{t['ratio']:.1f}x"
        trows += (f'    <div class="tcrow" data-tip="{e(tip)}"><span class="tn">{e(t["topic"])}</span>'
                  f'<span><i class="tb u" style="width:{max(us_ / tpeak * 100, 1):.0f}%"></i></span>'
                  f'<span><i class="tb r" style="width:{max(rs_ / tpeak * 100, 1):.0f}%"></i></span>'
                  f'<span class="tx num">{ratio}</span></div>\n')
    s3 = (f'  <section>\n    <div class="label viz-title">03 · Does the unread differ in kind · '
          f'top subjects, both corpora</div>\n{trows}'
          + _collapsed("How to read the ratio, and the caveat", [
              "Each share is a topic's mentions over all topic mentions in that corpus. The "
              "ratio is unread share over read share, so 3.0x means the subject is three times "
              "as prominent in what he saved and left as in what he read. The last rows reach "
              "down the read side for subjects the saved pile barely holds.",
              "The unread summaries were generated from whole article bodies. The read corpus "
              "was enriched under a 10,000-character cap, which 28 of 79 sampled bodies exceed. "
              "A topic present on one side and absent on the other may be a difference in how "
              "much text the model saw rather than in what was saved.",
          ])
          + '  </section>\n')

    # 04 drift by year
    drift = v["drift"]
    dyears = _year_axis(drift)
    thin = {y for y, d in drift.items() if d["items"] < analysis.MIN_DRIFT_ITEMS}
    series = [(y, (drift.get(y) or {}).get("unmatched_share")) for y in dyears]
    s4 = (f'  <section>\n    <div class="label viz-title">04 · Where the drift is largest · '
          f'share of saved topics not read that year</div>\n'
          + _single_band(series, lambda y: (
              f"{y} - {drift[y]['unmatched_topic_mentions']} of {drift[y]['topic_mentions']} "
              f"topic mentions unmatched across {drift[y]['items']} items"
              + (f", under {analysis.MIN_DRIFT_ITEMS} items, not eligible for the peak" if y in thin else "")
              if y in drift and drift[y]["unmatched_share"] is not None else f"{y} - no baseline"),
              hi=peak, thin=thin)
          + '    <div class="axis label"><span>0%</span><span>100% at full height</span></div>\n'
          + _collapsed("How drift is measured", [
              "For each year, the share of that year's saved topic mentions whose topic the read "
              "corpus did not carry for articles saved the same year. Weighted the way things "
              "were saved, so a topic saved forty times counts forty intentions.",
              f"Hatched years hold fewer than {analysis.MIN_DRIFT_ITEMS} unread items. They are "
              f"drawn, and they may not be named the peak.",
          ])
          + '  </section>\n')

    # 05 aging curve
    ayears = _year_axis(aging)
    series = [(y, (aging.get(y) or {}).get("fraction")) for y in ayears]
    s5 = (f'  <section>\n    <div class="label viz-title">05 · What aged out · '
          f'share of each year the model judged time-bound and past</div>\n'
          + _single_band(series, lambda y: (
              f"{y} - {aging[y]['aged_out']} of {aging[y]['judged']} judged aged out, "
              f"{aging[y]['unknown']} not judged" if y in aging else f"{y} - nothing saved"))
          + '    <div class="axis label"><span>0%</span><span>100% at full height</span></div>\n'
          + _collapsed("What this is", [
              "The model's constrained verdict on whether a piece was time-bound and its moment "
              "has passed. Rows the model would not judge, and rows its content check rejected, "
              "sit outside the denominator rather than counting as still current.",
          ])
          + '  </section>\n')

    # 06 abandonment bands
    labels = {derive.NEVER_OPENED: "Never opened", derive.STARTED: "Started",
              derive.NEARLY: "Nearly finished"}
    cells = ""
    for band in derive.BANDS:
        b = ab[band]
        chips = "".join(f'<span class="chip">{e(t)}</span>' for t in b["top_topics"][:4])
        cells += (f'      <div><div class="v num">{n(b["count"])}</div>'
                  f'<div class="label">{e(labels[band])}</div>'
                  f'<div class="delta num label" style="margin-top:4px">median '
                  f'{n(b["median_words"]) if b["median_words"] else "-"} words</div>'
                  f'<div class="chips">{chips}</div></div>\n')
    s6 = (f'  <section>\n    <div class="label viz-title">06 · What he nearly finished · '
          f'abandonment bands</div>\n    <div class="bandstats">\n{cells}    </div>\n'
          + _collapsed("How the bands are set", [
              "Arithmetic on Instapaper's read progress, not a model judgement. Exactly zero is "
              "never opened, 0.8 and above is nearly finished, anything between is started.",
          ])
          + '  </section>\n')

    # 07 sources saved but not read
    src = ""
    for row in v["sources"]:
        share = row["unread_share"] or 0
        tip = f"{row['domain']} - {n(row['unread'])} unread, {n(row['read'])} read"
        src += (f'    <div class="orow" data-tip="{e(tip)}"><span class="rk num">{_pct(share)}</span>'
                f'<span class="on">{e(row["domain"])}<span class="obar" style="width:{max(share * 100, 2):.0f}%">'
                f'</span></span><span class="oc num">{n(row["unread"])}/{n(row["read"])}</span></div>\n')
    s7 = (f'  <section>\n    <div class="label viz-title">07 · Trusted to save, not to read · '
          f'unread share per source, {MIN_SOURCE_ITEMS}+ unread</div>\n'
          f'{src or EMPTY_SOURCES}'
          + _collapsed("How to read this", [
              "The left figure is the source's unread share: its unread items over its unread "
              "plus read-it-later reads. The right pair is unread over read. A source he saved "
              "often and read rarely is a specific kind of aspiration.",
          ])
          + '  </section>\n')

    body = head + "\n" + stats + s1 + s2 + s3 + s4 + s5 + s6 + s7 + "\n" + _footer(domain)
    return page("Read versus unread - The Week in Reading", body, depth=depth, under="unread")


EMPTY_SOURCES = '    <div class="empty">no source clears the floor</div>\n'


# ---- /unread/worth/ ---------------------------------------------------------

BAND_WORDS = {derive.NEVER_OPENED: "never opened", derive.STARTED: "started",
              derive.NEARLY: "nearly finished"}


def render_worth(data, built, domain=""):
    w = data["worth"]
    why = analysis.why_saved_summary([]) if not data.get("versus") else data["versus"]["why"]
    depth = 2
    head = _header("Still worth your time",
                   f"The {len(w['picks'])} highest-ranked of the {n(w['eligible'])} unread pieces "
                   f"the model judged still current and whose text passed its content check. "
                   f"Ranked on the stored enrichment, not on a new model pass.", depth)
    head += _subnav("unread/worth", built, depth) + "  </header>\n"

    stats = '  <div class="stats">\n'
    stats += _stat(n(w["eligible"]), "Still current", f"of {n(w['items'])} in the pool", cls="time")
    stats += _stat(n(len(w["picks"])), "Ranked here")
    by_conf = Counter(p.get("why_saved_confidence") for p in w["picks"])
    stats += _stat(n(by_conf.get("high", 0)), "High-confidence inferences",
                   f"{n(by_conf.get('medium', 0))} medium · {n(by_conf.get('low', 0))} low")
    stats += "  </div>\n"

    rows = ""
    for i, p in enumerate(w["picks"], 1):
        url = safe_url(p.get("url"))
        title = e(str(p.get("title") or "Untitled"))
        title_html = (f'<a class="st" href="{url}">{title}</a>' if url
                      else f'<span class="st">{title}</span>')
        meta = " · ".join(x for x in (
            e(p.get("domain") or ""), e(str(p.get("saved_date") or p.get("saved_year") or "")),
            e(BAND_WORDS.get(p.get("abandonment"), "")),
        ) if x)
        conf = str(p.get("why_saved_confidence") or "").strip().lower()
        graded = conf in analysis.CONFIDENCE_GRADES
        if (p.get("why_saved") or "").strip():
            label = (f"Inference · {e(conf)} confidence" if graded
                     else "Inference · confidence not set")
            dim = " low" if (conf == "low" or not graded) else ""
            why_html = (f'<div class="why{dim}"><span class="label">{label}</span>'
                        f'{e(p["why_saved"])}</div>')
        else:
            why_html = ('<div class="why low"><span class="label">Inference</span>'
                        'the model offered no reason this was saved</div>')
        reason = str(p.get("reason") or "").replace("; ", " · ")
        rows += (f'    <div class="srow"><span class="rk">{i}</span><div>{title_html}'
                 f'<div class="sm label">{meta}</div>{why_html}'
                 f'<div class="rs">rank {p["score"]:.2f} · {e(reason)}</div></div></div>\n')

    s1 = (f'  <section>\n    <div class="label viz-title">The shortlist · ranked</div>\n'
          + _collapsed("How the ranking works, and what the inference is", [
              "Eligible means the model judged the piece not time-bound, and its content check "
              "did not reject the text. Among those, the rank is topic overlap with what he reads "
              "now (the read corpus's topics, weighted three to one toward 2023 onward), plus a "
              "bonus for having started it: a piece abandoned part way through ranks above one "
              "never opened. Every part traces to a stored field, and ties break on a hash so "
              "two builds agree.",
              "The line under each title is the model's guess at why it was saved, not a "
              "record. It carries the confidence the model set on it. Low-confidence guesses are "
              "shown here, dimmed, because this page is for judging the ranking, and they are "
              f"excluded from every aggregate on the other two pages. Across the pool, "
              f"{n(why['counted'])} guesses count, {n(why['excluded_low_confidence'])} are low "
              f"confidence, and {n(why['no_inference'])} rows carry none.",
          ])
          + rows + '  </section>\n')

    body = head + "\n" + stats + s1 + "\n" + _footer(domain)
    return page("Still worth your time - The Week in Reading", body, depth=depth, under="unread")


def write_pages(root, data, domain=""):
    """Write every page the data can draw under `root`. Returns what was built."""
    built = pages(data)
    if not built:
        return []
    renderers = {"unread": render_queue, "unread/versus": render_versus,
                 "unread/worth": render_worth}
    for rel in built:
        d = Path(root) / rel
        d.mkdir(parents=True, exist_ok=True)
        (d / "index.html").write_text(renderers[rel](data, built, domain=domain),
                                      encoding="utf-8")
    return built
