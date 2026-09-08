#!/bin/bash
# Mutation audit for the cover, the move to /weeks/, and the page row.
#
# Same method and the same harness as tests/mutation_audit.sh and
# tests/mutation_audit_sibling_nav.sh: break one thing at a time and confirm a
# named test fails. A test that stays green under its own mutation is not
# testing the thing its docstring claims.
#
#   bash tests/mutation_audit_cover.sh
#
# Every mutation is reverted immediately; the script verifies a clean worktree
# and a green suite at the end.
#
# INSTAPAPER_VAULT_PATH is exported deliberately. The live-archive figures test
# skips without it, and a mutation aimed at that test would then be reported as
# ESCAPED when it was never run. Set it to the mounted vault before running
# this, or the two live mutations below will say so.

set -u
cd "$(dirname "$0")/.." || exit 1

# See the note in mutation_audit.sh: restoring a mutated file with `mv` gives it
# the backup's mtime, which can be older than bytecode compiled from the mutated
# source, so Python may keep serving the mutant after the source is restored.
export PYTHONDONTWRITEBYTECODE=1
PY="${MATTER_TEST_PYTHON:-.venv/bin/python}"
[ -x "$PY" ] || PY="$(dirname "$(git rev-parse --git-common-dir)")/.venv/bin/python"
[ -x "$PY" ] || PY="$(dirname "$(git rev-parse --git-common-dir)")/venv/bin/python"
[ -x "$PY" ] || { echo "No test interpreter found; set MATTER_TEST_PYTHON."; exit 1; }

if [ -z "${INSTAPAPER_VAULT_PATH:-}" ]; then
  echo "NOTE: INSTAPAPER_VAULT_PATH is unset - the live-archive mutations will"
  echo "      be reported as STALE rather than run. Set it to the vault to"
  echo "      audit those two."
  echo
fi

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
  # `error` as well as `failed`: a mutation that makes a test blow up in its
  # fixture reports "1 error", and grepping only for "failed" scored that as an
  # ESCAPE - which is the one direction a mutation audit must never get wrong.
  out=$("$PY" -m pytest $tests -q -p no:cacheprovider 2>&1 | grep -oE '[0-9]+ (failed|error|passed)[^$]*' | head -1)
  if echo "$out" | grep -qE 'failed|error'; then
    printf '  CAUGHT   %-62s %s\n' "$label" "$out"; PASS=$((PASS+1))
  else
    printf '  ESCAPED  %-62s %s  <-- UNGUARDED\n' "$label" "$out"; FAIL=$((FAIL+1))
  fi
  mv "$file.mutbak" "$file"
}

C=site/cover.py
G=site/generate.py
H=site/htmlkit.py
T=tests/test_site_cover.py

echo "=== the figures on the cover ==="
run_mutation "the cumulative words line is drawn off reading time" "$C" \
  '    words = C.numeric_column(r, "word_count").fillna(0)' \
  '    words = C.numeric_column(r, "reading_time_min").fillna(0)' \
  "$T::test_the_cumulative_series_end_where_the_totals_do"

run_mutation "the seam is divided by the later year" "$C" \
  'f["seam"] = (round((f["seam_after"] - f["seam_before"]) / f["seam_before"] * 100)' \
  'f["seam"] = (round((f["seam_after"] - f["seam_before"]) / f["seam_after"] * 100)' \
  "$T::test_the_cover_prints_the_figures_an_independent_recompute_gives"

run_mutation "the AI share is taken over the whole corpus" "$C" \
  '    new = tagged[tagged["year"] >= NEW_ERA_FIRST_YEAR]' \
  '    new = tagged' \
  "$T::test_the_cover_prints_the_figures_an_independent_recompute_gives"

run_mutation "sources are counted over rows that kept no URL too" "$C" \
  '        median_words=st["median_words"], domains=st["domains"],' \
  '        median_words=st["median_words"], domains=st["articles"],' \
  "$T::test_the_cover_prints_the_figures_an_independent_recompute_gives"

