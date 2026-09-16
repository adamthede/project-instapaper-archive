---
title: "feat(matter): nightly queue snapshot ledger + reads backfill (backlog velocity data)"
status: "Done"
completed: 2026-09-16
linked_pr: "https://github.com/adamthede/project-instapaper-archive/pull/27"
---

Shipped via PR. No backing plan file of its own; the work is follow-on task 2 of `docs/plans-to-do/2026-09-15-unread-corpus-what-i-meant-to-read.md` (on the unread-corpus branch, PR #26), which stays open for the pipeline and the Matter-queue follow-on. Recorded via /shipped.

## Post-Deploy Checklist

- [ ] Install the nightly job: `launchctl load -w ~/Library/LaunchAgents/com.thedetech.article-sync.matter-queue.plist` after copying `launchd/com.thedetech.article-sync.matter-queue.plist` into place (command in the PR body)
- [ ] Confirm the first nightly row lands in `data/matter/queue_daily.csv` with a non-null inflow/outflow on night two
