"""Phase 5b: /trends/ - the archive at year grain.

Four bands, one grammar. Every band is years-across-the-top, so the eye learns
the axis once and then reads complexity, sentiment, sources, organizations and
places against the same twenty-two columns.

Three rulings are enforced here rather than merely respected:

- **Heatmaps, not spaghetti.** Fifteen entities over twenty-two years is 330
  data points. As overlaid lines that is unreadable; as a matrix it is a
  picture. Single hue only - amber intensity IS the count, so no categorical
  palette has to be decoded, and nothing on this page is encoded by colour
  alone (every cell carries its numbers in a tooltip, every row is named in
  text).
- **The volume confound is drawn, not hidden.** He read 2,675 articles in 2011
  and three in 2021. A raw-count heatmap is partly just a picture of that, so
  every matrix carries a year-total footer row and every cell's tooltip states
  its share of that year alongside its count.
- **Wide things scroll inside themselves.** The matrices are wider than the
  720px column on a phone. Each one lives in its own overflow-x container; the
  PAGE never scrolls sideways (generate.py pins html/body to overflow-x:clip).
"""
from corpus import (COMPLEXITY_MIN_GRADED, GRADE_MAX, GRADE_MIN,
                    SENTIMENT_MIN_RATED, SENTIMENTS,
                    complexity_by_year, complexity_stats, domain_year_matrix,
                    entity_year_matrix, sentiment_by_year, stats,
                    vocabulary_report)
from htmlkit import e, n, page

# The complexity axis does not start at zero. Twenty years of average grade
# level live between 10.0 and 13.2, and a zero-based axis renders that as
# twenty-two identical bars. The window is stated on the page, in text, next to
# the chart - a truncated axis is honest exactly as long as it is labelled.
GRADE_AXIS_LO, GRADE_AXIS_HI = 8.0, 16.0

