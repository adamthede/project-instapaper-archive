"""The cover page of reading.adamthede.com - four columns on one quarter axis.

Adam approved this design on 2026-09-08 from
`command-center/docs/mockups/2026-09-08-reading-cover.html`, rendered by
`site/mockup_reading_cover.py` on branch `mockup/reading-cover`. The
computations moved here rather than being copied, and that generator is retired
by this module: the mockup and the page it approved must not be two
implementations of the same arithmetic.

The anatomy is not invented. It is lifted from the covers already live on
books.adamthede.com and viewing.adamthede.com so the three surfaces read as one
family: four columns on one shared quarter axis, each with a bar strip
annotated by its era averages, a step chart of a second measure, one hero, and
four secondaries, with exactly one amber hero on the page.

The one thing this module must never lose is the era rule stated under the
columns. Two thirds of this corpus is dated by a filename rather than by a read
event, so a quarter before 2012 says when the writing appeared and a quarter
after it says when Adam read it. A cover that draws both on one axis without
saying so is a chart that lies quietly.

Nothing on the page is typed. Every figure comes out of
data/archive_index.parquet and the vault's week records, and render_cover()
refuses to emit a page with an unreplaced token in it.
"""
import datetime as dt
from collections import Counter

import pandas as pd

import corpus as C
from htmlkit import e, n, page

# Three eras of READING, not three sources: the flood, the fade, the return.
# The source split (legacy / Instapaper / Matter) is its own column.
ERA_BANDS = [("2005-2011", 2005, 2011), ("2012-2020", 2012, 2020),
             ("2021-2026", 2021, 2026)]

# The seam. The legacy import's dates are parsed out of filenames and stop
# where the read-it-later services start, so these two years are the hinge the
# cover names, and the AI share is measured on either side of it.
SEAM_YEAR = 2012
OLD_ERA_LAST_YEAR = 2011
NEW_ERA_FIRST_YEAR = 2018

# An entry has to appear on enough articles for a change in its share to be a
# movement rather than noise. 120 on the live corpus is about 0.9% of the
# tagged rows; the floor and the divisor keep a small fixture (a test, a
# weeks-only rebuild) producing a list rather than an empty block, without
# moving the number on the real archive, where len(tagged)//50 is already well
# past 120.
MOVED_MIN_ARTICLES = 120
MOVED_SHOWN = 2

AI_ENTRY = "Artificial Intelligence"

# The taxonomy join's output column. A quarter of this cover is the Subjects
# column, and it is drawn entirely from here.
CANONICAL_COLUMN = "canonical_entries"


def can_render(corpus_data):
    """Whether this index can carry a cover.

    The same precondition /concepts/ has, and for the same reason: without the
    taxonomy join there is nothing behind the Subjects column, and a fourth of
    the cover would be four empty measures reported as facts. A build that
    cannot draw the cover keeps the shape the site had before it - the weeks
    index at the root - rather than shipping a hollow one.
    """
    return (corpus_data is not None and len(corpus_data)
            and CANONICAL_COLUMN in corpus_data.rows.columns)


# ---------------------------------------------------------------------------
# the shared axis
# ---------------------------------------------------------------------------

class Axis:
    """The quarter axis every strip and step on the cover is drawn on.

    Computed, not declared. The first quarter is the corpus epoch and the last
    is the newest quarter carrying either an article or a written week, so the
    axis grows with the archive instead of needing an edit every October.
    """

    def __init__(self, first_quarter, last_quarter):
        self.quarters = list(pd.period_range(first_quarter, last_quarter,
                                             freq="Q"))
        self.count = len(self.quarters)
        # 260 user units wide, which is what the column's viewBox is.
        self.pitch = 260.0 / self.count
        self.bar_width = self.pitch * 0.78
        self.base, self.top = 81.0, 4.0

    @property
    def span(self):
        f, l = self.quarters[0], self.quarters[-1]
        return f"{f.year} Q{f.quarter} to {l.year} Q{l.quarter}"

    @property
    def ticks(self):
        """Five year labels: the epoch, three five-year steps, and the end."""
        first, last = self.quarters[0].year, self.quarters[-1].year
        marks = [first + 5 * i for i in range(4) if first + 5 * i < last]
        return [str(y) for y in marks] + [str(last)]

    def era_slices(self):
        """(label, first index, last index) for each era on this axis."""
        out = []
        for label, lo, hi in ERA_BANDS:
            idx = [i for i, q in enumerate(self.quarters) if lo <= q.year <= hi]
            if idx:
                out.append((label, idx[0], idx[-1]))
        return out

    def era_averages(self, values):
        return [round(sum(values[a:b + 1]) / (b - a + 1), 1)
                for _, a, b in self.era_slices()]


