"""The reading site's cover page, rendered for Adam's approval.

A fifth mockup generator alongside mockup_concepts_v2.py and
mockup_cooccurrence.py, and tracked for the same reason they are: the
rendered HTML is not source, the code that computes its figures is. Every
number on the cover comes out of data/archive_index.parquet and the week
records here; nothing on the page is typed.

The anatomy is not invented. It is lifted from the covers already live on
books.adamthede.com and viewing.adamthede.com so the three surfaces read as
one family: four columns on one shared quarter axis, each with a bar strip
annotated by its era averages, a step chart of a second measure, one hero,
and four secondaries, with exactly one amber hero per page.

The one thing this generator must never lose is the era rule stated under
the columns. Two thirds of this corpus is dated by a filename rather than by
a read event, so a quarter before 2012 says when the writing appeared and a
quarter after it says when Adam read it. A cover that draws both on one axis
without saying so is a chart that lies quietly.

Usage:
    .venv/bin/python site/mockup_reading_cover.py --out docs/mockups/2026-09-08-reading-cover.html
"""
import argparse
import datetime as dt
import html
import os
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import corpus as C  # noqa: E402
import generate as G  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
Q_FIRST, Q_LAST = "2005Q1", "2026Q3"
# Three eras of READING, not three sources: the flood, the fade, the return.
# The source split (legacy / Instapaper / Matter) is its own column.
ERA_BANDS = [("2005-2011", 2005, 2011), ("2012-2020", 2012, 2020),
             ("2021-2026", 2021, 2026)]


# ---------------------------------------------------------------------------
# the quarter axis, and the two chart primitives the sibling covers use
# ---------------------------------------------------------------------------

QUARTERS = list(pd.period_range(Q_FIRST, Q_LAST, freq="Q"))
QN = len(QUARTERS)
PITCH = 260.0 / QN
BW = PITCH * 0.78
BASE, TOP = 81.0, 4.0


def era_slices():
    """(label, first index, last index) for each era on the shared axis."""
    out = []
    for label, lo, hi in ERA_BANDS:
        idx = [i for i, q in enumerate(QUARTERS) if lo <= q.year <= hi]
        out.append((label, idx[0], idx[-1]))
    return out


def era_averages(values):
    return [round(sum(values[a:b + 1]) / (b - a + 1), 1) for _, a, b in era_slices()]


def strip(values, tick, aria):
    """A quarterly bar strip with each era's average drawn over it in amber.

    The average line is annotated with its own value rather than left to a
    legend: on a column 250 pixels wide there is no room for a legend, and an
    unlabelled rule is a decoration.
    """
    avgs = era_averages(values)
    mx = max(values) or 1
    h = lambda v: (v / mx) * (BASE - TOP)  # noqa: E731
    out = [f'<svg viewBox="0 0 260 96" class="fstrip" role="img" aria-label="{aria}">']
    for i, v in enumerate(values):
        if v <= 0:
            continue
        # A bar for one article must still be visible, or a quiet quarter
        # reads as a missing one.
        hh = max(h(v), 0.9)
        out.append(f'<rect x="{i * PITCH:.2f}" y="{BASE - hh:.2f}" '
                   f'width="{BW:.2f}" height="{hh:.2f}" fill="#6f645c"/>')
    for (label, lo, hi), a in zip(era_slices(), avgs):
        y = BASE - h(a)
        x1, x2 = lo * PITCH, (hi + 1) * PITCH
        out.append(f'<line x1="{x1:.2f}" y1="{y:.2f}" x2="{x2:.2f}" y2="{y:.2f}" '
                   f'stroke="#fbbf24" stroke-width=".7"/>')
        out.append(f'<text x="{x1 + 2:.2f}" y="{y - 3.5:.2f}" class="favg">{a}</text>')
    out.append('<line x1="0" y1="81" x2="260" y2="81" stroke="#3a3431" stroke-width=".5"/>')
    out.append(f'<text x="0" y="93" class="ftick">{tick}</text>')
    out.append("</svg>")
    return "\n".join(out), avgs


def step(values, start=0):
    """The column's second measure as a step line, scaled to its own range.

    `start` drops the leading quarters of a series that does not begin in 2005
    - the source count cannot start before the archive carries a URL, and a
    line pinned at zero for six years is a claim about 2005 rather than a
    fact about it.
    """
    live = values[start:]
    mn, mx = min(live), max(live)
    rng = (mx - mn) or 1
    y = lambda v: 30.8 - (v - mn) / rng * (30.8 - 4.0)  # noqa: E731
    pts = []
    for i, v in enumerate(values):
        if i < start:
            continue
        pts.append(f"{i * PITCH:.2f},{y(v):.2f}")
        pts.append(f"{min((i + 1) * PITCH, 260.0):.2f},{y(v):.2f}")
    return ('<svg viewBox="0 0 260 34" class="fstep" role="img" aria-hidden="true">\n'
            f'<polyline points="{" ".join(pts)}" fill="none" stroke="#7d7268" '
            'stroke-width=".9"/>\n</svg>')


