#!/usr/bin/env python3
"""Phase E — the pages the curated vocabulary earns.

Two pages, three sections:

  /concepts/    The Cascade   — 248 entries as 22-year density ridges, ordered
                                by the year each peaked, so the page reads as a
                                timeline of attention rather than a ranking.
                The Collapse  — the curation itself as data: 73,099 raw strings
                                reduced to 248 entries, and what each absorbed.
  /together/    The Matrix    — co-occurrence as an ORDERED adjacency matrix.

Three decisions worth knowing before changing anything here.

**Orange carries the data on these pages; amber does not appear.** Elsewhere on
the site orange is an accent (links, drop caps, the thread-of-the-week rule) and
amber is structural. Here the relationship inverts: these are the only pages
whose subject IS the curated vocabulary, so the brand hue does the encoding.
The ramp is sequential, five steps, and was validated rather than chosen —
monotonic in lightness with every step at 3.24:1 or better against the stone-900
surface. Three earlier orange ramps were rejected because orange sits darker
than amber at equal chroma and their bottom steps fell under the 3:1 floor; the
fix was lifting the floor, not extending the ramp downward. Zero renders as
bare surface, never as a sixth step.

**The Cascade's sort is the content.** Ordering by peak year — not by volume —
is what turns 1,320 cells into a readable diagonal: iPod (2006) above the
financial crisis (2008) above Social Media (2011) above AI (2025). Sorted by
article count instead, the same data says nothing.

**Intensity is per-entry, not global.** A small entry's peak is as visible as a
large one's. The alternative flattens two decades of quieter reading into
nothing so that Social Media can be bright, which is the opposite of the point.
"""
import collections
import html
import pathlib
import sys

from corpus import as_list, vocabulary_report  # noqa: F401
from htmlkit import page

e = html.escape

# Validated against surface #1c1917: lightness-monotonic, min contrast 3.24:1.
# Do not reorder or extend downward without re-validating — see module docstring.
ORANGE = ["#a4551c", "#c26a22", "#e0812c", "#FF8F3B", "#FFB877"]

CASCADE_LIMIT = 60
MATRIX_LIMIT = 42
COLLAPSE_LIMIT = 18

