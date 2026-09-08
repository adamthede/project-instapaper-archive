#!/bin/bash
# Mutation audit for the sibling-site bar.
#
# Same method and the same harness as tests/mutation_audit.sh: break one thing
# at a time and confirm a named test fails. A test that stays green under its
# own mutation is not testing the thing its docstring claims.
#
#   bash tests/mutation_audit_sibling_nav.sh
#
# Every mutation is reverted immediately; the script verifies a clean worktree
# and a green suite at the end.

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
  out=$("$PY" -m pytest $tests -q -p no:cacheprovider 2>&1 | grep -oE '[0-9]+ (failed|passed)[^$]*' | head -1)
  if echo "$out" | grep -q failed; then
    printf '  CAUGHT   %-62s %s\n' "$label" "$out"; PASS=$((PASS+1))
  else
    printf '  ESCAPED  %-62s %s  <-- UNGUARDED\n' "$label" "$out"; FAIL=$((FAIL+1))
  fi
  mv "$file.mutbak" "$file"
}

H=site/htmlkit.py
G=site/generate.py
D=site/deepdives.py
T=tests/test_site_sibling_nav.py

echo "=== the bar reaches every page ==="
run_mutation "page() stops emitting the bar" "$H" \
  '<body>
{nav(depth)}' '<body>' \
  "$T::test_every_emitted_page_carries_the_sibling_bar"

run_mutation "the bar is emitted only on the root page" "$H" \
  '{nav(depth)}' '{nav(depth) if depth == 0 else ""}' \
  "$T::test_every_emitted_page_carries_the_sibling_bar"

run_mutation "the bar moves inside the 720px content column" "$H" \
  '{nav(depth)}
<div class="page">' '<div class="page">
{nav(depth)}' \
  "$T::test_the_bar_sits_above_the_page_and_leaves_the_masthead_alone"

echo
echo "=== the three surfaces, named exactly ==="
run_mutation "Books points at the wrong host" "$H" \
  '("Books", "https://books.adamthede.com/")' \
  '("Books", "https://book.adamthede.com/")' \
  "$T::test_the_bar_names_the_three_surfaces_exactly"

run_mutation "Viewing goes back to a placeholder" "$H" \
  '("Viewing", "https://viewing.adamthede.com/")' '("Viewing", None)' \
  "$T::test_the_bar_names_the_three_surfaces_exactly"

run_mutation "Reading links to itself instead of being marked" "$H" \
  '("Reading", None)' '("Reading", "https://reading.adamthede.com/")' \
  "$T::test_the_bar_names_the_three_surfaces_exactly"

run_mutation "the sibling order stops matching the other two sites" "$H" \
  '    ("Reading", None),
    ("Books", "https://books.adamthede.com/")' \
  '    ("Books", "https://books.adamthede.com/"),
    ("Reading", None)' \
  "$T::test_the_bar_names_the_three_surfaces_exactly"

echo
echo "=== the wordmark ==="
run_mutation "the home link is hard-coded and 404s from a nested page" "$H" \
  '    home = "../" * depth or "./"' '    home = "./"' \
  "$T::test_the_wordmark_reads_adamthede_reading_and_links_home_from_any_depth"

run_mutation "the wordmark loses its second half" "$H" \
  'adamthede<i>reading</i>' 'adamthede' \
  "$T::test_the_wordmark_reads_adamthede_reading_and_links_home_from_any_depth"

echo
echo "=== the rules that make it pin, and clip ==="
run_mutation "the bar stops being sticky" "$G" \
  '.stickynav { position:sticky; top:0;' '.stickynav { position:relative; top:0;' \
  "$T::test_the_bar_is_sticky_at_the_top_of_the_viewport"

run_mutation "the sticky bar goes transparent" "$G" \
  'z-index:20; background:var(--bg);' 'z-index:20;' \
  "$T::test_the_bar_is_sticky_at_the_top_of_the_viewport"

run_mutation "hidden wins over clip on the document axes" "$G" \
  'html, body { overflow-x:hidden; overflow-x:clip; }' \
  'html, body { overflow-x:clip; overflow-x:hidden; }' \
  "$T::test_the_document_still_clips_horizontally_so_the_bar_keeps_pinning"

run_mutation "a later rule reinstates the scroll container" "$G" \
  '.page { max-width:720px;' '.page { overflow-x:hidden; max-width:720px;' \
  "$T::test_the_document_still_clips_horizontally_so_the_bar_keeps_pinning"

run_mutation "the wordmark is hand-coloured with a literal" "$G" \
  'text-transform:uppercase; color:var(--brand); text-decoration:none;' \
  'text-transform:uppercase; color:#FF8F3B; text-decoration:none;' \
  "$T::test_the_bar_uses_the_shared_skin_variables_rather_than_new_colours"

run_mutation "the bar's tokens are declared in a second :root" "$G" \
  '  --mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace; }' \
  '  }
:root { --mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace; }' \
  "$T::test_the_stylesheet_still_declares_one_root"

echo
echo "=== self-contained ==="
run_mutation "the wordmark pulls a webfont" "$H" \
  '  <div class="snin">' \
  '  <link rel="stylesheet" href="https://fonts.googleapis.com/css?family=X">
  <div class="snin">' \
  "$T::test_the_bar_brings_no_external_resource"

run_mutation "a sibling is linked over plain http" "$H" \
  '("Books", "https://books.adamthede.com/")' \
  '("Books", "http://books.adamthede.com/")' \
  "$T::test_the_bar_brings_no_external_resource"

echo
echo "=== the fixture keeps covering every page kind ==="
run_mutation "the build stops emitting the orgs facet" "$G" \
  '    (tmp / "orgs").mkdir()
    (tmp / "orgs" / "index.html").write_text(' \
  '    (tmp / "orgz").mkdir()
    (tmp / "orgz" / "index.html").write_text(' \
  "$T::test_the_build_under_test_covers_every_page_kind"

echo
echo "=== one home for the markup ==="
run_mutation "a second copy of the bar is pasted into a renderer" "$D" \
  'EXTRA_STYLE = ' 'PASTED_BAR = """<div class="stickynav"></div>"""
EXTRA_STYLE = ' \
  "$T::test_the_bar_markup_is_generated_once_not_per_renderer"

echo
echo "=== summary ==="
printf '  %d caught, %d escaped/stale\n' "$PASS" "$FAIL"
echo
echo "--- suite after restore ---"
"$PY" -m pytest tests/test_site_sibling_nav.py tests/test_site_generate.py \
  tests/test_site_deepdives.py -q 2>&1 | tail -1
echo "--- worktree ---"
git status --porcelain -- site tests | grep -v mutbak || echo "(clean)"
[ "$FAIL" -eq 0 ]
