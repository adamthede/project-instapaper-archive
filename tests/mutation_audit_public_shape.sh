#!/bin/bash
# Mutation audit for the public shape: the redaction, the leak scan, the six
# deviations, and the promise that the private build does not move.
#
# Same method and the same harness as tests/mutation_audit.sh,
# tests/mutation_audit_sibling_nav.sh and tests/mutation_audit_cover.sh: break
# one thing at a time and confirm a named test fails. A test that stays green
# under its own mutation is not testing the thing its docstring claims.
#
#   bash tests/mutation_audit_public_shape.sh
#
# Every mutation is reverted immediately; the script verifies a clean worktree
# and a green suite at the end.
#
# INSTAPAPER_VAULT_PATH is deliberately NOT required here. The two live tests
# in this file's suite cost about three minutes and none of the mutations below
# are aimed at them, so they are left to skip; the fixture corpus carries real-
# shaped titles and URLs precisely so the leak mutations have something to find
# without the archive being mounted.

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
  # fixture reports "1 error", and grepping only for "failed" scores that as an
  # ESCAPE - which is the one direction a mutation audit must never get wrong.
  out=$("$PY" -m pytest $tests -q -p no:cacheprovider 2>&1 | grep -oE '[0-9]+ (failed|error|passed)[^$]*' | head -1)
  if echo "$out" | grep -qE 'failed|error'; then
    printf '  CAUGHT   %-62s %s\n' "$label" "$out"; PASS=$((PASS+1))
  else
    printf '  ESCAPED  %-62s %s  <-- UNGUARDED\n' "$label" "$out"; FAIL=$((FAIL+1))
  fi
  mv "$file.mutbak" "$file"
}

P=site/public_shape.py
C=site/cover.py
H=site/htmlkit.py
G=site/generate.py
T=tests/test_public_shape.py

echo "=== redaction at the data layer ==="
run_mutation "the row reduction is skipped entirely" "$P" \
  '    return corpus_mod.Corpus(rows=corpus_data.rows[list(ROW_COLUMNS)].copy(),' \
  '    return corpus_mod.Corpus(rows=corpus_data.rows.copy(),' \
  "$T::test_the_renderer_is_handed_no_article_title_and_no_url"

run_mutation "the allowed columns grow a title" "$P" \
  'ROW_COLUMNS = ("source", "word_count", "reading_time_min", "canonical_entries",' \
  'ROW_COLUMNS = ("title", "source", "word_count", "reading_time_min", "canonical_entries",' \
  "$T::test_the_renderer_is_handed_no_article_title_and_no_url"

run_mutation "the week reduction hands the roster through" "$P" \
  '    return [{k: m[k] for k in WEEK_KEYS if k in m} for m in weeks]' \
  '    return [dict(m) for m in weeks]' \
  "$T::test_the_week_records_the_renderer_sees_carry_no_article_roster"

run_mutation "the reduction loses the column the Sources column is drawn from" "$P" \
  '               "date_read", "year", "domain", "proxy_dated")' \
  '               "date_read", "year", "proxy_dated")' \
  "$T::test_the_reduced_view_still_draws_the_cover_the_full_one_draws"

run_mutation "the reduction loses reading time" "$P" \
  'ROW_COLUMNS = ("source", "word_count", "reading_time_min", "canonical_entries",' \
  'ROW_COLUMNS = ("source", "word_count", "canonical_entries",' \
  "$T::test_the_reduced_view_still_draws_the_cover_the_full_one_draws"

run_mutation "the reduction loses the subjects column" "$P" \
  'ROW_COLUMNS = ("source", "word_count", "reading_time_min", "canonical_entries",' \
  'ROW_COLUMNS = ("source", "word_count", "reading_time_min",' \
  "$T::test_the_reduced_view_still_draws_the_cover_the_full_one_draws"