VOCAB_STYLE = """
/* --- Phase E: the vocabulary pages ---------------------------------- */
.vlegend{display:flex;align-items:center;gap:5px;margin:22px 0 12px;font-size:11px}
.vlegend i{width:26px;height:9px;display:inline-block}
.vlg{opacity:.45;letter-spacing:.06em;text-transform:uppercase;margin:0 5px}
.vlg-note{opacity:.3;margin-left:10px}

.cascade{margin-top:6px;overflow-x:auto}
/* 186px of name plus 42px of peak plus gaps leaves 22 columns sharing 48px
   on a 390px phone - 2.2px each, narrower than the two-digit year labels
   above them. The page body is overflow-x:clip, so without a scroll
   container here the far years are CLIPPED rather than reachable. */
.cascade .crow{min-width:560px}
@media (max-width:560px){
  .crow{grid-template-columns:110px 1fr 34px;gap:8px}
  .cn{font-size:11px}
}
.crow{display:grid;grid-template-columns:186px 1fr 42px;align-items:center;gap:12px;padding:1px 0}
.crow:hover .cn{opacity:1;color:var(--brand)}
.cn{font-size:12.5px;opacity:.72;text-align:right;overflow:hidden;
 text-overflow:ellipsis;white-space:nowrap}
.cgrid{display:grid;grid-template-columns:repeat(var(--yrs),1fr);gap:2px}
.cc{height:11px;background:transparent;display:block;border-radius:1px}
.cy{font-size:9.5px;opacity:.38;text-align:center;font-variant-numeric:tabular-nums}
.cpk{font-size:10.5px;opacity:.4;font-variant-numeric:tabular-nums}
.chead .cn,.chead .cpk{font-size:9.5px;letter-spacing:.1em;text-transform:uppercase;opacity:.4}

.funnel{margin-top:20px;max-width:720px}
.fstage{display:grid;grid-template-columns:104px 1fr;align-items:center;gap:14px;padding:7px 0}
.fstage b{font-size:22px;font-weight:300;font-variant-numeric:tabular-nums;text-align:right}
.fstage span{font-size:12px;opacity:.5;grid-column:2}
.fstage i{grid-column:2;height:8px;background:#a4551c;display:block;margin-top:3px;min-width:3px}
.fstage.lead i{background:var(--brand)}
.fstage.lead b{color:var(--brand)}
.collapse{margin-top:8px}
.krow{display:grid;grid-template-columns:210px 1fr 62px;align-items:center;gap:14px;
 padding:5px 0;border-top:1px solid var(--rule)}
.kn{font-size:12.5px;opacity:.75;text-align:right}
.kbar{position:relative;height:15px;display:flex;align-items:center}
.kbar i{height:11px;background:#a4551c;border-radius:0 2px 2px 0;display:block}
.kbar em{font-style:normal;font-size:10.5px;opacity:.45;margin-left:8px;white-space:nowrap}
.ka{font-size:12px;opacity:.6;text-align:right}

.mwrap{overflow-x:auto;padding-bottom:6px}
.mhead,.mrow{display:grid;grid-template-columns:196px 1fr;gap:10px;align-items:center}
.mcells{display:grid;grid-template-columns:repeat(var(--cols),14px);gap:2px}
.mc{width:14px;height:14px;display:block;background:transparent;border-radius:1px}
.mc.self{background:repeating-linear-gradient(45deg,#44403c,#44403c 1px,transparent 1px,transparent 3px)}
.mn{font-size:11.5px;opacity:.7;text-align:right;overflow:hidden;
 text-overflow:ellipsis;white-space:nowrap;
 /* Sticky, or scrolling right to reach column 42 takes every row name
    with it and leaves an unlabelled grid. The matrix is 876px against a
    672px content box, so this bites on DESKTOP, not just on a phone. */
 position:sticky;left:0;z-index:2;background:var(--bg);padding-right:4px}
.mrow:hover .mn{opacity:1;color:var(--brand)}
.mhead{height:152px;align-items:end;margin-bottom:4px;overflow:hidden}
.mh{display:block;width:14px;height:146px;position:relative;overflow:hidden}
.mh b{position:absolute;bottom:0;left:50%;transform-origin:left bottom;
 transform:rotate(-90deg) translateX(4px);font-weight:400;font-size:10px;
 opacity:.5;white-space:nowrap;
 /* Five of 42 names exceeded the old 120px band and rendered up through
    the legend above it. Bounded and ellipsised, not trusted to be short. */
 max-width:142px;overflow:hidden;text-overflow:ellipsis}
.plist{margin-top:14px;max-width:640px}
.prow{display:flex;justify-content:space-between;gap:20px;padding:8px 0;
 border-top:1px solid var(--rule);font-size:13.5px}
.prow:first-child{border-top:none}
.pn{opacity:.8}.pn em{font-style:normal;opacity:.35;margin:0 3px}
.pv{opacity:.65}
.vtip{position:relative}
.vtip:hover::after{content:attr(data-tip);position:absolute;left:50%;
 bottom:calc(100% + 7px);transform:translateX(-50%);background:#0c0a09;
 color:var(--ink);border:1px solid var(--rule);padding:5px 9px;font-size:11.5px;
 white-space:nowrap;z-index:30;pointer-events:none;border-radius:2px}
"""


def load_taxonomy(path):
    """The committed taxonomy, or None.

    Returns None rather than raising for every failure: a missing or broken
    taxonomy must cost the two pages, never the whole site build. generate.py
    is the last step of a nightly that has already walked the vault.
    """
    # Validate with the STRICT loader rather than re-deriving a weaker one.
    # scripts/core/taxonomy.load rejects entries missing aliases, null aliases,
    # non-mapping entries, null names and scalar excluded_aliases — each with a
    # comment saying why. A second permissive loader here let all five straight
    # back through to crash the render, which is precisely the defect commit
    # 64f1c55 was written to close. One validator, used twice.
    try:
        core = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "core"
        if str(core) not in sys.path:
            sys.path.insert(0, str(core))
        import taxonomy as strict
        import yaml
        strict.load(path)                        # raises TaxonomyError if unusable
        return yaml.safe_load(pathlib.Path(path).read_text())
    except Exception as exc:  # noqa: BLE001 - the site build outranks this file
        print(f"  WARNING: taxonomy unusable ({exc}); vocabulary pages skipped")
        return None


def n(x):
    return f"{x:,}"


def _peak(series):
    """The peak year, ties broken toward the earlier one — deterministically.

    Returns None for an empty series. An entry can have zero dated rows (every
    row NaN-yeared), and `max()` on an empty Counter raises — which took out
    the whole deep-dive leg from the SORT KEY, one line above the display that
    was already guarded.
    """
    if not series:
        return None
    return max(series, key=lambda y: (series[y], -y))


def _step(v, vmax):
    """Sequential bucket. Zero is the absence of a mark, not a colour."""
    if v <= 0:
        return None
    frac = v / vmax if vmax else 0
    for i, edge in enumerate((0.08, 0.22, 0.45, 0.72)):
        if frac <= edge:
            return ORANGE[i]
    return ORANGE[4]