TRENDS_STYLE = """
/* trends: year-grain heatmaps + bands */
.hmwrap { overflow-x:auto; padding:40px 0 10px; margin-top:-30px; }
.hm { border-collapse:separate; border-spacing:2px; }
.hm caption { caption-side:top; text-align:left; padding-bottom:10px; }
.hm th { font-weight:400; }
.hm th.hmn { width:156px; min-width:156px; max-width:156px; text-align:left;
  font-size:13px; color:var(--ink-2); overflow:hidden; text-overflow:ellipsis;
  white-space:nowrap; padding-right:8px; }
.hm th.hmy { width:20px; min-width:20px; color:var(--ink-3); font-size:9.5px;
  letter-spacing:0; text-align:center; padding-bottom:2px; }
.hm td.hc { width:20px; min-width:20px; height:20px; border-radius:2px;
  background:rgba(251,191,36,var(--i,0)); position:relative; }
.hm td.hc.zero { background:#231f1d; }
.hm td.hc:hover { outline:1px solid var(--brand); }
/* Low-confidence cells carry a glyph, never a colour difference alone. */
.hm td.hc.lown::before { content:"·"; position:absolute; inset:0;
  display:flex; align-items:center; justify-content:center; color:var(--ink-2);
  font-size:14px; line-height:1; }
.hm tfoot th.hmn { color:var(--ink-3); font-family:ui-monospace,"SF Mono",Menlo,monospace;
  font-size:10px; letter-spacing:.1em; text-transform:uppercase; }
.hm tfoot td.ht { height:22px; vertical-align:bottom; }
.hm tfoot td.ht span { display:block; background:var(--rule); border-radius:1px;
  min-height:1px; }
.hm tbody tr:hover th.hmn { color:var(--amber); }
/* The base stylesheet suppresses every tooltip under (hover:none), which on a
   page that is four matrices would leave the cell values encoded by colour
   alone. Cells carry an aria-label for assistive tech, and tap-and-hold gets
   the tooltip back here. */
@media (hover:none) {
  .hm td.hc:active::after, .hm th.hmn:active::after { display:block; opacity:1; }
}
/* The column header is set tighter than the house label so "Organization"
   fits the track instead of ellipsizing into "ORGANIZAT...". */
.hm thead th.hmn { font-size:10px; letter-spacing:.06em; }
/* Tooltips must stay inside the scroll box. overflow-x:auto forces overflow-y
   to auto as well, so anything escaping sideways is CLIPPED, not overflowed -
   measured on the real build, 433 of 1,101 tips lost an edge, the worst by
   187px. Two changes keep them in: they wrap to a fixed measure instead of
   running 600px on one nowrap line, and the cells near either end anchor to
   their own edge rather than centring on a 20px target. */
.hm [data-tip]::after { white-space:normal; width:max-content; max-width:230px;
  text-align:left; line-height:1.45; }
.hm td.hc:nth-child(-n+5)::after, .hm th.hmn::after {
  left:0; right:auto; transform:none; }
.hm td.hc:nth-last-child(-n+5)::after {
  left:auto; right:0; transform:none; }
/* complexity band */
.band { display:flex; gap:3px; align-items:flex-end; height:130px; margin-top:10px; }
.band .col { flex:1; display:flex; flex-direction:column; justify-content:flex-end;
  height:100%; position:relative; }
.band .col .bar { background:var(--amber-dim); border-radius:2px 2px 0 0;
  min-height:2px; }
.band .col.hi .bar { background:var(--amber); }
.band .col.none .bar { background:#231f1d; }
/* Thin years are drawn but marked: the glyph, not the shade, carries it. */
.band .col.thin .bar { background:repeating-linear-gradient(135deg,
  var(--amber-dim) 0 3px, #2b2622 3px 6px); }
.band .col.thin .bl::after { content:"·"; color:var(--amber); margin-left:2px; }
.band .col .bl { text-align:center; margin-top:7px; font-size:9px;
  color:var(--ink-3); letter-spacing:0; }
.band .col:hover .bar { background:var(--brand); }
.axis { display:flex; justify-content:space-between; margin-top:6px;
  color:var(--ink-3); }
.legend { display:flex; align-items:center; gap:8px; margin-top:14px;
  flex-wrap:wrap; }
.legend .sw { display:flex; gap:2px; }
.legend .sw i { width:20px; height:11px; border-radius:2px; display:block;
  background:rgba(251,191,36,var(--i)); }
@media (max-width:560px){
  .hm th.hmn { width:112px; min-width:112px; max-width:112px; font-size:12px; }
  .band { height:96px; } .band .col .bl { display:none; }
}
"""


# ---------------------------------------------------------------------------
# the shared matrix renderer
# ---------------------------------------------------------------------------

def _intensity(count, peak):
    """Amber opacity for a cell. Square-rooted so a long tail stays visible:
    at peak 606, a linear ramp renders a genuine 20-article year at 3% opacity,
    which is indistinguishable from never. Zero stays exactly zero."""
    if not peak or count <= 0:
        return 0.0
    return round(min((count / peak) ** 0.5, 1.0), 3)


def _year_label(year):
    return f"’{str(year)[2:]}"


