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
    """A fixture where every ordering DISAGREES with every other.

    Rewritten three times. Each earlier version fixed the collision it was
    written for and reintroduced another in a different direction:

      v1  early-peaking entry also had the most articles -> volume order ==
          peak order, and a volume-sort mutation passed.
      v2  every entry occupied exactly ONE year -> "peak" became
          indistinguishable from "first", "last" and "only", and per-entry
          scaling from global scaling. Six mutations escaped.
      v3  (this) the largest entry peaks LAST, the earliest-starting entry
          peaks LATE, one entry has an interior peak well below the global
          maximum, one pair TIES on peak year, and one cell sits at 5% of its
          entry's peak.

        Lens    2006:1  2010:6  2012:4  2014:2  2020:1   peak 2010  total 14
        Cinema  2005:3                  2018:4           peak 2018  first 2005
        Radio   2008:3                  2016:3           TIED peak -> 2008
        AI      2015:1                  2020:20          peak 2020  LARGEST

    What each property defeats:
      AI largest but peaks last      -> volume order != peak order
      Cinema starts first, peaks late -> first-year order != peak order
      Lens peak 6 vs global max 20    -> global scaling demotes it visibly
      Lens peak 6 vs its total 14     -> per-entry PEAK != per-entry TOTAL
      AI 2015 at 1/20 = 5%            -> a quiet-but-nonzero cell a
                                         frac<=0.08 blanking would eat
      Radio's tie                     -> pins the (count, -year) tie-break
    """
    def rows_for(name, per_year):
        out = []
        for year, count in per_year.items():
            out += [{"year": year, "canonical_entries": [name],
                     "concepts": [], "topics": []}] * count
        return out

    return frame(
        rows_for("Lens", {2006: 1, 2010: 6, 2012: 4, 2014: 2, 2020: 1})
        + rows_for("Cinema", {2005: 3, 2018: 4})
        + rows_for("Radio", {2008: 3, 2016: 3})
        + rows_for("Artificial Intelligence", {2015: 1, 2020: 20})
    )


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
    # Radio 2008, Lens 2010, Cinema 2018, AI 2020. Volume order would be
    # AI(21), Lens(14), Cinema(7), Radio(6) — completely different — and
    # first-year order would start with Cinema(2005). One assertion, three
    # mutations ruled out.
    assert peaks == [2008, 2010, 2018, 2020], f"got {peaks}"


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
            if re.search(r"class='cn'[^>]*>Lens<", ch)][0]
    marked = len(re.findall(r"style='background:", lens))
    assert marked == 5, f"Lens has 5 non-empty years, {marked} marked"

    # The sharpest case: AI's 2015 cell is 1 article against a peak of 20 —
    # 5% of its own peak. A blanking threshold anywhere at or above that eats
    # a real year, and no fixture with only loud cells can see it.
    ai = [ch for ch in html.split("<div class='crow'>")
          if re.search(r"class='cn'[^>]*>Artificial Intelligence<", ch)][0]
    assert len(re.findall(r"style='background:", ai)) == 2, (
        "AI's quiet 2015 cell was blanked — 'read a little' now renders as "
        "'read nothing'")


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
        m = re.search(r"class='cn'[^>]*>([^<]*)</span>", ch)
        if m and m.group(1):
            by_name[m.group(1)] = ch
    for nm in ("Lens", "Cinema", "Artificial Intelligence"):
        assert nm in by_name, f"{nm} missing from the cascade"
        assert top in by_name[nm], (
            f"{nm} never reaches the top ramp step — intensity is global, not per-entry")

    # Scaled by each entry's PEAK (5), not its TOTAL (9): 2014's two articles
    # are 40% of the peak and must land mid-ramp. Scaled by the total they are
    # 22% and drop a step — a real visual difference on any multi-year entry,
    # and invisible to a fixture where every entry occupies one year.
    # Lens peaks at 6 against a GLOBAL max of 20. Under global scaling its peak
    # is 30% and lands mid-ramp; per-entry it is 100% and must reach the top.
    lens = by_name["Lens"]
    swatches = re.findall(r"background:(#[0-9a-fA-F]{6})", lens)
    assert vocabulary.ORANGE[4] in swatches, (
        f"Lens never reaches the top step; got {swatches} — intensity is "
        "scaled globally, not per-entry")
    # ...and its 2012 cell (4 of a peak of 6 = 67%) must sit BELOW the top,
    # which per-entry TOTAL scaling (4 of 14 = 29%) would also fail.
    assert vocabulary.ORANGE[3] in swatches or vocabulary.ORANGE[2] in swatches


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


