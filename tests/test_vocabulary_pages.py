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
    """Two entries whose PEAK order and VOLUME order disagree.

    This is the whole point of the fixture and it took a surviving mutation to
    get right. The first version had iPod peaking earlier AND carrying more
    articles, so sorting by peak year and sorting by volume produced the
    identical order — the test could not tell the two apart, and replacing the
    peak sort with a volume sort left it green.

        iPod   1 article,  2006 only   -> peaks FIRST, smallest
        AI     4 articles, 2020 only   -> peaks LAST,  largest

    Peak order:   iPod, AI.   Volume order:  AI, iPod.
    """
    return frame(
        [{"year": 2006, "canonical_entries": ["iPod"], "concepts": [], "topics": []}]
        + [{"year": 2020, "canonical_entries": ["Artificial Intelligence"],
            "concepts": [], "topics": []} for _ in range(4)]
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
    # The discriminating precondition: volume order is the REVERSE of peak order.
    assert totals["iPod"] < totals["Artificial Intelligence"]
    html = vocabulary.render_cascade(per, totals, years)
    peaks = [int(y) for y in re.findall(r"class='cpk'>(\d{4})</span>", html)]
    assert peaks == sorted(peaks), f"cascade not in peak order: {peaks}"
    assert peaks[0] == 2006 and peaks[-1] == 2020


def test_a_year_with_no_articles_gets_no_mark_rather_than_a_colour():
    """Zero is the absence of a mark. Giving it a ramp step would make an empty
    year indistinguishable from a quiet one."""
    per, totals, _co, years = vocabulary.tally(tiny())
    html = vocabulary.render_cascade(per, totals, years)
    cells = re.findall(r"<i class='cc vtip' style='([^']*)'", html)
    assert "" in cells, "no unmarked cells — every year appears populated"
    assert any(c.startswith("background:") for c in cells)


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
    for nm in ("iPod", "Artificial Intelligence"):
        assert nm in by_name, f"{nm} missing from the cascade"
        assert top in by_name[nm], (
            f"{nm} never reaches the top ramp step — intensity is global, not per-entry")


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


def test_an_unjoined_index_does_not_take_down_the_other_deep_dives():
    """The bug this pins: `rankable` alone gated the pages, and on an index with
    no canonical column the verdict falls back to the raw `concepts` field —
    which on a small corpus trivially clears the bar. The render path then died
    on KeyError('canonical_entries') INSIDE the deep-dive try/except, so
    /trends/, /orgs/, /people/ and /locations/ all silently stopped being
    written. One missing column cost five pages.
    """
    import generate

    src = pathlib.Path(generate.__file__).read_text()
    # The column's presence must be a precondition in its own right.
    assert "joined = deepdives.CANONICAL_COLUMN in corpus_data.rows.columns" in src
    assert "if joined and rankable and tax_doc:" in src