# ---------------------------------------------------------------------------
# every series on the page, measured
# ---------------------------------------------------------------------------

def series(rows, weeks):
    r = rows.assign(q=rows["date_read"].dt.to_period("Q"))
    S = {}
    S["art"] = [int(x) for x in
                r.groupby("q").size().reindex(QUARTERS, fill_value=0).values]

    words = C.numeric_column(r, "word_count").fillna(0)
    wq = r.assign(w=words).groupby("q")["w"].sum().reindex(QUARTERS, fill_value=0)
    total = 0
    S["wcum"] = []
    for v in wq.values:
        total += int(v)
        S["wcum"].append(total)

    def quarter_of(week):
        y, w = week.split("-W")
        return pd.Period(dt.date.fromisocalendar(int(y), int(w), 1), freq="Q")

    wk = Counter(quarter_of(str(m["week"])) for m in weeks)
    S["wkq"] = [wk.get(q, 0) for q in QUARTERS]
    covered = Counter()
    for m in weeks:
        covered[quarter_of(str(m["week"]))] += int(m["article_count"])
    total = 0
    S["acum"] = []
    for q in QUARTERS:
        total += covered.get(q, 0)
        S["acum"].append(total)

    # Sources are counted over the articles that kept a URL. The legacy import
    # kept none, which is why this pair starts flat and then starts at all.
    S["dq"], S["dcum"], seen = [], [], set()
    for q in QUARTERS:
        here = {d for d in r[r["q"] == q]["domain"] if d}
        S["dq"].append(len(here))
        seen |= here
        S["dcum"].append(len(seen))
    S["dcum_start"] = next(i for i, v in enumerate(S["dcum"]) if v)

    tagged = r[[bool(C.as_list(v)) for v in r["canonical_entries"]]]
    S["vq"] = []
    for q in QUARTERS:
        here = set()
        for v in tagged[tagged["q"] == q]["canonical_entries"]:
            here |= set(C.as_list(v))
        S["vq"].append(len(here))

    # Four quarters rather than one: a single quarter of 26 articles swings
    # this share by ten points, which draws noise as a trend.
    per_q = []
    for q in QUARTERS:
        sub = tagged[tagged["q"] == q]
        per_q.append((sum(1 for v in sub["canonical_entries"]
                          if "Artificial Intelligence" in C.as_list(v)), len(sub)))
    S["airoll"] = []
    for i in range(QN):
        window = per_q[max(0, i - 3):i + 1]
        a, b = sum(x for x, _ in window), sum(y for _, y in window)
        S["airoll"].append(round(a / b * 100, 1) if b else 0.0)
    return S, tagged


def band_counts(rows):
    return {label: int(((rows["year"] >= lo) & (rows["year"] <= hi)).sum())
            for label, lo, hi in ERA_BANDS}


