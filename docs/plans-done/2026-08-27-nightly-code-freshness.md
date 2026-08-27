---
title: "Nightly runs the merged code, not whatever is checked out"
status: "Done"
completed: 2026-08-27
linked_pr: "https://github.com/adamthede/project-instapaper-archive/pull/21"
project: "articles"
---

Shipped via PR #21. No backing plan file existed — an operational fix found
while answering "will merging this make the pages appear?"

## The problem

The launchd job runs `export_matter_to_archive.py` with the repo as its working
directory and **no git step**, so the nightly rebuilt and deployed whatever was
checked out. Merged fixes reached production only if someone remembered to
pull — and had already sat unused for two days, with no symptom in any log.

## Why it wasn't three lines

Pulling inside a running Python process does not change modules it has already
imported. A pull without a re-exec would fix the *next* night while reporting
success for tonight — the same silent-staleness bug, now with a log line
claiming it was handled. So it pulls, and re-execs if HEAD actually moved.

## Failure posture

Fail-open throughout, and loud about every refusal. Dirty tree, detached HEAD,
no origin remote, branch not on origin, unreachable origin, git missing, git
timing out, mid-merge, mid-rebase, shallow clone, bare repo, unborn branch,
read-only `.git`, stale index.lock — each returns a named status and lets the
run continue. **A local edit is never clobbered.**

## Lessons banked

Two rounds of adversarial review found three defects in my own fixes:

- A module-level global made an early-failure heartbeat stamp the **previous**
  run's status — a heartbeat asserting something false about the run it
  describes, inside the feature built to stop exactly that.
- `def _git(..., timeout=TIMEOUT)` binds the default **once at definition**, so
  reassigning `freshness.TIMEOUT` silently did nothing. A knob that looks
  adjustable and is not cost the reviewer a measurement before it was found.
- An argv-shape mismatch made a fail-open module fail **closed**: `execv` would
  have run the interpreter with `--full` as its program, exit 2 — and since
  execv has already replaced the process, that exit *is* the nightly.

## Open follow-up

`freshness` reaches the heartbeat, but command-center's
`launchd_stats.py::_read_heartbeat` picks out start/finish/outcome and discards
unknown keys. The value is recorded and safe; **surfacing it in the cockpit
needs a command-center change**, or a decision here about whether a stale night
should influence `outcome` and be caught by the existing red/green logic.
