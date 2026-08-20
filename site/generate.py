#!/usr/bin/env python3
"""reading.adamthede.com - static site generator (Phase 3 of the weekly plan).

Renders the vault's synthesis/ files into the week-page idiom Adam approved
2026-08-19 (docs/mockups/2026-08-19-week-page-w33.html is the design
contract), plus a weeks index with a full-history trend strip.

Deliberately NOT Hugo: the Daybook reference architecture (audit Part 2,
verdict "migrate"). Everything renders from the synthesis files' frontmatter -
the parquet index is not read at all, so a site build has no dependency
beyond the vault being mounted.

    .venv/bin/python site/generate.py --out _site
    .venv/bin/python site/generate.py --synthesis-dir /path --out _site

Idempotent; regenerating overwrites. Build cost is file-writes, not compute
(~130 pages). Deploy is a separate act (site/DEPLOY.md) - this script never
touches the network.
"""
import argparse
import datetime as dt
import html
import os
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import urlparse

import frontmatter

SITE_TITLE = "The Week in Reading"
DOMAIN = "reading.adamthede.com"


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------

def source_host(url):
    if not url:
        return ""
    host = urlparse(str(url)).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def load_weeks(synthesis_dir):
    """All week files, sorted oldest first. Malformed files are skipped loudly
    on stderr rather than killing the whole build - one bad week must not
    take the site down (same posture as the Hugo frontmatter hazard in CC)."""
    weeks = []
    for f in sorted(Path(synthesis_dir).glob("*.md")):
        try:
            post = frontmatter.load(f)
            meta = dict(post.metadata)
            if not meta.get("week") or not isinstance(meta.get("articles"), list):
                raise ValueError("missing week/articles frontmatter")
            # Every stat the templates interpolate must be sound HERE, or a
            # single bad week kills the whole build at render time (round-1
            # review blocker 1). Coerce loudly; failure skips the week.
            dt.date.fromisoformat(str(meta["week_start"]))
            dt.date.fromisoformat(str(meta["week_end"]))
            meta["article_count"] = int(meta["article_count"])
            meta["total_words"] = int(meta["total_words"])
            meta["reading_time_hours"] = float(meta["reading_time_hours"])
            meta["prose"] = post.content.strip()
            # Older files predate the source field; derive at render time.
            for a in meta["articles"]:
                if not a.get("source"):
                    a["source"] = source_host(a.get("url"))
            weeks.append(meta)
        except Exception as e:
            print(f"skipping {f.name}: {e}", file=sys.stderr)
    weeks.sort(key=lambda m: str(m["week"]))
    return weeks


def _norm_title(t):
    return re.sub(r"[^a-z0-9]+", " ", str(t).lower()).strip()


def link_titles(paragraph, articles):
    """Escape a prose paragraph, bolding (and linking, when the article has a
    URL) any quoted span that matches a roster title. Deterministic - the
    digests quote titles verbatim, so no LLM re-run and no hallucinated
    markup; all 127 existing weeks get it at render time."""
    by_norm = {}
    for a in articles:
        by_norm[_norm_title(a.get("title"))] = a
    out = []
    last = 0
    # Three dialects the model actually writes: curly quotes, straight
    # quotes, and markdown *emphasis* (16 corpus weeks). Matched asterisk
    # titles render with curly quotes so every page reads the same.
    pattern = r"[\u201c\"]([^\u201d\u201c\"\n]+)[\u201d\"]|\*([^*\n]+)\*"
    for m in re.finditer(pattern, paragraph):
        quoted = m.group(1) or m.group(2)
        art = by_norm.get(_norm_title(quoted))
        if art is None:
            continue
        out.append(e(paragraph[last:m.start()]))
        inner = "\u201c" + e(quoted) + "\u201d"
        url = str(art.get("url") or "")
        if url.lower().startswith(("http://", "https://")):
            out.append(f'<a class="atitle" href="{e(url)}">{inner}</a>')
        else:
            out.append(f'<strong class="atitle">{inner}</strong>')
        last = m.end()
    out.append(e(paragraph[last:]))
    return "".join(out)


