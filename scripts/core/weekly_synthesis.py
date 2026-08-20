#!/usr/bin/env python3
"""Weekly reading synthesis - the cross-article layer above per-article TL;DRs.

Reads one ISO week of the archive index, computes the week's stats, and asks
the local model (LM Studio/Qwen, via the same fleet-flock plumbing as
enrich_archive_local) for a WOVEN digest: 2-4 themes, connections between
pieces, one thread-of-the-week. Output is one markdown file per week in the
vault's synthesis/ subdir - stats in frontmatter so the future week pages
(Phase 2+ of the plan) render without recomputing, prose as the body.

Settled decisions (2026-08-19): vault synthesis/ home, Sunday 20:00 cadence,
300-500 woven words, reading.adamthede.com later.

Run under the repo venv (pandas):

    .venv/bin/python scripts/core/weekly_synthesis.py [--week 2026-W33]
                                                      [--dry-run] [--out-dir D]

Default week: on Sundays (the scheduled run's day) the week ending that
evening; any other day, the last fully closed week. Regeneration overwrites -
the file is a projection of the index plus one model call, never the record
of anything unrecoverable.
"""
import argparse
import re
import datetime as dt
import json
import os
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from enrich_archive_local import PINNED_MODEL, _locked_completion  # noqa: E402
from enrich_archive_gemini import INDEX_PATH  # noqa: E402

HEARTBEAT = Path.home() / "Library" / "Logs" / "MatterSync" / "weekly-synthesis-heartbeat.json"
TOP_N = 5
MAX_HIGHLIGHT_CHARS = 4000


def default_week(today=None):
    """The week the scheduled run should digest. On a Sunday (the 20:00 job's
    own day) that is TODAY's ISO week - it closes at midnight and the reading
    day is effectively over; anything read after 20:00 is healed by a manual
    --week regeneration and is approximate anyway (Matter archive dates are
    day-granular UTC). On any other day: the last fully closed week."""
    today = today or dt.date.today()
    if today.weekday() == 6:
        y, w, _ = today.isocalendar()
        return f"{y}-W{w:02d}"
    monday_this_week = today - dt.timedelta(days=today.weekday())
    y, w, _ = (monday_this_week - dt.timedelta(days=1)).isocalendar()
    return f"{y}-W{w:02d}"


def parse_week(raw):
    """'2026-W5' and '2026-w05' normalize to '2026-W05'; garbage and
    nonexistent weeks (2027-W53) fail loudly with a usable message."""
    m = re.fullmatch(r"(\d{4})-[Ww](\d{1,2})", raw.strip())
    if not m:
        raise SystemExit(f"Bad --week {raw!r}: expected e.g. 2026-W33")
    y, w = int(m.group(1)), int(m.group(2))
    try:
        dt.date.fromisocalendar(y, w, 1)
    except ValueError as e:
        raise SystemExit(f"Bad --week {raw!r}: {e}")
    return f"{y}-W{w:02d}"


def week_bounds(week):
    """'2026-W33' -> (Monday date, Sunday date), ISO semantics."""
    year, wnum = week.split("-W")
    start = dt.date.fromisocalendar(int(year), int(wnum), 1)
    return start, start + dt.timedelta(days=6)


def derive_date_read(df):
    """Era-aware, mirroring the dashboard: Matter rows are dated only by a
    real archive event (positive evidence of reading); legacy/Instapaper rows
    fall back to date_saved because 11k of them have nothing else."""
    read = df["date_archived"].copy()
    non_matter = df["source"] != "matter"
    read[non_matter] = read[non_matter].fillna(df.loc[non_matter, "date_saved"])
    return read


def select_week(df, week):
    start, end = week_bounds(week)
    read = derive_date_read(df)
    # Timestamp bounds, half-open: NaT compares False on both sides, and
    # object-dtype .dt.date comparisons trip pandas on mixed NaT columns.
    mask = (read >= pd.Timestamp(start)) & (read < pd.Timestamp(end) + pd.Timedelta(days=1))
    if "content_corrupted" in df.columns:
        mask &= df["content_corrupted"] != True  # noqa: E712
    out = df[mask].copy()
    out["date_read"] = read[mask]
    return out.sort_values("date_read")


def top_values(series_of_lists, n=TOP_N):
    counts = {}
    for lst in series_of_lists.dropna():
        for v in list(lst):
            v = str(v).strip()
            if v:
                counts[v] = counts.get(v, 0) + 1
    # Singletons are alphabetical noise on a typical week (65 people all at
    # count 1); a stat that reads meaningful but is alphabetical is worse
    # than none. Emit [value, count] pairs, repeats only.
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"name": v, "count": c} for v, c in ranked[:n] if c >= 2]


