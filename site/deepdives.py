"""Phase 5 deep-dive pages: year rollups, the orgs facet, article detail.

These render from the Parquet index (site/corpus.py), unlike the week pages
which render from synthesis frontmatter. Same skin, same escaping discipline,
same tooltip idiom - EXTRA_STYLE appends to generate.py's stylesheet rather
than forking it.

Two audit rulings are enforced here rather than merely respected:

- **Orgs can be ranked; topics cannot.** The top 20 orgs cover ~45% of the
  archive; the topics vocabulary is ~29k strings, three quarters of them used
  exactly once. There is no topic river and no topic ranking on any page in
  this module. That is a data fact, not a design preference - and the figures
  the pages print are measured at build time by org_note(), never quoted from
  the audit.
- **One JSON payload, not 17k static pages.** The article detail view is
  client-side over a single compact payload, which keeps the nightly
  Wrangler deploy small.
"""
import json

from corpus import (RANKABLE_HEAD_COVERAGE, complexity_stats, entity_coverage,
                    head_coverage, month_series, payload_rows, stats,
                    top_entities, topic_vocabulary, vocabulary_report)
from htmlkit import e, n, page, safe_url

# The payload ships on every load of /articles/. The measured raw size at
# 17.9k rows is ~2.8 MB; this cap is the tripwire for a future corpus that
# grows a summary column by accident, not a prediction.
MAX_PAYLOAD_BYTES = 6 * 1024 * 1024

# How many boilerplate casts /people/ spells out before it summarises.
MAX_CASTS_NAMED = 4


def org_note(rows, vocab_rows=None):
    """Why orgs are ranked here and topics are not.

    The two halves take different scopes on purpose. Head coverage describes
    the list printed directly above it, so it is measured on `rows` - an
    earlier draft measured the whole corpus and printed "the top 20 cover
    45.2% of the 16,346 articles counted here" over a year page's 513.

    The topic half is an argument about the archive's vocabulary, and the
    singleton share is monotone in sample size (100% at n=3, 74.2% at
    n=16,346). Year-scoping it made the sentence contradict itself: on
    /years/2021/ both halves read 100%, so the contrast the sentence exists
    to draw - orgs can be ranked, topics cannot - collapsed on the page
    stating it. `vocab_rows` therefore stays archive-wide.

    Output is int and float interpolation only - no corpus strings reach it -
    but callers still escape it, because that is not a property to rely on.
    """
    head = head_coverage(rows, "orgs", 20)
    vocab, singles = topic_vocabulary(rows if vocab_rows is None else vocab_rows)
    return (f"Organizations are the one entity field this archive can rank honestly: "
            f"the top 20 cover {head:,.1f}% of the {n(len(rows))} articles "
            f"counted here. Topics cannot be ranked this way - across the whole "
            f"archive that vocabulary is {n(vocab)} free-text strings, "
            f"{singles:,.1f}% of them used exactly once.")


