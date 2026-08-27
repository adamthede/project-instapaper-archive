"""Tests for Phase E's vocabulary pages.

Three things here are load-bearing and easy to break silently, so each is
pinned by a test that fails for that specific reason:

  1. THE GATE READS THE POOLED COLUMN. Split by source field the vocabulary
     misses the 40% bar and the pages vanish. That regression would look like
     "the page stopped building" with no explanation.
  2. THE CASCADE IS SORTED BY PEAK YEAR. That sort is the visualization — by
     volume instead, the same 1,320 cells say nothing at all, and the page
     would still render, still look plausible, and mean nothing.
  3. THE RAMP WAS VALIDATED, NOT CHOSEN. Monotonic in lightness, every step
     >= 3:1 against the surface. The check is encoded here so a later "let's
     make it prettier" has to re-earn it rather than just look nice locally.
"""
import collections
import os
import pathlib
import re
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "site"))

import corpus  # noqa: E402
import deepdives  # noqa: E402
import vocabulary  # noqa: E402


def frame(rows):
    return pd.DataFrame(rows)


def tiny():
    """A fixture whose orderings all DISAGREE.

    Rewritten twice, and the second rewrite is the instructive one. Version 1
    let a volume-sort mutation pass because the early-peaking entry also had
    the most articles. Version 2 fixed that by giving each entry exactly ONE
    year — which quietly made "peak year" indistinguishable from "first year",
    "last year" and "the only year", and per-entry-PEAK indistinguishable from
    per-entry-TOTAL. Six further mutations sailed through, including "not
    sorted by peak at all".

    So `Lens` spans four years with an INTERIOR peak, and the two entries
    disagree on every axis a mutation could confuse:

        Lens   2006:1  2010:5  2014:2  2020:1   first 2006  peak 2010  last 2020  total 9
        AI                             2020:4   first 2020  peak 2020  last 2020  total 4

    peak order   -> Lens, AI     first order -> Lens, AI
    last order   -> AI, Lens     volume      -> Lens, AI  (but see the assertions)
    """
    rows = ([{"year": 2006, "canonical_entries": ["Lens"], "concepts": [], "topics": []}]
            + [{"year": 2010, "canonical_entries": ["Lens"], "concepts": [], "topics": []}] * 5
            + [{"year": 2014, "canonical_entries": ["Lens"], "concepts": [], "topics": []}] * 2
            + [{"year": 2020, "canonical_entries": ["Lens"], "concepts": [], "topics": []}]
            + [{"year": 2020, "canonical_entries": ["Artificial Intelligence"],
                "concepts": [], "topics": []}] * 4)
    return frame(rows)


# --- 1. the gate ----------------------------------------------------------

def test_the_gate_reads_the_pooled_column():
    assert deepdives.CANONICAL_COLUMN == "canonical_entries", (
        "the per-field columns each miss the 40% bar; only the union clears it")


def test_the_gate_falls_back_rather_than_crashing_on_an_unjoined_index():
    """An index built before the taxonomy join has no canonical column. The
    answer must be 'no page', not a KeyError that takes down the site build."""
    df = frame([{"concepts": ["a"], "topics": [], "canonical_entries": []}])
    df = df.drop(columns=["canonical_entries"])

    class C:
        rows = df

    # It must ANSWER, not raise. What it answers depends on the raw column it
    # falls back to; the point is that a missing canonical column is survivable.
    report, rankable, bar = deepdives.concepts_verdict(C())
    assert bar == corpus.RANKABLE_HEAD_COVERAGE
    assert isinstance(rankable, bool)
    assert report["vocabulary"] >= 0


def test_the_shipped_index_clears_the_bar_through_the_pooled_column():
    idx = REPO_ROOT / "data" / "archive_index.parquet"
    if not idx.exists():
        pytest.skip("no built index")
    c = corpus.load_corpus(str(idx))
    if vocabulary.load_taxonomy(REPO_ROOT / "data" / "taxonomy" / "v1.yaml") is None:
        pytest.skip("no taxonomy")
    if "canonical_entries" not in c.rows.columns:
        pytest.skip("index predates the join")
    _report, rankable, _bar = deepdives.concepts_verdict(c)
    assert rankable, "the pages would silently stop building"


# --- 2. the cascade -------------------------------------------------------

