#!/usr/bin/env python3
"""Phase A step 4 — render the CURATION GATE, the artifact Adam decides from.

    .venv/bin/python scripts/vocab/gate.py [--out data/vocab/curation-gate.html]

This is Phase B's input and the only human step in the plan, so the page is
built around the decision rather than around the data. Three things follow
from that:

  * **Cumulative coverage is a column.** Per-entry coverage says how big an
    entry is; the running union down the ranked list says where the taxonomy
    stops being worth extending. The whole size question ("~100 concepts was
    the starting instinct") is answered by reading down that column, so it is
    on every row rather than in a summary.
  * **The 40% bar is stated, not implied.** ``corpus.RANKABLE_HEAD_COVERAGE``
    is the constant that decides whether /concepts/ and /topics/ may exist at
    all. The header shows top-20 coverage against it next to the free-text
    numbers this phase is trying to beat (22.0% and 25.3%).
  * **Decisions are captured in the page.** ~150 accept/rename/merge/reject/
    split calls transcribed by hand into a separate file is where a curation
    pass dies. Choices persist in localStorage and export as a YAML draft.

The export is a DRAFT for Adam to edit into ``data/taxonomy/v1.yaml``. This
script does not write that file — the plan reserves it for the human, and a
generated taxonomy would defeat the gate it just passed through.

Self-contained: one file, inline CSS and JS, no external requests, and every
datum escaped through the site's own ``htmlkit.e`` / ``safe_url``. The strings
being rendered were extracted by a language model from scraped third-party
pages, so they are treated as hostile input throughout.
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "site"))

from vocab import common, name_clusters  # noqa: E402
from htmlkit import e, safe_url  # noqa: E402
import corpus  # noqa: E402

# The free-text baselines this phase exists to beat, measured by
# corpus.vocabulary_report over the same 16,346 rows.
BASELINE = {"concepts": 22.0, "topics": 25.3}

STYLE = """
:root { --bg:#1c1917; --bg-raise:#292524; --ink:#e7e5e4; --ink-2:#a8a29e;
  --ink-3:#78716c; --rule:#44403c; --amber:#fbbf24; --amber-dim:#92700c;
  --brand:#FF8F3B; --emerald:#34d399; --rose:#fb7185; --indigo:#818cf8; }
* { margin:0; padding:0; box-sizing:border-box; }
body { background:var(--bg); color:var(--ink); line-height:1.5;
  font-family:ui-sans-serif,-apple-system,"Helvetica Neue",sans-serif; }
a { color:inherit; }
.page { max-width:1080px; margin:0 auto; padding:56px 24px 120px; }
.label { font-family:ui-monospace,"SF Mono",Menlo,monospace; font-size:11px;
  letter-spacing:.14em; text-transform:uppercase; color:var(--ink-3); }
.num { font-variant-numeric:tabular-nums; }
header { border-bottom:1px solid var(--rule); padding-bottom:24px; }
header .kicker { color:var(--brand); }
h1 { font-size:46px; font-weight:200; letter-spacing:-.02em; margin-top:8px; }
.sub { margin-top:8px; color:var(--ink-2); font-size:15px; max-width:640px; }
.stats { display:flex; gap:36px; flex-wrap:wrap; padding:26px 0;
  border-bottom:1px solid var(--rule); }
.stat .v { font-size:34px; font-weight:200; font-variant-numeric:tabular-nums; }
.stat .v em { font-style:normal; font-size:17px; color:var(--ink-2); }
.stat.hero .v { color:var(--amber); }
.stat.pass .v { color:var(--emerald); }
.stat.fail .v { color:var(--rose); }
.stat .l { margin-top:2px; }
.stat .note { margin-top:4px; font-family:ui-monospace,Menlo,monospace;
  font-size:11px; color:var(--ink-3); }
section { padding:30px 0 0; }
.curve { display:flex; gap:6px; align-items:flex-end; height:120px;
  margin-top:14px; }
.curve .b { flex:1; display:flex; flex-direction:column;
  justify-content:flex-end; height:100%; }
.curve .bar { background:var(--amber-dim); border-radius:3px 3px 0 0;
  min-height:2px; }
.curve .b.clears .bar { background:var(--amber); }
.curve .cv { text-align:center; font-size:12px; color:var(--ink-2);
  margin-bottom:4px; font-variant-numeric:tabular-nums; }
.curve .cl { text-align:center; margin-top:6px; }
.barline { position:relative; height:1px; background:var(--rose); margin-top:-1px; }
.controls { display:flex; gap:12px; flex-wrap:wrap; align-items:center;
  position:sticky; top:0; background:var(--bg); padding:14px 0;
  border-bottom:1px solid var(--rule); z-index:5; }
.controls input[type=search] { flex:1; min-width:200px; background:var(--bg-raise);
  border:1px solid var(--rule); color:var(--ink); padding:8px 11px;
  border-radius:4px; font-size:14px; font-family:inherit; }
.controls button { background:var(--bg-raise); border:1px solid var(--rule);
  color:var(--ink-2); padding:8px 13px; border-radius:4px; cursor:pointer;
  font-family:ui-monospace,Menlo,monospace; font-size:11px;
  letter-spacing:.1em; text-transform:uppercase; }
.controls button:hover { color:var(--brand); border-color:var(--brand); }
.controls .tally { font-family:ui-monospace,Menlo,monospace; font-size:11.5px;
  color:var(--ink-3); font-variant-numeric:tabular-nums; }
.entry { border-bottom:1px solid #2a2523; padding:14px 0; }
.entry.hidden { display:none; }
.entry .head { display:grid; grid-template-columns:38px 1fr 92px 88px 176px;
  gap:14px; align-items:baseline; }
.entry .rank { color:var(--ink-3); font-size:12px;
  font-variant-numeric:tabular-nums; }
.entry .name { font-size:19px; color:var(--amber); font-weight:400; }
.entry .axis { font-family:ui-monospace,Menlo,monospace; font-size:10px;
  letter-spacing:.1em; text-transform:uppercase; margin-left:9px;
  padding:2px 6px; border-radius:3px; background:var(--bg-raise);
  color:var(--indigo); vertical-align:middle; }
.entry .axis.topic { color:var(--emerald); }
.entry .axis.unknown { color:var(--rose); }
.entry .def { font-family:Charter,Georgia,serif; font-size:15px;
  color:var(--ink-2); margin-top:4px; max-width:60ch; }
.entry .def.missing { color:var(--rose); font-style:italic; }
.entry .metric { text-align:right; font-variant-numeric:tabular-nums;
  font-size:15px; }
.entry .metric .u { font-size:11px; color:var(--ink-3); display:block;
  letter-spacing:.1em; text-transform:uppercase;
  font-family:ui-monospace,Menlo,monospace; }
.entry .metric.cum { color:var(--ink-2); }
.entry .metric.cum.crossed { color:var(--emerald); }
.entry .wbar { height:3px; border-radius:2px; background:var(--amber-dim);
  margin-top:6px; }
.acts { display:flex; gap:4px; justify-content:flex-end; }
.acts button { background:transparent; border:1px solid var(--rule);
  color:var(--ink-3); border-radius:3px; cursor:pointer; padding:3px 7px;
  font-family:ui-monospace,Menlo,monospace; font-size:10px;
  letter-spacing:.06em; text-transform:uppercase; }
.acts button:hover { color:var(--ink); border-color:var(--ink-3); }
.entry[data-decision=accept] .acts button[data-act=accept],
.entry[data-decision=accept] { border-left:2px solid var(--emerald); }
.entry[data-decision=reject] { border-left:2px solid var(--rose); opacity:.55; }
.entry[data-decision=rename] { border-left:2px solid var(--amber); }
.entry[data-decision=merge] { border-left:2px solid var(--indigo); }
.entry[data-decision=split] { border-left:2px solid var(--brand); }
.entry[data-decision] { padding-left:12px; }
.acts button.on { background:var(--bg-raise); color:var(--ink);
  border-color:var(--ink-3); }
.entry input.note { margin-top:8px; width:100%; max-width:520px;
  background:var(--bg-raise); border:1px solid var(--rule); color:var(--ink);
  padding:6px 9px; border-radius:4px; font-size:13px; font-family:inherit;
  display:none; }
.entry[data-decision=rename] input.note,
.entry[data-decision=merge] input.note,
.entry[data-decision=split] input.note { display:block; }
details.mem { margin-top:9px; }
details.mem summary { cursor:pointer; list-style:none; color:var(--ink-3);
  font-family:ui-monospace,Menlo,monospace; font-size:11px;
  letter-spacing:.1em; text-transform:uppercase; }
details.mem summary::before { content:"\\25b8  "; }
details.mem[open] summary::before { content:"\\25be  "; }
details.mem summary:hover { color:var(--brand); }
.chips { margin-top:10px; }
.chip { display:inline-block; padding:3px 9px; margin:0 5px 5px 0;
  background:var(--bg-raise); border-radius:3px; font-size:12.5px;
  color:var(--ink-2); }
.chip .c { color:var(--amber); font-size:.78em; margin-left:5px;
  font-variant-numeric:tabular-nums; }
.chip.solo { color:var(--ink-3); }
.sibs { margin-top:12px; padding-top:10px; border-top:1px dashed var(--rule); }
.sibs .why { color:var(--ink-3); font-size:12px; margin-bottom:8px;
  max-width:64ch; }
.sib { display:block; width:100%; text-align:left; cursor:pointer;
  background:transparent; border:1px solid var(--rule); border-radius:4px;
  color:var(--ink-2); padding:7px 10px; margin-bottom:5px; font:inherit;
  font-size:13px; }
.sib:hover { border-color:var(--ink-3); color:var(--ink); }
.sib.on { border-color:var(--indigo); color:var(--ink);
  background:var(--bg-raise); }
.sib .box { font-family:ui-monospace,Menlo,monospace; color:var(--ink-3);
  margin-right:8px; }
.sib.on .box { color:var(--indigo); }
.sib .n { font-variant-numeric:tabular-nums; color:var(--ink-3);
  font-size:11.5px; margin-left:6px; }
.banner { margin:18px 0 0; padding:12px 15px; border-radius:4px;
  background:#2a1416; border-left:2px solid var(--rose); color:var(--ink);
  font-size:14px; }
.caveat { margin-top:14px; padding:12px 15px; border-radius:4px;
  background:var(--bg-raise); border-left:2px solid var(--amber);
  font-size:13.5px; color:var(--ink-2); max-width:78ch; }
.caveat b { color:var(--ink); font-weight:600; }
table.cmp { border-collapse:collapse; margin-top:14px; font-size:13.5px;
  width:100%; }
table.cmp th, table.cmp td { text-align:right; padding:7px 10px;
  border-bottom:1px solid #2a2523; font-variant-numeric:tabular-nums; }
table.cmp th:first-child, table.cmp td:first-child { text-align:left;
  font-variant-numeric:normal; }
table.cmp thead th { color:var(--ink-3); font-family:ui-monospace,Menlo,monospace;
  font-size:11px; letter-spacing:.1em; text-transform:uppercase; }
table.cmp tr.hero td { color:var(--amber); }
table.cmp td.under { color:var(--rose); }
table.cmp td.over { color:var(--emerald); }
.scroll { overflow-x:auto; }
.egs { margin-top:12px; }
.eg { display:grid; grid-template-columns:46px 1fr; gap:10px; padding:4px 0;
  font-size:13.5px; color:var(--ink-2); text-decoration:none; }
.eg:hover { color:var(--brand); }
.eg .y { color:var(--ink-3); font-variant-numeric:tabular-nums; font-size:12px; }
.export { margin-top:22px; }
.export textarea { width:100%; height:280px; background:var(--bg-raise);
  border:1px solid var(--rule); color:var(--ink); padding:12px;
  border-radius:4px; font-family:ui-monospace,Menlo,monospace; font-size:12px;
  line-height:1.6; display:none; }
footer { margin-top:56px; border-top:1px solid var(--rule); padding-top:16px;
  color:var(--ink-3); font-size:12.5px; }
"""

SCRIPT = """
(function () {
  var KEY = 'vocab-gate-decisions-v1';
  var store = {};
  try { store = JSON.parse(localStorage.getItem(KEY) || '{}'); } catch (err) { store = {}; }

  var entries = Array.prototype.slice.call(document.querySelectorAll('.entry'));

  var storageBroken = false;
  function save() {
    try {
      localStorage.setItem(KEY, JSON.stringify(store));
    } catch (err) {
      // Swallowing this silently meant a full afternoon of decisions could
      // evaporate on reload with nothing on screen having looked wrong.
      storageBroken = true;
    }
    tally();
  }

  function apply(entry) {
    var rec = store[entry.dataset.key] || {};
    if (rec.decision) { entry.dataset.decision = rec.decision; }
    else { delete entry.dataset.decision; }
    entry.querySelectorAll('.acts button').forEach(function (b) {
      b.classList.toggle('on', b.dataset.act === rec.decision);
    });
    var note = entry.querySelector('input.note');
    if (note) { note.value = rec.note || ''; }
    var folded = rec.folded || [];
    entry.querySelectorAll('.sib').forEach(function (sib) {
      var on = folded.indexOf(sib.dataset.sib) !== -1;
      sib.classList.toggle('on', on);
      sib.querySelector('.box').textContent = on ? '[x]' : '[ ]';
    });
  }

  function tally() {
    var counts = {};
    var decided = 0;
    entries.forEach(function (entry) {
      var rec = store[entry.dataset.key];
      if (rec && rec.decision) {
        decided += 1;
        counts[rec.decision] = (counts[rec.decision] || 0) + 1;
      }
    });
    var accepted = entries.filter(function (entry) {
      var rec = store[entry.dataset.key];
      return rec && rec.decision && rec.decision !== 'reject';
    });
    var folded = 0;
    entries.forEach(function (entry) {
      var rec = store[entry.dataset.key];
      if (rec && rec.folded) { folded += rec.folded.length; }
    });
    var parts = ['decided ' + decided + '/' + entries.length];
    ['accept', 'rename', 'merge', 'split', 'reject'].forEach(function (k) {
      if (counts[k]) { parts.push(k + ' ' + counts[k]); }
    });
    parts.push('keeping ' + accepted.length);
    if (folded) { parts.push('folded-in ' + folded); }
    if (storageBroken) { parts.push('\\u26a0 NOT SAVING - export before leaving'); }
    document.getElementById('tally').textContent = parts.join('  \\u00b7  ');
  }

  entries.forEach(function (entry) {
    apply(entry);
    entry.querySelectorAll('.acts button').forEach(function (button) {
      button.addEventListener('click', function () {
        var rec = store[entry.dataset.key] || {};
        rec.decision = (rec.decision === button.dataset.act) ? '' : button.dataset.act;
        rec.name = entry.dataset.name;
        store[entry.dataset.key] = rec;
        apply(entry);
        save();
        refilter();
      });
    });
    entry.querySelectorAll('.sib').forEach(function (sib) {
      sib.addEventListener('click', function () {
        var rec = store[entry.dataset.key] || {};
        var folded = rec.folded || [];
        var at = folded.indexOf(sib.dataset.sib);
        if (at === -1) { folded.push(sib.dataset.sib); } else { folded.splice(at, 1); }
        rec.folded = folded;
        rec.name = entry.dataset.name;
        store[entry.dataset.key] = rec;
        apply(entry);
        save();
      });
    });
    var note = entry.querySelector('input.note');
    if (note) {
      note.addEventListener('input', function () {
        var rec = store[entry.dataset.key] || {};
        rec.note = note.value;
        store[entry.dataset.key] = rec;
        save();
      });
    }
  });

  var search = document.getElementById('filter');
  var undecidedOnly = false;
  function refilter() {
    var q = (search.value || '').toLowerCase();
    entries.forEach(function (entry) {
      var rec = store[entry.dataset.key];
      var hit = !q || entry.textContent.toLowerCase().indexOf(q) !== -1;
      if (undecidedOnly && rec && rec.decision) { hit = false; }
      entry.classList.toggle('hidden', !hit);
    });
  }
  search.addEventListener('input', refilter);
  document.getElementById('undecided').addEventListener('click', function () {
    undecidedOnly = !undecidedOnly;
    this.classList.toggle('on', undecidedOnly);
    refilter();
  });
  document.getElementById('expand').addEventListener('click', function () {
    var open = this.dataset.open !== '1';
    this.dataset.open = open ? '1' : '0';
    document.querySelectorAll('details.mem').forEach(function (d) { d.open = open; });
  });

  function yamlString(value) {
    return '"' + String(value).replace(/\\\\/g, '\\\\\\\\').replace(/"/g, '\\\\"') + '"';
  }

  document.getElementById('export').addEventListener('click', function () {
    var box = document.getElementById('draft');
    var lines = ['# DRAFT for data/taxonomy/v1.yaml - review every line before committing.',
                 '# Generated from the curation gate; rejected entries are omitted.',
                 'version: 1',
                 'entries:'];
    entries.forEach(function (entry) {
      var rec = store[entry.dataset.key];
      if (!rec || !rec.decision || rec.decision === 'reject') { return; }
      var name = (rec.decision === 'rename' && rec.note) ? rec.note : entry.dataset.name;
      lines.push('  - name: ' + yamlString(name));
      lines.push('    axis: ' + yamlString(entry.dataset.axis || 'concept'));
      lines.push('    definition: ' + yamlString(entry.dataset.definition || ''));
      if (rec.decision !== 'accept') {
        lines.push('    review: ' + yamlString(rec.decision + (rec.note ? ': ' + rec.note : '')));
      }
      lines.push('    aliases:');
      var seen = {};
      entry.querySelectorAll('.chip .s').forEach(function (chip) {
        seen[chip.textContent] = 1;
      });
      // Ticked look-alike clusters fold their strings in here. This is the
      // whole point of the sibling list: a concept fragmented across a dozen
      // clusters becomes ONE entry with all of their aliases.
      entry.querySelectorAll('.sib.on .s').forEach(function (chip) {
        seen[chip.textContent] = 1;
      });
      Object.keys(seen).forEach(function (alias) {
        lines.push('      - ' + yamlString(alias));
      });
    });
    box.value = lines.join('\\n');
    box.style.display = 'block';
    box.focus();
    box.select();
  });

  tally();
}());
"""


def stat(value, label, note="", cls=""):
    note_html = f'<div class="note">{e(note)}</div>' if note else ""
    return (f'<div class="stat {cls}"><div class="v">{value}</div>'
            f'<div class="l label">{e(label)}</div>{note_html}</div>')


def curve_block(curve, bar):
    """The coverage curve, drawn against the 40% rankability line."""
    top = max([p["coverage"] for p in curve] + [bar]) or 1.0
    bars = ""
    for point in curve:
        height = point["coverage"] / top * 100
        clears = " clears" if point["coverage"] >= bar else ""
        bars += (f'<div class="b{clears}"><div class="cv">{point["coverage"]:.0f}%</div>'
                 f'<div class="bar" style="height:{height:.1f}%"></div>'
                 f'<div class="cl label">{point["n"]}</div></div>')
    return (f'<div class="label">Cumulative article coverage by taxonomy size'
            f' &mdash; the rankability bar is {bar:.0f}%</div>'
            f'<div class="curve">{bars}</div>')


def entry_block(rank, entry):
    axis = entry["axis"] or "unset"
    axis_cls = entry["axis"] if entry["axis"] in ("topic", "concept") else "unknown"
    definition = entry["definition"]
    def_cls = "" if definition else " missing"
    def_text = definition or (
        f"no definition - the naming call failed ({entry.get('error') or 'unknown'})")

    chips = ""
    for member in entry["members"]:
        count = entry["member_counts"][member]
        solo = " solo" if count == 1 else ""
        chips += (f'<span class="chip{solo}"><span class="s">{e(member)}</span>'
                  f'<span class="c num">{count}</span></span>')

    egs = ""
    for example in entry["examples"]:
        href = safe_url(example["url"])
        year = e(str(example["year"] or ""))
        title = e(example["title"])
        inner = f'<span class="y num">{year}</span><span>{title}</span>'
        egs += (f'<a class="eg" href="{href}" target="_blank" rel="noopener">{inner}</a>'
                if href else f'<div class="eg">{inner}</div>')

    sibs = ""
    if entry["siblings"]:
        rows_html = ""
        for sib in entry["siblings"]:
            preview = ", ".join(sib["members"][:6])
            more = (f" +{len(sib['members']) - 6}" if len(sib["members"]) > 6
                    else "")
            aliases = "".join(f'<span class="s" hidden>{e(m)}</span>'
                              for m in sib["members"])
            rows_html += (
                f'<button class="sib" data-sib="{e(str(sib["rank"]))}">'
                f'<span class="box">[ ]</span>{e(preview)}{e(more)}'
                f'<span class="n">{sib["articles"]:,} art &middot; '
                f'{sib["similarity"]:.2f}</span>{aliases}</button>')
        sibs = (f'<div class="sibs"><div class="why">Look-alike clusters that '
                f'did not make this page. Tick any that belong to this entry '
                f'and their strings fold into its alias list on export &mdash; '
                f'this is how a fragmented concept gets reassembled.</div>'
                f'{rows_html}</div>')

    cum_cls = " crossed" if entry["crossed_bar"] else ""
    return f"""<div class="entry" data-key="{e(entry['key'])}" data-name="{e(entry['name'])}"
     data-axis="{e(axis_cls if axis_cls != 'unknown' else '')}"
     data-definition="{e(definition)}">
  <div class="head">
    <div class="rank num">{rank}</div>
    <div>
      <div><span class="name">{e(entry['name'])}</span><span class="axis {axis_cls}">{e(axis)}</span></div>
      <div class="def{def_cls}">{e(def_text)}</div>
    </div>
    <div class="metric"><span class="num">{entry['articles']:,}</span>
      <span class="u">articles</span>
      <div class="wbar" style="width:{entry['bar_pct']:.1f}%"></div></div>
    <div class="metric cum{cum_cls}"><span class="num">{entry['cumulative']:.1f}%</span>
      <span class="u">cumulative</span></div>
    <div class="acts">
      <button data-act="accept">accept</button>
      <button data-act="rename">rename</button>
      <button data-act="merge">merge</button>
      <button data-act="split">split</button>
      <button data-act="reject">reject</button>
    </div>
  </div>
  <input class="note" type="text" placeholder="new name / merge target / how to split">
  <details class="mem">
    <summary>{entry['size']:,} member strings &middot; {entry['solo']:,} used once</summary>
    <div class="chips">{chips}</div>
    <div class="egs">{egs}</div>
    {sibs}
  </details>
</div>"""


def build_entries(clusters, names, inventory, limit):
    """Ranked entries with names, examples, and the running coverage union."""
    entries = []
    seen = set()
    bar = corpus.RANKABLE_HEAD_COVERAGE
    for cluster in clusters[:limit]:
        key = name_clusters.cluster_key(cluster["members"])
        record = names.get(key, {})
        articles = inventory.article_set(cluster["members"])
        before = len(seen)
        seen |= articles
        cumulative = round(len(seen) / inventory.n_articles * 100, 1) \
            if inventory.n_articles else 0.0
        entries.append({
            "key": key,
            "name": record.get("name") or cluster["members"][0],
            "definition": record.get("definition", ""),
            "axis": record.get("axis", ""),
            "error": record.get("error", ""),
            "members": cluster["members"],
            "member_counts": {m: inventory.count(m) for m in cluster["members"]},
            "size": cluster["size"],
            "solo": sum(1 for m in cluster["members"] if inventory.count(m) == 1),
            "articles": cluster["articles"],
            "coverage": cluster["coverage"],
            "new_articles": len(seen) - before,
            "cumulative": cumulative,
            "crossed_bar": cumulative >= bar,
            "examples": common.example_articles(inventory, cluster["members"]),
            "siblings": cluster.get("siblings") or [],
        })
    peak = max([x["articles"] for x in entries] + [1])
    for x in entries:
        x["bar_pct"] = x["articles"] / peak * 100
    return entries


def render(entries, payload, inventory, curve):
    bar = corpus.RANKABLE_HEAD_COVERAGE
    top20 = next((p["coverage"] for p in curve if p["n"] == 20), 0.0)
    named = sum(1 for x in entries if x["definition"])
    strings_in = sum(x["size"] for x in entries)
    params = payload.get("params", {})

    def at20(points):
        return next((p["coverage"] for p in (points or []) if p["n"] == 20), 0.0)

    free_text20 = at20(payload.get("free_text_curve"))
    naive20 = at20((payload.get("naive_baseline") or {}).get("curve"))
    columns = payload.get("column_curves") or {}
    column20 = {field: at20(points) for field, points in columns.items()}
    # The verdict is per COLUMN, because that is how the bar is defined and
    # how Phase C builds the index. Pooled coverage clearing 40% while both
    # real columns sit below it is the single most misleading thing this page
    # could say, and it is what it used to say.
    clears = [f for f, v in column20.items() if v >= bar]
    verdict = "pass" if clears and len(clears) == len(column20) else "fail"

    rows_html = "\n".join(entry_block(i, x) for i, x in enumerate(entries, 1))
    gain = top20 - free_text20
    stats = "".join([
        stat(f'{len(entries):,}', "entries proposed",
             f'{named:,} named by model', "hero"),
        stat(f'{top20:.1f}<em>%</em>', "top-20, pooled",
             f'free-text pooled was {free_text20:.1f}% · +{gain:.1f} pts'),
        stat(" / ".join(f"{v:.1f}" for v in column20.values()) + "<em>%</em>",
             "top-20, per column",
             f'{" / ".join(columns)} · bar is {bar:.0f}%', verdict),
        stat(f'{inventory.n_articles:,}', "articles in corpus",
             f'{strings_in:,} of {len(inventory):,} strings on this page'),
    ])

    rows = [
        ("free text, pooled (no clustering)", payload.get("free_text_curve")),
        ("case-fold + de-plural only, no embeddings",
         (payload.get("naive_baseline") or {}).get("curve")),
        ("this derivation, pooled", curve),
    ] + [(f"this derivation, `{f}` column only", p) for f, p in columns.items()]
    points = (20, 50, 100, 150, 250)
    head = "".join(f"<th>top-{n}</th>" for n in points)
    body_rows = ""
    for label, series in rows:
        by_n = {p["n"]: p["coverage"] for p in (series or [])}
        hero = ' class="hero"' if label == "this derivation, pooled" else ""
        cells = ""
        for n in points:
            v = by_n.get(n)
            cls = ""
            if n == 20 and v is not None:
                cls = ' class="over"' if v >= bar else ' class="under"'
            cells += f"<td{cls}>{v:.1f}</td>" if v is not None else "<td>&mdash;</td>"
        body_rows += f"<tr{hero}><td>{e(label)}</td>{cells}</tr>"
    comparison = (f'<div class="label">What the derivation actually bought, '
                  f'measured the same way in every row</div>'
                  f'<div class="scroll"><table class="cmp"><thead><tr>'
                  f'<th>vocabulary</th>{head}</tr></thead>'
                  f'<tbody>{body_rows}</tbody></table></div>')

    caveat = ""
    if verdict == "fail":
        under = ", ".join(f"{f} {v:.1f}%" for f, v in column20.items() if v < bar)
        caveat = (
            f'<div class="caveat"><b>The {bar:.0f}% bar is not cleared per '
            f'column.</b> Pooled across both fields the head reaches '
            f'{top20:.1f}%, but measured against the individual index columns '
            f'Phase C builds it is {e(under)}. So <b>/concepts/ and /topics/ '
            f'do not switch on automatically from this run</b> unless the two '
            f'axes are merged into a single vocabulary &mdash; which is '
            f'exactly the open axis question, now with a number attached to '
            f'it. The alternative reading: at the taxonomy size actually being '
            f'proposed the pooled head reaches '
            f'{next((p["coverage"] for p in curve if p["n"] == 250), 0):.1f}% '
            f'by top-250, and a 20-entry test may simply be the wrong bar for '
            f'a curated vocabulary.</div>')

    chain = payload.get("chaining") or {}
    banner = ""
    if chain.get("chained"):
        banner = (f'<div class="banner"><b>Chained.</b> One cluster covers '
                  f'{chain.get("top_share", 0)}% of the corpus with '
                  f'{chain.get("top_size", 0):,} strings. Every coverage number '
                  f'on this page is an artifact of that blob. Re-run '
                  f'cluster.py at a higher --similarity before curating.</div>')

    body = f"""<div class="page">
<header>
  <div class="label kicker">Thede Technologies &middot; Reading Archive</div>
  <h1>Controlled Vocabulary &mdash; Curation Gate</h1>
  <div class="sub">Derived bottom-up from {len(inventory):,} distinct free-text
  concept and topic strings by embedding and clustering; the model named and
  defined each group but did not decide membership. Accept, rename, merge,
  split or reject each entry, then export a draft for
  <span class="label">data/taxonomy/v1.yaml</span>. Nothing classifies until
  that file exists.</div>
</header>
{banner}
<div class="stats">{stats}</div>
{caveat}
<section>{curve_block(curve, bar)}</section>
<section>{comparison}</section>
<section>
  <div class="controls">
    <input id="filter" type="search" placeholder="filter entries, definitions, member strings&hellip;">
    <button id="undecided">undecided only</button>
    <button id="expand" data-open="0">expand all</button>
    <button id="export">export draft</button>
    <span class="tally" id="tally"></span>
  </div>
  <div class="export"><textarea id="draft" readonly
    aria-label="YAML draft of accepted entries"></textarea></div>
  {rows_html}
</section>
<footer>
  Generated {e(time.strftime('%Y-%m-%d %H:%M'))} from clustering run
  {e(str(payload.get('generated', '')))} &middot;
  similarity {e(str(params.get('similarity')))} &middot;
  {e(str(params.get('dims')))}d &middot; k={e(str(params.get('neighbors')))} &middot;
  {e(str(params.get('embed_model')))}.
  Decisions are stored in this browser only &mdash; export before clearing site data.
</footer>
</div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Controlled Vocabulary &mdash; Curation Gate</title>
<style>{STYLE}</style>
</head>
<body>
{body}
<script>{SCRIPT}</script>
</body>
</html>
"""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data-dir", default=str(common.DEFAULT_DATA_DIR))
    ap.add_argument("--index", default=str(common.INDEX_PATH))
    ap.add_argument("--clusters", default=None)
    ap.add_argument("--names", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--limit", type=int, default=250,
                    help="how many ranked clusters to put on the page")
    args = ap.parse_args(argv)

    data_dir = Path(args.data_dir)
    clusters_path = Path(args.clusters or data_dir / "clusters.json")
    if not clusters_path.exists():
        raise SystemExit(f"no clusters at {clusters_path} — run cluster.py")
    payload = json.loads(clusters_path.read_text(encoding="utf-8"))
    names = name_clusters.load_named(
        Path(args.names or data_dir / name_clusters.NAMES_FILE))

    rows = common.load_rows(args.index)
    inv = common.Inventory(rows)
    entries = build_entries(payload["clusters"], names, inv, args.limit)
    curve = payload.get("coverage_curve") or []

    out = Path(args.out or data_dir / "curation-gate.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(entries, payload, inv, curve), encoding="utf-8")
    size_kb = out.stat().st_size / 1024
    print(f"wrote {out} ({size_kb:.0f} KB, {len(entries)} entries, "
          f"{sum(1 for x in entries if x['definition'])} named)")


if __name__ == "__main__":
    main()