EXTRA_STYLE = """
/* deep dives: year rollups, orgs facet, article detail */
.months { display:flex; gap:3px; align-items:flex-end; height:150px; margin-top:8px; }
.mo { flex:1; display:flex; flex-direction:column; justify-content:flex-end; height:100%; }
.mo .bar { background:var(--amber-dim); border-radius:3px 3px 0 0; min-height:2px; }
.mo.peak .bar { background:var(--amber); }
.mo:hover .bar { background:var(--brand); }
.mo .ml { text-align:center; margin-top:8px; }
.mo .mv { text-align:center; font-size:11px; color:var(--ink-2); margin-bottom:4px; }
.mo.peak .mv { color:var(--amber); }
.orow { display:grid; grid-template-columns:26px 1fr 58px; gap:12px;
  align-items:baseline; padding:6px 0; border-bottom:1px solid #2a2523; }
.orow .rk { font-size:11.5px; color:var(--ink-3); text-align:right;
  font-family:ui-monospace,"SF Mono",Menlo,monospace; }
/* min-width:0 or the 1fr track grows to fit a nowrap name instead of
   ellipsizing it, pushing the count column off a 390px screen. */
.orow .on { min-width:0; font-size:14.5px; overflow:hidden;
  text-overflow:ellipsis; white-space:nowrap; }
.orow .on .obar { display:block; height:3px; border-radius:2px;
  background:var(--amber-dim); margin-top:5px; }
.orow.lead .on { color:var(--amber); }
.orow.lead .obar { background:var(--amber); }
.orow .oc { font-size:12.5px; color:var(--ink-2); text-align:right; }
.note { margin-top:14px; color:var(--ink-3); font-size:12.5px; line-height:1.6;
  max-width:640px; }
.yearnav { display:flex; justify-content:space-between; margin-top:48px;
  padding-top:16px; border-top:1px solid var(--rule); }
.yearnav a { text-decoration:none; color:var(--ink-2); font-size:14px; }
.yearnav a:hover { color:var(--brand); }
.yearnav .home { color:var(--ink-3); font-family:ui-monospace,"SF Mono",Menlo,monospace;
  font-size:11px; letter-spacing:.14em; text-transform:uppercase; }
.wklist { display:flex; flex-wrap:wrap; gap:6px; margin-top:10px; }
.wklist a { display:inline-block; padding:5px 9px; background:var(--bg-raise);
  border-radius:3px; font-size:12.5px; text-decoration:none; color:var(--ink-2);
  font-family:ui-monospace,"SF Mono",Menlo,monospace; }
.wklist a:hover { color:var(--brand); }
.wklist a .c { color:var(--amber); }
.yearheads { display:flex; flex-wrap:wrap; gap:8px; margin-top:10px; }
.yearheads a { font-size:15px; color:var(--ink-2); text-decoration:none;
  padding:4px 8px; background:var(--bg-raise); border-radius:3px; }
.yearheads a:hover { color:var(--brand); }
.yearhead a { color:inherit; text-decoration:none; }
.yearhead a:hover { color:var(--brand); }
/* article detail */
.search { width:100%; max-width:640px; margin-top:8px; padding:11px 13px;
  background:var(--bg-raise); border:1px solid var(--rule); border-radius:4px;
  color:var(--ink); font-size:15px; font-family:inherit; }
.search:focus { outline:none; border-color:var(--amber); }
.hits { margin-top:12px; }
.arow { display:grid; grid-template-columns:78px 1fr 62px; gap:12px;
  align-items:baseline; padding:7px 0; border-bottom:1px solid #2a2523;
  cursor:pointer; width:100%; text-align:left; background:none; border-left:0;
  border-right:0; border-top:0; color:inherit; font:inherit; }
.arow:hover, .arow:focus { background:var(--bg-raise); outline:none; }
.arow .ad { font-size:11.5px; color:var(--ink-3);
  font-family:ui-monospace,"SF Mono",Menlo,monospace; }
.arow .at { min-width:0; font-size:14.5px; overflow:hidden;
  text-overflow:ellipsis; white-space:nowrap; }
.arow .at .asrc { color:var(--ink-3); font-size:12px; margin-left:8px; }
.arow .aw { font-size:12.5px; color:var(--ink-2); text-align:right;
  font-variant-numeric:tabular-nums; }
.detail { margin-top:24px; padding:20px; background:var(--bg-raise);
  border-left:2px solid var(--brand); }
.detail h2 { font-size:21px; font-weight:300; line-height:1.35; }
.detail h2 a { color:var(--amber); text-decoration:none; }
.detail h2 a:hover { color:var(--brand); }
.detail dl { display:grid; grid-template-columns:auto 1fr; gap:6px 18px;
  margin-top:14px; font-size:13.5px; }
.detail dt { font-family:ui-monospace,"SF Mono",Menlo,monospace; font-size:11px;
  letter-spacing:.14em; text-transform:uppercase; color:var(--ink-3); }
.detail dd { color:var(--ink-2); overflow-wrap:anywhere; }
@media (max-width:560px){
  .orow { grid-template-columns:22px 1fr 46px; }
  .arow { grid-template-columns:66px 1fr; } .arow .aw { display:none; }
  .months { gap:2px; } .mo .mv { display:none; }
}
"""


# ---------------------------------------------------------------------------
# shared fragments
# ---------------------------------------------------------------------------

def _stat(value_html, label, sub=""):
    """A stat tile. `value_html` is markup the caller built from formatted
    numerals (never from corpus data); `label` and `sub` are escaped here."""
    sub_html = f'<div class="delta">{e(str(sub))}</div>' if sub else ""
    return (f'<div class="stat"><div class="v num">{value_html}</div>'
            f'<div class="l label">{e(label)}</div>{sub_html}</div>')


