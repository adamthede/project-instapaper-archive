"""The private unread pages: /unread/, /unread/versus/, /unread/worth/.

Step 6 of the unread-corpus study. Everything here is synthetic: a hand-sized
enriched corpus, a hand-written Matter ledger, and the small index the cover
tests already build. The real corpus is never read.

Each test names the mutation that turns it red; `tests/mutation_audit_unread_pages.sh`
applies every one of them and checks the named test fails.
"""
import csv
import datetime as dt
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "site"))
sys.path.insert(0, str(ROOT / "tests"))

import generate as gen  # noqa: E402
import htmlkit  # noqa: E402
import unread_pages as up  # noqa: E402
from test_site_cover import index_file, synth_dir  # noqa: E402,F401

SUMMARY_SENTINEL = "BODYQUOTE the article body says exactly this and nothing else"
HOSTILE_TITLE = 'Bad <script>alert(1)</script> "quote" & <b>'


def rec(i, **over):
    base = {
        "url_sha256": f"{i:064x}", "title": f"Piece {i}",
        "url": f"https://www.example.com/p{i}", "domain": "www.example.com",
        "saved_date": "2018-03-0%d" % (i % 9 + 1), "saved_year": 2018,
        "folder": "", "starred": False, "read_progress": 0.0,
        "abandonment": "never_opened", "resolve_path": "instapaper",
        "body_words": 1200, "content_corrupted": False, "aged_out": False,
        "ai_topics": ["Artificial Intelligence"], "ai_summary": SUMMARY_SENTINEL,
        "why_saved": f"Saved because of reason {i}.",
        "why_saved_confidence": "high", "why_saved_kind": "inference",
    }
    base.update(over)
    return base


RECORDS = [
    rec(1),
    rec(2, saved_year=2017, saved_date="2017-05-01", abandonment="started",
        read_progress=0.4, why_saved_confidence="low"),
    rec(3, saved_year=2019, saved_date="2019-01-02", why_saved="Line one.\nLine two."),
    rec(4, saved_year=2014, saved_date="2014-02-02", aged_out=True, title="AGED OUT PIECE"),
    rec(5, saved_year=2023, saved_date="2023-07-07", aged_out=None, title="UNJUDGED PIECE"),
    rec(6, saved_year=2016, saved_date="2016-06-06", content_corrupted=True,
        title="CORRUPTED PIECE"),
    rec(7, saved_year=2018, resolve_path="metadata", body_words=0,
        domain="dead.example.org", url="javascript:alert(1)", title=HOSTILE_TITLE),
    rec(8, saved_year=2025, saved_date="2025-01-15", abandonment="nearly_finished",
        read_progress=0.9, why_saved=""),
]


def write_enriched(path, records=RECORDS):
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return path


DAILY = [
    # date, count, words, started, in, out, days_since, source
    ("2023-05-12", 431, 900000, None, None, None, None, "export-2023"),
    ("2026-09-17", 512, 1300000, 66, None, None, None, "matter-api"),
    ("2026-09-18", 508, 1290000, 64, 0, 4, 1, "matter-api"),
    ("2026-09-19", 506, 1280000, 64, 0, 2, 1, "matter-api"),
    ("2026-09-21", 510, 1300000, 65, 5, 1, 2, "matter-api"),
    ("2026-09-22", 512, 1310000, 65, 3, 1, 1, "matter-api"),
]


