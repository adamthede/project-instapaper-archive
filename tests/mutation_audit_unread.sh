#!/bin/bash
# Mutation audit for the unread corpus: the resolve chain, the queue's resume
# guarantee, the enrichment template, the analysis rules, and the leak scan.
#
# Same method and the same harness as tests/mutation_audit.sh and
# tests/mutation_audit_public_shape.sh: break one thing at a time and confirm a
# named test fails. A test that stays green under its own mutation is not
# testing the thing its docstring claims.
#
#   bash tests/mutation_audit_unread.sh
#
# Every mutation is reverted immediately; the script verifies a clean worktree
# and a green suite at the end.
#
# No network and no credentials. The Instapaper client, the HTTP session and
# the Gemini model are all fakes in this suite, so every mutation below is
# aimed at logic rather than at a live call.

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

FETCH=tests/test_unread_fetch.py
ENRICH=tests/test_unread_enrich.py
ANALYSIS=tests/test_unread_analysis.py
PUBLIC=tests/test_unread_public.py

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

echo
echo "STAGE 1 - the listing and the resolve chain"
run_mutation "the word floor comes off" \
  scripts/unread/resolve.py "WORD_FLOOR = 150" "WORD_FLOOR = 0" "$FETCH"
run_mutation "Wayback probes a bare year instead of the saved one" \
  scripts/unread/resolve.py 'return f"{WAYBACK_PREFIX}{year}/{url}"' \
  'return f"{WAYBACK_PREFIX}2/{url}"' "$FETCH"
run_mutation "any 200 from the Wayback probe counts as a snapshot" \
  scripts/unread/resolve.py \
  'return parts.netloc.lower().endswith(WAYBACK_HOST) and parts.path.startswith("/web/")' \
  'return True' "$FETCH"
run_mutation "the Instapaper leg stops ending the item" \
  scripts/unread/resolve.py \
  "    attempt, text, html = _leg_instapaper(row, instapaper, sleeper)
    attempts.append(attempt)
    if attempt.ok:" \
  "    attempt, text, html = _leg_instapaper(row, instapaper, sleeper)
    attempts.append(attempt)
    if False:" "$FETCH"
run_mutation "the rate limit between Instapaper calls" \
  scripts/unread/resolve.py "INSTAPAPER_DELAY = 0.8" "INSTAPAPER_DELAY = 0.0" "$FETCH"
run_mutation "script and style survive text extraction" \
  scripts/unread/resolve.py \
  'for tag in soup(["script", "style", "noscript", "template"]):' \
  'for tag in soup([]):' "$FETCH"
run_mutation "body files are named after titles" \
  scripts/unread/resolve.py 'return f"{url_sha256[:2]}/{url_sha256}.txt"' \
  'return f"{url_sha256[:2]}/x.txt"' "$FETCH"
run_mutation "an Instapaper call goes out with no timeout" \
  scripts/unread/instapaper.py "TIMEOUT = 30" "TIMEOUT = None" "$FETCH"
run_mutation "a hung Instapaper call kills the pass" \
  scripts/unread/instapaper.py "            log.warning(\"get_text %s failed: %s\", bookmark_id, exc)
            return (None, \"\")" "            raise" "$FETCH"
run_mutation "the per-item deadline is a no-op" \
  scripts/unread/resolve.py '    if not seconds or not hasattr(signal, "SIGALRM"):' \
  "    if True:" "$FETCH"
run_mutation "a stalled item is dropped instead of recorded" \
  scripts/unread/resolve.py '                    [Attempt(METADATA, None, False, 0, f"deadline: {exc}")])' \
  "                    [])" "$FETCH"
run_mutation "all 105 folder items enter the pool, not the 97" \
  scripts/unread/instapaper.py "            if progress > 0.0:
                continue" "            if False:
                continue" "$FETCH"
run_mutation "the queue flushes at the end instead of after each item" \
  scripts/unread/queue.py "        row.update(fields)
        self.save()" "        row.update(fields)" "$FETCH"
run_mutation "a settled row is offered as pending again" \
  scripts/unread/queue.py 'return [r for r in self._rows.values() if r.get("outcome") == PENDING]' \
  'return list(self._rows.values())' "$FETCH"
run_mutation "a refresh clobbers what the fetch stage wrote" \
  scripts/unread/queue.py \
  'REFRESHABLE = ("title", "folder", "starred", "read_progress", "abandonment",
               "bookmark_id")' \
  'REFRESHABLE = ("title", "folder", "starred", "read_progress", "abandonment",
               "bookmark_id", "outcome", "resolve_path")' "$FETCH"

echo
echo "STAGE 2 - the enrichment template and the meter"
run_mutation "the 10,000-character cap survives into the unread run" \
  scripts/unread/enrich.py "return base.build_prompt(text, max_chars=MAX_BODY_CHARS, extra_fields=extra)" \
  "return base.build_prompt(text, max_chars=10000, extra_fields=extra)" "$ENRICH"