def _legend(low="fewer", high="more", note=""):
    swatches = "".join(f"<i style='background:{c}'></i>" for c in ORANGE)
    extra = f"<span class='vlg-note'>{e(note)}</span>" if note else ""
    return (f"<div class='vlegend'><span class='vlg'>{e(low)}</span>{swatches}"
            f"<span class='vlg'>{e(high)}</span>{extra}</div>")


def tally(rows):
    """Per-entry totals, per-entry-per-year counts, and pair co-occurrence."""
    per = collections.defaultdict(collections.Counter)
    totals = collections.Counter()
    co = collections.Counter()
    years = collections.Counter()
    for _, r in rows.iterrows():
        # as_list, not the raw value: its docstring is "Entity columns arrive as
        # lists, numpy arrays, or NaN", and this was the only module in site/
        # reading an entity column without it. A single None row raised
        # TypeError inside the deep-dive try/except and cost SIX page groups
        # plus articles.json — the same blast radius as the column bug, from a
        # different trigger.
        ents = sorted({x for x in as_list(r.get("canonical_entries")) if isinstance(x, str)})
        y = r.get("year")
        if y is not None and y == y:            # NaN-safe
            y = int(y)
            years[y] += 1
            for name in ents:
                per[name][y] += 1
        totals.update(ents)
        for i, a in enumerate(ents):
            for b in ents[i + 1:]:
                co[frozenset((a, b))] += 1
    return per, totals, co, sorted(years)


def render_cascade(per, totals, years, limit=CASCADE_LIMIT):
    ranked = [nm for nm, _ in totals.most_common(limit)]
    # Peak year ascending, then volume descending inside a year. This sort IS
    # the visualization — by count instead, the same cells say nothing.
    # (count, -year) so a tie resolves to the EARLIER year deterministically.
    # max() alone returns the first maximal key in Counter insertion order, i.e.
    # whatever order rows happened to arrive — two of the 60 cascade entries
    # moved five years vertically under a reversed or shuffled index, on a page
    # whose entire thesis is "ordered by the year each peaked".
    # Name is the final tie-break: two pairs tie on BOTH peak year and total
    # (Mergers/Inflation at 2008/173, Privacy/Data Analysis at 2011/217), and
    # Counter.most_common preserves insertion order for equal counts — so the
    # rendered page differed between row shuffles. Cosmetic, but the page is
    # regenerated nightly and a churning diff is a real cost.
    ranked.sort(key=lambda nm: (_peak(per[nm]) or 0, -totals[nm], nm))
    head = "".join(f"<div class='cy'>{y % 100:02d}</div>" for y in years)
    out = ""
    for nm in ranked:
        s = per[nm]
        vmax = max(s.values()) if s else 0
        pk = _peak(s) or ""
        cells = ""
        for y in years:
            v = s.get(y, 0)
            col = _step(v, vmax)
            style = f"background:{col}" if col else ""
            tip = f"{nm} · {y} · {v} article{'' if v == 1 else 's'}"
            cells += (f"<i class='cc vtip' style='{style}' "
                      f"data-tip='{e(tip)}' title='{e(tip)}'></i>")
        out += (f"<div class='crow'><span class='cn' title='{e(nm)}'>{e(nm)}</span>"
                f"<span class='cgrid'>{cells}</span>"
                f"<span class='cpk'>{pk}</span></div>")
    return f"""  <section>
    <div class="label viz-title">The cascade · {len(ranked)} entries by year, ordered by peak</div>
    {_legend('fewer', 'more articles that year',
             '· intensity is per-entry, so a small peak reads as clearly as a large one')}
    <div class="cascade" style="--yrs:{len(years)}">
      <div class="crow chead"><span class="cn"></span>
        <span class="cgrid">{head}</span><span class="cpk">peak</span></div>
{out}
    </div>
    <div class="note">Read top to bottom and this is a timeline of attention, not a
      ranking: entries are ordered by the year they peaked. Hover any cell for its count.</div>
  </section>"""


