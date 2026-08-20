"""Phase 5 deep-dive pages: year rollups, the orgs facet, article detail.

These render from the Parquet index (site/corpus.py), unlike the week pages
which render from synthesis frontmatter. Same skin, same escaping discipline,
same tooltip idiom - EXTRA_STYLE appends to generate.py's stylesheet rather
than forking it.

Two audit rulings are enforced here rather than merely respected:

- **Orgs can be ranked; topics cannot.** Top 20 orgs cover 42.9% of the
  archive; the topics vocabulary is 29,882 strings of which 73.3% appear
  exactly once. There is no topic river and no topic ranking on any page in
  this module. That is a data fact, not a design preference.
- **One JSON payload, not 17k static pages.** The article detail view is
  client-side over a single compact payload, which keeps the nightly
  Wrangler deploy small.
"""
import json

from corpus import (entity_coverage, head_coverage, month_series, payload_rows,
                    stats, top_entities)
from htmlkit import e, n, page, safe_url

# The payload ships on every load of /articles/. The measured raw size at
# 17.9k rows is ~2.8 MB; this cap is the tripwire for a future corpus that
# grows a summary column by accident, not a prediction.
MAX_PAYLOAD_BYTES = 6 * 1024 * 1024

ORG_NOTE = ("Organizations are the one entity field the archive can rank honestly: "
            "the top 20 cover 42.9% of all articles. Topics cannot be ranked this way - "
            "that vocabulary is 29,882 free-text strings, 73.3% of them used exactly once.")

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
.orow .on { font-size:14.5px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
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
.arow .at { font-size:14.5px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
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


def _org_rows(orgs, denominator):
    if not orgs:
        return '      <span class="empty">no organizations tagged</span>\n'
    top = max((o["count"] for o in orgs), default=1) or 1
    out = ""
    for i, o in enumerate(orgs, start=1):
        pct = max(o["count"] / top * 100, 2)
        share = (o["count"] / denominator * 100) if denominator else 0
        lead = " lead" if i == 1 else ""
        tip = f"{o['name']} — {n(o['count'])} articles, {share:.1f}% of the year"
        out += (f'      <div class="orow{lead}" data-tip="{e(tip)}">'
                f'<span class="rk">{i}</span>'
                f'<span class="on">{e(str(o["name"]))}'
                f'<span class="obar" style="width:{pct:.0f}%"></span></span>'
                f'<span class="oc num">{n(o["count"])}</span></div>\n')
    return out


def _provenance(st):
    """How much of this year is dated by a real read event vs. inferred.

    The audit's two-corpora footnote, rendered where it bites: pre-2012 years
    are almost entirely legacy rows whose date came out of a filename.
    """
    if not st["articles"] or not st["proxy_dated"]:
        return ""
    share = st["proxy_dated"] / st["articles"] * 100
    if share >= 99.5:
        text = ("Dates approximate — this pre-tracking-era year is dated by "
                "publication or save, not by a recorded read")
    else:
        text = (f"{n(st['proxy_dated'])} of {n(st['articles'])} articles "
                f"({share:.0f}%) carry approximate dates — saved or published, not read")
    return f'\n    <div class="provenance label">{e(text)}</div>'


# ---------------------------------------------------------------------------
# year rollups
# ---------------------------------------------------------------------------

def render_year(corpus, year, weeks_in_year=(), prev_year=None, next_year=None,
                site_title="The Week in Reading", domain=""):
    year = int(year)
    rows = corpus.year(year)
    st = stats(rows)
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

    left = (f'<a href="../{prev_year}/">← {e(str(prev_year))}</a>'
            if prev_year else "<span></span>")
    right = (f'<a href="../{next_year}/">{e(str(next_year))} →</a>'
             if next_year else "<span></span>")

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
    {_stat(n(st['domains']), 'Sources')}
  </div>

  <section>
    <div class="label viz-title">The year's rhythm · words per month</div>
    <div class="months">
{month_html}    </div>
  </section>

  <section>
    <div class="label viz-title">Organizations in the year's reading · top {len(orgs)} by articles</div>
    <div class="roster">
{_org_rows(orgs, st['articles'])}    </div>
    <div class="note">{e(ORG_NOTE)}</div>
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
        "extraction ran over both corpora, so the pre-2012 legacy era (which carries no "
        "URLs at all) is counted here. Two caveats stand. Counts come from an enrichment "
        "pass with no controlled vocabulary, so an organization named several ways is "
        "ranked several times. And some rankings are partly site furniture the scraper "
        "captured instead of the article — the obviously contaminated rows are excluded "
        "as corrupted, but the flagging is not exhaustive."
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
{_org_rows(orgs, len(rows))}    </div>
    <div class="note">{e(ORG_NOTE)}</div>
    <div class="note">{e(footnote)}</div>
  </section>

  <div class="yearnav"><span></span><a class="home" href="../">All weeks</a><span></span></div>
  <footer>
    <span class="label">Computed from the archive index · corrupted rows excluded</span>
    <span class="label num">{e(domain)}</span>
  </footer>"""
    return page(f"Organizations — {site_title}", body, depth=1)


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
      ['URL', href || 'no URL recorded (pre-2012 legacy import)']
    ];
    for (var i = 0; i < pairs.length; i++) {
      if (!pairs[i][1]) { continue; }
      dl.appendChild(el('dt', null, pairs[i][0]));
      dl.appendChild(el('dd', null, pairs[i][1]));
    }
    detail.appendChild(dl);
    detail.hidden = false;
  }

  function render(term) {
    term = String(term || '').trim().toLowerCase();
    hits.textContent = '';
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


def render_articles_page(total, site_title="The Week in Reading", domain=""):
    """The shell. Every row is built client-side with textContent and every
    href passes a scheme test in the browser too - the payload carries
    third-party scraped titles and URLs, so neither end trusts the data."""
    body = f"""  <header>
    <a class="label kicker" href="../">{e(site_title)}</a>
    <h1>Every article</h1>
    <div class="daterange">{n(total)} articles, searchable by title, author, or source.</div>
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
                head_extra=f"<script>{ARTICLES_JS}</script>\n")


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
