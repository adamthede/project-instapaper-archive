#!/usr/bin/env python3
"""Concept D — co-occurrence, in brand orange. An experiment, not a proposal yet.

Two things are being tried at once here, deliberately kept separate in the
output so they can be judged separately:

  1. THE FORM. 11,369 entry pairs co-occur. A node-link network of that is a
     hairball and always is. An ORDERED ADJACENCY MATRIX is the honest form:
     every pair gets a cell, nothing overlaps, and the structure shows up as
     blocks along the diagonal — but ONLY if the rows are ordered well. An
     arbitrary order (alphabetical, or by frequency) shows nothing at all, so
     the ordering is the actual work. See _seriate.

  2. THE COLOUR. Brand orange instead of amber. Same rules as before: one
     sequential hue, five steps, monotonic in lightness, every step >= 3:1
     against the stone-900 surface, zero rendered as bare surface rather than
     a sixth step. Orange sits darker than amber at equal chroma, so three
     candidate ramps failed the contrast floor before this one passed — the
     bottom had to be lifted rather than extended down.

    python site/mockup_cooccurrence.py    # -> docs/mockups/cooccurrence.html
"""
import collections
import html
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "site"))

import corpus  # noqa: E402

OUT = REPO_ROOT / "docs" / "mockups" / "cooccurrence.html"
INDEX = REPO_ROOT / "data" / "archive_index.parquet"

e = html.escape

# Validated 2026-08-26 against surface #1c1917: monotonic, min contrast 3.24:1.
ORANGE = ["#a4551c", "#c26a22", "#e0812c", "#FF8F3B", "#FFB877"]
AMBER = ["#8a6a11", "#a37a12", "#c99a10", "#e0ac1a", "#fbbf24"]

N = 42


def n(x):
    return f"{x:,}"


def step(v, vmax, ramp):
    if v <= 0:
        return None
    frac = v / vmax if vmax else 0
    for i, edge in enumerate((0.06, 0.16, 0.34, 0.62)):
        if frac <= edge:
            return ramp[i]
    return ramp[4]


def _seriate(names, co):
    """Order rows so related entries sit next to each other.

    Greedy nearest-neighbour: start from the most-connected entry, then
    repeatedly append whichever unplaced entry co-occurs most with the one
    just placed. Crude next to spectral ordering, but it is deterministic,
    explainable in a sentence, and enough to pull the blocks out — which is
    the entire point of the form. An unordered matrix of the same data is
    indistinguishable from noise.
    """
    strength = {a: sum(co.get(frozenset((a, b)), 0) for b in names if b != a)
                for a in names}
    order = [max(names, key=lambda x: strength[x])]
    left = set(names) - set(order)
    while left:
        cur = order[-1]
        nxt = max(left, key=lambda b: (co.get(frozenset((cur, b)), 0), strength[b]))
        order.append(nxt)
        left.discard(nxt)
    return order


