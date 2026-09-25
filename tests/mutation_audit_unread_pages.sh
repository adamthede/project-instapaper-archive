#!/bin/bash
# Mutation audit for the private unread pages (site/unread_pages.py and its
# wiring in site/generate.py).
#
# Same method and harness as tests/mutation_audit_sibling_nav.sh: break one
# thing at a time and confirm the named test fails. A test that stays green
# under its own mutation is not testing what its docstring claims.
#
#   bash tests/mutation_audit_unread_pages.sh
#
# Every mutation is reverted immediately.

set -u
cd "$(dirname "$0")/.." || exit 1

export PYTHONDONTWRITEBYTECODE=1
PY="${MATTER_TEST_PYTHON:-.venv/bin/python}"
[ -x "$PY" ] || PY="$(dirname "$(git rev-parse --git-common-dir)")/.venv/bin/python"
[ -x "$PY" ] || PY="$(dirname "$(git rev-parse --git-common-dir)")/venv/bin/python"
[ -x "$PY" ] || { echo "No test interpreter found; set MATTER_TEST_PYTHON."; exit 1; }

PASS=0; FAIL=0

run_mutation() {
  local label="$1" file="$2" from="$3" to="$4" tests="$5"
  cp "$file" "$file.mutbak"
  "$PY" - "$file" "$from" "$to" <<'SUB'
import sys, pathlib
path, old, new = sys.argv[1], sys.argv[2], sys.argv[3]
p = pathlib.Path(path); s = p.read_text()
if s.count(old) != 1:
    print(f"    anchor matched {s.count(old)} times, expected 1"); sys.exit(9)
p.write_text(s.replace(old, new))
SUB
  if [ $? -eq 9 ]; then
    printf '  STALE    %-62s (anchor no longer matches)\n' "$label"
    mv "$file.mutbak" "$file"; FAIL=$((FAIL+1)); return
  fi
  local out
  find . -name "__pycache__" -type d -not -path "./.git/*" -exec rm -rf {} + 2>/dev/null
  out=$("$PY" -m pytest $tests -q -p no:cacheprovider 2>&1 | grep -oE '[0-9]+ (failed|error|passed)[^$]*' | head -1)
  if echo "$out" | grep -qE 'failed|error'; then
    printf '  CAUGHT   %-62s %s\n' "$label" "$out"; PASS=$((PASS+1))
  else
    printf '  ESCAPED  %-62s %s  <-- UNGUARDED\n' "$label" "$out"; FAIL=$((FAIL+1))
  fi
  mv "$file.mutbak" "$file"
}

U=site/unread_pages.py
G=site/generate.py
A=scripts/unread/analysis.py
T=tests/test_site_unread.py

echo "privacy"
run_mutation "ai_summary admitted by the field allowlist" $U \
  '"why_saved", "why_saved_confidence", "why_saved_kind",' \
  '"why_saved", "why_saved_confidence", "why_saved_kind", "ai_summary",' \
  "$T::test_load_records_drops_every_field_outside_the_allowlist"
run_mutation "inference shown without its label" $U \
  'label = (f"Inference · {e(conf)} confidence" if graded' \
  'label = (f"{e(conf)}" if graded' \
  "$T::test_every_shown_inference_is_labelled_with_its_confidence"
run_mutation "inference not flattened to one line" $U \
  '    flat = " ".join(str(text or "").split())' \
  '    flat = str(text or "")' \
  "$T::test_an_inference_is_flattened_to_one_line_and_fenced"
run_mutation "any URL scheme linked" $U \
  'url = safe_url(p.get("url"))' \
  'url = e(p.get("url") or "")' \
  "$T::test_a_non_http_url_is_never_linked_and_titles_are_escaped"