# Nothing the cover DRAWS reads `years` - the quarter axis is measured off
# `date_read` - so this one is invisible to the fidelity equality and only the
# rollup count can see it.
run_mutation "the reduction drops the years the rollup count is taken from" "$P" \
  '                             years=list(corpus_data.years))' \
  '                             years=[])' \
  "$T::test_the_reduced_view_keeps_the_years_the_page_counts_rollups_from"

echo
echo "=== the leak scan ==="
run_mutation "the scan reports nothing whatever it finds" "$P" \
  '                found.append({"file": path.relative_to(root).as_posix(),' \
  '                pass; _ = ({"file": path.relative_to(root).as_posix(),' \
  "$T::test_the_leak_scan_is_red_when_a_title_is_injected"

run_mutation "the scan walks only index.html" "$P" \
  '    for path in sorted(Path(root).rglob("*")):' \
  '    for path in sorted(Path(root).glob("index.html")):' \
  "$T::test_the_leak_scan_reads_every_file_the_build_wrote"

run_mutation "the build warns about a leak instead of refusing" "$P" \
  '            raise SystemExit(
                f"Refusing to publish: the leak scan found {len(found)} "' \
  '            print(
                f"Refusing to publish: the leak scan found {len(found)} "' \
  "$T::test_the_build_refuses_to_finish_when_the_scan_finds_something"

run_mutation "the scan runs after the directory is published" "$P" \
  '        found = leak_scan(tmp, titles, paths)' \
  '        found = leak_scan(tmp, titles, [])[:0]' \
  "$T::test_the_build_refuses_to_finish_when_the_scan_finds_something"

run_mutation "the needle floor drops to one character" "$P" \
  'MIN_NEEDLE = 12' 'MIN_NEEDLE = 1' \
  "$T::test_the_scan_ignores_strings_too_short_to_identify_an_article"

run_mutation "the needles come back in an arbitrary order" "$P" \
  '    longest = (lambda s: sorted(s, key=lambda x: (-len(x), x)))' \
  '    longest = (lambda s: sorted(s))' \
  "$T::test_the_scan_takes_the_longest_strings_first"

run_mutation "the scan takes hosts where it should take paths" "$P" \
  '        path = urlparse(str(u)).path.rstrip("/")' \
  '        path = urlparse(str(u)).netloc.rstrip("/")' \
  "$T::test_no_source_url_path_appears_in_the_public_build"

echo
echo "=== the six deviations ==="
run_mutation "the private six-item row ships on the record" "$P" \
  'PUBLIC_PAGE_ROW = [("cover", "Cover", "")]' \
  'PUBLIC_PAGE_ROW = list(htmlkit.PAGE_ROW)' \
  "$T::test_the_page_row_is_reduced_to_the_single_current_entry"

run_mutation "the neutralizer is never called" "$P" \
  '    return inline_stylesheet(neutralize_links(html))' \
  '    return inline_stylesheet(html)' \
  "$T::test_a_private_anchor_added_to_the_cover_is_neutralized_on_the_page"

run_mutation "the neutralizer drops the element instead of spanning it" "$P" \
  '        return f"<span{HREF.sub('"''"', attrs)}>{inner}</span>"' \
  '        return inner' \
  "$T::test_a_private_anchor_becomes_a_span_with_the_same_classes"

run_mutation "the neutralizer keeps the href on the span" "$P" \
  '        return f"<span{HREF.sub('"''"', attrs)}>{inner}</span>"' \
  '        return f"<span{attrs}>{inner}</span>"' \
  "$T::test_a_private_anchor_becomes_a_span_with_the_same_classes"

run_mutation "the sibling bar keeps the Access-walled hosts" "$P" \
  'PUBLIC_SIBLINGS = [
    ("Reading", None),
    ("Books", PUBLIC_BASE + "books/"),
    ("Viewing", PUBLIC_BASE + "viewing/"),
]' \
  'PUBLIC_SIBLINGS = list(htmlkit.SIBLING_SITES)' \
  "$T::test_the_sibling_bar_points_at_the_public_tier"

