#!/usr/bin/env python3
"""Make the nightly run the code that was merged, not the code lying around.

The launchd job invokes `export_matter_to_archive.py` with the repo as its
working directory and nothing else. There is no `git pull` anywhere in the
chain, so the nightly executes whatever happens to be checked out — and a merged
fix reaches production only if somebody remembers to pull. That has already cost
this project two days: fixes sat merged and unused while the nightly rebuilt and
deployed from stale code every morning, with no symptom anywhere in the log.

The awkward part, and the reason this is not three lines: **pulling inside a
running Python process does not change the code that process is already
running.** Modules are imported before the pull happens. So a pull without a
re-exec would fix the NEXT night and quietly mislead about tonight — the log
would say "updated", and the stale code would keep running.

So: pull, and if HEAD actually moved, re-exec so the new code is what runs.
`RE_EXEC_ENV` makes that a one-shot; a pull that somehow keeps moving HEAD can
loop at most once.

Fail-open throughout. This is a convenience at the head of a chain that walks an
hour of SMB; it must never be the reason a night produces nothing. Every refusal
is logged at WARNING with the reason, because a silent skip here recreates the
exact problem the module exists to solve.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)

RE_EXEC_ENV = "ARCHIVE_NIGHTLY_REEXECED"
SKIP_ENV = "ARCHIVE_SKIP_PULL"
TIMEOUT = 120


def _git(repo: Path, *args, timeout=TIMEOUT):
    """Run git, returning (ok, stdout). Never raises."""
    try:
        p = subprocess.run(["git", "-C", str(repo), *args],
                           capture_output=True, text=True, errors="replace",
                           timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("freshness: git %s failed: %s", " ".join(args), exc)
        return False, ""
    if p.returncode != 0:
        log.warning("freshness: git %s exited %d: %s",
                    " ".join(args), p.returncode, (p.stderr or "").strip()[:200])
        return False, ""
    return True, (p.stdout or "").strip()


def _state(repo: Path):
    """(branch, dirty, behind), or a status STRING explaining why not.

    The reason matters: "this is not a git checkout" and "this branch has no
    upstream" are different operator problems, and a nightly running from a
    feature branch is a real state worth naming rather than lumping in with
    a broken install.
    """
    ok, branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    if not ok:
        return "unknown (not a git checkout)"
    if branch == "HEAD":
        return "unknown (detached HEAD)"
    ok, porcelain = _git(repo, "status", "--porcelain")
    if not ok:
        return "unknown (git status failed)"
    # Untracked files are normal here — the vault, caches, .wrangler. Only
    # TRACKED modifications mean a pull could clobber real work.
    dirty = [ln for ln in porcelain.splitlines() if ln and not ln.startswith("??")]
    # One failed fetch conflated four different operator problems: network
    # down, branch absent from the remote, no remote named origin, and a
    # read-only .git. At 04:45 "cannot reach origin" sends someone to check
    # the network for a problem that is none of those. Name the real one.
    ok, remotes = _git(repo, "remote")
    if not ok or "origin" not in remotes.split():
        return "unknown (no remote named origin)"
    if not _git(repo, "fetch", "origin", branch, "--quiet")[0]:
        ok, _ = _git(repo, "ls-remote", "--exit-code", "--heads", "origin", branch)
        if not ok:
            return f"unknown ({branch} is not on origin)"
        return f"unknown (cannot reach origin/{branch})"
    ok, behind = _git(repo, "rev-list", "--count", f"HEAD..origin/{branch}")
    if not ok or not behind.isdigit():
        return f"unknown (no upstream for {branch})"
    return branch, dirty, int(behind)


def ensure_fresh(repo: Path, argv=None) -> str:
    """Fast-forward the checkout to origin, re-execing if HEAD moved.

    Returns a short status string for the caller to log and record. Does not
    return at all in the re-exec case — the process is replaced.

    `argv` is ARGPARSE-shaped (no script name), because that is what a
    `main(argv=None)` signature receives. execv needs argv[0] to be the script,
    so the script name is prepended here rather than trusted from the caller.
    Getting this wrong turned a module whose entire contract is "never be the
    reason a night produces nothing" into exactly that reason: execv would run
    `python --full --deploy`, which exits 2 — and since execv has already
    replaced the process, that exit IS the nightly.
    """
    repo = Path(repo)
    if os.environ.get(SKIP_ENV) == "1":
        return "skipped (ARCHIVE_SKIP_PULL=1)"
    if os.environ.get(RE_EXEC_ENV) == "1":
        return "already re-execed this run"

    state = _state(repo)
    if isinstance(state, str):
        log.warning("freshness: %s; the nightly is running whatever is "
                    "checked out at %s", state, repo)
        return state
    branch, dirty, behind = state

    if dirty:
        # Loud on purpose: this is the case where the nightly silently runs
        # stale code and the operator has no idea, which is the whole problem.
        log.warning("freshness: NOT pulling — %d tracked file(s) modified on %s; "
                    "the nightly is running whatever is checked out. First: %s",
                    len(dirty), branch, dirty[0][:80])
        return f"stale: {len(dirty)} modified file(s) on {branch}"

    if behind == 0:
        return f"current ({branch})"

    before = _git(repo, "rev-parse", "HEAD")[1]
    ok, _ = _git(repo, "merge", "--ff-only", f"origin/{branch}")
    if not ok:
        log.warning("freshness: fast-forward to origin/%s failed; running the "
                    "checked-out code (%d commit(s) behind)", branch, behind)
        return f"stale: ff-only failed, {behind} behind {branch}"
    after = _git(repo, "rev-parse", "HEAD")[1]

    if after == before or not after:
        return f"current ({branch})"

    log.warning("freshness: pulled %d commit(s) on %s (%s -> %s); re-execing so "
                "the new code is what runs", behind, branch, before[:8], after[:8])
    os.environ[RE_EXEC_ENV] = "1"
    # sys.argv[0] is the script under launchd; sys.argv entire when no argv was
    # passed. Never `argv` alone — see the docstring.
    exec_argv = [sys.argv[0], *argv] if argv is not None else list(sys.argv)
    try:
        # sys.executable, NOT a resolved-from-PATH python: the plist runs
        # /opt/homebrew/bin/python3 because the TCC grant for ~/Documents is
        # attributed to that exact binary. Re-execing through a different
        # interpreter loses the grant and the run dies with EPERM — a failure
        # this fleet has a documented history of.
        os.execv(sys.executable, [sys.executable, *exec_argv])
    except OSError as exc:
        # Could not replace the process. The pull already happened, so the code
        # on disk is now NEWER than the code running — say so rather than let
        # the run look clean.
        log.warning("freshness: re-exec failed (%s); this run continues on the "
                    "PREVIOUS code, tonight's output is one commit behind disk", exc)
        return f"pulled {behind}, re-exec failed"
    raise AssertionError("unreachable: execv replaces the process")
