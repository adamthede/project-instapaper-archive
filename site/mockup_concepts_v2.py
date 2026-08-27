#!/usr/bin/env python3
"""Three Data-as-Design treatments of the curated vocabulary, for comparison.

The first mockup was a reference list: correct, and not a visualization of
anything. These three each answer a DIFFERENT question with a different form,
and each uses the whole 22-year × 248-entry surface the curation bought.

  A. THE CASCADE      change over time — 248 ridges, sorted by peak year
  B. ERA FINGERPRINTS small multiples — one panel per year, 22 of them
  C. THE COLLAPSE     magnitude/hierarchy — the curation itself as data

Palette: one sequential amber ramp, five steps, validated against the site's
stone-900 surface — monotonic in lightness, every step >= 3:1 contrast, and
zero renders as bare surface rather than a sixth colour. Single hue throughout,
so there is no categorical identity to confuse and nothing for CVD to collapse.

    python site/mockup_concepts_v2.py     # -> docs/mockups/concepts-v2.html
"""
import collections
import html
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "site"))

import corpus  # noqa: E402

OUT = REPO_ROOT / "docs" / "mockups" / "concepts-v2.html"
INDEX = REPO_ROOT / "data" / "archive_index.parquet"
TAXONOMY = REPO_ROOT / "data" / "taxonomy" / "v1.yaml"

e = html.escape
RAMP = ["#8a6a11", "#a37a12", "#c99a10", "#e0ac1a", "#fbbf24"]


def n(x):
    return f"{x:,}"


def step(v, vmax):
    """Sequential bucket. 0 is not a colour — it is the absence of a mark."""
    if v <= 0:
        return None
    frac = v / vmax if vmax else 0
    for i, edge in enumerate((0.08, 0.22, 0.45, 0.72)):
        if frac <= edge:
            return RAMP[i]
    return RAMP[4]


def gather():
    c = corpus.load_corpus(str(INDEX))
    rows = c.rows
    tax = yaml.safe_load(TAXONOMY.read_text())
    defs = {x["name"]: x["definition"] for x in tax["entries"]}
    alias_n = {x["name"]: len(x["aliases"]) for x in tax["entries"]}

    per = collections.defaultdict(collections.Counter)
    totals = collections.Counter()
    per_year_top = collections.defaultdict(collections.Counter)
    year_articles = collections.Counter()

    for _, r in rows.iterrows():
        y = int(r["year"])
        year_articles[y] += 1
        ents = set(r["canonical_entries"])
        for name in ents:
            per[name][y] += 1
            totals[name] += 1
            per_year_top[y][name] += 1

    years = sorted(year_articles)
    return dict(rows=rows, defs=defs, alias_n=alias_n, per=per, totals=totals,
                per_year_top=per_year_top, years=years,
                year_articles=year_articles, tax=tax)


# --------------------------------------------------------------------------
# A. THE CASCADE
# --------------------------------------------------------------------------
def cascade(d, limit=60):
    years, per, totals = d["years"], d["per"], d["totals"]
    ranked = [nm for nm, _ in totals.most_common(limit)]

    def peak(nm):
        s = per[nm]
        return (max(s, key=lambda y: s[y]), -totals[nm])

    ranked.sort(key=peak)
    cols = "".join(f"<div class='cy'>{y % 100:02d}</div>" for y in years)
    out = ""
    for nm in ranked:
        s = per[nm]
        vmax = max(s.values())
        pk = max(s, key=lambda y: s[y])
        cells = ""
        for y in years:
            v = s.get(y, 0)
            col = step(v, vmax)
            style = f"background:{col}" if col else ""
            tip = f"{nm} · {y} · {v} article{'' if v == 1 else 's'}"
            cells += f"<i class='cc' style='{style}' data-tip='{e(tip)}'></i>"
        out += (f"<div class='crow'><span class='cn'>{e(nm)}</span>"
                f"<span class='cgrid'>{cells}</span>"
                f"<span class='cpk'>{pk}</span></div>")
    return f"""
  <section class="concept">
    <div class="ckicker">Concept A</div>
    <h2>The Cascade</h2>
    <p class="clede">Every entry as a 22-year density ridge, ordered by the year it
    peaked. Read top to bottom and the page becomes a timeline of attention: the
    iPod era gives way to the crisis, then to social, then to AI. This is the
    cross-era comparison the whole vocabulary was built to make possible —
    “what did I read about in 2012 versus 2024” answered by shape, not by query.</p>
    <div class="legend"><span class="lg-l">fewer</span>
      {''.join(f"<i style='background:{c}'></i>" for c in RAMP)}
      <span class="lg-l">more articles that year</span>
      <span class="lg-note">· intensity is per-entry, so a small entry's peak is
      as visible as a large one's</span></div>
    <div class="cascade">
      <div class="crow chead"><span class="cn"></span><span class="cgrid">{cols}</span>
        <span class="cpk">peak</span></div>
{out}
    </div>
    <div class="note">Top {limit} entries by article count, of {n(len(totals))}.</div>
  </section>"""