def heatmap(matrix, caption, row_label, unit="articles", empty=""):
    """A ranked entity x year matrix. `caption` and `row_label` are trusted
    strings from this module; every NAME comes from third-party enrichment and
    is escaped on both the way into the cell and the way into the tooltip."""
    names, years = matrix["names"], matrix["years"]
    if not names:
        return f'    <div class="empty">{e(empty or "nothing to rank here")}</div>\n'

    peak = matrix["peak"]
    head = "".join(f'<th class="hmy label" scope="col">{e(_year_label(y))}</th>'
                   for y in years)
    body = ""
    for name in names:
        total = matrix["row_totals"][name]
        runit = unit[:-1] if (total == 1 and unit.endswith("s")) else unit
        rtip = f"{name} — {n(total)} {runit} across {len(years)} years"
        cells = ""
        for y in years:
            count = matrix["cells"][name][y]
            year_total = matrix["year_totals"].get(y, 0)
            if count:
                share = (count / year_total * 100) if year_total else 0
                noun = unit[:-1] if (count == 1 and unit.endswith("s")) else unit
                tip = (f"{name} — {y}: {n(count)} {noun}, "
                       f"{share:.1f}% of that year's {n(year_total)}")
                cells += (f'<td class="hc" style="--i:{_intensity(count, peak)}" '
                          f'data-tip="{e(tip)}" aria-label="{e(tip)}"></td>')
            else:
                tip = f"{name} — {y}: none"
                cells += (f'<td class="hc zero" data-tip="{e(tip)}" '
                          f'aria-label="{e(tip)}"></td>')
        body += (f'      <tr><th class="hmn" scope="row" data-tip="{e(rtip)}">'
                 f'{e(str(name))}</th>{cells}</tr>\n')

    vpeak = max(matrix["year_totals"].values(), default=0) or 1
    foot = ""
    for y in years:
        total = matrix["year_totals"].get(y, 0)
        h = max((total / vpeak) ** 0.5 * 18, 1) if total else 1
        read = f"{y} - {n(total)} article{'' if total == 1 else 's'} read"
        foot += (f'<td class="ht" data-tip="{e(read)}">'
                 f'<span style="height:{h:.0f}px"></span></td>')

    swatches = "".join(f'<i style="--i:{v}"></i>' for v in (0.15, 0.35, 0.55, 0.78, 1.0))
    legend = (f'    <div class="legend"><span class="label">Fewer</span>'
              f'<span class="sw">{swatches}</span>'
              f'<span class="label">More · peak {n(peak)} {e(unit)} in one year</span></div>\n')

    return (f'    <div class="hmwrap">\n'
            f'    <table class="hm">\n'
            f'      <caption class="label">{e(caption)}</caption>\n'
            f'      <thead><tr><th class="hmn label" scope="col">{e(row_label)}</th>'
            f'{head}</tr></thead>\n'
            f'      <tbody>\n{body}      </tbody>\n'
            f'      <tfoot><tr><th class="hmn">Read that year</th>{foot}</tr></tfoot>\n'
            f'    </table>\n    </div>\n{legend}')


# ---------------------------------------------------------------------------
# bands
# ---------------------------------------------------------------------------

def complexity_band(series):
    """Average clipped grade level per year, on a stated non-zero axis.

    The densest/plainest callout is drawn only from years with enough graded
    articles to mean anything. 2021 holds three articles averaging grade 14.00
    - the highest number in the series - and naming it the archive's densest
    year would be a twenty-year claim resting on three files. Thin years are
    still DRAWN, and marked, because hiding them would be the other lie.
    """
    rendered = [row for row in series if row["avg"] is not None]
    if not rendered:
        return '    <div class="empty">no reading-level data in the index</div>\n', None, None
    solid = [row for row in rendered if not row["low"]] or rendered
    top = max(solid, key=lambda r: r["avg"])
    bottom = min(solid, key=lambda r: r["avg"])
    span = GRADE_AXIS_HI - GRADE_AXIS_LO
    out = ""
    for row in series:
        avg, delta = row["avg"], row["delta"]
        year_label = _year_label(row["year"])
        if avg is None:
            tip = f"{row['year']} — no reading-level data"
            out += (f'      <div class="col none" data-tip="{e(tip)}">'
                    f'<div class="bar" style="height:2px"></div>'
                    f'<div class="bl num">{e(year_label)}</div></div>\n')
            continue
        pct = max(min((avg - GRADE_AXIS_LO) / span, 1.0), 0.02) * 100
        cls = " hi" if row is top else ""
        if row["low"]:
            cls += " thin"
        move = "" if delta is None else (
            f", {'+' if delta > 0 else '−'}{abs(delta):.2f} vs {row['year'] - 1}"
            if delta else f", level with {row['year'] - 1}")
        tip = (f"{row['year']} — grade {avg:.2f} average across "
               f"{n(row['graded'])} of {n(row['articles'])} articles{move}")
        if row["low"]:
            tip += f" · under {COMPLEXITY_MIN_GRADED}, read as noise"
        out += (f'      <div class="col{cls}" data-tip="{e(tip)}">'
                f'<div class="bar" style="height:{pct:.1f}%"></div>'
                f'<div class="bl num">{e(year_label)}</div></div>\n')
    return out, top, bottom