def _org_rows(orgs, denominator, scope="the year", noun="organizations"):
    if not orgs:
        return f'      <span class="empty">no {e(noun)} tagged</span>\n'
    top = max((o["count"] for o in orgs), default=1) or 1
    out = ""
    for i, o in enumerate(orgs, start=1):
        pct = max(o["count"] / top * 100, 2)
        share = (o["count"] / denominator * 100) if denominator else 0
        lead = " lead" if i == 1 else ""
        # `scope` is the denominator's noun: the same helper renders a year
        # page and the all-time facet, and a share of the whole archive
        # labelled "of the year" is simply a wrong number.
        unit = "article" if o["count"] == 1 else "articles"
        tip = f"{o['name']} - {n(o['count'])} {unit}, {share:.1f}% of {scope}"
        out += (f'      <div class="orow{lead}" data-tip="{e(tip)}">'
                f'<span class="rk">{i}</span>'
                f'<span class="on">{e(str(o["name"]))}'
                f'<span class="obar" style="width:{pct:.0f}%"></span></span>'
                f'<span class="oc num">{n(o["count"])}</span></div>\n')
    return out


def _provenance(st):
    """Both halves of the audit's two-corpora seam, rendered where they bite.

    The dates half: a legacy row's date came out of a filename, so it is a
    publication date wearing a read date's clothes. The URLs half: the legacy
    corpus carries no URLs at all, so a source count on a pre-2012 year is a
    count over a tiny subset - five year pages used to show a large light 0
    under "Sources" and two more showed a number computed over a tenth of the
    year without saying so.
    """
    if not st["articles"]:
        return ""
    lines = []
    if st["proxy_dated"]:
        share = st["proxy_dated"] / st["articles"] * 100
        if share >= 99.5:
            lines.append("Dates approximate - this pre-tracking-era year is dated by "
                         "publication or save, not by a recorded read")
        else:
            lines.append(f"{n(st['proxy_dated'])} of {n(st['articles'])} articles "
                         f"({share:.0f}%) carry approximate dates - saved or "
                         f"published, not read")
    if st["url_bearing"] == 0:
        lines.append("No URLs in this era - these articles came in as files, so there "
                     "is no source to count")
    elif st["url_bearing"] < st["articles"]:
        lines.append(f"Sources are counted over the {n(st['url_bearing'])} of "
                     f"{n(st['articles'])} articles that carry a URL")
    return "".join(f'\n    <div class="provenance label">{e(t)}</div>' for t in lines)


# ---------------------------------------------------------------------------
# year rollups
# ---------------------------------------------------------------------------

def _densest_line(comp):
    """The year's hardest substantial read, linked when it has a URL.

    Restricted to in-band grades and articles of `min_words`+ on purpose: the
    unclipped maximum in this corpus is grade 857, and at no word floor at all
    the winner is reliably a 300-word stub with one runaway sentence.
    """
    densest = comp.get("densest")
    if not densest:
        return ""
    title = e(str(densest["title"]))
    href = safe_url(densest["url"])
    linked = f'<a class="atitle" href="{href}">{title}</a>' if href else \
        f'<strong class="atitle">{title}</strong>'
    lead = e(f"Densest read of the year, among articles over "
             f"{n(comp['min_words'])} words: ")
    tail = e(f" — grade {densest['grade']:.1f}, {n(densest['words'])} words")
    return f'    <div class="note">{lead}{linked}{tail}</div>\n'