# --------------------------------------------------------------------------
# B. ERA FINGERPRINTS
# --------------------------------------------------------------------------
def fingerprints(d, per_panel=6):
    years, per_year_top, year_articles = d["years"], d["per_year_top"], d["year_articles"]
    panels = ""
    for y in years:
        top = per_year_top[y].most_common(per_panel)
        if not top:
            continue
        vmax = top[0][1]
        bars = ""
        for nm, v in top:
            w = max(v / vmax * 100, 3)
            bars += (f"<div class='fb' data-tip='{e(f"{nm} · {y} · {v} articles")}'>"
                     f"<span class='fbn'>{e(nm)}</span>"
                     f"<span class='fbt'><i style='width:{w:.0f}%'></i></span></div>")
        panels += (f"<div class='fpanel'><div class='fy'>{y}</div>"
                   f"<div class='fa'>{n(year_articles[y])} articles</div>{bars}</div>")
    return f"""
  <section class="concept">
    <div class="ckicker">Concept B</div>
    <h2>Era Fingerprints</h2>
    <p class="clede">One panel per year, each showing what that year was actually
    about. Felton's annual-report idiom applied to 22 years at once: the panels are
    identical in structure, so the differences between them are the whole content.
    Scan the grid and the shape of a decade is legible before you read a single word.</p>
    <div class="fgrid">{panels}</div>
    <div class="note">Top {per_panel} entries per year by article count. Bar length
      is relative to that year's leader, so each panel is read on its own terms —
      a quiet year is not flattened by a loud one.</div>
  </section>"""


# --------------------------------------------------------------------------
# C. THE COLLAPSE
# --------------------------------------------------------------------------
def collapse(d, limit=18):
    alias_n, totals, defs = d["alias_n"], d["totals"], d["defs"]
    tax = d["tax"]
    excluded = len(tax.get("excluded_aliases") or [])
    ranked = sorted(alias_n.items(), key=lambda kv: -kv[1])[:limit]
    amax = ranked[0][1]
    rows_html = ""
    for nm, cnt in ranked:
        w = cnt / amax * 100
        arts = totals.get(nm, 0)
        rows_html += (
            f"<div class='krow' data-tip='{e(f"{nm}: {cnt} strings folded into 1 entry")}'>"
            f"<span class='kn'>{e(nm)}</span>"
            f"<span class='kbar'><i style='width:{w:.1f}%'></i>"
            f"<em>{cnt} strings</em></span>"
            f"<span class='ka num'>{n(arts)}</span></div>")
    return f"""
  <section class="concept">
    <div class="ckicker">Concept C</div>
    <h2>The Collapse</h2>
    <p class="clede">The curation itself, as the subject. A model reading 22 years of
    articles invented <strong>73,099</strong> different ways to name things —
    three quarters of them used exactly once. Clustering and one afternoon of human
    judgement reduced that to <strong>248</strong>. This page shows the reduction:
    how many distinct phrasings each surviving entry swallowed.</p>
    <div class="funnel">
      <div class="fstage"><b>73,099</b><span>raw strings from enrichment</span>
        <i style="width:100%"></i></div>
      <div class="fstage"><b>54,226</b><span>clusters after embedding</span>
        <i style="width:74.2%"></i></div>
      <div class="fstage"><b>250</b><span>reviewed at the curation gate</span>
        <i style="width:0.34%"></i></div>
      <div class="fstage lead"><b>248</b><span>entries in the vocabulary
        · {excluded} strings cut on purpose</span><i style="width:0.34%"></i></div>
    </div>
    <div class="label viz-title" style="margin-top:26px">What each entry absorbed</div>
    <div class="collapse">{rows_html}</div>
    <div class="note">Left bar: distinct source strings folded into that entry.
      Right number: articles it reaches. “Mobile Devices” absorbed 105 spellings —
      Smartphones, Mobile Apps, Android, Tablets, Feature Phones, Touchscreen — that
      no ranking could previously see as one thing.</div>
  </section>"""


