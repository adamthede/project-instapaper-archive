---
title: "Publishing opt-in + nightly leg exception handling (incident follow-up)"
status: "Done"
completed: 2026-08-22
linked_pr: "https://github.com/adamthede/project-instapaper-archive/pull/12"
---

Shipped via PR #12. Two things:

1. PR #11's adversarial review findings: main() now heartbeats on a RAISED
   leg (was skipping the write entirely, leaving yesterday's "ok" on disk),
   rebuild_index gained the TimeoutExpired guard the other three legs had,
   and generate.py retires the old site by rename so _site is never partial.

2. INCIDENT 2026-08-21: that same review published seven fixture deployments
   to the live reading.adamthede.com while probing whether deploy_site's
   guards hold - reaching the function at all was enough to publish, because
   PAGES_PROJECT is a hardcoded live target. Nothing leaked (site is behind
   Access; fixtures were empty pages). deploy_site now requires
   READING_DEPLOY=1, set only by the plist and deliberate hand-runs.

FOLLOW-UP REQUIRED: the plist must be reinstalled after this merge, or the
nightly will refuse to deploy (new code requires the opt-in the old installed
plist does not set).