def render_year(corpus, year, weeks_in_year=(), prev_year=None, next_year=None,
                site_title="The Week in Reading", domain=""):
    year = int(year)
    rows = corpus.year(year)
    st = stats(rows)
    comp = complexity_stats(rows)
    # vs the previous year that HAS a page - corpus-adjacent, matching the
    # week page's delta idiom rather than inventing a calendar-adjacent one.
    prev_comp = complexity_stats(corpus.year(prev_year)) if prev_year else None
    if comp["avg"] is not None and prev_comp and prev_comp["avg"] is not None:
        diff = round(comp["avg"] - prev_comp["avg"], 2)
        if diff:
            arrow = "▲" if diff > 0 else "▼"
            grade_sub = f"{arrow} {abs(diff):.2f} vs {prev_year}"
        else:
            grade_sub = f"= {prev_year}"
    elif comp["avg"] is not None:
        grade_sub = f"grade, clipped 0–20 · {n(comp['graded'])} articles"
    else:
        grade_sub = ""
    grade_tile = (_stat(f"{comp['avg']:.1f}", "Average reading level", grade_sub)
                  if comp["avg"] is not None else "")
    months = month_series(rows, year)
    peak = max((m["words"] for m in months), default=0)
    month_html = ""
    for m in months:
        pct = (m["words"] / peak * 100) if peak else 0
        cls = " peak" if m["words"] == peak and peak else ""
        mv = f"{m['words']/1000:.0f}k" if m["words"] >= 950 else (
            f"{m['words']/1000:.1f}k" if m["words"] else "—")
        tip = (f"{m['label']} {year} — {n(m['words'])} words, "
               f"{n(m['count'])} article{'s' if m['count'] != 1 else ''}"
               if m["count"] else f"{m['label']} {year} — no reading")
        month_html += (f'      <div class="mo{cls}" data-tip="{e(tip)}">'
                       f'<div class="mv num">{mv}</div>'
                       f'<div class="bar" style="height:{pct:.1f}%"></div>'
                       f'<div class="ml label">{e(m["label"])}</div></div>\n')

    orgs = top_entities(rows, "orgs", 20)
    week_html = ""
    for w in weeks_in_year:
        week_html += (f'      <a href="../../weeks/{e(str(w["week"]))}/">'
                      f'{e(str(w["week"]).split("-")[-1])}'
                      f'<span class="c num"> · {n(w["article_count"])}</span></a>\n')
    if not week_html:
        week_html = ('      <span class="empty">no weekly synthesis pages for this '
                     'year yet</span>\n')

    left = (f'<a href="../{e(str(prev_year))}/">← {e(str(prev_year))}</a>'
            if prev_year else "<span></span>")
    right = (f'<a href="../{e(str(next_year))}/">{e(str(next_year))} →</a>'
             if next_year else "<span></span>")
    sources_tile = (_stat("—", "Sources", "no URLs in this era")
                    if st["url_bearing"] == 0
                    else _stat(n(st["domains"]), "Sources",
                               f"across {n(st['url_bearing'])} articles with a URL"
                               if st["url_bearing"] < st["articles"] else ""))

    body = f"""  <header>
    <a class="label kicker" href="../../">{e(site_title)}</a>
    <h1>{e(str(year))}</h1>
    <div class="daterange num">{n(st['articles'])} articles · {n(st['words'])} words</div>{_provenance(st)}
  </header>

  <div class="stats">
    {_stat(n(st['articles']), 'Articles read')}
    {_stat(n(st['words']), 'Words')}
    <div class="stat time"><div class="v num">{st['hours']:,.1f}<em> hrs</em></div><div class="l label">Reading time</div></div>
    {_stat(n(st['median_words']), 'Median length · words')}
    {grade_tile}
    {sources_tile}
  </div>

  <section>
    <div class="label viz-title">The year's rhythm · words per month</div>
    <div class="months">
{month_html}    </div>
{_densest_line(comp)}  </section>

  <section>
    <div class="label viz-title">Organizations in the year's reading · top {len(orgs)} by articles</div>
    <div class="roster">
{_org_rows(orgs, st['articles'], scope='the year')}    </div>
    <div class="note">{e(org_note(rows, vocab_rows=corpus.rows))}</div>
  </section>

  <section>
    <div class="label viz-title">Weekly syntheses from {e(str(year))}</div>
    <div class="wklist">
{week_html}    </div>
  </section>

  <div class="yearnav">{left}<a class="home" href="../../">All weeks</a>{right}</div>
  <footer>
    <span class="label">Computed from the archive index · corrupted rows excluded</span>
    <span class="label num">{e(domain)}</span>
  </footer>"""
    return page(f"{year} — {site_title}", body, depth=2)


# ---------------------------------------------------------------------------
# orgs facet
# ---------------------------------------------------------------------------

def render_orgs(corpus, limit=100, site_title="The Week in Reading", domain=""):
    rows = corpus.rows
    orgs = top_entities(rows, "orgs", limit)
    all_orgs = top_entities(rows, "orgs", 10 ** 9)
    coverage = entity_coverage(rows, "orgs")
    head_share = head_coverage(rows, "orgs", 20)

    footnote = (
        "Unlike anything keyed on a URL, this page covers the whole archive: entity "
        "extraction ran over both corpora, so the file-sourced legacy era is counted "
        "here even though it carries no URLs (URL coverage runs from 0% before 2010 to "
        "effectively 100% from 2014 on). Two caveats stand. Counts come from an "
        "enrichment pass with no controlled vocabulary, so an organization named several "
        "ways is ranked several times - 'The New York Times' and 'The New York Times "
        "Company' are two rows below. And some rankings are partly site furniture the "
        "scraper captured instead of the article; the obviously contaminated rows are "
        "excluded as corrupted, but the flagging is not exhaustive."
    )

    body = f"""  <header>
    <a class="label kicker" href="../">{e(site_title)}</a>
    <h1>Organizations</h1>
    <div class="daterange">Who the reading was about, across {n(len(rows))} articles.</div>
  </header>

  <div class="stats">
    {_stat(n(len(all_orgs)), 'Distinct organizations')}
    {_stat(f'{coverage:,.1f}<em>%</em>', 'Articles tagged')}
    {_stat(f'{head_share:,.1f}<em>%</em>', 'Covered by the top 20')}
  </div>

  <section>
    <div class="label viz-title">Ranked by articles · top {len(orgs)}</div>
    <div class="roster">
{_org_rows(orgs, len(rows), scope='the archive')}    </div>
    <div class="note">{e(org_note(rows))}</div>
    <div class="note">{e(footnote)}</div>
  </section>

  <div class="yearnav"><span></span><a class="home" href="../">All weeks</a><span></span></div>
  <footer>
    <span class="label">Computed from the archive index · corrupted rows excluded</span>
    <span class="label num">{e(domain)}</span>
  </footer>"""
    return page(f"Organizations — {site_title}", body, depth=1)