def week_rereads(vault, start, end):
    """Re-reads recorded in the window, from the sync manifest. Zero on any
    trouble - this is a nice-to-have stat, never a reason to fail the run."""
    try:
        manifest = json.loads((Path(vault) / ".matter_manifest.json").read_text())
        n = 0
        for item in manifest.get("items", {}).values():
            d = item.get("reread_date")
            if d and item.get("reread_recorded") and start.isoformat() <= d <= end.isoformat():
                n += 1
        return n
    except Exception:
        return 0


def source_host(url):
    """The source is the URL's host, www stripped. Subdomains stay - the
    substacks are distinct publications, not one source. Empty for the
    ~10.5k legacy rows that carry no URL."""
    from urllib.parse import urlparse
    if not url or pd.isna(url):
        return ""
    host = urlparse(str(url)).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def top_sources(rows, n=TOP_N):
    hosts = [h for h in (source_host(u) for u in rows["url"]) if h]
    counts = {}
    for h in hosts:
        counts[h] = counts.get(h, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return {"distinct": len(counts),
            "repeats": [{"name": h, "count": c} for h, c in ranked[:n] if c >= 2]}


def gather_highlights(rows):
    """Adam's own words about the week's articles - the highest-signal input."""
    chunks = []
    total = 0
    matter_rows = rows[rows["source"] == "matter"] if "source" in rows.columns else rows
    for _, r in matter_rows.iterrows():
        try:
            text = Path(r["file_path"]).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "## Highlights" not in text:
            continue
        section = text.split("## Highlights", 1)[1]
        # Stop at the next heading rather than swallowing the rest of the file.
        section = re.split(r"\n## ", section, maxsplit=1)[0].strip()
        chunk = f"From \u201c{r['title']}\u201d:\n{section}"
        if total + len(chunk) > MAX_HIGHLIGHT_CHARS:
            continue  # skip the oversized one, keep collecting small ones
        chunks.append(chunk)
        total += len(chunk)
    return "\n\n".join(chunks)


def as_list(v):
    """Parquet round-trips list columns as numpy arrays, whose truthiness is
    ambiguous - never write `v or []` against them."""
    if v is None:
        return []
    try:
        return [str(x) for x in list(v)]
    except TypeError:
        return []


def safe_int(v):
    return 0 if pd.isna(v) else int(v)


MAX_PROMPT_ARTICLES = 40


def build_weekly_prompt(week, rows, highlights):
    lines = []
    # Backfill weeks can hold 180+ articles (~100k chars) - enough to blow a
    # local context window SILENTLY (LM Studio truncates, exit 0). Cap the
    # prompt roster; the frontmatter roster stays complete.
    capped = rows.head(MAX_PROMPT_ARTICLES)
    for _, r in capped.iterrows():
        topics = ", ".join(as_list(r.get("topics"))[:4])
        summary = "" if pd.isna(r.get("summary")) else str(r.get("summary")).strip()
        lines.append(f"- \u201c{r['title']}\u201d ({safe_int(r.get('word_count'))} words; {topics})\n  {summary}")
    omitted = len(rows) - len(capped)
    if omitted > 0:
        lines.append(f"(and {omitted} more articles this week, omitted for length)")
    articles_block = "\n".join(lines)
    highlight_block = f"\nThe reader's own highlights from this week:\n{highlights}\n" if highlights else ""
    return f"""You are writing a weekly reading digest for the week {week}. Below are the articles one reader actually read this week, each with its summary and topics.{highlight_block}

Articles read this week:
{articles_block}

Write a woven digest of this week's reading: 300-500 words of flowing prose in 2-4 paragraphs. Find the 2-4 real themes that ran through the week and the connections BETWEEN pieces - do not summarize the articles one by one, and do not write a list. Close with a single final sentence that begins exactly with “The thread of the week”. Refer to articles naturally by their titles. Plain paragraphs only: no headers, no bullets, no numbering. Use hyphens for asides, never em dashes. Do not invent articles or facts not present above."""


def synthesize(prompt):
    return _locked_completion(prompt, temperature=0.7, max_tokens=1200)


def prev_week_stats(out_dir, week):
    start, _ = week_bounds(week)
    py, pw, _ = (start - dt.timedelta(days=1)).isocalendar()
    prev = Path(out_dir) / f"{py}-W{pw:02d}.md"
    if not prev.exists():
        return None
    try:
        import frontmatter
        meta = frontmatter.load(prev).metadata
        return {"articles": meta.get("article_count"), "words": meta.get("total_words")}
    except Exception:
        return None


def write_heartbeat(outcome, week, article_count, error=None):
    try:
        HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)
        now = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
        HEARTBEAT.write_text(json.dumps({
            "started_at": now, "finished_at": now, "outcome": outcome,
            "week": week, "articles": article_count, "error": error,
        }, indent=2))
    except OSError:
        pass