def write_daily(path, rows=DAILY):
    fields = ["date", "count", "total_words", "started_not_finished_count",
              "new_ids_since_previous", "gone_ids_since_previous",
              "previous_date", "days_since_previous", "source"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(fields)
        for d, c, words, st, i, o, gap, src in rows:
            w.writerow([d, c, words, "" if st is None else st, "" if i is None else i,
                        "" if o is None else o, "", "" if gap is None else gap, src])
    return path


def write_ledger(path, n_lines=3):
    lines = []
    for k in range(n_lines):
        items = [{"id": f"itm_{k}_{j}", "word_count": 300 + j * 700,
                  "reading_progress": [0.0, 0.5, 1.0][j % 3],
                  "site": ["Site A", "Site B"][j % 2], "updated_at": "2026-09-22T00:00:00Z"}
                 for j in range(k + 2)]
        lines.append(json.dumps({"taken_at": f"2026-09-2{k}T00:00:00Z", "date": f"2026-09-2{k}",
                                 "count": len(items), "total_words": 0, "items": items}))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def unread_files(tmp_path):
    return {"enriched": write_enriched(tmp_path / "enriched.jsonl"),
            "daily_csv": write_daily(tmp_path / "daily.csv"),
            "ledger": write_ledger(tmp_path / "ledger.jsonl")}


@pytest.fixture
def built(synth_dir, index_file, unread_files, tmp_path):  # noqa: F811
    out = tmp_path / "_site"
    gen.generate(synth_dir, out, index_path=index_file,
                 unread_paths={k: str(v) for k, v in unread_files.items()})
    return out


@pytest.fixture
def pages_html(built):
    return {rel: (built / rel / "index.html").read_text(encoding="utf-8")
            for rel in ("unread", "unread/versus", "unread/worth")}


def visible_text(raw):
    """The page's prose: tags, attribute values and data strings stripped."""
    body = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", raw, flags=re.S)
    return re.sub(r"<[^>]+>", " ", body)


# ---------------------------------------------------------------------------
# privacy
# ---------------------------------------------------------------------------

def test_load_records_drops_every_field_outside_the_allowlist(tmp_path):
    """Mutation: add "ai_summary" to RECORD_FIELDS. The summary is the field most
    likely to quote an article body, and the renderers must not be able to see it."""
    records = up.load_records(write_enriched(tmp_path / "e.jsonl"))
    assert records and all("ai_summary" not in r for r in records)
    assert all(set(r) <= up.RECORD_FIELDS for r in records)


def test_no_page_carries_the_summary_text(pages_html):
    """A backstop with no single-edit mutation: reaching the summary takes
    both widening RECORD_FIELDS and a renderer that reads it. Held at the page
    so that pair of edits cannot land together silently."""
    for rel, raw in pages_html.items():
        assert "BODYQUOTE" not in raw, rel


def test_every_shown_inference_is_labelled_with_its_confidence(pages_html):
    """Mutation: drop the "Inference · {conf} confidence" label from render_worth."""
    raw = pages_html["unread/worth"]
    shown = re.findall(r'<div class="why( low)?"><span class="label">(.*?)</span>(.*?)</div>', raw)
    assert shown, "no inference lines rendered"
    for _, label, text in shown:
        assert label.startswith("Inference"), label
        if text.startswith("Saved because"):
            assert re.match(r"Inference · (high|medium|low) confidence", label), label
    # the low-confidence row is dimmed, not hidden
    assert 'class="why low"><span class="label">Inference · low confidence' in raw


def test_an_inference_is_flattened_to_one_line_and_fenced():
    """Mutation: make one_line() return its input unchanged."""
    assert up.one_line("Line one.\nLine two.") == "Line one. Line two."
    long = "word " * 200
    out = up.one_line(long)
    assert len(out) <= up.MAX_INFERENCE_CHARS and "\n" not in out


def test_a_non_http_url_is_never_linked_and_titles_are_escaped(pages_html):
    """Mutation: replace safe_url(p.get("url")) with e(p.get("url") or "")."""
    raw = pages_html["unread/worth"]
    assert "javascript:" not in raw
    assert "<script>alert(1)</script>" not in raw
    assert "Bad &lt;script&gt;" in raw


# ---------------------------------------------------------------------------
# the shortlist
# ---------------------------------------------------------------------------

def test_the_shortlist_holds_only_pieces_judged_still_current(pages_html):
    """Mutation: in analysis.shortlist, admit aged_out None. Held at the page:
    aged-out, unjudged and corrupted pieces are never recommended."""
    raw = pages_html["unread/worth"]
    for title in ("AGED OUT PIECE", "UNJUDGED PIECE", "CORRUPTED PIECE"):
        assert title not in raw, title


def test_each_pick_carries_its_saved_date_source_and_link(pages_html):
    """Mutation: drop the saved_date join in worth_view()."""
    raw = pages_html["unread/worth"]
    assert '<a class="st" href="https://www.example.com/p1">Piece 1</a>' in raw
    row = raw.split("Piece 1</a>", 1)[1].split("</div>", 1)[0]
    assert "example.com · 2018-03-02" in row


def test_the_cover_counts_what_qualified_not_the_page_size(unread_files, monkeypatch):
    """Mutation: print len(w["picks"]) where w["eligible"] is printed. Five of
    the eight are still current (1, 2, 3, 7, 8); with a page size of two the
    two figures differ and the cover has to print the measurement."""
    monkeypatch.setattr(up, "SHORTLIST_SIZE", 2)
    data = up.load(enriched=unread_files["enriched"], daily_csv=None, ledger=None)
    raw = up.render_worth(data, up.pages(data))
    assert re.search(r'<div class="v num">5</div><div class="l label">Still current</div>'
                     r'<div class="delta">of 8 in the pool</div>', raw)
    assert '<div class="v num">2</div><div class="l label">Ranked here</div>' in raw


# ---------------------------------------------------------------------------
# the Matter ledger
# ---------------------------------------------------------------------------

def test_load_daily_keeps_only_the_nightly_ledger_rows(tmp_path):
    """Mutation: remove the LEDGER_SOURCE filter. The 2023 export seed is a
    different definition of the queue measured once, not a night."""
    rows = up.load_daily(write_daily(tmp_path / "d.csv"))
    assert [r["date"].isoformat() for r in rows][0] == "2026-09-17"
    assert len(rows) == 5


def test_ledger_weeks_sum_flows_and_skip_the_undiffable_first_night(tmp_path):
    """Mutation: `flows = rows` in ledger_weeks. The first night has nothing to
    diff against and must contribute no flow, not a zero or a crash."""
    weeks = up.ledger_weeks(up.load_daily(write_daily(tmp_path / "d.csv")))
    by = {w["week"]: w for w in weeks}
    w38, w39 = by["2026-W38"], by["2026-W39"]
    assert (w38["inflow"], w38["outflow"], w38["net"], w38["flow_days"]) == (0, 6, -6, 2)
    assert (w39["inflow"], w39["outflow"], w39["net"]) == (8, 2, 6)
    assert w38["end_count"] == 506


def test_a_week_with_too_few_nights_is_not_measured():
    """Mutation: MIN_DAYS_FOR_A_WEEK = 1."""
    day = dt.date(2026, 9, 21)
    daily = [{"date": day + dt.timedelta(days=i), "count": 500, "total_words": 0,
              "started": 0, "inflow": 1, "outflow": 1, "days_since_previous": 1}
             for i in range(3)]
    assert up.ledger_weeks(daily)[0]["measured"] is False


def _weeks(nets):
    return [{"measured": True, "net": v} for v in nets]


def test_projection_refuses_to_name_a_figure_on_too_few_weeks():
    """Mutation: MIN_PROJECTION_WEEKS = 1."""
    p = up.projection(_weeks([-10, -10, -10]), 500)
    assert p["state"] == "too_early" and p["measured"] == 3


def test_projection_says_never_when_the_median_week_does_not_drain():
    """Mutation: `median_net >= 0` becomes `median_net > 0`. A flat queue never empties."""
    assert up.projection(_weeks([0, 0, -5, 3]), 500)["state"] == "never"


def test_projection_divides_the_queue_by_the_median_drain():
    """Mutation: use the mean instead of the median."""
    p = up.projection(_weeks([-10, -10, -10, -70]), 500)
    assert p["state"] == "draining" and p["weeks"] == 50.0


def test_the_latest_snapshot_is_the_last_line(tmp_path):
    """Mutation: take lines[0] in load_latest_snapshot."""
    snap = up.load_latest_snapshot(write_ledger(tmp_path / "l.jsonl", n_lines=3))
    assert snap["date"] == "2026-09-22" and len(snap["items"]) == 4


def test_matter_reads_by_week_counts_matter_archive_events_only():
    """Mutation: drop the source == "matter" filter."""
    import pandas as pd
    frame = pd.DataFrame({
        "source": ["matter", "matter", "instapaper", "matter"],
        "date_archived": pd.to_datetime(["2026-09-21", "2026-09-22", "2026-09-22", None]),
    })
    assert up.matter_reads_by_week(frame) == {"2026-W39": 2}


def test_length_bands_are_inclusive_at_the_top_and_zero_is_no_length():
    """Mutation: `w <= hi` becomes `w < hi`."""
    out = up.length_bands([0, 500, 501, 1000, 10001, None])
    bands = dict(out["bands"])
    assert bands["1 to 500"] == 1 and bands["501 to 1,000"] == 2
    assert bands["Over 10,000"] == 1 and out["no_length"] == 2


# ---------------------------------------------------------------------------
# the comparison
# ---------------------------------------------------------------------------

def test_the_hypothesis_band_is_measured_on_both_corpora(pages_html):
    """Mutation: HYPOTHESIS_YEARS = (2018,). Four of the eight unread items were
    saved 2017 to 2019 (2, 1, 3, 7) and 2 of the 7 read-it-later rows in the
    cover fixture were (2018, 2019), so the cover reads 50% against 29%."""
    raw = pages_html["unread/versus"]
    assert re.search(r'<div class="v num">50%</div><div class="l label">Unread saved 2017-19</div>'
                     r'<div class="delta">against 29% of what was read</div>', raw), raw[:3000]


def test_the_comparison_is_not_built_without_the_read_corpus(synth_dir, unread_files, tmp_path):  # noqa: F811
    """Mutation: make pages() always include unread/versus. With no index there
    is no read side, and the subnav must not point at a page that was not built."""
    out = tmp_path / "_site"
    gen.generate(synth_dir, out, index_path=None,
                 unread_paths={k: str(v) for k, v in unread_files.items()})
    assert (out / "unread" / "index.html").exists()
    assert not (out / "unread" / "versus").exists()
    assert "versus/" not in (out / "unread" / "index.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# the generator
# ---------------------------------------------------------------------------

def test_the_build_writes_three_pages_and_the_weeks_index_links_them(built):
    """Mutation: remove the render_unread() call from generate()."""
    for rel in ("unread", "unread/versus", "unread/worth"):
        assert (built / rel / "index.html").exists(), rel
    weeks = (built / "weeks" / "index.html").read_text(encoding="utf-8")
    assert '<a href="../unread/">What I meant to read</a>' in weeks


def test_the_unread_pages_carry_the_six_item_row_and_mark_nothing(pages_html):
    """Mutation: narrow generate()'s `built` set so a row item drops out.
    Adam named the six; these pages sit outside the row, like /trends/, and
    carry the same row as every other page."""
    for rel, raw in pages_html.items():
        row = re.search(r'<nav class="pagelinks".*?</nav>', raw, re.S).group(0)
        got = re.findall(r'>([A-Z][a-z]+)</(?:a|span)>', row)
        assert got == ["Cover", "Weeks", "Years", "Sources", "Subjects", "Articles"], (rel, got)
        assert 'class="here"' not in row, rel


def test_every_subnav_link_resolves(built, pages_html):
    """Mutation: change a SUBPAGES target, e.g. "worth/" to "shortlist/"."""
    for rel, raw in pages_html.items():
        nav = re.search(r'<nav class="subnav".*?</nav>', raw, re.S).group(0)
        for href in re.findall(r'href="([^"]+)"', nav):
            assert ((built / rel) / href / "index.html").resolve().exists(), (rel, href)


def test_no_enriched_corpus_means_no_pages_and_no_link(synth_dir, index_file, tmp_path):  # noqa: F811
    """Mutation: drop the `if not records: return None` guard in load().

    The generator's except branch would also keep an empty corpus off the
    site, so the guard is held on load() itself: no corpus is a result, not
    an error to be caught."""
    assert up.load(enriched=tmp_path / "missing.jsonl", daily_csv=None, ledger=None) is None
    out = tmp_path / "_site"
    gen.generate(synth_dir, out, index_path=index_file,
                 unread_paths={"enriched": str(tmp_path / "missing.jsonl"),
                               "daily_csv": str(tmp_path / "x.csv"),
                               "ledger": str(tmp_path / "x.jsonl")})
    assert not (out / "unread").exists()
    assert "../unread/" not in (out / "weeks" / "index.html").read_text(encoding="utf-8")


def test_a_malformed_corpus_costs_these_pages_not_the_build(synth_dir, index_file, tmp_path):  # noqa: F811
    """Mutation: re-raise in render_unread()'s except branch."""
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"url_sha256": "a"}\n{not json\n', encoding="utf-8")
    out = tmp_path / "_site"
    gen.generate(synth_dir, out, index_path=index_file,
                 unread_paths={"enriched": str(bad), "daily_csv": str(tmp_path / "x.csv"),
                               "ledger": str(tmp_path / "x.jsonl")})
    assert (out / "weeks" / "index.html").exists()
    assert not (out / "unread").exists()


def test_main_passes_the_default_paths_so_the_nightly_needs_no_new_arguments(monkeypatch, tmp_path):
    """Mutation: default unread_paths to None in main(). The nightly runs
    `generate.py --out _site` and nothing else."""
    seen = {}
    monkeypatch.setattr(gen, "generate", lambda *a, **k: seen.update(k) or 1)
    gen.main(["--synthesis-dir", str(tmp_path), "--out", str(tmp_path / "o")])
    assert seen["unread_paths"]["enriched"] == str(up.DEFAULT_ENRICHED)
    assert seen["unread_paths"]["ledger"] == str(up.DEFAULT_LEDGER)
    seen.clear()
    gen.main(["--synthesis-dir", str(tmp_path), "--out", str(tmp_path / "o"), "--no-unread"])
    assert seen["unread_paths"] is None


# ---------------------------------------------------------------------------
# house style
# ---------------------------------------------------------------------------

def test_the_copy_uses_no_semicolons_em_dashes_or_analyze(pages_html):
    """Mutation: put a semicolon or an em dash back into any note, e.g. the
    reason line's "; " separator left unreplaced. Data strings from the
    fixture carry neither, so anything found is copy."""
    for rel, raw in pages_html.items():
        text = visible_text(raw)
        text = text.replace("&amp;", "&")
        assert ";" not in re.sub(r"&[a-z#0-9]+;", "", text), rel
        assert "—" not in text, rel
        assert not re.search(r"\banaly[sz]e", text, re.I), rel