# ---------------------------------------------------------------------------
# locations facet
# ---------------------------------------------------------------------------

def render_locations(corpus, limit=100, site_title="The Week in Reading", domain=""):
    """Modelled on /orgs/, and it earns the same treatment on the same test.

    Places clear the ranking bar more comfortably than organizations do - top-20
    article coverage of 57.0% against 45.2%, over a vocabulary a third the size
    - so this page ranks rather than hedges. Everything it prints about that is
    measured here at build time, not quoted.
    """
    rows = corpus.rows
    places = top_entities(rows, "locations", limit)
    report = vocabulary_report(rows, "locations")

    footnote = (
        "Like organizations and unlike anything keyed on a URL, this covers the whole "
        "archive: entity extraction ran over both corpora, so the file-sourced legacy "
        "era is counted here even though it carries no URLs. The same caveat applies "
        "as everywhere else in this enrichment - there is no controlled vocabulary, so "
        "a place named several ways is ranked several times. 'United States', 'U.S.' "
        "and 'America' are three separate rows below, and 'New York' does not "
        "distinguish the city from the state. Read the ranking as which names the "
        "articles used, not as a census of places."
    )
    note = (
        f"Places rank as cleanly as anything in this archive: the top {report['head_k']} "
        f"cover {report['head_coverage']:,.1f}% of the {n(len(rows))} articles counted "
        f"here, over a vocabulary of {n(report['vocabulary'])} strings, "
        f"{report['singleton_share']:,.1f}% of them used exactly once. "
        f"{report['tagged_share']:,.1f}% of articles carry at least one."
    )

    body = f"""  <header>
    <a class="label kicker" href="../">{e(site_title)}</a>
    <h1>Places</h1>
    <div class="daterange">Where the reading was set, across {n(len(rows))} articles.</div>
  </header>

  <div class="stats">
    {_stat(n(report['vocabulary']), 'Distinct places')}
    {_stat(f"{report['tagged_share']:,.1f}<em>%</em>", 'Articles tagged')}
    {_stat(f"{report['head_coverage']:,.1f}<em>%</em>", 'Covered by the top 20')}
  </div>

  <section>
    <div class="label viz-title">Ranked by articles · top {len(places)}</div>
    <div class="roster">
{_org_rows(places, len(rows), scope='the archive', noun='places')}    </div>
    <div class="note">{e(note)}</div>
    <div class="note">{e(footnote)}</div>
    <div class="note"><a href="../trends/">Places by year, as a heatmap →</a></div>
  </section>

  <div class="yearnav"><span></span><a class="home" href="../">All weeks</a><span></span></div>
  <footer>
    <span class="label">Computed from the archive index · corrupted rows excluded</span>
    <span class="label num">{e(domain)}</span>
  </footer>"""
    return page(f"Places — {site_title}", body, depth=1)