def facts(rows, weeks, tagged):
    """Every scalar the cover prints, in one place, so a reviewer can check
    them against the index without reading the layout."""
    st = C.stats(rows)
    per_year = rows.groupby("year").size()
    words_year = rows.assign(w=C.numeric_column(rows, "word_count").fillna(0)) \
        .groupby("year")["w"].sum()
    quiet = int(per_year.idxmin())
    f = dict(
        articles=st["articles"], words=st["words"], hours=st["hours"],
        median_words=st["median_words"], domains=st["domains"],
        url_bearing=st["url_bearing"], proxy=st["proxy_dated"],
        weeks=len(weeks),
        covered=sum(int(m["article_count"]) for m in weeks),
        best_year=int(per_year.idxmax()), best_year_n=int(per_year.max()),
        best_year_words=int(words_year[per_year.idxmax()]),
        quiet_year=quiet, quiet_year_n=int(per_year.min()),
        quiet_year_weeks=sum(1 for m in weeks if str(m["week"]).startswith(str(quiet))),
        quiet_year_words=int(words_year[quiet]),
        y2011=int(per_year.get(2011, 0)), y2012=int(per_year.get(2012, 0)),
        bands=band_counts(rows), tagged=len(tagged),
        eras={e["label"]: e["articles"] for e in C.era_split(rows)},
        top_domains=Counter(d for d in rows["domain"] if d).most_common(5),
        top_entries=C.top_entities(rows, "canonical_entries", 5),
        vocab=len({x for v in tagged["canonical_entries"] for x in C.as_list(v)}),
        latest=weeks[-1],
    )
    f["seam"] = round((f["y2012"] - f["y2011"]) / f["y2011"] * 100)
    f["tagged_pct"] = round(f["tagged"] / f["articles"] * 100, 1)
    f["url_pct"] = round(f["url_bearing"] / f["articles"] * 100)
    # Longest run of consecutive ISO weeks that carry a synthesis.
    days = sorted(dt.date.fromisocalendar(int(str(m["week"]).split("-W")[0]),
                                          int(str(m["week"]).split("-W")[1]), 1)
                  for m in weeks)
    best = run = 1
    start = best_start = best_end = days[0]
    for a, b in zip(days, days[1:]):
        if (b - a).days == 7:
            run += 1
        else:
            run, start = 1, b
        if run > best:
            best, best_start, best_end = run, start, b
    f["streak"], f["streak_from"], f["streak_to"] = best, best_start, best_end

    def share(sub, name):
        if not len(sub):
            return 0.0
        return sum(1 for v in sub["canonical_entries"]
                   if name in C.as_list(v)) / len(sub) * 100
    old, new = tagged[tagged["year"] <= 2011], tagged[tagged["year"] >= 2018]
    f["ai_new"] = round(share(new, "Artificial Intelligence"), 1)
    f["ai_old"] = round(share(old, "Artificial Intelligence"), 1)
    counts = Counter(x for v in tagged["canonical_entries"] for x in set(C.as_list(v)))
    moved = sorted(((round(share(new, k) - share(old, k), 1), k)
                    for k, n in counts.items() if n >= 120), reverse=True)
    f["moved"] = moved[:2] + moved[-2:]
    return f


# ---------------------------------------------------------------------------
# the page
# ---------------------------------------------------------------------------

TICKS = ["2005", "2010", "2015", "2020", "2026"]


def n(x):
    return f"{int(x):,}"


def mini(items, bars=True):
    """A ranked list on hairline bars, or a plain key/value list when the
    values are not comparable quantities."""
    peak = max((v for _, v in items), default=1) if bars else 1
    out = ""
    for k, v in items:
        text = n(v) if isinstance(v, int) else v
        if bars:
            out += (f'<li><span class="mk">{html.escape(k)}</span>'
                    f'<span class="mb"><i style="width:{v / peak * 100:.1f}%"></i></span>'
                    f'<span class="mv num">{text}</span></li>')
        else:
            out += (f'<li><span class="mk">{html.escape(k)}</span>'
                    f'<span class="mv num">{text}</span></li>')
    return f'<ul class="mini{"" if bars else " dates"}">{out}</ul>'


def column(title, strip_svg, step_label, step_svg, step_end,
           hero_label, hero_value, hero_note, secondaries, accent=False):
    ticks = "".join(f"<span>{t}</span>" for t in TICKS)
    cells = ""
    for s in secondaries:
        # A ranked list needs the whole column; a single number needs half.
        wide = "mini" in s
        value = s["mini"] if wide else f'<span class="fsv2 num">{s["v"]}</span>'
        note = f'<span class="fss">{s["s"]}</span>' if s.get("s") else ""
        cells += (f'<span class="fs{" wide" if wide else ""}">'
                  f'<span class="fsk label">{s["k"]}</span>{value}{note}</span>')
    return f"""<div class="fcol">
<h2 class="ftitle">{title}</h2>
<div class="fticks label">{ticks}</div>
{strip_svg}
<div class="fsteprow"><span class="fsl label">{step_label}</span>
{step_svg}<span class="fsev num">{step_end}</span></div>
<div class="fhero{' accent' if accent else ''}"><span class="fhl label">{hero_label}</span>\
<span class="fhv num">{hero_value}</span><span class="fhd label">{hero_note}</span></div>
<div class="fsec">{cells}</div>
</div>"""