def split_prose(prose):
    """Paragraphs, plus the thread-of-the-week sentence lifted into its own
    callout when the digest closes with one (the prompt asks for it, so most
    weeks have it; a week without one just renders no callout)."""
    paras = [p.strip() for p in prose.split("\n\n") if p.strip()]
    thread = None
    if paras:
        m = re.search(r"([^.!?]*thread of the week[^.!?]*[.!?])\s*$",
                      paras[-1], re.IGNORECASE)
        if m is None:
            # 31 of 127 corpus weeks paraphrase instead of using the literal
            # phrase ("The week explored how..."). The prompt mandates that
            # the digest CLOSE with the thread, so the final sentence is the
            # thread by construction - lift it when it is sentence-sized.
            pieces = re.split(r"(?<=[.!?])\s+(?=[A-Z\u201c])", paras[-1])
            if len(pieces) > 1 and 60 <= len(pieces[-1]) <= 400:
                m = re.search(re.escape(pieces[-1]) + r"\s*$", paras[-1])
            elif (len(pieces) == 1 and len(paras) > 1
                    and 60 <= len(paras[-1]) <= 400):
                # 17 corpus weeks close with the thread as its own one-
                # sentence paragraph - lift the whole paragraph, but never
                # when it is the digest's ONLY paragraph.
                thread = paras.pop()
                return paras, thread
        if m:
            thread = m.group(0).strip() if m.lastindex is None else m.group(1).strip()
            remainder = paras[-1][: m.start()].strip()
            if remainder:
                paras[-1] = remainder
            else:
                paras.pop()
    return paras, thread


def day_series(meta):
    """(label, iso_date, words, count) for Mon..Sun of the week."""
    start = dt.date.fromisoformat(str(meta["week_start"]))
    days = []
    for i in range(7):
        d = start + dt.timedelta(days=i)
        arts = [a for a in meta["articles"] if str(a.get("date_read")) == d.isoformat()]
        days.append({"label": d.strftime("%a"), "date": d,
                     "words": sum(int(a.get("words") or 0) for a in arts),
                     "count": len(arts)})
    return days


def week_sources(meta):
    hosts = [a.get("source") or "" for a in meta["articles"]]
    hosts = [h for h in hosts if h]
    counts = {}
    for h in hosts:
        counts[h] = counts.get(h, 0) + 1
    repeats = sorted(((h, c) for h, c in counts.items() if c >= 2),
                     key=lambda kv: (-kv[1], kv[0]))
    return len(counts), repeats


def fmt_range(meta):
    a = dt.date.fromisoformat(str(meta["week_start"]))
    b = dt.date.fromisoformat(str(meta["week_end"]))
    if a.month == b.month:
        return f"{a.strftime('%B')} {a.day} – {b.day}, {b.year}"
    return f"{a.strftime('%b')} {a.day} – {b.strftime('%b')} {b.day}, {b.year}"


def n(x):
    return f"{int(x):,}"


e = html.escape


# ---------------------------------------------------------------------------
# templates
# ---------------------------------------------------------------------------