def render_people(corpus, limit=100, site_title="The Week in Reading", domain=""):
    """Who the reading was about - and the page where the cleanup is visible.

    This field does NOT clear the ranking bar the rest of the site holds itself
    to: the top 20 people appear in ~18% of articles, against 45% for
    organizations and 57% for places, and four fifths of the 41,514 names are
    used exactly once. It is ranked here anyway, for a reason the page states in
    its own words: a top-15 list is a weaker claim than a river, the head is
    made of proper nouns rather than the generic abstractions that sank the
    concepts page, and this is the surface where the archive's one measured
    fabrication was visible. Two Fast Company staffers used to rank above Tim
    Cook here. They no longer appear at all, and the page says why.

    People deliberately gets NO heatmap row on /trends/. The bar governs
    whether a field can carry a time series, and 18% cannot.
    """
    rows = corpus.rows
    people = top_entities(rows, "people", limit)
    report = vocabulary_report(rows, "people")
    # Measured here, not quoted: the comparison is the whole argument of this
    # paragraph, and a pasted figure drifts away from the corpus it describes.
    orgs_cov = vocabulary_report(rows, "orgs")["head_coverage"]
    locs_cov = vocabulary_report(rows, "locations")["head_coverage"]

    note = (
        f"The thinnest of the ranked fields, and it is ranked here with that said "
        f"out loud: the top {report['head_k']} names appear in {report['head_coverage']:,.1f}% "
        f"of the {n(len(rows))} articles counted here, against {orgs_cov:,.1f}% for "
        f"organizations and {locs_cov:,.1f}% for places. "
        f"The vocabulary runs {n(report['vocabulary'])} names, "
        f"{report['singleton_share']:,.1f}% of them mentioned exactly once, and "
        f"{report['tagged_share']:,.1f}% of articles carry any name at all. A long tail "
        f"like that means the list below is a real head over a very wide base - not a "
        f"summary of the archive."
    )

    if corpus.people_clusters:
        hosts = sorted({c["host"] for c in corpus.people_clusters})
        leaked = corpus.scrubbed_people_in_corpus
        # One sentence PER CLUSTER. Unioning the casts and then calling them
        # identical is exactly the unearned claim this page exists to refuse:
        # the archive holds two variants, and 229 of the 283 rows never listed
        # the name that a union would put in front of all of them.
        # Capped. The rule groups on an EXACT cast, so a site that reshuffled
        # its navigation would split one defect into dozens of near-identical
        # groups - and this paragraph would become dozens of sentences saying
        # almost the same thing. Name the big ones, count the rest.
        ranked = sorted(corpus.people_clusters,
                        key=lambda c: (-len(c["row_ids"]), c["host"]))
        casts = []
        for c in ranked[:MAX_CASTS_NAMED]:
            casts.append(f"{n(len(c['row_ids']))} of them, all "
                         f"{n(c['word_count'])} words long, list "
                         f"{', '.join(c['names'])}")
        rest = ranked[MAX_CASTS_NAMED:]
        if rest:
            casts.append(f"the remaining {n(sum(len(c['row_ids']) for c in rest))} "
                         f"across {n(len(rest))} further variants of the same cast")
        variants = ("in one cast" if len(casts) == 1
                    else f"in {len(casts)} near-identical casts")
        cleanup = (
            f"{n(corpus.scrubbed_people)} articles in the index carry a cast that was "
            f"never in them, {variants}, all from "
            f"{', '.join(hosts)}. {'; '.join(casts)}. The scraper captured the site's "
            f"navigation furniture instead of the article and the enrichment pass then "
            f"read entities out of the furniture. Nobody read those articles about "
            f"those people, and their casts are set aside before anything on this page "
            f"is counted."
        )
        # Credit only for what this actually changed HERE. On the current index
        # every one of those rows is also flagged as corrupted content, so this
        # ranking was clean before the rule existed - and saying otherwise
        # would be the same kind of unearned confidence the rule exists to fix.
        if leaked:
            cleanup += (
                f" {n(leaked)} of them clear this site's other filters, so without that "
                f"step they would be sitting in the ranking above.")
        else:
            cleanup += (
                " None of them clear this site's corrupted-content filter, so this "
                "particular ranking was already clean and the rule changes nothing you "
                "can see here. It earns its place upstream, in the index itself, where "
                "those names outranked Tim Cook.")
    else:
        cleanup = ("No boilerplate entity clusters were found in this index at build "
                   "time.")

    footnote = (
        "As with organizations and places, entity extraction ran over both corpora, so "
        "the file-sourced legacy era is counted here despite carrying no URLs — and "
        "that is how the reader's own name reaches this list, since his own documents "
        "came in through the legacy import and the enrichment pass extracted him as a "
        "subject of his own archive. No controlled vocabulary: a person named several "
        "ways ranks several times."
    )

    body = f"""  <header>
    <a class="label kicker" href="../">{e(site_title)}</a>
    <h1>People</h1>
    <div class="daterange">Who the reading was about, across {n(len(rows))} articles.</div>
  </header>

  <div class="stats">
    {_stat(n(report['vocabulary']), 'Distinct names')}
    {_stat(f"{report['tagged_share']:,.1f}<em>%</em>", 'Articles tagged')}
    {_stat(f"{report['head_coverage']:,.1f}<em>%</em>", 'Covered by the top 20')}
    {_stat(n(corpus.scrubbed_people), 'Fabricated casts dropped',
           'articles, before any other filter')}
  </div>

  <section>
    <div class="label viz-title">Ranked by articles · top {len(people)}</div>
    <div class="roster">
{_org_rows(people, len(rows), scope='the archive', noun='people')}    </div>
    <div class="note">{e(note)}</div>
  </section>

  <section>
    <div class="label viz-title">What was taken out of this list</div>
    <div class="note">{e(cleanup)}</div>
    <div class="note">{e(footnote)}</div>
    <div class="note"><a href="../orgs/">Organizations</a> · <a href="../locations/">Places</a> · <a href="../trends/">Both by year, as heatmaps →</a></div>
  </section>

  <div class="yearnav"><span></span><a class="home" href="../">All weeks</a><span></span></div>
  <footer>
    <span class="label">Computed from the archive index · corrupted rows excluded</span>
    <span class="label num">{e(domain)}</span>
  </footer>"""
    return page(f"People — {site_title}", body, depth=1)