def test_a_null_entity_column_does_not_take_down_the_deep_dive_leg(tmp_path):
    """Behaviour, not source text.

    The first version of this test asserted two literal lines existed in
    generate.py. That is defeated by appending `joined = True` after them —
    both lines still present, guard gone, suite green — and it breaks on any
    harmless rename. It also duplicated a pre-existing trends-layer test that
    does the real check. This builds the failure instead.
    """
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


def test_the_seriation_beats_the_orderings_it_claims_to_beat_synthetically():
    """Same metric, planted blocks, NO parquet — so the matrix's whole payoff is
    still guarded on a machine that has never seen the archive. The real-index
    version below skips there, which is the wrong place for a guard to vanish.
    """
    import itertools
    blocks = [["a1", "a2", "a3"], ["b1", "b2", "b3"], ["c1", "c2", "c3"]]
    co = collections.Counter()
    for block in blocks:
        for x, y in itertools.combinations(block, 2):
            co[frozenset((x, y))] = 20
    for x, y in itertools.combinations([b[0] for b in blocks], 2):
        co[frozenset((x, y))] = 1          # weak cross-block links
    names = [n for b in blocks for n in b]

    def cost(order):
        pos = {nm: i for i, nm in enumerate(order)}
        return sum(v * abs(pos[a] - pos[b])
                   for k, v in co.items() for a, b in [tuple(k)]
                   if a in pos and b in pos)

    seriated = vocabulary._seriate(names, co)
    # The baseline has to actually SCATTER the blocks. Alphabetical and
    # reverse-alphabetical both group these names by accident (the block letter
    # sorts first), so either would score identically to a perfect seriation
    # and the assertion would compare 252 with 252. Interleaving is the honest
    # bad ordering: one member of each block, repeating.
    interleaved = [b[i] for i in range(3) for b in blocks]
    assert cost(seriated) < cost(interleaved), (
        f"seriated {cost(seriated)} not better than interleaved {cost(interleaved)}")
    # Every block must come out contiguous — that is what "blocks on the
    # diagonal" means, and it is the claim the page makes.
    for block in blocks:
        idx = sorted(seriated.index(x) for x in block)
        assert idx == list(range(idx[0], idx[0] + 3)), (
            f"block {block} is not contiguous in {seriated}")


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
                 "concepts": [], "topics": []},
                {"year": 2020, "canonical_entries": [HOSTILE, "Third"],
                 "concepts": [], "topics": []},
                {"year": 2020, "canonical_entries": ["Plain", "Fourth"],
                 "concepts": [], "topics": []}])
    per, totals, co, years = vocabulary.tally(df)
    # limit=2 with 2 entries left the omitted-pair list EMPTY, so the
    # disclosure note's name path was never exercised — which is exactly how an
    # unescaped interpolation shipped on the commit that added these tests.
    # limit must be BELOW the entry count for `off` to be non-empty.
    html = {"cascade": lambda: vocabulary.render_cascade(per, totals, years),
            "matrix": lambda: vocabulary.render_matrix(totals, co, limit=2),
            "collapse": lambda: vocabulary.render_collapse(
                {HOSTILE: 5, "Plain": 1}, totals, 0, 100, 50, 10)}[render]()
    assert "<script>" not in html, "raw script tag reached the page"
    assert "Ev'il" not in html, "bare apostrophe would close a single-quoted attribute"
    assert "&#x27;" in html and "&lt;script&gt;" in html


def test_the_disclosure_numbers_are_the_real_ones():
    """Three user-facing honesty claims, added in direct response to review, and
    none of them was pinned: a mutation making the page announce "Showing 11,369
    of 11,369 pairs (100%)" left the suite entirely green. A disclosure nobody
    checks is worse than no disclosure, because it reads as verified."""
    df = frame(
        [{"year": 2020, "canonical_entries": ["Big", "Also"],
          "concepts": [], "topics": []}] * 5
        + [{"year": 2020, "canonical_entries": ["Rare", "Tiny"],
            "concepts": [], "topics": []}] * 2)
    _per, totals, co, _years = vocabulary.tally(df)
    html = vocabulary.render_matrix(totals, co, limit=2)

    # 2 pairs exist; the top-2 matrix draws exactly 1 of them.
    assert "Showing 1 of 2 pairs" in html, html[html.find("Showing"):][:80]
    assert "(50%" in html, "pair share is not the real fraction"
    # Weight: Big+Also is 5 of 7 shared articles = 71%.
    assert "71% by shared-article weight" in html, "weight share is not real"
    # And the omitted pair must be the STRONGEST omitted, not the weakest.
    assert "Rare + Tiny (2 articles)" in html