run_mutation "the AI window opens two years early" "$C" \
  'NEW_ERA_FIRST_YEAR = 2018' 'NEW_ERA_FIRST_YEAR = 2016' \
  "$T::test_the_cover_prints_the_figures_an_independent_recompute_gives"

run_mutation "the streak counts every week rather than the longest run" "$C" \
  '    return best, best_start, best_end' \
  '    return len(days), days[0], days[-1]' \
  "$T::test_the_cover_prints_the_figures_an_independent_recompute_gives"

run_mutation "the deep-dive count goes back to the mockup's typed 29" "$C" \
  '{"k": "deep dives", "v": n(deep_dives["total"]),
              "s": f'"'"'{deep_dives["years"]} year rollups, {deep_dives["facets"]} facets'"'"'}' \
  '{"k": "deep dives", "v": "29", "s": "22 year rollups, 7 facets"}' \
  "$T::test_the_deep_dive_count_is_the_pages_the_build_wrote"

echo
echo "=== the hero row Adam approved ==="
run_mutation "a second column takes the amber" "$C" \
  '                   f'"'"'{e(week_topics(m))}'"'"'}]),' \
  '                   f'"'"'{e(week_topics(m))}'"'"'}], accent=True),' \
  "$T::test_the_hero_row_is_the_one_adam_approved_with_articles_the_only_amber"

run_mutation "the amber moves off Articles" "$C" \
  '                   f'"'"'{n(f["seam_after"])} in {SEAM_YEAR}'"'"'}],
            accent=True),' \
  '                   f'"'"'{n(f["seam_after"])} in {SEAM_YEAR}'"'"'}]),' \
  "$T::test_the_hero_row_is_the_one_adam_approved_with_articles_the_only_amber"

run_mutation "the Subjects hero becomes the vocabulary instead of the AI share" "$C" \
  '            "artificial intelligence", f'"'"'{f["ai_new"]}<em>%</em>'"'"',' \
  '            "vocabulary", n(f["vocab"]),' \
  "$T::test_the_cover_prints_the_figures_an_independent_recompute_gives"

echo
echo "=== the move to /weeks/ ==="
run_mutation "the index is edited on its way to its new address" "$G" \
  '    <h1>{e(SITE_TITLE)}</h1>' \
  '    <h1>{e(SITE_TITLE)} archive</h1>' \
  "$T::test_the_weeks_index_is_the_old_root_index_and_nothing_else_changed"

run_mutation "the era bar is dropped from the moved index" "$G" \
  '    <div class="label viz-title">Three ways of saving · how the archive was made</div>' \
  '    <div class="label viz-title">How the archive was made</div>' \
  "$T::test_the_weeks_index_is_the_old_root_index_and_nothing_else_changed"

run_mutation "the moved index forgets to rewrite its week links" "$G" \
  '        rows += (f'"'"'  <a class="wrow" href="{root}weeks/{w}/" {bg}>'"'"'' \
  '        rows += (f'"'"'  <a class="wrow" href="weeks/{w}/" {bg}>'"'"'' \
  "$T::test_every_link_on_the_moved_index_resolves_from_its_new_address"

run_mutation "the canonical link is dropped from the two moved pages" "$H" \
  '    link = (f'"'"'\n<link rel="canonical" href="{e(canonical)}">'"'"'
            if canonical else "")' \
  '    link = ""' \
  "$T::test_both_moved_pages_declare_where_they_now_live"

run_mutation "the weeks index is written after the week directories" "$G" \
  '            (tmp / "weeks" / "index.html").write_text(index_html, encoding="utf-8")' \
  '            pass' \
  "$T::test_the_week_pages_still_live_under_the_index_that_now_sits_above_them"

echo
echo "=== the page row ==="
run_mutation "page() stops emitting the row" "$H" \
  '    <nav class="pagelinks" aria-label="the pages of this site">{'"''"'.join(pages)}</nav>' \
  '' \
  "$T::test_every_emitted_page_carries_the_page_row"

run_mutation "the current page is linked instead of marked" "$H" \
  '        if key == here:' \
  '        if False:' \
  "$T::test_every_page_in_the_row_marks_itself"