def test_the_cascade_is_ordered_by_peak_year_not_by_volume():
    """The sort IS the visualization. iPod has more articles than AI here, so a
    volume sort puts it first for the wrong reason; a peak-year sort puts it
    first for the right one. The assertion checks the PEAK COLUMN is ascending,
    which volume ordering cannot satisfy."""
    per, totals, _co, years = vocabulary.tally(tiny())
    html = vocabulary.render_cascade(per, totals, years)
    peaks = [int(y) for y in re.findall(r"class='cpk'>(\d{4})</span>", html)]

    # The DISPLAYED peak must be the real peak — not the first year (2006) and
    # not the last (2020). Lens spans 2006-2020 and peaks in 2010, so this one
    # assertion rules out three separate mutations at once.
    assert peaks == [2010, 2020], f"expected Lens@2010 then AI@2020, got {peaks}"
    assert peaks == sorted(peaks), "cascade not in ascending peak order"


def test_a_year_with_no_articles_gets_no_mark_rather_than_a_colour():
    """Zero is the absence of a mark. Giving it a ramp step would make an empty
    year indistinguishable from a quiet one."""
    per, totals, _co, years = vocabulary.tally(tiny())
    html = vocabulary.render_cascade(per, totals, years)
    cells = re.findall(r"<i class='cc vtip' style='([^']*)'", html)
    assert "" in cells, "no unmarked cells — every year appears populated"
    assert any(c.startswith("background:") for c in cells)

    # A QUIET year must still be marked. Lens 2006 is 1 of a peak of 5 (20%),
    # so it sits low on the ramp but is not blank — otherwise "read nothing
    # that year" and "read a little" render identically.
    lens = [ch for ch in html.split("<div class='crow'>")
            if "class='cn'>Lens<" in ch][0]
    marked = len(re.findall(r"style='background:", lens))
    assert marked == 4, f"Lens has 4 non-empty years, {marked} marked"


def test_cascade_intensity_is_per_entry_not_global():
    """A small entry's peak must read as brightly as a large one's, or two
    decades of quieter reading flatten to nothing."""
    per, totals, _co, years = vocabulary.tally(tiny())
    html = vocabulary.render_cascade(per, totals, years)
    top = vocabulary.ORANGE[4]
    # One <div class='crow'> per entry; split on the row boundary rather than
    # trying to balance nested spans with a regex.
    chunks = html.split("<div class='crow'>")[1:]
    by_name = {}
    for ch in chunks:
        m = re.search(r"class='cn'>([^<]*)</span>", ch)
        if m and m.group(1):
            by_name[m.group(1)] = ch
    for nm in ("Lens", "Artificial Intelligence"):
        assert nm in by_name, f"{nm} missing from the cascade"
        assert top in by_name[nm], (
            f"{nm} never reaches the top ramp step — intensity is global, not per-entry")

    # Scaled by each entry's PEAK (5), not its TOTAL (9): 2014's two articles
    # are 40% of the peak and must land mid-ramp. Scaled by the total they are
    # 22% and drop a step — a real visual difference on any multi-year entry,
    # and invisible to a fixture where every entry occupies one year.
    lens = by_name["Lens"]
    swatches = re.findall(r"background:(#[0-9a-fA-F]{6})", lens)
    assert vocabulary.ORANGE[2] in swatches, (
        f"2014 did not land mid-ramp; got {swatches} — intensity is scaled by "
        "the entry total rather than its peak")


# --- 3. the palette -------------------------------------------------------