def build():
    c = corpus.load_corpus(str(INDEX))
    rows = c.rows

    totals = collections.Counter()
    co = collections.Counter()
    for _, r in rows.iterrows():
        ents = sorted(set(r["canonical_entries"]))
        totals.update(ents)
        for i, a in enumerate(ents):
            for b in ents[i + 1:]:
                co[frozenset((a, b))] += 1

    top = [nm for nm, _ in totals.most_common(N)]
    order = _seriate(top, co)
    vmax = max((co.get(frozenset((a, b)), 0)
                for a in order for b in order if a != b), default=1)

    def matrix(ramp, ident):
        head = "".join(
            f"<span class='mh' style='--i:{i}'><b>{e(nm)}</b></span>"
            for i, nm in enumerate(order))
        body = ""
        for a in order:
            cells = ""
            for b in order:
                if a == b:
                    cells += ("<i class='mc self' data-tip='"
                              f"{e(f'{a} · {totals[a]} articles')}'></i>")
                    continue
                v = co.get(frozenset((a, b)), 0)
                col = step(v, vmax, ramp)
                st = f"background:{col}" if col else ""
                tip = (f"{a} + {b} · {v} article{'' if v == 1 else 's'} together"
                       if v else f"{a} + {b} · never together")
                cells += f"<i class='mc' style='{st}' data-tip='{e(tip)}'></i>"
            body += (f"<div class='mrow'><span class='mn'>{e(a)}</span>"
                     f"<span class='mcells'>{cells}</span></div>")
        legend = "".join(f"<i style='background:{x}'></i>" for x in ramp)
        return f"""
      <div class="mwrap" id="{ident}">
        <div class="legend"><span class="lg-l">rarer</span>{legend}
          <span class="lg-l">more articles shared</span></div>
        <div class="mhead"><span class="mn"></span>
          <span class="mcells">{head}</span></div>
{body}
      </div>"""

    pairs = [(sorted(k), v) for k, v in co.most_common(10)]
    plist = "".join(
        f"<div class='prow'><span class='pn'>{e(a)} <em>+</em> {e(b)}</span>"
        f"<span class='pv num'>{n(v)}</span></div>"
        for (a, b), v in pairs)

    body = f"""  <div class="mockbar">Concept D — experiment. Co-occurrence, in brand orange.</div>
  <h1>What travels together</h1>
  <div class="sub">{n(len(co))} pairs of the {n(len(totals))} entries appear in the
    same article at least once. This is the shape of the reading — not what was
    read about, but what was read about <em>at the same time</em>.</div>

  <section class="concept">
    <div class="ckicker">The form</div>
    <h2>An ordered matrix, not a network</h2>
    <p class="clede">A node-link diagram of {n(len(co))} edges is a hairball, always.
    A matrix gives every pair its own cell with nothing overlapping — but only pays
    off if the rows are ordered so related entries sit together. These are seriated
    by greedy nearest-neighbour, so the bright blocks along the diagonal are real
    clusters of your attention: the startup/VC/funding block, the Apple/mobile block,
    the 2008 financial block. Alphabetical order of the same data shows nothing.</p>
{matrix(ORANGE, "m-orange")}
    <div class="note">Top {N} entries by article count. The diagonal is muted — an
      entry always co-occurs with itself, so colouring it would be a bright line of
      no information. Hover any cell for the pair and its count.</div>
  </section>

  <section class="concept">
    <div class="ckicker">The colour experiment</div>
    <h2>Orange against amber</h2>
    <p class="clede">Same matrix, same data, the amber ramp from concepts A and C.
    Worth comparing directly: orange reads hotter and pulls the eye to the dense
    blocks, amber recedes and reads more like a surface. Orange is also the brand
    hue, so it carries more identity — which is either the point or a distraction,
    depending on whether this page should feel like a chart or like the site.</p>
{matrix(AMBER, "m-amber")}
    <div class="note">Three candidate orange ramps were rejected before this one:
      orange sits darker than amber at equal chroma, so their bottom steps fell
      below the 3:1 contrast floor against the stone-900 surface. The fix was
      lifting the floor, not extending the ramp downward. This one is monotonic in
      lightness with every step at 3.24:1 or better.</div>
  </section>

  <section class="concept">
    <div class="ckicker">The headline</div>
    <h2>Strongest pairs</h2>
    <div class="plist">{plist}</div>
    <div class="note">The top three are one story — Entrepreneurship, Venture
      Capital and Startups form a near-solid triangle. That block is the densest
      thing in the matrix and it is 22 years of reading about company-building.</div>
  </section>
"""

    css = """
:root{--bg:#1c1917;--bg-raise:#292524;--ink:#e7e5e4;--rule:#44403c;--brand:#FF8F3B}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
 font:15px/1.6 ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif;
 -webkit-font-smoothing:antialiased}
.page{max-width:1240px;margin:0 auto;padding:0 28px 90px}
.mockbar{background:var(--brand);color:#1c1917;padding:7px 14px;font-size:12px;
 letter-spacing:.06em;text-transform:uppercase;margin:0 -28px 34px}
h1{font-size:44px;font-weight:300;letter-spacing:-.02em;margin:34px 0 6px}
h2{font-size:28px;font-weight:300;letter-spacing:-.015em;margin:0 0 10px}
.sub{opacity:.55;max-width:74ch;margin-bottom:8px}
.ckicker{font-size:11px;letter-spacing:.14em;text-transform:uppercase;
 color:var(--brand);margin-bottom:6px}
.concept{margin:70px 0 0;padding-top:30px;border-top:1px solid var(--rule)}
.clede{opacity:.62;max-width:78ch;font-size:14px;line-height:1.65}
.note{font-size:12px;opacity:.42;margin-top:16px;max-width:82ch;line-height:1.6}
.num{font-variant-numeric:tabular-nums}
.legend{display:flex;align-items:center;gap:5px;margin:24px 0 14px;font-size:11px}
.legend i{width:26px;height:9px;display:inline-block}
.lg-l{opacity:.45;letter-spacing:.06em;text-transform:uppercase;margin:0 5px}

/* matrix */
.mwrap{overflow-x:auto;padding-bottom:6px}
.mhead,.mrow{display:grid;grid-template-columns:196px 1fr;gap:10px;align-items:center}
.mcells{display:grid;grid-template-columns:repeat(42,14px);gap:2px}
.mc{width:14px;height:14px;display:block;background:transparent;border-radius:1px}
.mc.self{background:repeating-linear-gradient(45deg,#44403c,#44403c 1px,transparent 1px,transparent 3px)}
.mn{font-size:11.5px;opacity:.7;text-align:right;overflow:hidden;
 text-overflow:ellipsis;white-space:nowrap}
.mrow:hover .mn{opacity:1;color:var(--brand)}
.mhead{height:126px;align-items:end;margin-bottom:4px}
.mh{display:block;width:14px;height:120px;position:relative}
.mh b{position:absolute;bottom:0;left:50%;transform-origin:left bottom;
 transform:rotate(-90deg) translateX(4px);font-weight:400;font-size:10.5px;
 opacity:.5;white-space:nowrap}
/* strongest pairs */
.plist{margin-top:14px;max-width:640px}
.prow{display:flex;justify-content:space-between;gap:20px;padding:8px 0;
 border-top:1px solid var(--rule);font-size:13.5px}
.prow:first-child{border-top:none}
.pn{opacity:.8}.pn em{font-style:normal;opacity:.35;margin:0 3px}
.pv{opacity:.65}
[data-tip]{position:relative}
[data-tip]:hover::after{content:attr(data-tip);position:absolute;left:50%;
 bottom:calc(100% + 7px);transform:translateX(-50%);background:#0c0a09;
 color:var(--ink);border:1px solid var(--rule);padding:5px 9px;font-size:11.5px;
 white-space:nowrap;z-index:30;pointer-events:none;border-radius:2px}
"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Co-occurrence — orange experiment</title><style>{css}</style></head>
<body><div class="page">{body}</div></body></html>""", encoding="utf-8")
    print(f"wrote {OUT}")
    print(f"  {N}x{N} matrix · {n(len(co))} pairs · max pair {vmax}")
    print(f"  seriation head: {' -> '.join(order[:5])}")


if __name__ == "__main__":
    build()
