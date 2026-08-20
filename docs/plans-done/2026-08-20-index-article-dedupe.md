---
title: "Index-layer article dedupe — 582 double-filed bookmarks + Matter re-pushes"
status: "Done"
completed: 2026-08-20
linked_pr: "https://github.com/adamthede/project-instapaper-archive/pull/6"
---

Shipped via PR #6. No backing plan file — Adam spotted duplicates on the
2023-W50 week page; investigation found 582 bookmarks double-filed by the
two historical exporters plus 12 Matter-era re-pushes. Deduped at the index
layer (17,998 -> 17,416 rows); vault files untouched. Follow-up executed in
the 2026-08-20 morning batch: regenerate 59 affected weekly digests, remove
2 all-phantom weeks, rebuild the site.

Note: the adversarial review of this PR was still in flight when Adam
merged; any late findings become follow-ups.