run_mutation "the wordmark stays pointed at the record itself" "$P" \
  '        self._home = htmlkit.set_wordmark_home(PUBLIC_BASE)' \
  '        self._home = htmlkit.set_wordmark_home(None)' \
  "$T::test_the_wordmark_points_at_the_public_index"

# Dropping the argument entirely is an EQUIVALENT mutant since the byline
# changed: cover.render_cover falls back to https://<domain>/, the domain is
# now data.adamthede.com/reading, and that is the record URL character for
# character. So the mutation names the private host instead, which is the
# thing the test is actually there to catch.
run_mutation "the private canonical ships on the public page" "$P" \
  '            canonical=RECORD_URL, footer_note=FOOTER_NOTE)' \
  '            canonical="https://reading.adamthede.com/", footer_note=FOOTER_NOTE)' \
  "$T::test_the_canonical_is_the_record_url"

run_mutation "the footer goes on offering the weekly syntheses" "$P" \
  '            canonical=RECORD_URL, footer_note=FOOTER_NOTE)' \
  '            canonical=RECORD_URL)' \
  "$T::test_the_footer_says_the_full_record_is_private"

run_mutation "the masthead bylines the Access-walled host" "$P" \
  '            domain=domain or PUBLIC_DOMAIN,' \
  '            domain=domain or gen.DOMAIN,' \
  "$T::test_the_masthead_kicker_names_the_public_record_not_the_private_host"

# A seventh difference that no other test in the file is looking for: the
# document title. Only the equality test can see it, which is why that test is
# written as an equality and not as a list of spot checks.
run_mutation "a seventh difference is introduced on the public path only" "$P" \
  '            site_title=site_title or gen.SITE_TITLE,' \
  '            site_title="A Reading Life",' \
  "$T::test_the_public_cover_is_the_private_cover_but_for_the_six_deviations"

echo
echo "=== self-contained ==="
run_mutation "the stylesheet is left as a link to a file that is not there" "$P" \
  '    return inline_stylesheet(neutralize_links(html))' \
  '    return neutralize_links(html)' \
  "$T::test_the_public_page_fetches_nothing_at_view_time"

run_mutation "the record forks the stylesheet instead of using the site's" "$P" \
  '    return "<style>" + gen.STYLE + cover.COVER_STYLE + "</style>"' \
  '    return "<style>body{background:#1c1917;color:#e7e5e4}</style>"' \
  "$T::test_the_inlined_stylesheet_is_the_rules_the_private_cover_renders_under"

echo
echo "=== the private build does not move ==="
run_mutation "the public chrome is installed and never restored" "$P" \
  '    def __exit__(self, *exc):
        htmlkit.set_page_row(self._row)' \
  '    def __exit__(self, *exc):
        pass
    def _unused(self):
        htmlkit.set_page_row(self._row)' \
  "$T::test_the_private_build_is_unchanged_by_a_public_build"

run_mutation "nav() ignores the installed sibling row" "$H" \
  '    for label, href in _sibling_sites:' \
  '    for label, href in SIBLING_SITES:' \
  "$T::test_the_sibling_bar_points_at_the_public_tier"

run_mutation "nav() ignores the installed wordmark home" "$H" \
  '    home = _wordmark_home or up or "./"' \
  '    home = up or "./"' \
  "$T::test_the_wordmark_points_at_the_public_index"

echo
echo "=== the figures the record prints ==="
run_mutation "the deep-dive count is typed rather than computed" "$P" \
  '    facets = len(ALWAYS_FACETS)' \
  '    facets = 7' \
  "$T::test_the_deep_dive_count_matches_what_the_private_build_writes"