def _parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", help="ISO week like 2026-W33 (default: Sunday = the week ending today, else last closed week)")
    ap.add_argument("--dry-run", action="store_true", help="Print, write nothing.")
    ap.add_argument("--out-dir", help="Override output dir (default: <vault>/synthesis).")
    ap.add_argument("--no-heartbeat", action="store_true")
    return ap.parse_args()


def _run(args):
    week = parse_week(args.week) if args.week else default_week()
    start, end = week_bounds(week)

    vault = os.environ.get("INSTAPAPER_VAULT_PATH")
    out_dir = Path(args.out_dir) if args.out_dir else (Path(vault) / "synthesis" if vault else None)
    if out_dir is None:
        sys.exit("Set INSTAPAPER_VAULT_PATH or pass --out-dir.")
    if not INDEX_PATH.exists():
        sys.exit(f"Index not found: {INDEX_PATH}")

    df = pd.read_parquet(INDEX_PATH)
    rows = select_week(df, week)
    if rows.empty:
        print(f"{week}: no articles read this week - nothing to synthesize.")
        if not args.no_heartbeat and not args.dry_run:
            write_heartbeat("ok", week, 0)
        return 0

    words = int(rows["word_count"].fillna(0).sum())
    stats = {
        "week": week,
        "week_start": start.isoformat(),
        "week_end": end.isoformat(),
        "generated": dt.date.today().isoformat(),
        "model": PINNED_MODEL,
        "article_count": int(len(rows)),
        "total_words": words,
        "reading_time_hours": round(words / 238 / 60, 1),
        "top_topics": top_values(rows["topics"]),
        "top_people": top_values(rows["people"]),
        "top_orgs": top_values(rows["orgs"]),
        "rereads_recorded": week_rereads(vault, start, end) if vault else 0,
        "sources": top_sources(rows),
        "articles": [
            {"title": str(r["title"]), "url": "" if pd.isna(r.get("url")) else str(r.get("url")),
             "source": source_host(r.get("url")),
             "words": safe_int(r.get("word_count")), "date_read": r["date_read"].date().isoformat()}
            for _, r in rows.iterrows()
        ],
    }
    prev = prev_week_stats(out_dir, week)
    if prev:
        stats["prev_week"] = prev

    try:
        prose = synthesize(build_weekly_prompt(week, rows, gather_highlights(rows)))
    except Exception as e:
        print(f"Synthesis failed: {e}", file=sys.stderr)
        if not args.no_heartbeat and not args.dry_run:
            write_heartbeat("fail", week, len(rows), error=str(e)[:300])
        return 1

    import yaml
    doc = f"---\n{yaml.safe_dump(stats, sort_keys=False, allow_unicode=True)}---\n\n{prose.strip()}\n"
    if args.dry_run:
        print(doc)
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{week}.md"
    tmp = out_dir / f".{week}.md.tmp"
    tmp.write_text(doc, encoding="utf-8")
    tmp.replace(out)
    print(f"Wrote {out} ({len(rows)} articles, {words} words read).")
    if not args.no_heartbeat:
        write_heartbeat("ok", week, len(rows))
    return 0


def main():
    """Any failure - unmounted vault, bad week, torn write - must heartbeat
    `fail` and exit 1, or the cockpit reads last week's stale `ok` on exactly
    the nights something broke. Only a clean run or dry-run skips that."""
    args = _parse_args()
    try:
        return _run(args)
    except SystemExit as e:
        if not args.no_heartbeat and not args.dry_run and e.code not in (0, None):
            write_heartbeat("fail", args.week or default_week(), 0, error=str(e)[:300])
        raise
    except Exception as e:
        print(f"weekly synthesis failed: {e}", file=sys.stderr)
        if not args.no_heartbeat and not args.dry_run:
            write_heartbeat("fail", args.week or default_week(), 0, error=str(e)[:300])
        return 1


if __name__ == "__main__":
    sys.exit(main())