echo "shortlist"
run_mutation "unjudged pieces admitted to the shortlist" $A \
  '    eligible = [r for r in records
                if r.get("aged_out") is False' \
  '    eligible = [r for r in records
                if r.get("aged_out") is not True' \
  "$T::test_the_shortlist_holds_only_pieces_judged_still_current"
run_mutation "saved date not joined back onto the pick" $U \
  'p["saved_date"] = (by_hash.get(p["url_sha256"]) or {}).get("saved_date")' \
  'p["saved_date"] = None' \
  "$T::test_each_pick_carries_its_saved_date_source_and_link"
run_mutation "cover prints the page size, not what qualified" $U \
  '_stat(n(w["eligible"]), "Still current"' \
  '_stat(n(len(w["picks"])), "Still current"' \
  "$T::test_the_cover_counts_what_qualified_not_the_page_size"

echo "ledger"
run_mutation "2023 export seed treated as a night" $U \
  'if r.get("source") != LEDGER_SOURCE:' \
  'if False:' \
  "$T::test_load_daily_keeps_only_the_nightly_ledger_rows"
run_mutation "undiffable first night counted as flow" $U \
  'flows = [r for r in rows if r["inflow"] is not None and r["outflow"] is not None]' \
  'flows = rows' \
  "$T::test_ledger_weeks_sum_flows_and_skip_the_undiffable_first_night"
run_mutation "a one-night week counts as measured" $U \
  'MIN_DAYS_FOR_A_WEEK = 5' 'MIN_DAYS_FOR_A_WEEK = 1' \
  "$T::test_a_week_with_too_few_nights_is_not_measured"
run_mutation "projection on one week" $U \
  'MIN_PROJECTION_WEEKS = 4' 'MIN_PROJECTION_WEEKS = 1' \
  "$T::test_projection_refuses_to_name_a_figure_on_too_few_weeks"
run_mutation "flat queue projected to empty" $U \
  'if median_net >= 0 or not queue_size:' 'if median_net > 0 or not queue_size:' \
  "$T::test_projection_says_never_when_the_median_week_does_not_drain"
run_mutation "projection on the mean" $U \
  'median_net = statistics.median(measured)' 'median_net = statistics.mean(measured)' \
  "$T::test_projection_divides_the_queue_by_the_median_drain"
run_mutation "first ledger line read as the latest" $U \
  'for line in reversed(complete):' \
  'for line in complete:' \
  "$T::test_the_latest_snapshot_is_the_last_line"
run_mutation "reads from every source counted as drain" $U \
  'rows = frame[frame["source"] == "matter"]' 'rows = frame' \
  "$T::test_matter_reads_by_week_counts_matter_archive_events_only"
run_mutation "length band top exclusive" $U \
  'if w >= lo and (hi is None or w <= hi):' 'if w >= lo and (hi is None or w < hi):' \
  "$T::test_length_bands_are_inclusive_at_the_top_and_zero_is_no_length"

echo "comparison"
run_mutation "hypothesis band narrowed" $U \
  'HYPOTHESIS_YEARS = (2017, 2018, 2019)' 'HYPOTHESIS_YEARS = (2018,)' \
  "$T::test_the_hypothesis_band_is_measured_on_both_corpora"
run_mutation "comparison built without the read corpus" $U \
  '    if data.get("versus"):' '    if True:' \
  "$T::test_the_comparison_is_not_built_without_the_read_corpus"

echo "generator"
run_mutation "unread leg never called" $G \
  '            unread_mod.write_pages(tmp, unread_data, domain=DOMAIN)' '            pass' \
  "$T::test_the_build_writes_three_pages_and_the_weeks_index_links_them"
run_mutation "row narrowed under the unread pages" $G \
  'built = {"years", "orgs", "articles"} | unread_keys' 'built = {"years", "orgs"} | unread_keys' \
  "$T::test_the_unread_pages_carry_the_seven_item_row_and_mark_unread"
run_mutation "subnav target renamed" $U \
  '("unread/worth", "Still worth your time", "worth/")' \
  '("unread/worth", "Still worth your time", "shortlist/")' \
  "$T::test_every_subnav_link_resolves"
run_mutation "empty corpus still builds pages" $U \
  '    if not records:
        return None
    if frame is None:' \
  '    if frame is None:' \
  "$T::test_no_enriched_corpus_means_no_pages_and_no_link"
run_mutation "unread failure takes the build down" $G \
  '        print(f"unread pages failed ({err!r}): skipping /unread/", file=sys.stderr)' \
  '        raise' \
  "$T::test_a_malformed_corpus_costs_these_pages_not_the_build"
run_mutation "nightly default drops the unread pages" $G \
  '    unread_paths = None if args.no_unread else {' \
  '    unread_paths = None if True else {' \
  "$T::test_main_passes_the_default_paths_so_the_nightly_needs_no_new_arguments"

echo "house style"
run_mutation "reason line keeps its semicolons" $U \
  'reason = str(p.get("reason") or "").replace("; ", " · ")' \
  'reason = str(p.get("reason") or "")' \
  "$T::test_the_copy_uses_no_semicolons_em_dashes_or_analyze"

echo "fix round 1: ledger reader and failure domains"
run_mutation "torn ledger line re-raised" $U \
  'continue  # torn: keep walking back' \
  'raise' \
  "$T::test_a_torn_last_line_falls_back_to_the_previous_night $T::test_a_torn_ledger_still_builds_every_page"
# Not run: trusting the fragment before the first newline
# (`complete = pieces`) is an equivalent mutant. A suffix of one JSON object
# always carries more closing brackets than opening ones, so json.loads can
# never accept it and the reader walks on either way. The rule stays because
# it makes the invariant explicit, not because a test can tell the difference.
run_mutation "Matter failure not isolated" $U \
  '    except Exception as err:
        print(f"Matter queue ledger unreadable' \
  '    except ImportError as err:
        print(f"Matter queue ledger unreadable' \
  "$T::test_an_unreadable_matter_ledger_drops_only_the_matter_section"
run_mutation "window reaching the current year ranked" $U \
  'if end >= current_year:' 'if end > current_year:' \
  "$T::test_a_window_reaching_the_current_year_is_not_ranked"
run_mutation "windows ranked highest first" $U \
  'return sorted(out, key=lambda w: (w[2], w[0]))' \
  'return sorted(out, key=lambda w: (-w[2], w[0]))' \
  "$T::test_the_hypothesis_note_says_where_2017_19_actually_ranks"
run_mutation "typed volume claim restored" $U \
  '              hypothesis_window_note(v["read_by_year"]),' \
  '              "The unread queue is heaviest in exactly the band where reading volume was lowest.",' \
  "$T::test_the_versus_page_no_longer_types_the_volume_claim"
run_mutation "delta label fixed at 7 days" $U \
  'delta = f"{'"'"'+'"'"' if wk > 0 else '"'"''"'"'}{wk} over {span} days"' \
  'delta = f"{'"'"'+'"'"' if wk > 0 else '"'"''"'"'}{wk} in 7 days"' \
  "$T::test_the_cover_prints_the_days_the_delta_spans"
run_mutation "missed night not marked" $U \
  'gap = (d.get("days_since_previous") or 1) > 1' 'gap = False' \
  "$T::test_a_missed_night_is_marked_on_the_nightly_chart"
run_mutation "Matter no-length count dropped" $U \
  "f'{n(lengths[\"no_length\"])} carry no word count and sit outside the bands. '" \
  "f''" \
  "$T::test_the_matter_note_counts_the_items_outside_the_length_bands"

echo "seventh row item (Adam, 2026-09-24)"
run_mutation "Unread left out of the predicted row" $G \
  'unread_keys = {"unread"} if unread_data else set()' \
  'unread_keys = set()' \
  "$T::test_every_page_of_the_site_names_unread_last $T::test_the_unread_pages_carry_the_seven_item_row_and_mark_unread"
run_mutation "Unread row item removed from PAGE_ROW" site/htmlkit.py \
  '    ("unread", "Unread", "unread/"),' \
  '' \
  "$T::test_the_unread_pages_carry_the_seven_item_row_and_mark_unread"
run_mutation "Unread moved ahead of Articles" site/htmlkit.py \
  '    ("articles", "Articles", "articles/"),
    # Added 2026-09-24' \
  '    ("unread", "Unread", "unread/"), ("articles", "Articles", "articles/"),
    # Added 2026-09-24' \
  "tests/test_site_cover.py::test_the_row_names_the_seven_pages_in_the_order_adam_gave"
run_mutation "/unread/ does not mark itself" $U \
  'depth=depth, here="unread")' 'depth=depth)' \
  "$T::test_the_unread_pages_carry_the_seven_item_row_and_mark_unread"
run_mutation "throwaway render skipped" $G \
  '            unread_mod.write_pages(scratch, data, domain=DOMAIN)' '            pass' \
  "$T::test_a_renderer_that_fails_drops_the_row_item_before_any_page"
run_mutation "fallback row drops Unread" $G \
  'page_row(unread_keys, weeks_at_root=True)' 'page_row(set(), weeks_at_root=True)' \
  "$T::test_the_comparison_is_not_built_without_the_read_corpus"

echo "reviewer's mutations (PR #31 review, section 5)"
run_mutation "M1  medium relabelled high" $U \
  'label = (f"Inference · {e(conf)} confidence" if graded' \
  'label = (f"Inference · {e('"'"'high'"'"' if conf == '"'"'medium'"'"' else conf)} confidence" if graded' \
  "$T"
run_mutation "M1b missing confidence labelled high" $U \
  'else "Inference · confidence not set")' \
  'else "Inference · high confidence")' \
  "$T"
run_mutation "M1c low not dimmed" $U \
  'dim = " low" if (conf == "low" or not graded) else ""' \
  'dim = " low" if (not graded) else ""' \
  "$T"
run_mutation "M1d cover tile counts medium as high" $U \
  'by_conf = Counter(p.get("why_saved_confidence") for p in w["picks"])' \
  'by_conf = Counter("high" if p.get("why_saved_confidence") == "medium" else p.get("why_saved_confidence") for p in w["picks"])' \
  "$T"
run_mutation "M2  safe_url becomes a javascript denylist" site/htmlkit.py \
  'return e(u) if u.lower().startswith(SAFE_SCHEMES) else ""' \
  'return e(u) if not u.lower().startswith("javascript:") else ""' \
  "$T"
run_mutation "M2b worth links any non-javascript scheme" $U \
  'url = safe_url(p.get("url"))' \
  'url = "" if str(p.get("url") or "").startswith("javascript:") else e(p.get("url") or "")' \
  "$T"
run_mutation "M3  ledger tail reads one block only" $U \
  '            if pos == 0:
                return None
            step = min(block, pos)' \
  '            if pos == 0 or buf:
                return None
            step = min(block, pos)' \
  "$T"
run_mutation "M4  week-over-week reaches 6 days back" $U \
  'cutoff = last["date"] - dt.timedelta(days=days)' \
  'cutoff = last["date"] - dt.timedelta(days=days - 1)' \
  "$T"
run_mutation "M5  opened-abandoned drops nearly finished" $U \
  'stats += _stat(n(ab[derive.STARTED]["count"] + ab[derive.NEARLY]["count"]),' \
  'stats += _stat(n(ab[derive.STARTED]["count"]),' \
  "$T"

echo
echo "caught $PASS, escaped or stale $FAIL"
git diff --quiet -- site scripts tests || { echo "WORKTREE NOT CLEAN after audit"; exit 1; }
[ "$FAIL" -eq 0 ]