STYLE = """
:root { --bg:#1c1917; --bg-raise:#292524; --ink:#e7e5e4; --ink-2:#a8a29e;
  --ink-3:#78716c; --rule:#44403c; --amber:#fbbf24; --amber-dim:#92700c;
  --brand:#FF8F3B; }
* { margin:0; padding:0; box-sizing:border-box; }
body { background:var(--bg); color:var(--ink); line-height:1.5;
  font-family:ui-sans-serif,-apple-system,"Helvetica Neue",sans-serif; }
a { color:inherit; }
.page { max-width:720px; margin:0 auto; padding:64px 24px 96px; }
.label { font-family:ui-monospace,"SF Mono",Menlo,monospace; font-size:11px;
  letter-spacing:.14em; text-transform:uppercase; color:var(--ink-3); }
.num { font-variant-numeric:tabular-nums; }
header { border-bottom:1px solid var(--rule); padding-bottom:28px; }
header .kicker { color:var(--brand); text-decoration:none; display:inline-block; }
h1 { font-size:56px; font-weight:200; letter-spacing:-.02em; margin-top:10px; }
h1 .wk { color:var(--amber); }
.daterange { margin-top:6px; color:var(--ink-2); font-size:15px; }
.stats { display:flex; gap:40px; flex-wrap:wrap; padding:32px 0;
  border-bottom:1px solid var(--rule); }
.stat .v { font-size:40px; font-weight:200; }
.stat .v em { font-style:normal; font-size:20px; color:var(--ink-2); }
.stat .l { margin-top:2px; }
.stat .delta { margin-top:4px; font-family:ui-monospace,"SF Mono",Menlo,monospace;
  font-size:11px; color:var(--ink-3); font-variant-numeric:tabular-nums; }
.stat .delta b { color:var(--ink-2); font-weight:400; }
.stat.time .v { color:var(--amber); }
section { padding:36px 0 0; }
.prose { font-family:Charter,Georgia,serif; font-size:17px; line-height:1.75;
  max-width:640px; }
.prose p+p { margin-top:1.2em; }
.prose .lede::first-letter { color:var(--brand); font-size:3.1em; float:left;
  line-height:.82; padding-right:8px; font-weight:400; }
.thread { margin-top:28px; padding:18px 20px; background:var(--bg-raise);
  border-left:2px solid var(--brand); font-family:Charter,Georgia,serif;
  font-size:16px; }
.prose .atitle, .thread .atitle { font-weight:700; color:var(--amber);
  text-decoration:none; }
a.atitle:hover { color:var(--brand); }
.viz-title { margin-bottom:18px; }
.days { display:flex; gap:2px; align-items:flex-end; height:140px; margin-top:8px; }
.day { flex:1; display:flex; flex-direction:column; justify-content:flex-end; height:100%; }
.day .bar { background:var(--amber-dim); border-radius:4px 4px 0 0; min-height:2px;
  transition:background .15s; }
.day.peak .bar { background:var(--amber); }
.day:hover .bar { background:var(--brand); }
.day .dl { text-align:center; margin-top:8px; }
.day .dv { text-align:center; font-size:12px; color:var(--ink-2); margin-bottom:4px; }
.day.peak .dv { color:var(--amber); }
/* styled tooltips (trend bars + day bars) */
[data-tip] { position:relative; }
[data-tip]::after { content:attr(data-tip); position:absolute;
  bottom:calc(100% + 10px); left:50%; transform:translateX(-50%);
  background:#0c0a09; color:var(--ink); border:1px solid var(--rule);
  border-left:2px solid var(--amber); padding:7px 11px; border-radius:4px;
  font-family:ui-monospace,"SF Mono",Menlo,monospace; font-size:11.5px;
  letter-spacing:.02em; white-space:nowrap; opacity:0; pointer-events:none;
  transition:opacity .12s; z-index:6; }
[data-tip]:hover::after { opacity:1; }
.roster { margin-top:8px; }
.row { display:grid; grid-template-columns:44px 1fr 64px; gap:12px;
  align-items:baseline; padding:7px 0; border-bottom:1px solid #2a2523;
  text-decoration:none; }
.row:hover { background:var(--bg-raise); }
.row .d { font-size:12px; color:var(--ink-3); }
.row .t { font-size:14.5px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.row .t .src { color:var(--ink-3); font-size:12px; margin-left:8px; }
.row .t .wbar { display:block; height:3px; border-radius:2px;
  background:var(--amber-dim); margin-top:5px; }
.row .w { font-size:12.5px; color:var(--ink-2); text-align:right; }
.row.longest .t { color:var(--amber); }
.row.longest .wbar { background:var(--amber); }
.facets { display:flex; gap:40px; flex-wrap:wrap; margin-top:8px; }
.facet .l { margin-bottom:8px; }
.chip { display:inline-block; padding:4px 10px; margin:0 6px 6px 0;
  background:var(--bg-raise); border-radius:3px; font-size:13px;
  vertical-align:baseline; }
.chip .c { color:var(--amber); font-size:.72em; margin-left:5px; }
.empty { color:var(--ink-3); font-size:13px; font-style:italic; }
.weeknav { display:flex; justify-content:space-between; margin-top:48px;
  padding-top:16px; border-top:1px solid var(--rule); }
.weeknav a { text-decoration:none; color:var(--ink-2); font-size:14px; }
.weeknav a:hover { color:var(--brand); }
.weeknav .home { color:var(--ink-3); font-family:ui-monospace,"SF Mono",Menlo,monospace;
  font-size:11px; letter-spacing:.14em; text-transform:uppercase; }
header .kicker:hover { text-decoration:underline; }
footer { margin-top:56px; border-top:1px solid var(--rule); padding-top:16px;
  display:flex; justify-content:space-between; flex-wrap:wrap; gap:8px; }
/* index */
.trend { display:flex; gap:1px; align-items:flex-end; height:90px; margin-top:24px; }
.trend a { flex:1; background:var(--amber-dim); border-radius:2px 2px 0 0;
  min-height:2px; display:block; }
.trend a:hover { background:var(--brand); }
.trend a.latest { background:var(--amber); }
.yearhead { margin:40px 0 4px; color:var(--ink-2); font-size:22px; font-weight:200; }
.ystrip { display:flex; gap:2px; margin:8px 0 14px; }
.ystrip a, .ystrip span { flex:1; height:9px; border-radius:1px; display:block; }
.ystrip span { background:#231f1d; }
.ystrip a:hover { outline:1px solid var(--brand); }
.wrow { display:grid; grid-template-columns:170px 1fr 70px 90px; gap:12px;
  align-items:baseline; padding:9px 0; border-bottom:1px solid #2a2523;
  text-decoration:none; }
.wrow:hover { background:var(--bg-raise); }
.wrow .wk2 { color:var(--amber); font-size:14px; white-space:nowrap; }
.wrow .wk2 em { font-style:normal; color:var(--ink-3); font-size:11.5px;
  margin-left:8px; }
.wrow .topic { font-size:14px; color:var(--ink-2); overflow:hidden;
  text-overflow:ellipsis; white-space:nowrap; }
.wrow .c2,.wrow .w2 { font-size:13px; color:var(--ink-2); text-align:right; }
@media (max-width:560px){ h1{font-size:40px;} .stats{gap:24px;}
  .wrow{grid-template-columns:80px 1fr 60px;} .wrow .w2{display:none;} }
"""