def axis_for(rows, weeks):
    """The axis the whole cover shares, measured off the data it draws."""
    ends = [quarter_of_week(m["week"]) for m in weeks]
    newest_read = rows["date_read"].max()
    if pd.notna(newest_read):
        ends.append(pd.Period(newest_read, freq="Q"))
    return Axis(pd.Period(year=C.MIN_YEAR, quarter=1, freq="Q"), max(ends))


def quarter_of_week(week):
    y, w = str(week).split("-W")
    return pd.Period(dt.date.fromisocalendar(int(y), int(w), 1), freq="Q")


# ---------------------------------------------------------------------------
# the two chart primitives the sibling covers use
# ---------------------------------------------------------------------------

def strip(axis, values, tick, aria):
    """A quarterly bar strip with each era's average drawn over it in amber.

    The average line is annotated with its own value rather than left to a
    legend: on a column 250 pixels wide there is no room for a legend, and an
    unlabelled rule is a decoration.
    """
    avgs = axis.era_averages(values)
    mx = max(values) or 1
    def h(v):
        return (v / mx) * (axis.base - axis.top)
    out = [f'<svg viewBox="0 0 260 96" class="fstrip" role="img" '
           f'aria-label="{e(aria)}">']
    for i, v in enumerate(values):
        if v <= 0:
            continue
        # A bar for one article must still be visible, or a quiet quarter
        # reads as a missing one.
        hh = max(h(v), 0.9)
        out.append(f'<rect x="{i * axis.pitch:.2f}" y="{axis.base - hh:.2f}" '
                   f'width="{axis.bar_width:.2f}" height="{hh:.2f}" fill="#6f645c"/>')
    for (label, lo, hi), a in zip(axis.era_slices(), avgs):
        y = axis.base - h(a)
        x1, x2 = lo * axis.pitch, (hi + 1) * axis.pitch
        out.append(f'<line x1="{x1:.2f}" y1="{y:.2f}" x2="{x2:.2f}" y2="{y:.2f}" '
                   f'stroke="#fbbf24" stroke-width=".7"/>')
        out.append(f'<text x="{x1 + 2:.2f}" y="{y - 3.5:.2f}" class="favg">{a}</text>')
    out.append('<line x1="0" y1="81" x2="260" y2="81" stroke="#3a3431" stroke-width=".5"/>')
    out.append(f'<text x="0" y="93" class="ftick">{e(tick)}</text>')
    out.append("</svg>")
    return "\n".join(out)


def step(axis, values, start=0):
    """The column's second measure as a step line, scaled to its own range.

    `start` drops the leading quarters of a series that does not begin at the
    epoch - the source count cannot start before the archive carries a URL, and
    a line pinned at zero for six years is a claim about 2005 rather than a
    fact about it.
    """
    live = values[start:] or values
    mn, mx = min(live), max(live)
    rng = (mx - mn) or 1
    def y(v):
        return 30.8 - (v - mn) / rng * (30.8 - 4.0)
    pts = []
    for i, v in enumerate(values):
        if i < start:
            continue
        pts.append(f"{i * axis.pitch:.2f},{y(v):.2f}")
        pts.append(f"{min((i + 1) * axis.pitch, 260.0):.2f},{y(v):.2f}")
    return ('<svg viewBox="0 0 260 34" class="fstep" role="img" aria-hidden="true">\n'
            f'<polyline points="{" ".join(pts)}" fill="none" stroke="#7d7268" '
            'stroke-width=".9"/>\n</svg>')