def sentiment_strip(series, years):
    """Three named rows, one per sentiment, scaled WITHIN each row.

    Within-row scaling is the honest choice for the question this answers -
    when was the reading angriest? Neutral runs 51-72% in every year of the
    archive, so a globally-scaled strip renders one permanently hot row and two
    permanently cold ones, and the 2008 and 2020 spikes that are the whole
    point disappear. The note under the strip says so, and every cell's tooltip
    carries the real share and the real counts, so nothing is legible only as
    intensity.
    """
    by_year = {row["year"]: row for row in series}
    body = ""
    for label in SENTIMENTS:
        # Thin years are excluded from the SCALE, not from the strip. 2021 rates
        # two of three articles Negative and would otherwise own that row at
        # full intensity, pushing 2020's real 39.8% spike - 51 of 128 - down to
        # a middling shade. That is the precise failure within-row scaling
        # exists to prevent, reintroduced by the denominator.
        solid = [by_year[y]["shares"][label] for y in years
                 if by_year.get(y) and by_year[y]["rated"] and not by_year[y]["low"]]
        thin = [by_year[y]["shares"][label] for y in years
                if by_year.get(y) and by_year[y]["rated"]]
        rmax = max(solid, default=0) or max(thin, default=0) or 1
        cells = ""
        for y in years:
            row = by_year.get(y)
            if not row or not row["rated"]:
                nil = f"{label} - {y}: nothing rated"
                cells += (f'<td class="hc zero" data-tip="{e(nil)}" '
                          f'aria-label="{e(nil)}"></td>')
                continue
            share = row["shares"][label]
            count = row["counts"][label]
            low = " lown" if row["low"] else ""
            # The tooltip reports the TRUE share, computed from the counts
            # beside it. `shares` carries a tenth of rounding residue so the
            # three add to exactly 100; nothing here is stacked, so spending
            # that fudge on a number the note calls "the real share" would buy
            # nothing and cost the one claim this page makes about itself.
            exact = count / row["rated"] * 100
            tip = (f"{label} — {y}: {exact:.1f}% "
                   f"({n(count)} of {n(row['rated'])} rated)")
            if row["low"]:
                tip += f" · under {SENTIMENT_MIN_RATED}, read as noise"
            if share <= 0:
                cells += (f'<td class="hc zero{low}" data-tip="{e(tip)}" '
                          f'aria-label="{e(tip)}"></td>')
            else:
                i = round(min(share / rmax, 1.0), 3)
                cells += (f'<td class="hc{low}" style="--i:{i}" '
                          f'data-tip="{e(tip)}" aria-label="{e(tip)}"></td>')
        body += (f'      <tr><th class="hmn" scope="row">{e(label)}</th>'
                 f'{cells}</tr>\n')

    head = "".join(f'<th class="hmy label" scope="col">{e(_year_label(y))}</th>'
                   for y in years)
    return (f'    <div class="hmwrap">\n    <table class="hm">\n'
            f'      <caption class="label">Share of each year’s rated articles'
            f'</caption>\n'
            f'      <thead><tr><th class="hmn label" scope="col">Sentiment</th>'
            f'{head}</tr></thead>\n'
            f'      <tbody>\n{body}      </tbody>\n    </table>\n    </div>\n')


# ---------------------------------------------------------------------------
# the page
# ---------------------------------------------------------------------------