def page(title, body, depth=0):
    css = "../" * depth + "style.css"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<link rel="stylesheet" href="{css}">
</head>
<body>
<div class="page">
{body}
</div>
</body>
</html>
"""


def render_week(meta, prev_wk=None, next_wk=None, prev_meta=None):
    week = str(meta["week"])
    year, wnum = week.split("-")
    arts = meta["articles"]
    paras, thread = split_prose(meta["prose"])
    prose_html = ""
    for i, p in enumerate(paras):
        cls = ' class="lede"' if i == 0 else ""
        prose_html += f"      <p{cls}>{link_titles(p, arts)}</p>\n"
    thread_html = ""
    if thread:
        thread_html = (f'    <div class="thread"><span class="label" '
                       f'style="color:var(--brand)">Thread of the week&nbsp;&nbsp;</span>{e(thread)}</div>\n')

    days = day_series(meta)
    peak = max((d["words"] for d in days), default=0)
    day_html = ""
    for d in days:
        pct = (d["words"] / peak * 100) if peak else 0
        cls = " peak" if d["words"] == peak and peak else ""
        dv = f"{d['words']/1000:.1f}k" if d["words"] else "—"
        tip = (f"{d['date'].strftime('%a %b %-d')} — {n(d['words'])} words, "
               f"{d['count']} article{'s' if d['count'] != 1 else ''}"
               if d["count"] else f"{d['date'].strftime('%a %b %-d')} — no reading")
        day_html += (f'      <div class="day{cls}" data-tip="{e(tip)}">'
                     f'<div class="dv num">{dv}</div>'
                     f'<div class="bar" style="height:{pct:.1f}%"></div>'
                     f'<div class="dl label">{d["label"]}</div></div>\n')

    max_words = max((int(a.get("words") or 0) for a in arts), default=1) or 1
    roster_html = ""
    for a in arts:
        w = int(a.get("words") or 0)
        pct = max(w / max_words * 100, 2)
        longest = " longest" if w == max_words and len(arts) > 1 else ""
        d = str(a.get("date_read") or "")[5:].replace("-", "·")
        src = f'<span class="src">{e(a["source"])}</span>' if a.get("source") else ""
        url = str(a.get("url") or "")
        # Scheme allowlist: these are third-party scraped URLs, and e()
        # escapes quotes but not a javascript: scheme. A missing URL renders
        # a non-link row, not a dead href="#" (33 real cases in 2025-W47).
        linkable = url.lower().startswith(("http://", "https://"))
        inner = (f'<span class="d num">{d}</span>'
                 f'<span class="t">{e(str(a.get("title") or "Untitled"))}{src}'
                 f'<span class="wbar" style="width:{pct:.0f}%"></span></span>'
                 f'<span class="w num">{n(w)}</span>')
        if linkable:
            tag = f'<a class="row{longest}" href="{e(url)}">{inner}</a>'
        else:
            tag = f'<span class="row{longest}">{inner}</span>'
        roster_html += "      " + tag + "\n"


    def chips(pairs, empty_note):
        if not pairs:
            return f'        <span class="empty">{e(empty_note)}</span>\n'
        out = ""
        for item in pairs:
            name, count = (item["name"], item["count"]) if isinstance(item, dict) else item
            # Type scale carries the count: a x4 topic reads twice the
            # size of a x2 - size IS the datum, the xN confirms it.
            try:
                size = min(13 + (int(count) - 2) * 3, 24)
            except (TypeError, ValueError):
                size = 13
            out += (f'        <span class="chip" style="font-size:{size}px">{e(str(name))}'
                    f'<span class="c num">×{e(str(count))}</span></span>\n')
        return out

    distinct_sources, repeat_sources = week_sources(meta)
    longest_words = max((int(a.get("words") or 0) for a in arts), default=0)
    longest_title = ""
    for a in arts:
        if int(a.get("words") or 0) == longest_words:
            longest_title = str(a.get("title") or "")[:34]
            break

    def delta(key):
        # vs the previous GENERATED week (corpus-adjacent, not calendar-
        # adjacent): a small motion line under the headline numeral.
        if not prev_meta:
            return ""
        try:
            cur, prev = float(meta[key]), float(prev_meta[key])
        except (KeyError, TypeError, ValueError):
            return ""
        diff = cur - prev
        if diff == 0:
            return '<div class="delta">= ' + e(str(prev_meta["week"])) + "</div>"
        arrow = "\u25b2" if diff > 0 else "\u25bc"
        val = f"{abs(diff):,.1f}" if key == "reading_time_hours" else f"{abs(diff):,.0f}"
        return ('<div class="delta">' + arrow + " <b>" + val + "</b> vs "
                + e(str(prev_meta["week"])) + "</div>")

    left = f'<a href="../{prev_wk}/">← {prev_wk}</a>' if prev_wk else "<span></span>"
    right = f'<a href="../{next_wk}/">{next_wk} →</a>' if next_wk else "<span></span>"
    nav = ('  <div class="weeknav">' + left
           + '<a class="home" href="../../">All weeks</a>' + right + '</div>\n')

    body = f"""  <header>
    <a class="label kicker" href="../../">{e(SITE_TITLE)}</a>
    <h1>{e(year)} <span class="wk">· {e(wnum)}</span></h1>
    <div class="daterange num">{e(fmt_range(meta))}</div>
  </header>

  <div class="stats">
    <div class="stat"><div class="v num">{n(meta["article_count"])}</div><div class="l label">Articles read</div>{delta("article_count")}</div>
    <div class="stat"><div class="v num">{n(meta["total_words"])}</div><div class="l label">Words</div>{delta("total_words")}</div>
    <div class="stat time"><div class="v num">{e(str(meta["reading_time_hours"]))}<em> hrs</em></div><div class="l label">Reading time</div>{delta("reading_time_hours")}</div>
    <div class="stat"><div class="v num">{n(longest_words)}</div><div class="l label">Longest read · words</div><div class="delta">{e(longest_title)}</div></div>
    <div class="stat"><div class="v num">{distinct_sources}</div><div class="l label">Sources</div></div>
  </div>

  <section>
    <div class="prose">
{prose_html}    </div>
{thread_html}  </section>

  <section>
    <div class="label viz-title">When the reading happened · words per day</div>
    <div class="days">
{day_html}    </div>
  </section>

  <section>
    <div class="label viz-title">The {len(arts)} article{"s" if len(arts) != 1 else ""} · scaled by length</div>
    <div class="roster">
{roster_html}    </div>
  </section>

  <section>
    <div class="facets">
      <div class="facet">
        <div class="l label">Recurring topics</div>
{chips(meta.get("top_topics") or [], "none this week")}      </div>
      <div class="facet">
        <div class="l label">Recurring sources</div>
{chips([{"name": h, "count": c} for h, c in repeat_sources], "none — every source once")}      </div>
      <div class="facet">
        <div class="l label">Recurring people</div>
{chips(meta.get("top_people") or [], "none this week")}      </div>
    </div>
  </section>