def render_collapse(alias_counts, totals, excluded, n_strings, n_clusters,
                    n_gate, limit=COLLAPSE_LIMIT):
    ranked = sorted(alias_counts.items(), key=lambda kv: -kv[1])[:limit]
    amax = ranked[0][1] if ranked else 1
    rows_html = ""
    for nm, cnt in ranked:
        w = cnt / amax * 100
        rows_html += (
            f"<div class='krow vtip' data-tip='"
            f"{e(f'{nm}: {cnt} strings folded into one entry')}'>"
            f"<span class='kn'>{e(nm)}</span>"
            f"<span class='kbar'><i style='width:{w:.1f}%'></i>"
            f"<em>{cnt} strings</em></span>"
            f"<span class='ka num'>{n(totals.get(nm, 0))}</span></div>")
    kept = len(alias_counts)
    return f"""  <section>
    <div class="label viz-title">The collapse · how the vocabulary was made</div>
    <div class="funnel">
      <div class="fstage"><b>{n(n_strings)}</b><span>raw strings from enrichment</span>
        <i style="width:100%"></i></div>
      <div class="fstage"><b>{n(n_clusters)}</b><span>clusters after embedding</span>
        <i style="width:{n_clusters / n_strings * 100:.1f}%"></i></div>
      <div class="fstage"><b>{n(n_gate)}</b><span>clusters reviewed by hand at the curation gate</span>
        <i style="width:{max(n_gate / n_strings * 100, 0.3):.2f}%"></i></div>
      <div class="fstage lead"><b>{n(kept)}</b><span>entries kept · {excluded} strings
        cut on purpose</span><i style="width:{max(kept / n_strings * 100, 0.3):.2f}%"></i></div>
    </div>
    <div class="label viz-title" style="margin-top:26px">What each entry absorbed</div>
    <div class="collapse">{rows_html}</div>
    <div class="note">Left bar: distinct source strings folded into that entry.
      Right number: articles it reaches. Every other entity page on this site warns
      that one thing is ranked several times under different names — this page is
      the exception, and the bars are why.</div>
  </section>"""


def _seriate(names, co):
    """Order rows so related entries sit adjacent.

    Greedy nearest-neighbour from the most-connected entry. Crude beside a
    spectral ordering, but deterministic and explainable — and the ordering is
    the whole payoff of a matrix. Unordered, the same cells are noise.
    """
    strength = {a: sum(co.get(frozenset((a, b)), 0) for b in names if b != a)
                for a in names}
    order = [max(names, key=lambda x: (strength[x], x))]
    left = set(names) - set(order)
    while left:
        cur = order[-1]
        nxt = max(left, key=lambda b: (co.get(frozenset((cur, b)), 0), strength[b], b))
        order.append(nxt)
        left.discard(nxt)
    return order


def render_matrix(totals, co, limit=MATRIX_LIMIT):
    top = [nm for nm, _ in totals.most_common(limit)]
    order = _seriate(top, co)
    vmax = max((co.get(frozenset((a, b)), 0)
                for a in order for b in order if a != b), default=1)
    # What the top-N cut hides, computed rather than hand-waved. This codebase
    # states every other exclusion it makes (pre-min-year weeks, the people
    # quarantine, the grade-level clip); a matrix that draws 6% of the pairs
    # under a headline of 11,369 should not be the exception.
    shown = {frozenset((a, b)) for a in order for b in order if a != b}
    shown_w = sum(co.get(k, 0) for k in shown if k in co)
    total_w = sum(co.values()) or 1
    shown_pairs = sum(1 for k in shown if co.get(k))
    total_pairs = len([1 for v in co.values() if v])
    pair_share = 100 * shown_pairs / (total_pairs or 1)
    weight_share = 100 * shown_w / total_w
    off = [(v, sorted(k)) for k, v in co.items() if k not in shown]
    # e() on the NAMES, not on the assembled string: these are entry names from
    # the taxonomy and this is the only path on these pages that was still
    # interpolating them raw. It shipped on the commit whose deliverable was
    # escaping tests, and the test missed it because limit=2 left `off` empty.
    omitted = (f"{e(max(off)[1][0])} + {e(max(off)[1][1])} "
               f"({n(max(off)[0])} articles)" if off else "none")
    head = "".join(f"<span class='mh'><b>{e(nm)}</b></span>" for nm in order)
    body = ""
    for a in order:
        cells = ""
        for b in order:
            if a == b:
                cells += (f"<i class='mc self vtip' data-tip='"
                          f"{e(f'{a} · {totals[a]} articles')}'></i>")
                continue
            v = co.get(frozenset((a, b)), 0)
            col = _step(v, vmax)
            st = f"background:{col}" if col else ""
            tip = (f"{a} + {b} · {v} article{'' if v == 1 else 's'} together"
                   if v else f"{a} + {b} · never together")
            cells += (f"<i class='mc vtip' style='{st}' "
                      f"data-tip='{e(tip)}' title='{e(tip)}'></i>")
        body += (f"<div class='mrow'><span class='mn' title='{e(a)}'>{e(a)}</span>"
                 f"<span class='mcells'>{cells}</span></div>")
    return f"""  <section>
    <div class="label viz-title">The matrix · top {len(order)} entries, ordered so
      related entries sit together</div>
    {_legend('rarer', 'more articles shared')}
    <div class="mwrap" style="--cols:{len(order)}">
      <div class="mhead"><span class="mn"></span><span class="mcells">{head}</span></div>
{body}
    </div>
    <div class="note">Showing {n(shown_pairs)} of {n(total_pairs)} pairs ({pair_share:.0f}%
      by count, {weight_share:.0f}% by shared-article weight). Strongest pair left
      off: {omitted}. A node-link diagram of these pairs is a hairball. A matrix gives
      every pair its own cell — but only pays off if related entries are adjacent, so
      rows are seriated by nearest neighbour. The bright blocks on the diagonal are
      real clusters of attention. The diagonal itself is hatched: an entry always
      co-occurs with itself, and colouring that would be a bright line of no information.</div>
  </section>"""


