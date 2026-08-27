---
title: "build_index.py times out under launchd but runs in 4 min by hand"
type: "plan"
status: "Queued"
priority: "P2"
project: "instapaper-archive"
created: 2026-08-27
completed:
effort: "S - diagnose QoS, one plist or pipeline change"
linked_pr: ""
---

# build_index.py times out under launchd but runs in 4 min by hand

## Evidence

Two consecutive nights (2026-08-26, 2026-08-27), the nightly matter-sync
pipeline logged:

    ERROR build_index.py timed out after 1h. The Markdown files are written and safe; re-run it by hand.

Both mornings the same script, run by hand from a terminal, completed in ~4
minutes (17,317-row Parquet index). The 8/26 failure was initially blamed on
post-macOS-update Spotlight reindexing; the 8/27 recurrence on a quiet night
kills that theory. The site deploy still succeeds each night but renders from
the last-good index, so freshness silently degrades if this keeps failing.

## Leading suspect

launchd background jobs run at Utility/Background QoS and macOS throttles
their disk I/O aggressively; a pandas/parquet full-archive scan is exactly the
workload that starves. Terminal runs get user-interactive QoS, hence 4 min.

## Candidate fixes (pick after confirming)

1. Confirm with `taskpolicy -b` reproduction by hand (should crawl) or check
   the job's QoS at runtime
2. Plist fix: `ProcessType: Interactive` (or `LowPriorityIO: false`) on
   com.thedetech.article-sync.matter
3. Or wrap the build_index subprocess in `taskpolicy -c utility` override
4. Or reorder: run build_index FIRST in the pipeline while the 1h budget is
   fresh (does not fix throttling, only masks)

## done_when

- [ ] Root cause confirmed (QoS throttling proven or disproven)
- [ ] Nightly run completes build_index inside its budget two consecutive nights
- [ ] No more "re-run it by hand" mornings