def concepts_verdict(corpus):
    """Whether /concepts/ may be built, with the numbers behind the answer.

    Phase 5b measured the concepts vocabulary against the bar the audit set with
    orgs (rankable) and topics (not), and it failed: top-20 article coverage of
    22.0% against the 40% bar, over 50,601 strings, 74.0% of them used exactly
    once - worse than topics, which already lost this argument. So there is no
    concepts page. This function exists so the verdict is recomputed on every
    build rather than frozen into a comment: if a normalization pass ever lands
    (the audit's recommendation #9), the numbers move and the answer with them.
    """
    report = vocabulary_report(corpus.rows, "concepts")
    return report, report["rankable"], RANKABLE_HEAD_COVERAGE


# ---------------------------------------------------------------------------
# article detail (client-side, one payload)
# ---------------------------------------------------------------------------

ARTICLES_JS = """
(function () {
  var q = document.getElementById('q'), hits = document.getElementById('hits'),
      count = document.getElementById('count'), detail = document.getElementById('detail');
  var A = [], HAY = [], F = {}, CAP = 250, timer = null;

  function idx(fields) {
    var m = {};
    for (var i = 0; i < fields.length; i++) { m[fields[i]] = i; }
    return m;
  }
  function safeHref(u) {
    u = String(u || '');
    return (/^https?:\\/\\//i).test(u) ? u : '';
  }
  function el(tag, cls, text) {
    var x = document.createElement(tag);
    if (cls) { x.className = cls; }
    if (text !== undefined && text !== null) { x.textContent = String(text); }
    return x;
  }
  function nfmt(x) { return Number(x || 0).toLocaleString(); }

  function show(a) {
    detail.textContent = '';
    var h = el('h2'), href = safeHref(a[F.url]);
    if (href) {
      var link = el('a', null, a[F.title] || 'Untitled');
      link.href = href;
      link.rel = 'noreferrer';
      link.target = '_blank';
      h.appendChild(link);
    } else {
      h.textContent = a[F.title] || 'Untitled';
    }
    detail.appendChild(h);
    var dl = document.createElement('dl');
    var pairs = [
      ['Read', a[F.date_read]], ['Saved', a[F.date_saved]],
      ['Author', a[F.author]], ['Source', a[F.domain]],
      ['Corpus', a[F.source]], ['Words', nfmt(a[F.words])],
      ['Reading time', a[F.reading_time] ? a[F.reading_time] + ' min' : ''],
      // Null where the index has no grade. `== null` catches undefined too,
      // which is what an older payload without the field would hand back.
      ['Reading level', a[F.grade] == null ? '' : 'grade ' + a[F.grade]],
      ['URL', href || 'no URL recorded (pre-2012 legacy import)']
    ];
    for (var i = 0; i < pairs.length; i++) {
      if (!pairs[i][1]) { continue; }
      dl.appendChild(el('dt', null, pairs[i][0]));
      dl.appendChild(el('dd', null, pairs[i][1]));
    }
    detail.appendChild(dl);
    detail.hidden = false;
    // The panel renders below the hit list, so a click on row 40 would
    // otherwise open a detail view the reader never sees.
    detail.scrollIntoView({ block: 'nearest' });
  }

  function render(term) {
    term = String(term || '').trim().toLowerCase();
    hits.textContent = '';
    // A detail panel left open under a list that no longer contains it is a
    // stale answer to a question the reader has moved on from.
    detail.hidden = true;
    detail.textContent = '';
    var shown = 0, matched = 0, frag = document.createDocumentFragment();
    for (var i = 0; i < A.length; i++) {
      if (term && HAY[i].indexOf(term) === -1) { continue; }
      matched++;
      if (shown >= CAP) { continue; }
      shown++;
      var a = A[i], row = el('button', 'arow');
      row.type = 'button';
      row.appendChild(el('span', 'ad num', a[F.date_read]));
      var t = el('span', 'at', a[F.title] || 'Untitled');
      if (a[F.domain]) { t.appendChild(el('span', 'asrc', a[F.domain])); }
      row.appendChild(t);
      row.appendChild(el('span', 'aw num', nfmt(a[F.words])));
      (function (art) { row.addEventListener('click', function () { show(art); }); })(a);
      frag.appendChild(row);
    }
    hits.appendChild(frag);
    if (matched === 0) {
      hits.appendChild(el('span', 'empty',
        'nothing matches - search covers titles, authors and sources only'));
    }
    count.textContent = matched === A.length
      ? nfmt(matched) + ' articles' + (shown < matched ? ' · showing first ' + nfmt(shown) : '')
      : nfmt(matched) + ' of ' + nfmt(A.length) + ' articles'
        + (shown < matched ? ' · showing first ' + nfmt(shown) : '');
  }

  q.addEventListener('input', function () {
    clearTimeout(timer);
    timer = setTimeout(function () { render(q.value); }, 120);
  });

  fetch('../articles.json').then(function (r) { return r.json(); }).then(function (p) {
    F = idx(p.fields);
    A = p.articles;
    for (var i = 0; i < A.length; i++) {
      HAY.push(((A[i][F.title] || '') + ' ' + (A[i][F.author] || '') + ' '
                + (A[i][F.domain] || '')).toLowerCase());
    }
    q.disabled = false;
    render('');
  }).catch(function () {
    count.textContent = 'Could not load the article index.';
  });
}());
"""