def render_trends(corpus, site_title="The Week in Reading", domain=""):
    rows = corpus.rows
    st = stats(rows)
    years = [int(y) for y in corpus.years]
    first, last = (years[0], years[-1]) if years else (0, 0)

    comp_series = complexity_by_year(corpus)
    comp_html, comp_top, comp_bottom = complexity_band(comp_series)
    comp_all = complexity_stats(rows)
    graded = comp_all["graded"]
    all_avg = comp_all["avg"]
    thin = [r for r in comp_series if r["low"] and r["avg"] is not None]
    thin_years = [r["year"] for r in thin]
    thin_top = max(thin, key=lambda r: r["avg"]) if thin else None

    sent_series = sentiment_by_year(corpus)
    sent_html = sentiment_strip(sent_series, corpus.year_axis)
    low_years = [r["year"] for r in sent_series if r["rated"] and r["low"]]
    rated_total = sum(r["rated"] for r in sent_series)

    src = domain_year_matrix(corpus, 15)
    orgs = entity_year_matrix(corpus, "orgs", 15)
    locs = entity_year_matrix(corpus, "locations", 15)
    org_v = vocabulary_report(rows, "orgs")
    loc_v = vocabulary_report(rows, "locations")

    if comp_top and comp_bottom and all_avg is not None:
        comp_note = (
            f"Reading level is Flesch-Kincaid, computed at index time and clipped to "
            f"grade {GRADE_MIN:.0f}–{GRADE_MAX:.0f} before anything here averages it: "
            f"{n(comp_all['clipped'])} of {n(len(rows))} articles carry a parsed grade "
            f"outside that band, the highest of them {comp_all['raw_max']:,.0f}, and an "
            f"unclipped mean reads {comp_all['raw_avg']:.2f} where the honest figure is "
            f"{all_avg:.2f}. The axis runs {GRADE_AXIS_LO:.0f} to {GRADE_AXIS_HI:.0f} "
            f"rather than from zero, because every yearly average in the series sits "
            f"inside a span of {comp_top['avg'] - comp_bottom['avg']:.2f} grades. "
            f"Densest year: {comp_top['year']} at {comp_top['avg']:.2f}. "
            f"Plainest: {comp_bottom['year']} at {comp_bottom['avg']:.2f}. Measured over "
            f"the {n(graded)} articles that carry a reading level."
        )
        # When EVERY year is thin, complexity_band ranks them anyway rather
        # than naming nothing - so the note must not then go on to call those
        # same years ineligible. It would be arguing with the sentence before
        # it.
        if thin_years and comp_top not in thin:
            comp_note += (
                f" Hatched and dotted: {', '.join(str(y) for y in thin_years)} - fewer "
                f"than {COMPLEXITY_MIN_GRADED} graded articles apiece. They are drawn "
                f"because hiding a year is its own distortion, but they are not "
                f"eligible to be named the densest or the plainest: {thin_top['year']} "
                f"averages {thin_top['avg']:.2f} across {n(thin_top['graded'])} "
                f"article{'s' if thin_top['graded'] != 1 else ''}, which is the highest "
                f"number in the series and the least evidence behind one.")
    else:
        comp_note = "No reading-level data in this index."

    solid_sent = [r for r in sent_series if r["rated"] and not r["low"]]
    if solid_sent:
        lo = min(r["shares"]["Neutral"] for r in solid_sent)
        hi = max(r["shares"]["Neutral"] for r in solid_sent)
        neutral_range = (f" - Neutral runs between {lo:.0f}% and {hi:.0f}% of every "
                         f"year with enough articles to say")
    else:
        neutral_range = ""
    sent_note = (
        f"Sentiment is one label per article from the enrichment pass, not a score. "
        f"{n(rated_total)} of {n(len(rows))} articles carry one. Each row is scaled "
        f"against its own busiest year, not against the other two rows{neutral_range}, "
        f"so a shared scale would render one permanently bright row and hide the "
        f"swings inside Positive and Negative that are the reason to draw this at "
        f"all. Thin years are drawn but do not set any row's scale. Hover or tap any "
        f"cell for the real share and count."
    )
    if low_years:
        sent_note += (
            f" Marked with a dot: {', '.join(str(y) for y in low_years)} - fewer than "
            f"{SENTIMENT_MIN_RATED} rated articles, so the percentages are arithmetic "
            f"rather than evidence.")

    src_note = (
        f"Sources are read off the article URL, and only {n(st['url_bearing'])} of "
        f"{n(len(rows))} articles have one - the legacy era arrived as PDFs, Word "
        f"documents and text files with no link at all. This band therefore describes "
        f"the Instapaper and Matter eras and is blank-by-construction across the early "
        f"years, which is a fact about how the archive was made rather than about what "
        f"was read. Hosts are not normalised: nytimes.com and bits.blogs.nytimes.com "
        f"are two rows."
    )
    org_note = (
        f"Organizations are the entity field this archive ranks most honestly: the top "
        f"20 cover {org_v['head_coverage']:,.1f}% of all {n(len(rows))} articles, and "
        f"unlike anything keyed on a URL, entity extraction ran over both corpora, so "
        f"the file-sourced legacy era is counted here too. The vocabulary is "
        f"uncontrolled, so an organization named several ways ranks several times."
    )
    loc_note = (
        f"Places clear the same bar as organizations and then some - the top 20 cover "
        f"{loc_v['head_coverage']:,.1f}% of articles against "
        f"{org_v['head_coverage']:,.1f}% for organizations, over a smaller vocabulary "
        f"({n(loc_v['vocabulary'])} against {n(org_v['vocabulary'])}). Also "
        f"uncontrolled: 'United States', 'U.S.' and 'America' are separate rows, and "
        f"'New York' does not distinguish the city from the state."
    )

    avg_tile = (f'<div class="stat"><div class="v num">{all_avg:.1f}</div>'
                f'<div class="l label">Average reading level</div>'
                f'<div class="delta">grade, clipped 0–20</div></div>'
                if graded else "")

    body = f"""  <header>
    <a class="label kicker" href="../">{e(site_title)}</a>
    <h1>Trends</h1>
    <div class="daterange">{n(len(rows))} articles across {last - first + 1} years, {first}–{last}, read at year grain.</div>
  </header>

  <div class="stats">
    <div class="stat"><div class="v num">{n(len(rows))}</div><div class="l label">Articles</div></div>
    <div class="stat"><div class="v num">{n(len(years))}</div><div class="l label">Years covered</div></div>
    {avg_tile}
    <div class="stat"><div class="v num">{n(st["domains"])}</div><div class="l label">Distinct sources</div><div class="delta">across {n(st["url_bearing"])} linked articles</div></div>
  </div>

  <section>
    <div class="label viz-title">How hard the reading got · average grade level per year</div>
    <div class="band">
{comp_html}    </div>
    <div class="axis label"><span>grade {GRADE_AXIS_LO:.0f}</span><span>grade {GRADE_AXIS_HI:.0f} at full height</span></div>
    <div class="note">{e(comp_note)}</div>
  </section>

  <section>
    <div class="label viz-title">The mood of the reading · sentiment mix by year</div>
{sent_html}    <div class="note">{e(sent_note)}</div>
  </section>

  <section>
    <div class="label viz-title">Where the reading came from · top {len(src['names'])} sources by year</div>
{heatmap(src, "Articles per source per year", "Source", "articles",
         "no URLs in this archive - nothing to rank")}    <div class="note">{e(src_note)}</div>
  </section>

  <section>
    <div class="label viz-title">Who the reading was about · top {len(orgs['names'])} organizations by year</div>
{heatmap(orgs, "Articles per organization per year", "Organization", "articles",
         "no organizations tagged")}    <div class="note">{e(org_note)}</div>
    <div class="note"><a href="../orgs/">All {n(org_v["vocabulary"])} organizations →</a></div>
  </section>

  <section>
    <div class="label viz-title">Where the reading was set · top {len(locs['names'])} places by year</div>
{heatmap(locs, "Articles per place per year", "Place", "articles",
         "no locations tagged")}    <div class="note">{e(loc_note)}</div>
    <div class="note"><a href="../locations/">All {n(loc_v["vocabulary"])} places →</a></div>
  </section>

  <div class="yearnav"><span></span><a class="home" href="../">All weeks</a><span></span></div>
  <footer>
    <span class="label">Computed from the archive index · corrupted rows excluded</span>
    <span class="label num">{e(domain)}</span>
  </footer>"""
    return page(f"Trends — {site_title}", body, depth=1)