def _stat(value, label):
    return (f'<div class="stat"><div class="v">{value}</div>'
            f'<div class="label">{e(label)}</div></div>')


def render_concepts(corpus_data, taxonomy_doc, derivation=None, tallied=None,
                    site_title="The Week in Reading", domain=""):
    """The /concepts/ page: the Cascade, then the Collapse."""
    rows = corpus_data.rows
    per, totals, _co, years = tallied or tally(rows)
    report = vocabulary_report(rows, "canonical_entries")
    raw = vocabulary_report(rows, "concepts")
    # as_list here too — round 1 fixed tally() and left this line, which
    # failed on the same null row with the same six-page blast radius.
    tagged = sum(1 for _, r in rows.iterrows() if as_list(r.get("canonical_entries")))
    entries = taxonomy_doc.get("entries") or []
    alias_counts = {x["name"]: len(x["aliases"]) for x in entries}
    excluded = len(taxonomy_doc.get("excluded_aliases") or [])
    d = derivation or taxonomy_doc.get("derivation") or {}

    body = f"""  <header>
    <a class="label kicker" href="../">{e(site_title)}</a>
    <h1>Concepts</h1>
    <div class="daterange">What the reading was about, in one controlled vocabulary
      of {n(len(entries))} defined entries across {len(years)} years.</div>
  </header>

  <div class="stats">
    {_stat(n(len(entries)), 'Defined entries')}
    {_stat(f'{100 * tagged / len(rows):,.1f}<em>%</em>', 'Articles tagged')}
    {_stat(f'{report["head_coverage"]:,.1f}<em>%</em>', 'Covered by the top 20')}
    {_stat(f'{report["singleton_share"]:,.1f}<em>%</em>', 'Used only once')}
  </div>

{render_cascade(per, totals, years)}

{render_collapse(alias_counts, totals, excluded,
                 d.get("strings", raw["vocabulary"]),
                 d.get("clusters", 0),
                 taxonomy_doc.get("gate_reviewed", len(entries)))}

  <div class="yearnav"><span></span><a class="home" href="../">All weeks</a>
    <a href="../together/">What travels together &rarr;</a></div>
"""
    return page("Concepts", body, depth=1)


def render_together(corpus_data, tallied=None,
                    site_title="The Week in Reading", domain=""):
    """The /together/ page: co-occurrence as an ordered matrix."""
    rows = corpus_data.rows
    _per, totals, co, _years = tallied or tally(rows)
    pairs = "".join(
        f"<div class='prow'><span class='pn'>{e(sorted(k)[0])} <em>+</em> "
        f"{e(sorted(k)[1])}</span><span class='pv num'>{n(v)}</span></div>"
        for k, v in co.most_common(10))

    body = f"""  <header>
    <a class="label kicker" href="../">{e(site_title)}</a>
    <h1>What travels together</h1>
    <div class="daterange">{n(len(co))} pairs of the {n(len(totals))} entries share
      an article. Not what the reading was about — what it was about at the same time.</div>
  </header>

  <div class="stats">
    {_stat(n(len(co)), 'Co-occurring pairs')}
    {_stat(n(co.most_common(1)[0][1] if co else 0), 'Strongest pair')}
    {_stat(n(len(totals)), 'Entries')}
  </div>

{render_matrix(totals, co)}

  <section>
    <div class="label viz-title">Strongest pairs</div>
    <div class="plist">{pairs}</div>
  </section>

  <div class="yearnav"><a href="../concepts/">&larr; Concepts</a>
    <a class="home" href="../">All weeks</a><span></span></div>
"""
    return page("What travels together", body, depth=1)