def render_articles_page(corpus_data, site_title="The Week in Reading", domain=""):
    """The shell. Every row is built client-side with textContent and every
    href passes a scheme test in the browser too - the payload carries
    third-party scraped titles and URLs, so neither end trusts the data.

    The excluded-row reconciliation is rendered, not just computed: this page
    says 16,346 where the dashboard says 17,416, and the difference has to be
    on the page or the site is quietly hiding a thousand rows.
    """
    total = len(corpus_data)
    dropped = []
    if corpus_data.excluded_corrupted:
        dropped.append(f"{n(corpus_data.excluded_corrupted)} corrupted")
    if corpus_data.excluded_undated:
        dropped.append(f"{n(corpus_data.excluded_undated)} undated (saved, never read)")
    if corpus_data.excluded_pre_min_year:
        dropped.append(f"{n(corpus_data.excluded_pre_min_year)} dated before 2005")
    if dropped:
        raw = total + sum((corpus_data.excluded_corrupted,
                           corpus_data.excluded_undated,
                           corpus_data.excluded_pre_min_year))
        reconciliation = (f'\n    <div class="provenance label">'
                          f'{e(f"{n(total)} of {n(raw)} indexed rows - excluding " + ", ".join(dropped))}'
                          f'</div>')
    else:
        reconciliation = ""

    body = f"""  <header>
    <a class="label kicker" href="../">{e(site_title)}</a>
    <h1>Every article</h1>
    <div class="daterange">{n(total)} articles, searchable by title, author, or source.</div>{reconciliation}
  </header>

  <section>
    <label class="label" for="q">Search</label>
    <input class="search" id="q" type="search" autocomplete="off" disabled
           placeholder="title, author, or source…">
    <div class="label" id="count" style="margin-top:12px">Loading…</div>
    <div class="hits" id="hits"></div>
    <div class="detail" id="detail" hidden></div>
    <div class="note">Search covers titles, authors and sources — not article
      text. Bodies live in the vault, not in this index.</div>
  </section>

  <div class="yearnav"><span></span><a class="home" href="../">All weeks</a><span></span></div>
  <footer>
    <span class="label">Computed from the archive index · corrupted rows excluded</span>
    <span class="label num">{e(domain)}</span>
  </footer>"""
    return page(f"Every article — {site_title}", body, depth=1,
                body_extra=f"<script>{ARTICLES_JS}</script>\n")


def payload_json(corpus):
    """The payload bytes, with the size cap enforced at build time."""
    blob = json.dumps(payload_rows(corpus), separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")
    if len(blob) > MAX_PAYLOAD_BYTES:
        raise SystemExit(
            f"articles.json is {len(blob)/1e6:.1f} MB, over the "
            f"{MAX_PAYLOAD_BYTES/1e6:.1f} MB cap. This payload downloads on every "
            f"visit to /articles/ - trim fields rather than raising the cap.")
    return blob