def _luminance(hexcolor):
    def chan(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hexcolor[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * chan(r) + 0.7152 * chan(g) + 0.0722 * chan(b)


def test_the_ramp_is_monotonic_in_lightness():
    ls = [_luminance(c) for c in vocabulary.ORANGE]
    assert ls == sorted(ls), f"sequential ramp is not monotonic: {ls}"


def test_every_ramp_step_clears_three_to_one_against_the_surface():
    """Three earlier orange ramps failed exactly here — orange sits darker than
    amber at equal chroma, so its natural low steps disappear on stone-900."""
    surface = _luminance("#1c1917")
    for c in vocabulary.ORANGE:
        lum = _luminance(c)
        ratio = (max(lum, surface) + 0.05) / (min(lum, surface) + 0.05)
        assert ratio >= 3.0, f"{c} is {ratio:.2f}:1 against the surface"


def test_the_brand_hue_is_in_the_ramp():
    assert "#FF8F3B" in vocabulary.ORANGE


# --- the matrix -----------------------------------------------------------

def test_seriation_is_deterministic():
    """Rendered on every build; a set-iteration-order dependency would make the
    page churn in git and in the deploy for no reason."""
    _per, totals, co, _years = vocabulary.tally(tiny())
    names = list(totals)
    assert vocabulary._seriate(names, co) == vocabulary._seriate(names, co)


def test_the_matrix_diagonal_is_hatched_not_coloured():
    """An entry always co-occurs with itself. Colouring that draws a bright
    diagonal of no information straight through the data."""
    _per, totals, co, _years = vocabulary.tally(tiny())
    html = vocabulary.render_matrix(totals, co, limit=2)
    assert html.count("mc self") == 2
    assert "background:#" not in re.findall(r"<i class='mc self[^>]*>", html)[0]


def test_pairs_that_never_co_occur_get_no_mark():
    df = frame([
        {"year": 2020, "canonical_entries": ["A"], "concepts": [], "topics": []},
        {"year": 2020, "canonical_entries": ["B"], "concepts": [], "topics": []},
    ])
    _per, totals, co, _years = vocabulary.tally(df)
    html = vocabulary.render_matrix(totals, co, limit=2)
    assert "never together" in html


# --- the taxonomy loader --------------------------------------------------

@pytest.mark.parametrize("content", ["", "not a mapping", "entries: []", "{}"])
def test_an_unusable_taxonomy_costs_the_pages_not_the_build(tmp_path, content):
    """generate.py is the last step of a nightly that has already walked the
    vault. A broken taxonomy must not take that down."""
    p = tmp_path / "t.yaml"
    p.write_text(content)
    assert vocabulary.load_taxonomy(p) is None


def test_a_missing_taxonomy_returns_none(tmp_path):
    assert vocabulary.load_taxonomy(tmp_path / "absent.yaml") is None


def test_the_shipped_taxonomy_carries_its_own_provenance():
    """The Collapse states where the vocabulary came from. Those numbers live
    in the taxonomy because clusters.json is gitignored — without them the page
    would hardcode three figures that rot on the next derivation."""
    doc = vocabulary.load_taxonomy(REPO_ROOT / "data" / "taxonomy" / "v1.yaml")
    if doc is None:
        pytest.skip("no taxonomy")
    d = doc.get("derivation") or {}
    assert d.get("strings", 0) > len(doc["entries"]), "derivation missing or implausible"
    assert d.get("clusters", 0) > 0
    assert doc.get("gate_reviewed", 0) >= len(doc["entries"])


def test_an_unjoined_index_does_not_take_down_the_other_deep_dives(tmp_path):
    """Behaviour, not source text.

    The first version of this test asserted two literal lines existed in
    generate.py. That is defeated by appending `joined = True` after them —
    both lines still present, guard gone, suite green — and it breaks on any
    harmless rename. It also duplicated a pre-existing trends-layer test that
    does the real check. This builds the failure instead.
    """
    per, totals, co, years = vocabulary.tally(tiny())
    # A row whose entity column is None is the shape that raised TypeError
    # inside the deep-dive try/except and cost six page groups.
    df = frame([
        {"year": 2020, "canonical_entries": None, "concepts": [], "topics": []},
        {"year": 2020, "canonical_entries": ["Lens"], "concepts": [], "topics": []},
    ])
    per, totals, _co, years = vocabulary.tally(df)      # must not raise
    assert totals["Lens"] == 1
    assert vocabulary.render_cascade(per, totals, years)


@pytest.mark.parametrize("value", [None, float("nan"), "a bare string", 3])
def test_tally_survives_every_shape_an_entity_column_arrives_in(value):
    """corpus.as_list exists because this guarantee has failed before. This
    module was the only one in site/ reading an entity column without it."""
    df = frame([{"year": 2020, "canonical_entries": value,
                 "concepts": [], "topics": []}])
    per, totals, co, years = vocabulary.tally(df)       # must not raise
    assert isinstance(totals, collections.Counter)


def test_the_seriation_beats_the_orderings_it_claims_to_beat():
    """The matrix's whole payoff is that related entries sit adjacent, and
    nothing pinned it — seriation could be replaced by alphabetical, by input
    order, or by picking the WEAKEST neighbour, and the suite stayed green.

    Distance-weighted co-occurrence cost: sum of pair-weight times how far
    apart the pair sits. Lower means tighter blocks.
    """
    idx = REPO_ROOT / "data" / "archive_index.parquet"
    if not idx.exists():
        pytest.skip("no built index")
    c = corpus.load_corpus(str(idx))
    if "canonical_entries" not in c.rows.columns:
        pytest.skip("index predates the join")
    _per, totals, co, _years = vocabulary.tally(c.rows)
    names = [nm for nm, _ in totals.most_common(vocabulary.MATRIX_LIMIT)]

    def cost(order):
        pos = {nm: i for i, nm in enumerate(order)}
        return sum(v * abs(pos[a] - pos[b])
                   for k, v in co.items()
                   for a, b in [tuple(k)]
                   if a in pos and b in pos)

    seriated = cost(vocabulary._seriate(names, co))
    assert seriated < cost(sorted(names)), "no better than alphabetical"
    assert seriated < cost(names), "no better than input order"


def test_seriation_is_stable_across_hash_seeds():
    """The previous version called _seriate twice in ONE process, which proves
    nothing about hash seeding — the thing it was named for."""
    import subprocess
    script = (
        "import sys,collections;sys.path.insert(0,'site');import vocabulary as V;"
        "co=collections.Counter({frozenset(('a','b')):3,frozenset(('b','c')):2,"
        "frozenset(('a','c')):1});print(V._seriate(['a','b','c','d'],co))")
    outs = set()
    for seed in ("0", "1", "42", "7919"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        r = subprocess.run([sys.executable, "-c", script], capture_output=True,
                           text=True, cwd=REPO_ROOT, env=env)
        assert r.returncode == 0, r.stderr
        outs.add(r.stdout.strip())
    assert len(outs) == 1, f"seriation varies with PYTHONHASHSEED: {outs}"


HOSTILE = "Ev'il \"Quote\" <script>alert(1)</script> & more"


@pytest.mark.parametrize("render", ["cascade", "matrix", "collapse"])
def test_entry_names_are_escaped_on_every_renderer(render):
    """Entry names are LLM output landing in single-quoted attributes. Removing
    e() from any of the four paths left the suite green before this existed."""
    df = frame([{"year": 2020, "canonical_entries": [HOSTILE, "Plain"],
                 "concepts": [], "topics": []}])
    per, totals, co, years = vocabulary.tally(df)
    html = {"cascade": lambda: vocabulary.render_cascade(per, totals, years),
            "matrix": lambda: vocabulary.render_matrix(totals, co, limit=2),
            "collapse": lambda: vocabulary.render_collapse(
                {HOSTILE: 5, "Plain": 1}, totals, 0, 100, 50, 10)}[render]()
    assert "<script>" not in html, "raw script tag reached the page"
    assert "Ev'il" not in html, "bare apostrophe would close a single-quoted attribute"
    assert "&#x27;" in html and "&lt;script&gt;" in html


def test_adjacent_ramp_steps_stay_distinguishable():
    """The floor is pinned; the SEPARATION was not — and a 'let's make it
    prettier' pass is likelier to compress the steps than to lower the floor.
    At 11px and 14px cells, steps that converge make the ramp unreadable while
    every existing test stays green."""
    ls = [_luminance(c) for c in vocabulary.ORANGE]
    for a, b in zip(ls, ls[1:]):
        ratio = (b + 0.05) / (a + 0.05)
        assert ratio >= 1.2, f"adjacent steps only {ratio:.2f}:1 apart"


def test_every_colour_in_the_stylesheet_clears_the_floor_it_claims():
    """The page argues every step is >=3:1. Three hardcoded colours in
    VOCAB_STYLE did not clear it, one of them the funnel's own data ink."""
    surface = _luminance("#1c1917")
    data_ink = re.findall(r"\.(?:fstage|kbar) i\{[^}]*background:(#[0-9a-fA-F]{6})",
                          vocabulary.VOCAB_STYLE)
    assert data_ink, "no data-ink colours found — did the selectors change?"
    for c in data_ink:
        lum = _luminance(c)
        ratio = (max(lum, surface) + 0.05) / (min(lum, surface) + 0.05)
        assert ratio >= 3.0, f"{c} is {ratio:.2f}:1 — below the floor the page claims"