run_mutation "the gated facet pair is counted whether or not the gate opens" "$P" \
  '    if gen.concepts_gate(corpus_data)[0]:
        facets += len(GATED_FACETS)' \
  '    facets += len(GATED_FACETS)' \
  "$T::test_the_deep_dive_count_is_right_where_the_taxonomy_gate_is_closed"

run_mutation "the clock is stamped into the page instead of the date" "$C" \
  '        "GENERATED": today.isoformat(),' \
  '        "GENERATED": dt.datetime.now().isoformat(),' \
  "$T::test_two_public_builds_of_the_same_data_are_byte_identical"

echo
echo "=== what the build writes ==="
run_mutation "the record ships the private site's stylesheet beside it" "$P" \
  '        thumb_path = tmp / "thumb.jpg"' \
  '        (tmp / "style.css").write_text("x"); thumb_path = tmp / "thumb.jpg"' \
  "$T::test_the_public_build_emits_exactly_the_record_files"

echo
echo "=== the guards a working build never reaches ==="
run_mutation "the inliner shrugs when there is no stylesheet link" "$P" \
  '    if html.count(link) != 1:
        raise SystemExit(' \
  '    if False:
        raise SystemExit(' \
  "$T::test_the_inliner_refuses_a_page_with_no_stylesheet_to_inline"

run_mutation "the out guard lets a build clear a foreign directory" "$P" \
  '    if strays:
        raise SystemExit(' \
  '    if False:
        raise SystemExit(' \
  "$T::test_the_build_refuses_an_out_directory_it_did_not_write"

run_mutation "the out guard refuses a previous record too" "$P" \
  '    strays = [p.name for p in out.iterdir() if p.name not in RECORD_FILES]' \
  '    strays = [p.name for p in out.iterdir()]' \
  "$T::test_the_build_refuses_an_out_directory_it_did_not_write"

run_mutation "the note is written before the thumbnail it describes" "$P" \
  '                       scanned, thumb_path if thumbnail else None, today),' \
  '                       scanned, None, today),' \
  "$T::test_the_provenance_note_records_the_thumbnail_it_was_built_with"

run_mutation "the capture-source hash is the thumbnail's own" "$P" \
  '        thumb = THUMB_PRESENT.format(w=THUMB_WIDTH, h=THUMB_HEIGHT,
                                     page_hash=page_hash,' \
  '        thumb = THUMB_PRESENT.format(w=THUMB_WIDTH, h=THUMB_HEIGHT,
                                     page_hash=sha256(thumb_path),' \
  "$T::test_the_provenance_note_records_the_thumbnail_it_was_built_with"

run_mutation "the entry point routes every build to the record" "$G" \
  '    if args.public:
        if args.no_index:' \
  '    if True:
        if args.no_index:' \
  "$T::test_the_command_line_builds_the_private_site_unless_public_is_asked_for"

run_mutation "the private footer token resolves to nothing" "$C" \
  '    body = body.replace("{{FOOTER_NOTE}}",
                        FOOTER_NOTE if footer_note is None else footer_note)' \
  '    body = body.replace("{{FOOTER_NOTE}}", "")' \
  "$T::test_the_private_build_shape_is_still_what_the_nightly_asks_for"

run_mutation "the record is renamed on the public path only" "$C" \
  'COVER_TITLE = "A Reading Life"' \
  'COVER_TITLE = "The Reading Archive"' \
  "$T::test_the_public_page_is_the_cover_and_no_part_of_the_weeks_index"

echo
echo "=== summary ==="
printf '  %d caught, %d escaped/stale\n' "$PASS" "$FAIL"
echo
echo "--- suite after restore ---"
"$PY" -m pytest tests/test_public_shape.py tests/test_site_cover.py \
  tests/test_site_sibling_nav.py tests/test_site_generate.py \
  tests/test_site_deepdives.py -q 2>&1 | tail -1
echo "--- worktree ---"
git status --porcelain -- site tests | grep -v mutbak || echo "(clean)"
[ "$FAIL" -eq 0 ]