run_mutation "a facet outside the row starts marking one" "site/trends.py" \
  '    return page(f"Trends — {site_title}", body, depth=1)' \
  '    return page(f"Trends — {site_title}", body, depth=1, here="cover")' \
  "$T::test_every_page_in_the_row_marks_itself"

run_mutation "the year pages stop marking YEARS" "site/deepdives.py" \
  '    return page(f"{year} — {site_title}", body, depth=2, here="years")' \
  '    return page(f"{year} — {site_title}", body, depth=2)' \
  "$T::test_every_page_in_the_row_marks_itself"

run_mutation "SOURCES is renamed back to the directory name" "$H" \
  '    ("orgs", "Sources", "orgs/"),' \
  '    ("orgs", "Organizations", "orgs/"),' \
  "$T::test_the_row_names_the_six_pages_in_the_order_adam_gave"

run_mutation "the row is reordered" "$H" \
  '    ("cover", "Cover", ""),
    ("weeks", "Weeks", "weeks/"),' \
  '    ("weeks", "Weeks", "weeks/"),
    ("cover", "Cover", ""),' \
  "$T::test_the_row_names_the_six_pages_in_the_order_adam_gave"

run_mutation "the row targets stop being rewritten for depth" "$H" \
  '            pages.append(f'"'"'<a href="{e(up + target) or "./"}">{e(label)}</a>'"'"')' \
  '            pages.append(f'"'"'<a href="{e(target) or "./"}">{e(label)}</a>'"'"')' \
  "$T::test_every_row_target_is_a_page_that_exists"

run_mutation "the row is hardcoded instead of narrowed to what was built" "$G" \
  '        elif key in built:' \
  '        else:' \
  "$T::test_an_index_without_the_taxonomy_join_keeps_the_site_it_had"

run_mutation "the broken-row guard is never called" "$G" \
  '        check_page_row_targets(tmp, htmlkit._page_row)' \
  '        pass' \
  "$T::test_a_build_whose_row_names_a_missing_page_refuses_to_swap"

run_mutation "the guard warns instead of raising" "$G" \
  '        raise SystemExit(
            f"Refusing to publish a broken page row: {'"'"', '"'"'.join(missing)} "' \
  '        print(
            f"Refusing to publish a broken page row: {'"'"', '"'"'.join(missing)} "' \
  "$T::test_a_row_target_that_was_not_built_stops_the_build"

run_mutation "a build leaks its narrowed row into the next one" "$G" \
  '        if restore_row is not None:
            htmlkit.set_page_row(restore_row)' \
  '        pass' \
  "$T::test_a_build_does_not_leak_its_row_into_the_next_one"

echo
echo "=== the cover matches the design it was approved from ==="
run_mutation "the cover renders in the 720px reading column" "$C" \
  '    return page(f"{COVER_TITLE} — {site_title}", body, depth=0, here="cover",
                canonical=f"https://{domain}/", wide=True)' \
  '    return page(f"{COVER_TITLE} — {site_title}", body, depth=0, here="cover",
                canonical=f"https://{domain}/")' \
  "$T::test_nothing_on_the_cover_is_pinned_wider_than_the_viewport"

run_mutation "the secondaries stop rendering their ranked lists" "$C" \
  '        value = s["mini"] if wide else f'"'"'<span class="fsv2 num">{s["v"]}</span>'"'"'' \
  '        value = "" if wide else f'"'"'<span class="fsv2 num">{s["v"]}</span>'"'"'' \
  "$T::test_every_class_the_approved_cover_uses_is_emitted"

run_mutation "an amber is pasted into the cover rules as a literal" "$C" \
  '.fhero.accent .fhv { color:var(--amber); }' \
  '.fhero.accent .fhv { color:#fbbf24; }' \
  "$T::test_the_cover_rules_invent_no_colour_of_their_own"

run_mutation "the cover's tokens arrive in a second :root" "$C" \
  'COVER_STYLE = """