STYLE = """
:root{--bg:#1c1917;--bg-raise:#292524;--ink:#e7e5e4;--rule:#44403c;
 --amber:#fbbf24;--amber-dim:#92700c;--brand:#FF8F3B}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
 font:15px/1.6 ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif;
 -webkit-font-smoothing:antialiased}
.page{max-width:1180px;margin:0 auto;padding:0 28px 90px}
.mockbar{background:var(--brand);color:#1c1917;padding:7px 14px;font-size:12px;
 letter-spacing:.06em;text-transform:uppercase;margin:0 -28px 34px}
h1{font-size:44px;font-weight:300;letter-spacing:-.02em;margin:34px 0 6px}
h2{font-size:30px;font-weight:300;letter-spacing:-.015em;margin:0 0 10px}
.sub{opacity:.55;max-width:74ch;margin-bottom:8px}
.ckicker{font-size:11px;letter-spacing:.14em;text-transform:uppercase;
 color:var(--brand);margin-bottom:6px}
.concept{margin:74px 0 0;padding-top:30px;border-top:1px solid var(--rule)}
.clede{opacity:.62;max-width:76ch;font-size:14px;line-height:1.65}
.note{font-size:12px;opacity:.42;margin-top:14px;max-width:80ch;line-height:1.6}
.label,.viz-title{font-size:11px;letter-spacing:.12em;text-transform:uppercase;opacity:.5}
.num{font-variant-numeric:tabular-nums}

/* legend */
.legend{display:flex;align-items:center;gap:5px;margin:22px 0 12px;font-size:11px}
.legend i{width:26px;height:9px;display:inline-block}
.lg-l{opacity:.45;letter-spacing:.06em;text-transform:uppercase;margin:0 5px}
.lg-note{opacity:.3;margin-left:10px;text-transform:none;letter-spacing:0}

/* A. cascade */
.cascade{margin-top:6px}
.crow{display:grid;grid-template-columns:186px 1fr 42px;align-items:center;
 gap:12px;padding:1px 0}
.crow:hover .cn{opacity:1;color:var(--amber)}
.cn{font-size:12.5px;opacity:.72;text-align:right;overflow:hidden;
 text-overflow:ellipsis;white-space:nowrap}
.cgrid{display:grid;grid-template-columns:repeat(22,1fr);gap:2px}
.cc{height:11px;background:transparent;display:block;border-radius:1px}
.chead .cc,.chead .cy{height:auto}
.cy{font-size:9.5px;opacity:.38;text-align:center;font-variant-numeric:tabular-nums}
.cpk{font-size:10.5px;opacity:.4;font-variant-numeric:tabular-nums}
.chead .cn,.chead .cpk{font-size:9.5px;letter-spacing:.1em;text-transform:uppercase;opacity:.4}

/* B. fingerprints */
.fgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(232px,1fr));
 gap:2px;margin-top:22px}
.fpanel{background:var(--bg-raise);padding:13px 14px 15px}
.fy{font-size:23px;font-weight:300;letter-spacing:-.01em}
.fa{font-size:10px;letter-spacing:.08em;text-transform:uppercase;opacity:.38;
 margin-bottom:11px}
.fb{margin-bottom:6px}
.fbn{display:block;font-size:11.5px;opacity:.68;white-space:nowrap;
 overflow:hidden;text-overflow:ellipsis}
.fbt{display:block;height:4px;background:rgba(255,255,255,.05);margin-top:2px}
.fbt i{display:block;height:100%;background:var(--amber);border-radius:0 2px 2px 0}

/* C. collapse */
.funnel{margin-top:24px}
.fstage{display:grid;grid-template-columns:104px 1fr;align-items:center;
 gap:14px;padding:7px 0;position:relative}
.fstage b{font-size:22px;font-weight:300;font-variant-numeric:tabular-nums;
 text-align:right}
.fstage span{font-size:12px;opacity:.5;grid-column:2}
.fstage i{grid-column:2;height:8px;background:var(--amber-dim);display:block;
 margin-top:3px;min-width:3px}
.fstage.lead i{background:var(--amber)}
.fstage.lead b{color:var(--amber)}
.collapse{margin-top:8px}
.krow{display:grid;grid-template-columns:210px 1fr 62px;align-items:center;
 gap:14px;padding:5px 0;border-top:1px solid var(--rule)}
.kn{font-size:12.5px;opacity:.75;text-align:right}
.kbar{position:relative;height:15px;display:flex;align-items:center}
.kbar i{height:11px;background:var(--amber-dim);border-radius:0 2px 2px 0;display:block}
.kbar em{font-style:normal;font-size:10.5px;opacity:.45;margin-left:8px;
 white-space:nowrap}
.ka{font-size:12px;opacity:.6;text-align:right}

/* tooltip */
[data-tip]{position:relative}
[data-tip]:hover::after{content:attr(data-tip);position:absolute;left:50%;
 bottom:calc(100% + 7px);transform:translateX(-50%);background:#0c0a09;
 color:var(--ink);border:1px solid var(--rule);padding:5px 9px;font-size:11.5px;
 white-space:nowrap;z-index:20;pointer-events:none;border-radius:2px}
.crow [data-tip]:hover::after{white-space:nowrap}
"""


def build():
    d = gather()
    rows = d["rows"]
    body = f"""  <div class="mockbar">Mockups — not live. Three treatments of the same data, for Phase E.</div>
  <h1>Three ways to show a vocabulary</h1>
  <div class="sub">248 curated entries across {n(len(rows))} articles and
    {len(d['years'])} years. The same underlying data in each — the question is
    which one earns the page.</div>
{cascade(d)}
{fingerprints(d)}
{collapse(d)}
"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Vocabulary — three treatments</title><style>{STYLE}</style></head>
<body><div class="page">{body}</div></body></html>""", encoding="utf-8")
    print(f"wrote {OUT}")
    print(f"  {len(d['totals'])} entries · {len(d['years'])} years · {n(len(rows))} articles")


if __name__ == "__main__":
    build()