run_mutation "the cap comes off the read corpus too" \
  scripts/core/enrich_archive_gemini.py "def build_prompt(content, max_chars=10000, extra_fields=\"\"):" \
  "def build_prompt(content, max_chars=None, extra_fields=\"\"):" "$ENRICH"
run_mutation "the template lands after the article instead of before it" \
  scripts/core/enrich_archive_gemini.py "{extra}
Article Text:" "
Article Text:
{extra}" "$ENRICH"
run_mutation "the guard on a pathological body" \
  scripts/unread/enrich.py "MAX_BODY_CHARS = 120_000" "MAX_BODY_CHARS = 100_000_000" "$ENRICH"
run_mutation "a missing confidence defaults to high" \
  scripts/unread/enrich.py 'DEFAULT_CONFIDENCE = "low"' 'DEFAULT_CONFIDENCE = "high"' "$ENRICH"
run_mutation "an unknown aged_out becomes False" \
  scripts/unread/enrich.py "        aged_out = None" "        aged_out = False" "$ENRICH"
run_mutation "the unread lines are left in the summary" \
  scripts/unread/enrich.py "    body, taken = _split_unread_lines(answer)" \
  "    _body, taken = _split_unread_lines(answer); body = answer" "$ENRICH"
run_mutation "the inference ships without its label" \
  scripts/unread/enrich.py 'record["why_saved_kind"] = "inference"' \
  'record["why_saved_kind"] = "note"' "$ENRICH"
run_mutation "the nearly-finished band floor moves" \
  scripts/unread/derive.py "NEARLY_FINISHED = 0.8" "NEARLY_FINISHED = 0.6" "$ENRICH"
run_mutation "the stale pre-paid-tier Flash-Lite pricing" \
  scripts/unread/enrich.py "PRICE_INPUT_PER_M = 0.10" "PRICE_INPUT_PER_M = 0.015" "$ENRICH"
run_mutation "a run with no usage metadata bills zero" \
  scripts/unread/enrich.py \
  'return (max(1, len(prompt or "") // 4), max(1, len(answer or "") // 4))' \
  "return (0, 0)" "$ENRICH"
run_mutation "the SDK stays on gRPC" \
  scripts/unread/enrich.py 'TRANSPORT = "rest"' 'TRANSPORT = "grpc"' "$ENRICH"
run_mutation "one refused article kills the enrichment pass" \
  scripts/unread/enrich.py "        except Exception as exc:  # noqa: BLE001" \
  "        except ZeroDivisionError as exc:" "$ENRICH"
run_mutation "the Gemini call has no timeout of its own" \
  scripts/unread/enrich.py "            prompt, request_options={\"timeout\": self.timeout})" \
  "            prompt)" "$ENRICH"
run_mutation "one stalled article stops the enrichment pass" \
  scripts/unread/enrich.py "            with deadline(item_deadline):" \
  "            with deadline(0):" "$ENRICH"
run_mutation "the enrichment re-runs over what it already did" \
  scripts/unread/enrich.py "    done = _already_done(out_path)" "    done = set()" "$ENRICH"

echo
echo "STAGE 3 - the analysis rules"
run_mutation "a low-confidence inference is counted" \
  scripts/unread/analysis.py '        elif confidence == "low":' "        elif False:" "$ANALYSIS"
run_mutation "an unknown aged_out is counted as still current" \
  scripts/unread/analysis.py \
  '        if verdict is None or record.get("content_corrupted"):' \
  '        if record.get("content_corrupted"):' "$ANALYSIS"
run_mutation "a year nothing was judged in reports 0.0 rather than None" \
  scripts/unread/analysis.py '                   "fraction": (round(aged[year] / judged[year], 4)
                                if judged[year] else None)}' \
  '                   "fraction": (round(aged[year] / judged[year], 4)
                                if judged[year] else 0.0)}' "$ANALYSIS"
run_mutation "the legacy document archive joins the read baseline" \
  scripts/unread/analysis.py 'READ_IT_LATER_SOURCES = ("instapaper", "matter")' \
  'READ_IT_LATER_SOURCES = ("instapaper", "matter", "legacy_pdf")' "$ANALYSIS"
run_mutation "corrupted rows set the read baseline" \
  scripts/unread/analysis.py '        rows = rows[rows["content_corrupted"] != True]  # noqa: E712' \
  "        rows = rows" "$ANALYSIS"
run_mutation "www.nytimes.com and nytimes.com count separately" \
  scripts/unread/analysis.py 'return host[4:] if host.startswith("www.") else host' \
  "return host" "$ANALYSIS"
run_mutation "archived-only and live-web survival are folded together" \
  scripts/unread/analysis.py '"live_web": counts.get("direct", 0),' \
  '"live_web": counts.get("direct", 0) + counts.get("wayback", 0),' "$ANALYSIS"
