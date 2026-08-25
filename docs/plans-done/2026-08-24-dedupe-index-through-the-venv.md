---
title: "Read the dedupe index through the venv, not the vault"
status: "Done"
completed: 2026-08-24
linked_pr: "https://github.com/adamthede/project-instapaper-archive/pull/14"
project: "articles"
---

Shipped via PR #14. No backing plan file existed — this was an operational fix
found by running the nightly, not a planned deliverable.

## What it changed

The nightly dedupe was walking the whole vault over SMB to build its URL index.
That read now goes through the Parquet index via the venv interpreter instead:
**206 seconds → 0.7 seconds** on the real 15MB index, measured under the actual
launchd interpreter rather than a developer shell.

The launchd interpreter has no pandas/pyarrow, so the read happens in a
subprocess through the venv python, with the vault scan kept as the fallback.

## Why the review took two rounds

The risk was never the speed — it was whether a degraded read could still report
a healthy `dedupe_source`. If it could, duplicate articles get written and
cross-era re-reads get dropped with nothing in the heartbeat to say so.

It can't, and structurally: `UrlIndex.source` is only ever constructed at the
point a path actually succeeds, and both Parquet returns sit inside the `try`.
Anything entering the broad `except` can only fall through to the vault-scan
return. Verified by forcing MemoryError, ArrowNotImplemented, ArrowTypeError and
RecursionError — every one produced `source='vault scan (...)'`.

## Lesson banked: a test can pass for the wrong reason

Round 1 found the returncode guard's test passing with the guard deleted. Round 2
found the *same defect in its sibling* — the timeout test used a `sleep(30)`
child that ended on its own, so `json.loads('')` rejected the empty payload and
satisfied the assertion no matter what. Deleting `timeout=` left the suite green.

The fix was to assert on the **clock** (`elapsed < 30` against a `sleep(300)`
child), which makes green-and-fast and red-and-slow different states. Mutation
verification: 1.01s passing vs **300.24s failing**.

Same pattern in the log test — asserting a warning *exists* would have rotted
silently, so it asserts `exc_info` names the exception, which is what
`MATTER_SYNC.md` actually promises. Dropping `exc_info=True` alone fails it.

All four mutations were re-run independently in a separate checkout before merge.

## Open follow-up

`NON_ARTICLE_DIRS` is now stated twice (`build_index.py:37`, `vaultindex.py:56`).
PR #14 made the duplication self-policing with a drift test; the real fix is to
import it. Filed as issue #16 — deliberately not done at the merge gate.