# ---------------------------------------------------------------------------
# every series on the page, measured
# ---------------------------------------------------------------------------

def series(axis, rows, weeks):
    """The six quarterly series the four columns draw."""
    quarters = axis.quarters
    r = rows.assign(q=rows["date_read"].dt.to_period("Q"))
    S = {}
    S["art"] = [int(x) for x in
                r.groupby("q").size().reindex(quarters, fill_value=0).values]

    words = C.numeric_column(r, "word_count").fillna(0)
    wq = r.assign(w=words).groupby("q")["w"].sum().reindex(quarters, fill_value=0)
    total = 0
    S["wcum"] = []
    for v in wq.values:
        total += int(v)
        S["wcum"].append(total)

    wk = Counter(quarter_of_week(m["week"]) for m in weeks)
    S["wkq"] = [wk.get(q, 0) for q in quarters]
    covered = Counter()
    for m in weeks:
        covered[quarter_of_week(m["week"])] += int(m["article_count"])
    total = 0
    S["acum"] = []
    for q in quarters:
        total += covered.get(q, 0)
        S["acum"].append(total)

    # Sources are counted over the articles that kept a URL. The legacy import
    # kept none, which is why this pair starts flat and then starts at all.
    S["dq"], S["dcum"], seen = [], [], set()
    for q in quarters:
        here = {d for d in r[r["q"] == q]["domain"] if d}
        S["dq"].append(len(here))
        seen |= here
        S["dcum"].append(len(seen))
    S["dcum_start"] = next((i for i, v in enumerate(S["dcum"]) if v), 0)

    tagged = r[[bool(C.as_list(v)) for v in r[CANONICAL_COLUMN]]]
    S["vq"] = []
    for q in quarters:
        here = set()
        for v in tagged[tagged["q"] == q][CANONICAL_COLUMN]:
            here |= set(C.as_list(v))
        S["vq"].append(len(here))

    # Four quarters rather than one: a single quarter of 26 articles swings
    # this share by ten points, which draws noise as a trend.
    per_q = []
    for q in quarters:
        sub = tagged[tagged["q"] == q]
        per_q.append((sum(1 for v in sub[CANONICAL_COLUMN]
                          if AI_ENTRY in C.as_list(v)), len(sub)))
    S["airoll"] = []
    for i in range(axis.count):
        window = per_q[max(0, i - 3):i + 1]
        a, b = sum(x for x, _ in window), sum(y for _, y in window)
        S["airoll"].append(round(a / b * 100, 1) if b else 0.0)
    return S, tagged


def band_counts(rows):
    return {label: int(((rows["year"] >= lo) & (rows["year"] <= hi)).sum())
            for label, lo, hi in ERA_BANDS}