run_mutation "an unjudged item becomes eligible for the shortlist" \
  scripts/unread/analysis.py 'if r.get("aged_out") is False and not r.get("content_corrupted")]' \
  'if r.get("aged_out") is not True and not r.get("content_corrupted")]' "$ANALYSIS"
run_mutation "the abandonment band stops moving the ranking" \
  scripts/unread/analysis.py "ABANDONMENT_SCALE = 0.6" "ABANDONMENT_SCALE = 0.0" "$ANALYSIS"
run_mutation "the shortlist order is not deterministic" \
  scripts/unread/analysis.py 'picks.sort(key=lambda p: (-p["score"], p["url_sha256"]))' \
  'picks.sort(key=lambda p: -p["score"])' "$ANALYSIS"
run_mutation "an unmeasurable year is scored as total drift" \
  scripts/unread/analysis.py \
  '            "mean_drift": round(statistics.mean(measured), 4) if measured else None,' \
  '            "mean_drift": round(statistics.mean(measured), 4) if measured else 1.0,' "$ANALYSIS"
run_mutation "the comparison ships without its caveat" \
  scripts/unread/analysis.py '        "caveat": COMPARISON_CAVEAT,' '        "caveat": "",' "$ANALYSIS"
run_mutation "a corrupted row joins the topic aggregates" \
  scripts/unread/analysis.py 'return [r for r in records if not r.get("content_corrupted")]' \
  "return list(records)" "$ANALYSIS"

echo
echo "STAGE 4 - the public shape and the leak scan"
run_mutation "the needle floor comes off" \
  scripts/unread/public.py "MIN_NEEDLE = 12" "MIN_NEEDLE = 1" "$PUBLIC"
run_mutation "a corpus with no needles publishes anyway" \
  scripts/unread/public.py "    if not titles and not paths:" "    if False:" "$PUBLIC"
run_mutation "the scan reads raw bytes only" \
  scripts/unread/public.py 'haystack = (text + "\n" + html_mod.unescape(text)).casefold()' \
  "haystack = text.casefold()" "$PUBLIC"
run_mutation "URL paths are not scanned for" \
  scripts/unread/public.py '               + [(p, "url path") for p in paths if len(p) >= min_len])' \
  "               )" "$PUBLIC"
run_mutation "needles are not longest-first" \
  scripts/unread/public.py "    longest = lambda values: sorted(values, key=lambda v: (-len(v), v))  # noqa: E731" \
  "    longest = lambda values: sorted(values)  # noqa: E731" "$PUBLIC"
run_mutation "the record publishes its URLs" \
  scripts/unread/public.py '        "distinct_domains": len(domains),' \
  '        "distinct_domains": len(domains),
        "urls": [r.get("url") for r in records],' "$PUBLIC"
run_mutation "the record publishes its people and orgs" \
  scripts/unread/public.py '        "domain_concentration": _concentration(domains),' \
  '        "domain_concentration": _concentration(domains),
        "people": [p for r in records for p in (r.get("ai_people") or [])],' "$PUBLIC"
run_mutation "the record publishes the inference sentences" \
  scripts/unread/public.py '        "caveat": analysis.COMPARISON_CAVEAT,' \
  '        "caveat": analysis.COMPARISON_CAVEAT,
        "why": [r.get("why_saved") for r in records],' "$PUBLIC"
run_mutation "the record publishes the shortlist itself" \
  scripts/unread/public.py '        "still_worth_your_time": shortlist_count,' \
  '        "still_worth_your_time": shortlist_count,
        "picks": [{"title": r.get("title")} for r in records],' "$PUBLIC"
run_mutation "the inference count ships without its label" \
  scripts/unread/public.py '"why_saved": dict(analysis.why_saved_summary(records), kind="inference"),' \
  '"why_saved": analysis.why_saved_summary(records),' "$PUBLIC"
run_mutation "a leak publishes and then reports itself" \
  scripts/unread/public.py "        found = leak_scan(tmp, titles, paths)
        if found:" "        found = leak_scan(tmp, titles, paths)
        if False:" "$PUBLIC"
run_mutation "the build clears an --out it did not write" \
  scripts/unread/public.py "    strays = [p.name for p in out.iterdir() if p.name not in RECORD_FILES]" \
  "    strays = []" "$PUBLIC"

echo
find . -name "__pycache__" -type d -not -path "./.git/*" -exec rm -rf {} + 2>/dev/null
echo "Mutations caught: $PASS   escaped or stale: $FAIL"

if [ -n "$(git status --porcelain scripts/ tests/)" ]; then
  echo "WORKTREE NOT CLEAN after the audit - a mutation was not reverted:"
  git status --porcelain scripts/ tests/
  exit 1
fi

echo "Worktree clean. Re-running the four suites to confirm they are green:"
"$PY" -m pytest $FETCH $ENRICH $ANALYSIS $PUBLIC -q -p no:cacheprovider 2>&1 | tail -3
[ "$FAIL" -eq 0 ] || exit 1
