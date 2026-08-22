---
title: "Nightly publish leg + whole-night heartbeat"
status: "Done"
completed: 2026-08-22
linked_pr: "https://github.com/adamthede/project-instapaper-archive/pull/11"
---

Shipped via PR #11 — the final wiring step of the weekly-synthesis plan's
Phase 3. The 04:45 chain now ends at reading.adamthede.com:
sync → enrich → rebuild index → rebuild site → deploy.

Also closed a pre-existing defect found while wiring: the heartbeat was
written ABOVE the enrich/rebuild legs, so a failed index rebuild had been
reporting a green night. Legs now run first and each status lands in the
heartbeat's `legs{}`.

Note: the adversarial review was still in flight when Adam merged; any
findings become follow-ups. Plist reinstall was done separately by Adam —
installing it is what makes the chain publish unattended.