{nav}  <footer>
    <span class="label">Synthesized by {e(str(meta.get("model") or "local model"))} · on-device</span>
    <span class="label num">Generated {e(str(meta.get("generated") or ""))} · {e(DOMAIN)}</span>
  </footer>"""
    return page(f"{week} — {SITE_TITLE}", body, depth=2)


def render_index(weeks):
    total_articles = sum(int(m["article_count"]) for m in weeks)
    total_words = sum(int(m["total_words"]) for m in weeks)
    total_hours = round(sum(float(m["reading_time_hours"]) for m in weeks), 1)
    first, last = weeks[0], weeks[-1]

    peak = max(int(m["total_words"]) for m in weeks) or 1
    trend = ""
    for m in weeks:
        w = str(m["week"])
        # sqrt scale: linear pinned 51 of 127 real weeks to the visual floor.
        pct = max((int(m["total_words"]) / peak) ** 0.5 * 100, 3)
        cls = ' class="latest"' if m is weeks[-1] else ""
        tip = f"{w} — {n(m['total_words'])} words, {m['article_count']} articles"
        trend += (f'    <a href="weeks/{w}/" data-tip="{e(tip)}"{cls} '
                  f'style="height:{pct:.1f}%"></a>\n')

    by_week = {str(m["week"]): m for m in weeks}
    max_words = peak

    def year_strip(year):
        """52/53 cells, one per ISO week of the year - the calendar itself as
        a chart. Present weeks are amber scaled by words and link through;
        absent weeks are faint stubs. GitHub-contribution grammar, one row."""
        try:
            n_weeks = dt.date(int(year), 12, 28).isocalendar()[1]
        except ValueError:
            n_weeks = 52
        cells = ""
        for i in range(1, n_weeks + 1):
            wk = f"{year}-W{i:02d}"
            m2 = by_week.get(wk)
            if m2 is None:
                cells += "      <span></span>\n"
            else:
                alpha = 0.22 + 0.78 * (int(m2["total_words"]) / max_words) ** 0.5
                tip = f"{wk} — {n(m2['total_words'])} words"
                cells += (f'      <a href="weeks/{wk}/" data-tip="{e(tip)}" '
                          f'style="background:rgba(251,191,36,{alpha:.2f})"></a>\n')
        return f'  <div class="ystrip">\n{cells}  </div>\n'

    rows = ""
    year_seen = None
    for m in reversed(weeks):
        w = str(m["week"])
        year = w.split("-")[0]
        if year != year_seen:
            rows += f'  <div class="yearhead num">{year}</div>\n'
            rows += year_strip(year)
            year_seen = year
        topics = m.get("top_topics") or []
        if topics and isinstance(topics[0], dict):
            top = topics[0].get("name", "")
        elif topics and isinstance(topics[0], (list, tuple)):
            top = topics[0][0]
        else:
            top = str(topics[0]) if topics else ""
        ws = dt.date.fromisoformat(str(m["week_start"]))
        we = dt.date.fromisoformat(str(m["week_end"]))
        span = f"{ws.strftime('%b')} {ws.day}–{we.day}" if ws.month == we.month \
            else f"{ws.strftime('%b')} {ws.day}–{we.strftime('%b')} {we.day}"
        # The row's background IS the words bar - sqrt-scaled amber wash.
        pct = (int(m["total_words"]) / max_words) ** 0.5 * 100
        bg = (f'style="background:linear-gradient(90deg,rgba(251,191,36,.08) '
              f'{pct:.1f}%,transparent {pct:.1f}%)"')
        rows += (f'  <a class="wrow" href="weeks/{w}/" {bg}>'
                 f'<span class="wk2 num">{w.split("-")[1]}<em>{e(span)}</em></span>'
                 f'<span class="topic">{e(str(top))}</span>'
                 f'<span class="c2 num">{n(m["article_count"])} art</span>'
                 f'<span class="w2 num">{n(m["total_words"])} w</span></a>\n')

    body = f"""  <header>
    <span class="label kicker">{e(DOMAIN)}</span>
    <h1>{e(SITE_TITLE)}</h1>
    <div class="daterange">Weekly syntheses of one reader's article diet, {first["week"]} — {last["week"]}, written by a local model from the reading archive.</div>
  </header>

  <div class="stats">
    <div class="stat"><div class="v num">{n(len(weeks))}</div><div class="l label">Weeks</div></div>
    <div class="stat"><div class="v num">{n(total_articles)}</div><div class="l label">Articles</div></div>
    <div class="stat"><div class="v num">{n(total_words)}</div><div class="l label">Words read</div></div>
    <div class="stat time"><div class="v num">{total_hours:,.1f}<em> hrs</em></div><div class="l label">Reading time</div></div>
  </div>

  <section>
    <div class="label viz-title">Words per week · {first["week"]} → {last["week"]}</div>
    <div class="trend">
{trend}    </div>
  </section>

  <section>
{rows}  </section>

  <footer>
    <span class="label">Synthesized on-device · qwen · one page per ISO week</span>
    <span class="label num">Generated {dt.date.today().isoformat()}</span>
  </footer>"""
    return page(SITE_TITLE, body, depth=0)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

MARKER = ".reading-site"


def generate(synthesis_dir, out_dir):
    """Render into a temp dir and swap only on success: a failed build must
    never leave the deploy dir empty (review blocker 1), and --out must never
    delete a directory this generator did not create (review blocker 2)."""
    weeks = load_weeks(synthesis_dir)
    if not weeks:
        raise SystemExit(f"No synthesis files found in {synthesis_dir}")
    # resolve() so a symlinked --out swaps at the real path instead of
    # rendering fully and then dying mute on rmtree(symlink) (round-2 minor B).
    out = Path(out_dir).resolve()
    if out.exists() and any(out.iterdir()) and not (out / MARKER).exists():
        raise SystemExit(
            f"Refusing to overwrite {out}: it is not empty and was not "
            f"generated by this script (no {MARKER} marker). Pick another --out.")

    tmp = out.parent / (out.name + ".building")
    # Same guard as --out: the sibling temp path must never consume a foreign
    # directory either (round-2 minor A - blocker 2's hole at a sibling path).
    if tmp.exists() and any(tmp.iterdir()) and not (tmp / MARKER).exists():
        raise SystemExit(
            f"Refusing to clear {tmp}: it is not empty and was not "
            f"generated by this script (no {MARKER} marker).")
    if tmp.exists():
        shutil.rmtree(tmp)
    try:
        (tmp / "weeks").mkdir(parents=True)
        (tmp / MARKER).write_text("generated by site/generate.py\n")
        (tmp / "style.css").write_text(STYLE, encoding="utf-8")
        (tmp / "index.html").write_text(render_index(weeks), encoding="utf-8")
        for i, m in enumerate(weeks):
            w = str(m["week"])
            prev_wk = str(weeks[i - 1]["week"]) if i > 0 else None
            next_wk = str(weeks[i + 1]["week"]) if i < len(weeks) - 1 else None
            d = tmp / "weeks" / w
            d.mkdir()
            prev_meta = weeks[i - 1] if i > 0 else None
            (d / "index.html").write_text(
                render_week(m, prev_wk, next_wk, prev_meta), encoding="utf-8")

        if out.exists():
            shutil.rmtree(out)
        os.replace(tmp, out)
    finally:
        # A failed swap must not strand a full rendered site on disk.
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
    return len(weeks)


def main():
    ap = argparse.ArgumentParser()
    default_dir = None
    vault = os.environ.get("INSTAPAPER_VAULT_PATH")
    if vault:
        default_dir = str(Path(vault) / "synthesis")
    ap.add_argument("--synthesis-dir", default=default_dir,
                    help="Week files (default: $INSTAPAPER_VAULT_PATH/synthesis)")
    ap.add_argument("--out", default="_site")
    args = ap.parse_args()
    if not args.synthesis_dir:
        sys.exit("Set INSTAPAPER_VAULT_PATH or pass --synthesis-dir.")
    count = generate(args.synthesis_dir, args.out)
    print(f"Rendered {count} week pages + index into {args.out}/")


if __name__ == "__main__":
    main()