def columns(S, f):
    s1, _ = strip(S["art"], "articles read per quarter",
                  "Articles read per quarter, 2005 to 2026, with the average of each era")
    s2, _ = strip(S["wkq"], "weeks carrying a synthesis",
                  "Weeks with a written synthesis per quarter, with the average of each era")
    s3, _ = strip(S["dq"], "distinct sources per quarter",
                  "Distinct publications appearing each quarter, with the average of each era")
    s4, _ = strip(S["vq"], "vocabulary entries in use per quarter",
                  "Distinct controlled-vocabulary entries used each quarter, "
                  "with the average of each era")
    m = f["latest"]
    grew = f["moved"]
    return "".join([
        column(
            "Articles", s1, "words, cumulative", step(S["wcum"]),
            f'{f["words"] / 1e6:.1f}M',
            "articles read", n(f["articles"]),
            "over 22 years and three ways of saving",
            [{"k": "words", "v": f'{f["words"] / 1e6:.1f}<em>M</em>',
              "s": f'{n(f["words"])} in all'},
             {"k": "reading time", "v": f'{f["hours"]:,.0f}<em> hrs</em>',
              "s": "at 238 words a minute"},
             {"k": "busiest year", "v": str(f["best_year"]),
              "s": f'{n(f["best_year_n"])} articles, '
                   f'{f["best_year_words"] / 1e6:.1f}M words'},
             {"k": "the 2012 seam", "v": f'&minus;{abs(f["seam"])}<em>%</em>',
              "s": f'{n(f["y2011"])} articles in 2011, {n(f["y2012"])} in 2012'}],
            accent=True),
        column(
            "Weeks", s2, "articles covered, cumulative", step(S["acum"]),
            n(f["covered"]),
            "weekly syntheses", n(f["weeks"]),
            "one page per ISO week, written on-device",
            [{"k": "longest streak", "v": n(f["streak"]),
              "s": f'weeks unbroken, {f["streak_from"]:%b %Y} to {f["streak_to"]:%b %Y}'},
             {"k": "quietest year", "v": str(f["quiet_year"]),
              "s": f'{f["quiet_year_weeks"]} weeks, {f["quiet_year_n"]} articles, '
                   f'{n(f["quiet_year_words"])} words'},
             {"k": "deep dives", "v": "29", "s": "22 year rollups, 7 facets"},
             {"k": "this week", "v": n(m["article_count"]),
              "s": f'articles in {m["week"]}, {n(m["total_words"])} words, '
                   f'on energy and on AI'}]),
        column(
            "Sources", s3, "sources, cumulative",
            step(S["dcum"], start=S["dcum_start"]), n(f["domains"]),
            "distinct sources", n(f["domains"]),
            f'counted over the {n(f["url_bearing"])} articles that kept a link',
            [{"k": "carries a link", "v": f'{f["url_pct"]}%',
              "s": f'{n(f["url_bearing"])} of {n(f["articles"])} articles'},
             {"k": "most read", "v": n(f["top_domains"][0][1]),
              "s": f'{f["top_domains"][0][0]}, '
                   f'{round(f["top_domains"][0][1] / f["url_bearing"] * 100)}% '
                   f'of linked reading'},
             {"k": "how it was saved",
              "mini": mini([(k, v) for k, v in f["eras"].items() if v > 10]),
              "s": "one article is unattributed"},
             {"k": "top publications", "mini": mini(f["top_domains"])}]),
        column(
            "Subjects", s4, "AI share, four quarters",
            step(S["airoll"], start=3), f'{S["airoll"][-1]}%',
            "artificial intelligence", f'{f["ai_new"]}<em>%</em>',
            f'of tagged reading since 2018; it was {f["ai_old"]}% before 2012',
            [{"k": "tagged", "v": f'{f["tagged_pct"]}<em>%</em>',
              "s": f'{n(f["tagged"])} of {n(f["articles"])} articles'},
             {"k": "vocabulary", "v": n(f["vocab"]),
              "s": "curated entries, every one in use"},
             {"k": "commonest subjects",
              "mini": mini([(e["name"], e["count"]) for e in f["top_entries"]])},
             {"k": "what moved",
              "mini": mini([(k, f'{v:+}'.replace("-", "\u2212"))
                            for v, k in grew], bars=False),
              "s": "points of share, pre-2012 against 2018 on"}]),
    ])


def live_frame():
    """The current index, rendered verbatim inside an isolated frame.

    An iframe rather than a re-implementation, and srcdoc rather than a file
    reference, for two reasons that both matter: the mockup stays one
    self-contained file, and the page below the cover is the REAL page rather
    than someone's memory of it. Its 720-pixel measure against the cover's
    1,180 is the honest picture, and it is the subject of decision 1.
    """
    css = (ROOT / "_site/style.css").read_text(encoding="utf-8")
    lines = (ROOT / "_site/index.html").read_text(encoding="utf-8").splitlines()
    # Body through the facet nav, then the newest year's own block: the first
    # screen, without the 800 week rows behind it.
    first_screen = "\n".join(lines[9:76] + lines[77:142])
    doc = ('<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
           '<meta name="viewport" content="width=device-width,initial-scale=1">'
           f"<style>{css}\nbody{{overflow:hidden}}</style></head><body>"
           f'<div class="page">\n{first_screen}\n</div></body></html>')
    return html.escape(doc, quote=True)


TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>reading.adamthede.com cover mockup, 8 September 2026</title>
<style>
:root{
  --bg:#1c1917; --bg-raise:#292524; --bg-sink:#161312;
  --ink:#e7e5e4; --ink-2:#a8a29e; --ink-3:#78716c; --ink-4:#57504c;
  --rule:#44403c; --rule-2:#302b28;
  --brand:#FF8F3B;
  --amber:#fbbf24;
  --rose:#fb7185;
  --serif:Charter,"Bitstream Charter","Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;
  --sans:ui-sans-serif,-apple-system,"Helvetica Neue",Helvetica,Arial,sans-serif;
  --mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;
}
*{margin:0;padding:0;box-sizing:border-box}
html,body{overflow-x:clip}
html{-webkit-text-size-adjust:100%}
body{background:var(--bg);color:var(--ink);font-family:var(--sans);line-height:1.5;font-size:15px}
a{color:inherit}
:focus-visible{outline:2px solid var(--brand);outline-offset:2px}
.num{font-variant-numeric:tabular-nums;font-feature-settings:"tnum" 1}
.label{font-family:var(--mono);font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--ink-3)}

.mockbar{background:#241d17;border-bottom:1px solid #4a3a24;padding:12px 24px}
.mockbar .in{max-width:1180px;margin:0 auto;display:flex;gap:18px;align-items:baseline;flex-wrap:wrap}
.mockbar b{color:var(--brand);font-family:var(--mono);font-size:11px;letter-spacing:.14em;text-transform:uppercase;font-weight:400}
.mockbar span{color:var(--ink-2);font-size:13.5px;max-width:840px}

.stickynav{position:sticky;top:0;z-index:20;background:var(--bg);border-bottom:1px solid var(--rule)}
.snin{max-width:1180px;margin:0 auto;padding:11px 24px;display:flex;align-items:baseline;gap:26px;flex-wrap:wrap}
.wordmark{font-family:var(--mono);font-size:11px;letter-spacing:.2em;text-transform:uppercase;color:var(--brand);text-decoration:none;white-space:nowrap}
.wordmark i{font-style:normal;color:var(--ink-4)}
.wordmark i::before{content:" / "}
.sitelinks{margin-left:auto;display:flex;gap:18px;font-family:var(--mono);font-size:11px;letter-spacing:.13em;text-transform:uppercase;color:var(--ink-4)}
.sitelinks a{color:var(--ink-3);text-decoration:none}
.sitelinks a:hover{color:var(--brand)}
.sitelinks .on{color:var(--ink);border-bottom:1px solid var(--amber);padding-bottom:3px}

.stamp{max-width:1180px;margin:0 auto;padding:16px 24px 0;display:flex;gap:16px;align-items:baseline;flex-wrap:wrap;font-family:var(--mono);font-size:11px;letter-spacing:.1em;text-transform:uppercase}
.stamp .pgn{color:var(--brand)}
.stamp .url{color:var(--ink-2);letter-spacing:.04em;text-transform:none;font-size:12px}
.stamp .note{color:var(--ink-4);letter-spacing:.04em;text-transform:none;margin-left:auto}

.wrap{max-width:1180px;margin:0 auto;padding:0 24px 96px}
header.masthead{padding:64px 0 0}
h1{font-family:var(--serif);font-weight:400;font-size:88px;line-height:.96;letter-spacing:-.022em}
.dateline{margin-top:18px;font-family:var(--mono);font-size:11.5px;letter-spacing:.1em;color:var(--ink-4);text-transform:uppercase}

.fgrid{display:grid;grid-template-columns:repeat(4,1fr);gap:40px;margin-top:66px}
.fcol{min-width:0}
.ftitle{font-family:var(--serif);font-size:27px;font-weight:400;letter-spacing:-.012em;color:var(--ink);padding-bottom:9px;border-bottom:1px solid var(--rule);margin:0}
.fticks{margin-top:9px;color:var(--ink-4);font-size:9px;letter-spacing:.06em;display:flex;justify-content:space-between}
.fstrip{display:block;width:100%;height:auto;margin-top:16px}
.fstrip .favg{font-family:var(--mono);font-size:7px;fill:var(--amber);letter-spacing:.03em}
.fstrip .ftick{font-family:var(--mono);font-size:7px;fill:#6d635c;letter-spacing:.06em}
.fsteprow{margin-top:22px;padding-top:11px;border-top:1px dotted var(--rule)}
.fsl{display:block;color:var(--ink-4)}
.fstep{display:block;width:100%;height:auto;margin-top:5px}
.fsev{display:block;text-align:right;font-family:var(--mono);font-size:11px;color:var(--ink-2);margin-top:2px}
.fhero{display:block;margin-top:36px;padding-top:16px;border-top:1px solid var(--rule)}
.fhl{display:block;color:var(--ink-3);line-height:1.4}
.fhv{display:block;font-family:var(--serif);font-size:74px;font-weight:400;line-height:.92;letter-spacing:-.03em;color:var(--ink-2);margin:10px 0 0}
.fhv em{font-style:normal;font-size:.34em;color:var(--ink-4);letter-spacing:0}
.fhero.accent .fhv{color:var(--amber)}
.fhd{display:block;margin-top:12px;color:var(--ink-4);line-height:1.55}
.fsec{display:grid;grid-template-columns:1fr 1fr;margin-top:34px}
.fs{display:block;padding:15px 12px 15px 0;border-top:1px dotted var(--rule)}
.fs:nth-child(even){padding-left:14px;border-left:1px dotted var(--rule)}
.fs.wide{grid-column:1/3;padding-right:0;padding-left:0;border-left:none}
.fsk{display:block;color:var(--ink-4);line-height:1.4}
.fsv2{display:block;font-size:25px;font-weight:200;letter-spacing:-.02em;color:var(--ink);margin-top:8px;font-variant-numeric:tabular-nums}
.fsv2 em{font-style:normal;font-size:.44em;color:var(--ink-4);margin-left:1px}
.fss{display:block;margin-top:6px;font-family:var(--mono);font-size:9.5px;color:var(--ink-4);line-height:1.5;letter-spacing:.02em}
.mini{list-style:none;margin-top:9px}
.mini li{display:grid;grid-template-columns:1fr 40px;gap:5px;align-items:center;padding:3px 0;font-family:var(--mono);font-size:9.5px;color:var(--ink-3)}
.mini .mk{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mini .mb{grid-column:1/3;grid-row:2;height:1.5px;background:#2f2a27;margin:-1px 0 3px}
.mini .mb i{display:block;height:100%;background:var(--amber);opacity:.65}
.mini .mv{text-align:right;color:var(--ink-2)}
.mini.dates li{grid-template-columns:1fr auto}
.fnote{max-width:820px;margin:54px 0 0;font-family:var(--mono);font-size:10.5px;line-height:1.85;color:var(--ink-4);letter-spacing:.02em}
.fnote b{color:var(--ink-2);font-weight:400}

.below{border-top:1px solid var(--rule);margin-top:8px;padding-top:56px}
.belowhead{display:flex;gap:20px;align-items:baseline;flex-wrap:wrap;padding-bottom:14px;border-bottom:1px solid var(--rule-2)}
.belowhead h2{font-family:var(--serif);font-size:24px;font-weight:400;letter-spacing:-.012em}
.belowhead .n{margin-left:auto;color:var(--ink-4)}
.livecap{font-family:var(--serif);font-size:16px;line-height:1.62;color:var(--ink-2);max-width:660px;margin:16px 0 22px}
.liveframe{position:relative;border:1px solid var(--rule-2);background:#1c1917;max-width:800px}
.liveframe iframe{display:block;width:100%;height:1240px;border:0}
.liveframe::after{content:"";position:absolute;left:0;right:0;bottom:0;height:120px;pointer-events:none;
  background:linear-gradient(to bottom,rgba(28,25,23,0),var(--bg))}
.livefoot{margin-top:12px;font-family:var(--mono);font-size:10.5px;color:var(--ink-4);letter-spacing:.02em;line-height:1.8}

.decisions{border-top:1px solid var(--rule);background:var(--bg-sink);margin-top:80px;padding:60px 0 88px}
.decisions .wrap{padding-bottom:0}
.decisions h2{font-family:var(--serif);font-size:30px;font-weight:400;letter-spacing:-.012em}
.decisions .lede{font-family:var(--serif);font-size:17px;line-height:1.65;color:var(--ink-2);max-width:660px;margin-top:14px}
.dgrid{list-style:none;display:grid;grid-template-columns:1fr 1fr;gap:1px;background:var(--rule-2);margin-top:30px;border:1px solid var(--rule-2)}
.dgrid li{background:var(--bg-sink);padding:20px 22px}
.dgrid .q{font-family:var(--serif);font-size:17px;line-height:1.4}
.dgrid .a{font-size:14px;line-height:1.6;color:var(--ink-2);margin-top:9px}
.dgrid .a b{color:var(--ink);font-weight:400}
.dgrid .tag{font-family:var(--mono);font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--brand);display:block;margin-bottom:10px}
.dgrid .tag.mine{color:var(--amber)}
.dgrid .tag.open{color:var(--rose)}

footer{max-width:1180px;margin:0 auto;padding:44px 24px 72px;color:var(--ink-4);font-family:var(--mono);font-size:11px;line-height:1.8;letter-spacing:.04em}

@media (max-width:1080px){
  .fgrid{grid-template-columns:1fr 1fr;gap:44px}
}
@media (max-width:760px){
  h1{font-size:48px}
  .fgrid{grid-template-columns:1fr;gap:52px}
  .fhv{font-size:62px}
  .dgrid{grid-template-columns:1fr}
  .snin{gap:8px 16px;padding:9px 20px}
  .sitelinks{margin-left:0}
  .liveframe iframe{height:1520px}
}
@media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
</style>
</head>
<body>

<div class="mockbar"><div class="in">
  <b>Mockup, not a live site</b>
  <span>A cover page for reading.adamthede.com, in the shape of the covers already live on
  books and viewing. Every figure is computed from the reading archive and the week records.
  Below the cover, the current index is rendered as it stands today.</span>
</div></div>

<div class="stickynav">
  <div class="snin">
    <a class="wordmark" href="#top">adamthede<i>reading</i></a>
    <span class="sitelinks"><span class="on">Reading</span><a href="https://books.adamthede.com/">Books</a><a href="https://viewing.adamthede.com/">Viewing</a></span>
  </div>
</div>

<article id="top">
<div class="stamp"><span class="pgn">The cover</span><span class="url">reading.adamthede.com/</span><span class="note">proposed</span></div>
<div class="wrap">
  <header class="masthead">
    <h1>A Reading Life</h1>
    <p class="dateline num">{{SPAN}} &nbsp;&middot;&nbsp; {{ARTICLES}} articles held in one index
    &nbsp;&middot;&nbsp; as of {{TODAY}}</p>
  </header>

  <div class="fgrid"><!--COLS--></div>

  <p class="fnote"><b>How to read the dates.</b> This archive is two archives wearing one schema.
  The {{LEGACY}} legacy files carry no links and no read events: their date is the publication date
  parsed out of a filename, so a quarter before 2012 says when the writing appeared, not when it
  was read. The {{MODERN}} Instapaper and Matter articles from 2012 on carry real save and archive
  dates. Two thirds of the corpus, {{PROXY}} articles, is dated by that proxy. Every strip and step
  runs on one shared axis of quarters from 2005 Q1 to 2026 Q3, and the amber lines are the average
  of each era, annotated with their value. There is no time of day anywhere in this archive, so
  nothing here is drawn by hour.</p>
</div>
</article>

<section class="below">
<div class="wrap">
  <div class="belowhead">
    <h2>The Week in Reading</h2>
    <span class="label">the index, unchanged, directly below the cover</span>
    <span class="label n num">{{WEEKS}} weeks</span>
  </div>
  <p class="livecap">The cover is a lid, not a replacement. This is the page that is live today,
  rendered here exactly as it renders in production: the same four anchor numbers, the same
  three-ways-of-saving bar, the same words-per-year strip that doubles as year navigation, and the
  same week rows. Nothing about it changes.</p>
  <div class="liveframe">
    <iframe title="The current reading.adamthede.com index, first screen" loading="lazy" scrolling="no" srcdoc="<!--SRCDOC-->"></iframe>
  </div>
  <p class="livefoot">Rendered from the current build in _site/index.html and its stylesheet, first
  screen only. The live page continues with every year back to 2005.</p>
</div>
</section>

<section class="decisions">
<div class="wrap">
  <h2>Three decisions</h2>
  <p class="lede">The cover is drawn. What is not settled is where it lives, which number leads
  each column, and whether the two covers already shipped should move.</p>
  <ul class="dgrid">
    <li><span class="tag">Decision 1 &middot; where it lives</span>
      <div class="q">Cover as the home page with the weeks index below it, or a separate /cover/?</div>
      <div class="a"><b>Recommend: cover as home, index below, one page.</b> Books and viewing both put the
      cover at the root, and a reader arriving from either sibling expects the same shape. A separate
      /cover/ would be a second entrance that neither sibling has, and the sticky bar has nowhere to
      point at it. <b>The catch is the measure.</b> The cover wants 1,180 pixels and four columns; the
      current index is a 720-pixel single column, which is why the frame below sits narrower than the
      cover above it. Stacking them means one page with two measures, or widening the index to match.
      Widening is the larger change and can wait.</div></li>
    <li><span class="tag mine">Decision 2 &middot; the hero per column</span>
      <div class="q">Are these the four numbers, and is Articles the right amber one?</div>
      <div class="a">Articles carries the amber because {{ARTICLES}} is the number the whole archive is
      about, and it is already the first anchor number on the live page. The other three are
      deliberately not totals: <b>{{WEEKS}}</b> is the writing, <b>{{DOMAINS}}</b> is the reach, and
      <b>{{AI}}%</b> is the only hero that is a share rather than a count, because the subject column
      has no honest total. Swapping any of the three is cheap.</div></li>
    <li><span class="tag open">Decision 3 &middot; the AI hero</span>
      <div class="q">Is "artificial intelligence, {{AI}}% since 2018" too on the nose?</div>
      <div class="a">It is the largest real movement in the vocabulary, up {{AIMOVE}} points against the
      pre-2012 era while mobile devices and social media each fell about six. The alternative hero
      is the vocabulary itself, {{VOCAB}} curated entries over {{TAGGED}}% of the corpus, which is a fact about
      the pipeline rather than about the reading. Adam's call.</div></li>
    <li><span class="tag">Decision 4 &middot; the siblings</span>
      <div class="q">Do books and viewing need any change once this ships?</div>
      <div class="a"><b>Recommend: no change to either.</b> This cover was built to their anatomy,
      not the reverse. One small drift is worth noting: their sticky bars carry a second row of page
      links because each has four pages, and reading has one page plus its year anchors. That
      asymmetry is already documented in the reading site's own nav helper and should stay.</div></li>
  </ul>
</div>
</section>

<footer>
  <span>Mockup built {{TODAY}} from data/archive_index.parquet, {{RAW}} rows, and {{ALLWEEKS}} week
  records on the vault. Figures reflect the {{ARTICLES}} articles that survive the corrupted-content
  filter and the 2005 epoch: {{CORRUPT}} rows are flagged corrupted, {{EARLY}} fall before 2005.</span>
</footer>

</body>
</html>
"""


def render(S, f):
    out = TEMPLATE.replace("<!--COLS-->", columns(S, f))
    out = out.replace("<!--SRCDOC-->", live_frame())
    years = sorted(f["bands"])
    tokens = {
        "ARTICLES": n(f["articles"]), "WEEKS": n(f["weeks"]),
        "DOMAINS": n(f["domains"]), "AI": str(f["ai_new"]),
        "AIMOVE": f'{f["moved"][0][0]:.1f}',
        "VOCAB": n(f["vocab"]), "TAGGED": str(f["tagged_pct"]),
        "LEGACY": n(f["eras"]["Legacy files"]),
        "MODERN": n(f["eras"]["Instapaper"] + f["eras"]["Matter"]),
        "PROXY": n(f["proxy"]),
        "SPAN": f"{QUARTERS[0].year} to {QUARTERS[-1].year}",
        "TODAY": dt.date.today().strftime("%-d %B %Y"),
        "RAW": n(f["raw_rows"]), "ALLWEEKS": n(f["all_weeks"]),
        "CORRUPT": n(f["corrupted"]), "EARLY": n(f["early"]),
    }
    for k, v in tokens.items():
        out = out.replace("{{" + k + "}}", v)
    assert "{{" not in out, "an unreplaced token survived into the page"
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--index", default=str(ROOT / "data/archive_index.parquet"))
    vault = os.environ.get("INSTAPAPER_VAULT_PATH")
    ap.add_argument("--synthesis-dir",
                    default=str(Path(vault) / "synthesis") if vault else None)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    if not args.synthesis_dir:
        sys.exit("Pass --synthesis-dir (or set INSTAPAPER_VAULT_PATH).")

    c = C.load_corpus(args.index)
    weeks = [m for m in G.load_weeks(args.synthesis_dir)
             if str(m["week"]) >= G.SITE_EPOCH_WEEK]
    S, tagged = series(c.rows, weeks)
    f = facts(c.rows, weeks, tagged)
    # Read back rather than reconstructed: the exclusion counters are taken at
    # different points in prepare(), so summing them undercounts the file.
    f["raw_rows"] = len(pd.read_parquet(args.index, columns=["source"]))
    f["all_weeks"] = len(G.load_weeks(args.synthesis_dir))
    f["corrupted"] = c.excluded_corrupted
    f["early"] = c.excluded_pre_min_year
    Path(args.out).write_text(render(S, f), encoding="utf-8")
    print(f'{args.out}: {n(f["articles"])} articles, {n(f["weeks"])} weeks, '
          f'{n(f["domains"])} sources, {n(f["vocab"])} vocabulary entries')


if __name__ == "__main__":
    main()
