#!/bin/bash
# Mutation audit for the Matter sync.
#
# Breaks one safety rule at a time and confirms a test fails. A test that stays
# green under mutation is not testing the thing it claims to.
#
# Lives in the repo on purpose: an earlier version sat in a shared scratchpad
# and was overwritten by another project's script mid-review, which cost a
# review round its audit.
#
#   bash tests/mutation_audit.sh
#
# Every mutation is reverted immediately; the script verifies a clean worktree
# and a green suite at the end.

set -u
cd "$(dirname "$0")/.." || exit 1

# No .pyc files, at all. Restoring a mutated file with `mv` gives it the backup's
# mtime, which can be OLDER than the bytecode compiled from the mutated source --
# so Python may keep serving the mutated module after the source is restored.
# That produces false CAUGHT and false ESCAPED results, which is worse than
# having no audit: it was masking a real gap the first time this ran.
export PYTHONDONTWRITEBYTECODE=1
PY="${MATTER_TEST_PYTHON:-.venv/bin/python}"
# In a worktree the virtualenv lives in the main checkout, which is the parent
# of the shared git dir.
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
    printf '  STALE    %-64s (anchor no longer matches)\n' "$label"
    mv "$file.mutbak" "$file"; FAIL=$((FAIL+1)); return
  fi
  local out
  # Belt as well as braces: purge any bytecode an earlier run may have left.
  find . -name "__pycache__" -type d -not -path "./.git/*" -exec rm -rf {} + 2>/dev/null
  out=$("$PY" -m pytest $tests -q -p no:cacheprovider 2>&1 | grep -oE '[0-9]+ (failed|passed)[^$]*' | head -1)
  if echo "$out" | grep -q failed; then
    printf '  CAUGHT   %-64s %s\n' "$label" "$out"; PASS=$((PASS+1))
  else
    printf '  ESCAPED  %-64s %s  <-- UNGUARDED\n' "$label" "$out"; FAIL=$((FAIL+1))
  fi
  mv "$file.mutbak" "$file"
}

S=scripts/matter/sync.py
M=scripts/matter/mapping.py
D=dashboard/app.py
T=tests/test_sync.py

echo "=== read-vs-saved semantics ==="
run_mutation "status default pulls the unread queue again" "$S" \
  'DEFAULT_STATUS = "archive"' 'DEFAULT_STATUS = "archive,queue"' \
  "$T::test_only_read_articles_are_pulled_by_default"

run_mutation "date_read falls back for Matter rows too" "$D" \
  '    return fallback.mask(is_matter, df["date_archived"])' '    return fallback' \
  "tests/test_dashboard_smoke.py::test_a_matter_row_with_no_archive_date_never_gets_a_read_date"

run_mutation "date_read stops falling back for Instapaper/legacy" "$D" \
  '    fallback = df["date_archived"].fillna(df["date_saved"])' '    fallback = df["date_archived"]' \
  "tests/test_dashboard_smoke.py::test_instapaper_rows_still_fall_back_to_date_saved"

echo
echo "=== the re-read annotation (writes to files the sync did not create) ==="
run_mutation "vault containment removed" "$S" \
  '    if not resolved.is_relative_to(vault):' '    if False:' \
  "$T::test_a_reread_never_writes_outside_the_vault $T::test_an_absolute_parquet_path_outside_the_vault_is_refused"

run_mutation "lossy utf-8 decode restored" "$S" \
  '        text = target.read_text(encoding="utf-8-sig")' \
  '        text = target.read_text(encoding="utf-8-sig", errors="replace")' \
  "$T::test_a_reread_refuses_a_file_that_is_not_valid_utf8"

run_mutation "identity check removed" "$S" \
  '    if normalize_url(metadata.get("original_url")) != normalize_url(item_url):' '    if False:' \
  "$T::test_a_reread_checks_the_matched_file_is_really_that_article"

run_mutation "a re-read revises the original read date" "$M" \
  '    updated = dict(metadata)
    updated[REREAD_DATES_KEY] = sorted(dates + [read_date])' \
  '    updated = dict(metadata)
    updated["date_archived"] = read_date
    updated[REREAD_DATES_KEY] = sorted(dates + [read_date])' \
  "$T::test_a_reread_never_revises_the_original_read_date"

run_mutation "a re-read stamps matter_id on a foreign file" "$M" \
  '    updated[REREAD_SOURCE_KEY] = REREAD_SOURCE' \
  '    updated[REREAD_SOURCE_KEY] = REREAD_SOURCE
    updated["matter_id"] = "itm_stamped"' \
  "$T::test_a_reread_never_stamps_matter_id_on_a_foreign_file"

run_mutation "re-reads recorded twice (idempotency broken)" "$M" \
  '    if not read_date or read_date in dates:
        return metadata, False' \
  '    if not read_date:
        return metadata, False' \
  "tests/test_mapping.py::test_annotate_reread_is_idempotent_on_its_own"