def longest_streak(weeks):
    """The longest run of consecutive ISO weeks that each carry a synthesis."""
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
    return best, best_start, best_end


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
        seam_before=int(per_year.get(OLD_ERA_LAST_YEAR, 0)),
        seam_after=int(per_year.get(SEAM_YEAR, 0)),
        bands=band_counts(rows), tagged=len(tagged),
        era_rows=C.era_split(rows),
        eras={era["label"]: era["articles"] for era in C.era_split(rows)},
        top_domains=[(d, c) for d, c in
                     Counter(d for d in rows["domain"] if d).most_common(5)],
        top_entries=C.top_entities(rows, CANONICAL_COLUMN, 5),
        vocab=len({x for v in tagged[CANONICAL_COLUMN] for x in C.as_list(v)}),
        latest=weeks[-1],
    )
    f["seam"] = (round((f["seam_after"] - f["seam_before"]) / f["seam_before"] * 100)
                 if f["seam_before"] else 0)
    f["tagged_pct"] = round(f["tagged"] / f["articles"] * 100, 1) if f["articles"] else 0.0
    f["url_pct"] = round(f["url_bearing"] / f["articles"] * 100) if f["articles"] else 0
    f["streak"], f["streak_from"], f["streak_to"] = longest_streak(weeks)

    def share(sub, name):
        if not len(sub):
            return 0.0
        return sum(1 for v in sub[CANONICAL_COLUMN]
                   if name in C.as_list(v)) / len(sub) * 100
    old = tagged[tagged["year"] <= OLD_ERA_LAST_YEAR]
    new = tagged[tagged["year"] >= NEW_ERA_FIRST_YEAR]
    f["ai_new"] = round(share(new, AI_ENTRY), 1)
    f["ai_old"] = round(share(old, AI_ENTRY), 1)
    counts = Counter(x for v in tagged[CANONICAL_COLUMN] for x in set(C.as_list(v)))
    floor = min(MOVED_MIN_ARTICLES, max(3, len(tagged) // 50))
    moved = sorted(((round(share(new, k) - share(old, k), 1), k)
                    for k, c in counts.items() if c >= floor), reverse=True)
    f["moved"] = moved[:MOVED_SHOWN] + moved[-MOVED_SHOWN:] if len(moved) > MOVED_SHOWN * 2 \
        else moved
    return f


# ---------------------------------------------------------------------------
# the page
# ---------------------------------------------------------------------------

def mini(items, bars=True):
    """A ranked list on hairline bars, or a plain key/value list when the
    values are not comparable quantities."""
    peak = max((v for _, v in items), default=1) if bars else 1
    out = ""
    for k, v in items:
        text = n(v) if isinstance(v, int) else str(v)
        if bars:
            out += (f'<li><span class="mk">{e(k)}</span>'
                    f'<span class="mb"><i style="width:{v / (peak or 1) * 100:.1f}%"></i></span>'
                    f'<span class="mv num">{e(text)}</span></li>')
        else:
            out += (f'<li><span class="mk">{e(k)}</span>'
                    f'<span class="mv num">{e(text)}</span></li>')
    return f'<ul class="mini{"" if bars else " dates"}">{out}</ul>'


def column(axis, title, strip_svg, step_label, step_svg, step_end,
           hero_label, hero_value, hero_note, secondaries, accent=False):
    ticks = "".join(f"<span>{e(t)}</span>" for t in axis.ticks)
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


def week_topics(meta):
    """"on energy and on AI" - the latest week's subjects, from its own record.

    The mockup typed that phrase by hand from reading the digest. It is read
    off `top_topics` here instead, the same field the weeks index reads for its
    own topic column, so the cover cannot go on describing last month's
    reading.

    The consequence, stated rather than discovered later: THIS CLAUSE IS
    INTERMITTENT. The weekly synthesis populates `top_topics` on some weeks and
    not others - 511 of the 827 week records carry it, and 4 of the last 12 -
    so the clause will appear and disappear from the cover between nightly
    rebuilds depending on what the newest week's synthesis extracted. That is
    the honest behaviour. The alternative, ranking the week's canonical entries
    from the index, was measured on 2026-W35 and returns six subjects tied at
    one article each, which is a coin flip printed as a fact.
    """
    names = []
    for t in (meta.get("top_topics") or [])[:2]:
        if isinstance(t, dict):
            names.append(str(t.get("name", "")).strip())
        elif isinstance(t, (list, tuple)) and t:
            names.append(str(t[0]).strip())
        else:
            names.append(str(t).strip())
    names = [x for x in names if x]
    if not names:
        return ""
    return ", on " + " and on ".join(names)


def columns(axis, S, f, deep_dives):
    s1 = strip(axis, S["art"], "articles read per quarter",
               "Articles read per quarter, with the average of each era")
    s2 = strip(axis, S["wkq"], "weeks carrying a synthesis",
               "Weeks with a written synthesis per quarter, with the average of each era")
    s3 = strip(axis, S["dq"], "distinct sources per quarter",
               "Distinct publications appearing each quarter, with the average of each era")
    s4 = strip(axis, S["vq"], "vocabulary entries in use per quarter",
               "Distinct controlled-vocabulary entries used each quarter, "
               "with the average of each era")
    m = f["latest"]
    years = axis.quarters[-1].year - axis.quarters[0].year + 1
    # An era with a share this small is a data artifact, not a way of saving:
    # the live index carries one row whose source is unattributable. It is
    # named under the list rather than drawn as a fourth bar nobody can see,
    # and the cut is a share rather than a count so a small corpus still
    # renders every era it has.
    saved = [(x["label"], x["articles"]) for x in f["era_rows"] if x["share"] >= 0.1]
    unattributed = sum(x["articles"] for x in f["era_rows"] if x["share"] < 0.1)
    top = f["top_domains"][0] if f["top_domains"] else ("none", 0)
    return "".join([
        column(
            axis, "Articles", s1, "words, cumulative", step(axis, S["wcum"]),
            f'{f["words"] / 1e6:.1f}M',
            "articles read", n(f["articles"]),
            f'over {years} years and three ways of saving',
            [{"k": "words", "v": f'{f["words"] / 1e6:.1f}<em>M</em>',
              "s": f'{n(f["words"])} in all'},
             {"k": "reading time", "v": f'{f["hours"]:,.0f}<em> hrs</em>',
              "s": "at 238 words a minute"},
             {"k": "busiest year", "v": str(f["best_year"]),
              "s": f'{n(f["best_year_n"])} articles, '
                   f'{f["best_year_words"] / 1e6:.1f}M words'},
             {"k": f'the {SEAM_YEAR} seam', "v": f'&minus;{abs(f["seam"])}<em>%</em>',
              "s": f'{n(f["seam_before"])} articles in {OLD_ERA_LAST_YEAR}, '
                   f'{n(f["seam_after"])} in {SEAM_YEAR}'}],
            accent=True),
        column(
            axis, "Weeks", s2, "articles covered, cumulative", step(axis, S["acum"]),
            n(f["covered"]),
            "weekly syntheses", n(f["weeks"]),
            "one page per ISO week, written on-device",
            [{"k": "longest streak", "v": n(f["streak"]),
              "s": f'weeks unbroken, {f["streak_from"]:%b %Y} to {f["streak_to"]:%b %Y}'},
             {"k": "quietest year", "v": str(f["quiet_year"]),
              "s": f'{f["quiet_year_weeks"]} weeks, {f["quiet_year_n"]} articles, '
                   f'{n(f["quiet_year_words"])} words'},
             {"k": "deep dives", "v": n(deep_dives["total"]),
              "s": f'{deep_dives["years"]} year rollups, {deep_dives["facets"]} facets'},
             {"k": "this week", "v": n(m["article_count"]),
              "s": f'articles in {e(str(m["week"]))}, {n(m["total_words"])} words'
                   f'{e(week_topics(m))}'}]),
        column(
            axis, "Sources", s3, "sources, cumulative",
            step(axis, S["dcum"], start=S["dcum_start"]), n(f["domains"]),
            "distinct sources", n(f["domains"]),
            f'counted over the {n(f["url_bearing"])} articles that kept a link',
            [{"k": "carries a link", "v": f'{f["url_pct"]}%',
              "s": f'{n(f["url_bearing"])} of {n(f["articles"])} articles'},
             {"k": "most read", "v": n(top[1]),
              "s": f'{e(top[0])}, '
                   f'{round(top[1] / f["url_bearing"] * 100) if f["url_bearing"] else 0}% '
                   f'of linked reading'},
             {"k": "how it was saved", "mini": mini(saved),
              "s": (f'{n(unattributed)} article{"" if unattributed == 1 else "s"} '
                    f'unattributed' if unattributed else "")},
             {"k": "top publications", "mini": mini(f["top_domains"])}]),
        column(
            axis, "Subjects", s4, "AI share, four quarters",
            step(axis, S["airoll"], start=min(3, axis.count - 1)),
            f'{S["airoll"][-1]}%',
            "artificial intelligence", f'{f["ai_new"]}<em>%</em>',
            f'of tagged reading since {NEW_ERA_FIRST_YEAR}; it was {f["ai_old"]}% '
            f'before {SEAM_YEAR}',
            [{"k": "tagged", "v": f'{f["tagged_pct"]}<em>%</em>',
              "s": f'{n(f["tagged"])} of {n(f["articles"])} articles'},
             {"k": "vocabulary", "v": n(f["vocab"]),
              "s": "curated entries, every one in use"},
             {"k": "commonest subjects",
              "mini": mini([(x["name"], x["count"]) for x in f["top_entries"]])},
             {"k": "what moved",
              "mini": mini([(k, f'{v:+}'.replace("-", "−"))
                            for v, k in f["moved"]], bars=False),
              "s": f'points of share, pre-{SEAM_YEAR} against '
                   f'{NEW_ERA_FIRST_YEAR} on'}]),
    ])


COVER_TITLE = "A Reading Life"

BODY = """  <header class="masthead">
    <span class="label kicker">{{DOMAIN}}</span>
    <h1>{{TITLE}}</h1>
    <p class="dateline num">{{SPAN}} &nbsp;&middot;&nbsp; {{ARTICLES}} articles held in
    one index &nbsp;&middot;&nbsp; as of {{TODAY}}</p>
  </header>

  <div class="fgrid">{{COLUMNS}}</div>

  <p class="fnote"><b>How to read the dates.</b> This archive is two archives wearing one
  schema. The {{LEGACY}} legacy files carry no links and no read events: their date is the
  publication date parsed out of a filename, so a quarter before {{SEAM}} says when the writing
  appeared, not when it was read. The {{MODERN}} Instapaper and Matter articles from {{SEAM}} on
  carry real save and archive dates. Two thirds of the corpus, {{PROXY}} articles, is dated by
  that proxy. Every strip and step runs on one shared axis of quarters from {{AXIS}}, and the
  amber lines are the average of each era, annotated with their value. There is no time of day
  anywhere in this archive, so nothing here is drawn by hour.</p>

  <footer>
    <span class="label">The weekly syntheses are at <a href="weeks/">/weeks/</a></span>
    <span class="label num">Generated {{GENERATED}}</span>
  </footer>"""


def render_cover(corpus_data, weeks, deep_dives, site_title="The Week in Reading",
                 domain="reading.adamthede.com", today=None):
    """The cover, as a complete document.

    `deep_dives` is {"years": int, "facets": int, "total": int} - what the build
    actually wrote, so the "deep dives" secondary counts pages rather than
    repeating a number someone typed into a mockup.
    """
    rows = corpus_data.rows
    axis = axis_for(rows, weeks)
    S, tagged = series(axis, rows, weeks)
    f = facts(rows, weeks, tagged)
    today = today or dt.date.today()
    # The note names Instapaper and Matter, so it counts Instapaper and
    # Matter. The live index also carries one row whose source is
    # unattributable, and folding it in here would put a number in the
    # sentence that the sentence does not describe; the Sources column names
    # it instead.
    legacy = f["eras"].get("Legacy files", 0)
    modern = sum(x["articles"] for x in f["era_rows"]
                 if x["label"] != "Legacy files" and x["share"] >= 0.1)

    body = BODY.replace("{{COLUMNS}}", columns(axis, S, f, deep_dives))
    tokens = {
        "TITLE": e(COVER_TITLE), "DOMAIN": e(domain),
        "ARTICLES": n(f["articles"]),
        "SPAN": f"{axis.quarters[0].year} to {axis.quarters[-1].year}",
        "TODAY": today.strftime("%-d %B %Y"),
        "GENERATED": today.isoformat(),
        "LEGACY": n(legacy), "MODERN": n(modern), "PROXY": n(f["proxy"]),
        "SEAM": str(SEAM_YEAR), "AXIS": axis.span,
    }
    for k, v in tokens.items():
        body = body.replace("{{" + k + "}}", v)
    assert "{{" not in body, "an unreplaced token survived into the cover"
    return page(f"{COVER_TITLE} — {site_title}", body, depth=0, here="cover",
                canonical=f"https://{domain}/", wide=True)


# ---------------------------------------------------------------------------
# the cover's own rules, appended to the site stylesheet the way
# deepdives.EXTRA_STYLE already is. Every token here is declared in
# generate.STYLE's single :root - none of these rules invents a colour.
# ---------------------------------------------------------------------------

COVER_STYLE = """
/* ---- the cover ---- */
.page.wide { max-width:1180px; }
.wide header.masthead { border-bottom:none; padding-bottom:0; }
.wide h1 { font-family:var(--serif); font-weight:400; font-size:88px;
  line-height:.96; letter-spacing:-.022em; }
.wide .dateline { margin-top:18px; font-family:var(--mono); font-size:11.5px;
  letter-spacing:.1em; color:var(--ink-4); text-transform:uppercase; }
.fgrid { display:grid; grid-template-columns:repeat(4,1fr); gap:40px;
  margin-top:66px; }
.fcol { min-width:0; }
.ftitle { font-family:var(--serif); font-size:27px; font-weight:400;
  letter-spacing:-.012em; color:var(--ink); padding-bottom:9px;
  border-bottom:1px solid var(--rule); margin:0; }
.fticks { margin-top:9px; color:var(--ink-4); font-size:9px; letter-spacing:.06em;
  display:flex; justify-content:space-between; }
.fstrip { display:block; width:100%; height:auto; margin-top:16px; }
.fstrip .favg { font-family:var(--mono); font-size:7px; fill:var(--amber);
  letter-spacing:.03em; }
.fstrip .ftick { font-family:var(--mono); font-size:7px; fill:#6d635c;
  letter-spacing:.06em; }
.fsteprow { margin-top:22px; padding-top:11px; border-top:1px dotted var(--rule); }
.fsl { display:block; color:var(--ink-4); }
.fstep { display:block; width:100%; height:auto; margin-top:5px; }
.fsev { display:block; text-align:right; font-family:var(--mono); font-size:11px;
  color:var(--ink-2); margin-top:2px; }
.fhero { display:block; margin-top:36px; padding-top:16px;
  border-top:1px solid var(--rule); }
.fhl { display:block; color:var(--ink-3); line-height:1.4; }
.fhv { display:block; font-family:var(--serif); font-size:74px; font-weight:400;
  line-height:.92; letter-spacing:-.03em; color:var(--ink-2); margin:10px 0 0; }
.fhv em { font-style:normal; font-size:.34em; color:var(--ink-4); letter-spacing:0; }
.fhero.accent .fhv { color:var(--amber); }
.fhd { display:block; margin-top:12px; color:var(--ink-4); line-height:1.55; }
.fsec { display:grid; grid-template-columns:1fr 1fr; margin-top:34px; }
.fs { display:block; padding:15px 12px 15px 0; border-top:1px dotted var(--rule); }
.fs:nth-child(even) { padding-left:14px; border-left:1px dotted var(--rule); }
.fs.wide { grid-column:1/3; padding-right:0; padding-left:0; border-left:none; }
.fsk { display:block; color:var(--ink-4); line-height:1.4; }
.fsv2 { display:block; font-size:25px; font-weight:200; letter-spacing:-.02em;
  color:var(--ink); margin-top:8px; font-variant-numeric:tabular-nums; }
.fsv2 em { font-style:normal; font-size:.44em; color:var(--ink-4); margin-left:1px; }
.fss { display:block; margin-top:6px; font-family:var(--mono); font-size:9.5px;
  color:var(--ink-4); line-height:1.5; letter-spacing:.02em; }
.mini { list-style:none; margin-top:9px; }
.mini li { display:grid; grid-template-columns:1fr 40px; gap:5px; align-items:center;
  padding:3px 0; font-family:var(--mono); font-size:9.5px; color:var(--ink-3); }
.mini .mk { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.mini .mb { grid-column:1/3; grid-row:2; height:1.5px; background:#2f2a27;
  margin:-1px 0 3px; }
.mini .mb i { display:block; height:100%; background:var(--amber); opacity:.65; }
.mini .mv { text-align:right; color:var(--ink-2); }
.mini.dates li { grid-template-columns:1fr auto; }
.fnote { max-width:820px; margin:54px 0 0; font-family:var(--mono); font-size:10.5px;
  line-height:1.85; color:var(--ink-4); letter-spacing:.02em; }
.fnote b { color:var(--ink-2); font-weight:400; }
@media (max-width:1080px){ .fgrid { grid-template-columns:1fr 1fr; gap:44px; } }
@media (max-width:760px){
  .wide h1 { font-size:48px; }
  .fgrid { grid-template-columns:1fr; gap:52px; }
  .fhv { font-size:62px; } }
"""