def test_the_omitted_pair_disclosure_escapes_the_names_it_prints():
    """The blocker this file failed to catch once. render_matrix's 'strongest
    pair left off' note interpolated two entry names with no e(), and the
    escaping test above could not see it because its limit left the omitted
    list empty."""
    # The hostile name must be OMITTED, so it has to be RARE — the top-N are
    # the ones drawn, and the note names the strongest pair left OUT.
    df = frame(
        [{"year": 2020, "canonical_entries": ["Big", "Also"],
          "concepts": [], "topics": []}] * 5
        + [{"year": 2020, "canonical_entries": [HOSTILE, "Rare"],
            "concepts": [], "topics": []}] * 2)
    _per, totals, co, _years = vocabulary.tally(df)
    html = vocabulary.render_matrix(totals, co, limit=2)   # draws Big+Also only
    # NOT "left off:" — the source f-string wraps between those two words, so
    # that assertion never matches and the test fails for the wrong reason.
    # Assert on the DATA the path emits instead.
    assert "articles)" in html and "Rare" in html, (
        "fixture did not reach the omitted-pair disclosure")
    assert "<script>" not in html
    assert "Ev'il" not in html


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

    # Extract EVERY colour and require each to be classified. A selector-scoped
    # regex was evadable four ways — a space before the brace, a 3-digit hex,
    # an rgb() form, or simply a NEW dark data-ink selector — each of which left
    # the list non-empty so the "did it match anything" guard never fired.
    # Here an unrecognised colour fails rather than being skipped.
    NON_DATA_INK = {
        "#0c0a09": "tooltip background — its INK is 15.7:1 on it",
        "#44403c": "the self-cell hatch, which encodes NO information",
    }
    found = set(re.findall(r"(#[0-9a-fA-F]{3,6})\b", vocabulary.VOCAB_STYLE))
    assert found, "no colours found — did VOCAB_STYLE change shape?"
    assert not re.search(r"\brgb\(", vocabulary.VOCAB_STYLE), (
        "an rgb() colour would slip past the hex scan above")
    data_ink = sorted(c for c in found if c not in NON_DATA_INK)
    assert data_ink, "every colour was classified as non-data-ink — suspicious"
    for c in data_ink:
        lum = _luminance(c)
        ratio = (max(lum, surface) + 0.05) / (min(lum, surface) + 0.05)
        assert len(c) == 7, f"{c} is short-form hex; the luminance check needs #rrggbb"
        assert ratio >= 3.0, f"{c} is {ratio:.2f}:1 — below the floor the page claims"


def test_the_two_sub_floor_colours_are_measured_against_the_right_thing():
    """Both were flagged as below 3:1, and both are fine — because the ratio
    that matters is not the one against the page surface.

    #0c0a09 is the TOOLTIP BACKGROUND. Its job is to be dark; what has to be
    readable is the ink on top of it, which is 15.7:1.

    #44403c is the diagonal hatch on the matrix's self-cells, which deliberately
    encodes NO INFORMATION — an entry always co-occurs with itself. A recessive
    non-data mark that stayed under the data floor is correct, not an oversight,
    and raising it would draw a bright line through the middle of the matrix.
    """
    assert "#0c0a09" in vocabulary.VOCAB_STYLE and "#44403c" in vocabulary.VOCAB_STYLE
    ink_on_tooltip = _luminance("#e7e5e4"), _luminance("#0c0a09")
    ratio = (max(ink_on_tooltip) + 0.05) / (min(ink_on_tooltip) + 0.05)
    assert ratio >= 4.5, f"tooltip text is {ratio:.2f}:1 on its own background"
    # The hatch must stay recessive: brighter than this and it competes with data.
    hatch = _luminance("#44403c")
    surface = _luminance("#1c1917")
    assert (hatch + 0.05) / (surface + 0.05) < 3.0, (
        "the no-information hatch is now as loud as the data")