run_mutation "a queued duplicate counts as a re-read" "$S" \
  '            if item.get("status") == "archive":' '            if True:' \
  "$T::test_a_queued_duplicate_is_not_recorded_as_a_reread"

run_mutation "provenance note dropped" "$M" \
  '    updated[REREAD_SOURCE_KEY] = REREAD_SOURCE' '    pass' \
  "$T::test_reread_dates_carry_their_provenance"

run_mutation "dry run records re-reads" "$S" \
  '            elif reread_date and config.annotate_rereads and not config.dry_run:' \
  '            elif reread_date and config.annotate_rereads:' \
  "$T::test_a_dry_run_never_records_a_reread"

echo
echo "=== read-date estimation ==="
run_mutation "highlight date ignored (always updated_at)" "$M" \
  '    if newest and updated and newest < updated - HIGHLIGHT_SLACK:' '    if False:' \
  "tests/test_mapping.py::test_a_highlight_older_than_updated_at_wins"

run_mutation "highlight preferred even when it is NEWER" "$M" \
  '    if newest and updated and newest < updated - HIGHLIGHT_SLACK:' \
  '    if newest and updated:' \
  "tests/test_mapping.py::test_a_highlight_later_than_updated_at_is_ignored_as_noise"

run_mutation "the whole steady-state gate removed" "$S" \
  '    steady_state = (
        config.full
        and bool(state.full_listing_completed_at)
        # Every status being pulled now must have been covered by that listing,
        # or an article "appearing for the first time" may simply never have
        # been asked for before.
        and _statuses(config.status) <= _statuses(state.full_listing_status)
    )' \
  '    steady_state = config.full' \
  "$T::test_a_first_run_admits_its_dates_are_a_fallback"

run_mutation "a truncated run records a completed listing" "$S" \
  '            state.full_listing_completed_at = to_iso(checkpoint)
            state.full_listing_status = config.status' \
  '            pass
    if config.full and not config.dry_run:
            state.full_listing_completed_at = to_iso(checkpoint)
            state.full_listing_status = config.status' \
  "$T::test_a_chunked_backfill_never_claims_to_have_witnessed_a_transition"

run_mutation "a queued item labelled as entering the archive" "$M" \
  '    if observed_transition and item.get("status") == "archive":' \
  '    if observed_transition:' \
  "$T::test_a_queued_item_is_never_labelled_as_having_entered_the_archive"

run_mutation "listing coverage ignored (queue run licenses archive claims)" "$S" \
  '        and _statuses(config.status) <= _statuses(state.full_listing_status)' \
  '        and True' \
  "$T::test_a_queue_only_listing_does_not_license_claims_about_the_archive"

run_mutation "matched files re-read from disk every night" "$S" \
  '            if already:
                reread_status = "already-recorded"
            elif reread_date' \
  '            if False:
                reread_status = "already-recorded"
            elif reread_date' \
  "$T::test_an_already_recorded_reread_is_not_re_read_from_disk_every_night"

run_mutation "a run with errors still licenses the claim" "$S" \
  '    if result.errors == 0 and not truncated and not config.dry_run:' \
  '    if not truncated and not config.dry_run:' \
  "$T::test_a_run_with_errors_does_not_license_the_claim"

run_mutation "observed transitions claimed in --sync mode" "$S" \
  '        config.full
        and bool(state.full_listing_completed_at)' \
  '        bool(state.full_listing_completed_at)
        and True' \
  "$T::test_sync_mode_does_not_claim_to_have_observed_anything"

run_mutation "a later estimate rewrites a recorded date" "$M" \
  '    saved = previous.get("date_saved")
    if saved:' \
  '    saved = previous.get("date_saved")
    if False:' \
  "tests/test_mapping.py::test_a_better_estimate_arriving_later_does_not_rewrite_history"

echo
echo "=== durability ==="
run_mutation "manifest not saved when the run raises" "$S" \
  '            try:
                state.save()
            except Exception as exc:' \
  '            try:
                pass
            except Exception as exc:' \
  "$T::test_an_exception_escaping_the_loop_still_saves_what_was_written"

run_mutation "atomic_write_text recreates a vanished vault" "scripts/matter/state.py" \
  '    if create_parents:
        path.parent.mkdir(parents=True, exist_ok=True)
    elif not path.parent.is_dir():' \
  '    if True:
        path.parent.mkdir(parents=True, exist_ok=True)
    elif not path.parent.is_dir():' \
  "$T::test_a_vault_that_vanishes_mid_run_is_not_recreated"

echo
echo "=== summary ==="
printf '  %d caught, %d escaped/stale\n' "$PASS" "$FAIL"
echo
echo "--- suite after restore ---"
"$PY" -m pytest tests/ -q 2>&1 | tail -1
echo "--- worktree ---"
git status --porcelain -- scripts dashboard tests | grep -v mutbak || echo "(clean)"
[ "$FAIL" -eq 0 ]