/* ---- the cover ---- */' \
  'COVER_STYLE = """
:root { --serif:Georgia,serif; }
/* ---- the cover ---- */' \
  "$T::test_the_stylesheet_still_declares_one_root"

run_mutation "the cover pulls a webfont for its serif" "$C" \
  '  <div class="fgrid">{{COLUMNS}}</div>' \
  '  <link rel="stylesheet" href="https://fonts.googleapis.com/css?family=X">
  <div class="fgrid">{{COLUMNS}}</div>' \
  "$T::test_the_cover_reaches_nothing_off_this_machine"

run_mutation "the cover stamps the clock instead of the date" "$C" \
  '        "GENERATED": today.isoformat(),' \
  '        "GENERATED": dt.datetime.now().isoformat(),' \
  "$T::test_two_builds_of_the_same_data_are_byte_identical"

echo
echo "=== the rules that keep it from scrolling sideways ==="
run_mutation "the strips get a fixed pixel width" "$C" \
  '.fstrip { display:block; width:100%; height:auto; margin-top:16px; }' \
  '.fstrip { display:block; width:1180px; height:auto; margin-top:16px; }' \
  "$T::test_nothing_on_the_cover_is_pinned_wider_than_the_viewport"

run_mutation "the four columns stop collapsing on a phone" "$C" \
  '@media (max-width:760px){
  .wide h1 { font-size:48px; }
  .fgrid { grid-template-columns:1fr; gap:52px; }' \
  '@media (max-width:760px){
  .wide h1 { font-size:48px; }
  .fgrid { gap:52px; }' \
  "$T::test_nothing_on_the_cover_is_pinned_wider_than_the_viewport"

run_mutation "the cover's measure becomes a width instead of a max-width" "$C" \
  '.page.wide { max-width:1180px; }' \
  '.page.wide { width:1180px; }' \
  "$T::test_nothing_on_the_cover_is_pinned_wider_than_the_viewport"

run_mutation "hidden wins over clip on the document axes" "$G" \
  'html, body { overflow-x:hidden; overflow-x:clip; }' \
  'html, body { overflow-x:clip; overflow-x:hidden; }' \
  "$T::test_the_document_still_clips_horizontally_at_every_width"

run_mutation "the bar stops being sticky over the cover" "$G" \
  '.stickynav { position:sticky; top:0;' '.stickynav { position:relative; top:0;' \
  "$T::test_the_bar_is_still_sticky_over_the_cover"

run_mutation "the 880px block that hides the siblings is dropped" "$G" \
  '  .sitelinks{display:none;}
  .pagelinks{gap:14px; font-size:10px; letter-spacing:.08em;}' \
  '  .pagelinks{gap:14px; font-size:10px; letter-spacing:.08em;}' \
  "$T::test_the_bar_hides_the_siblings_on_a_phone_the_way_the_books_bar_does"

echo
echo "=== fail-open ==="
run_mutation "a cover ships on an index that cannot fill its Subjects column" "$C" \
  '    return (corpus_data is not None and len(corpus_data)
            and CANONICAL_COLUMN in corpus_data.rows.columns)' \
  '    return corpus_data is not None and len(corpus_data)' \
  "$T::test_an_index_without_the_taxonomy_join_keeps_the_site_it_had"

run_mutation "the index moves to /weeks/ even with no cover to take the root" "$G" \
  '        covered = bool(year_pages) and cover.can_render(corpus_data)' \
  '        covered = True' \
  "$T::test_a_weeks_only_build_still_has_a_root_page_and_an_honest_row"

echo
echo "=== the live archive ==="
run_mutation "the streak allows a one-week gap" "$C" \
  '        if (b - a).days == 7:' '        if (b - a).days <= 14:' \
  "$T::test_the_live_archive_reproduces_the_figures_adam_approved"

echo
echo "=== summary ==="
printf '  %d caught, %d escaped/stale\n' "$PASS" "$FAIL"
echo
echo "--- suite after restore ---"
"$PY" -m pytest tests/test_site_cover.py tests/test_site_sibling_nav.py \
  tests/test_site_generate.py tests/test_site_deepdives.py -q 2>&1 | tail -1
echo "--- worktree ---"
git status --porcelain -- site tests | grep -v mutbak || echo "(clean)"
[ "$FAIL" -eq 0 ]
