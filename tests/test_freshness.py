"""Tests for the nightly's code-freshness check.

The failure this guards is specific and has already happened: the launchd job
has no git step, so the nightly runs whatever is checked out. Merged fixes sat
unused for two days while the chain rebuilt and deployed stale code every
morning, and nothing in the log said so.

Two properties matter more than the happy path.

**It must never cost a night.** Every git failure, missing remote, detached
HEAD or timeout has to return a string and let the run continue. The check sits
at the head of a chain that walks an hour of SMB; a convenience that can abort
that is worse than no convenience.

**It must not lie about what ran.** Pulling inside a live process does not
change the modules already imported, so a pull without a re-exec would fix the
NEXT night while reporting success for tonight. That is the same silent-staleness
bug wearing a different hat, which is why the re-exec is not optional and why
the failed-re-exec path says the output is behind the disk.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "core"))

import freshness  # noqa: E402


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """ensure_fresh sets RE_EXEC_ENV in os.environ directly — it has to, so the
    flag survives execv into the replaced process. That makes it leak between
    tests in one pytest process: without this, every test after the re-exec one
    returns 'already re-execed this run' and passes or fails for that reason
    rather than its own."""
    monkeypatch.delenv(freshness.RE_EXEC_ENV, raising=False)
    monkeypatch.delenv(freshness.SKIP_ENV, raising=False)


@pytest.fixture(autouse=True)
def never_really_exec(monkeypatch):
    """Structural safety net: no test may replace the pytest process.

    Found by mutation testing. Disabling the opt-out made
    test_the_opt_out_works reach a REAL os.execv, which replaced the test
    runner with a fresh python running pytest's own argv — the suite hung
    until it was killed. A test file that can execv itself is a trap for
    whoever runs the mutations next, so execv is stubbed for every test and
    the two that care re-patch it to observe the call.
    """
    def refuse(*_a, **_k):
        raise OSError("os.execv is disabled inside the test suite")
    monkeypatch.setattr(freshness.os, "execv", refuse)


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=True)


@pytest.fixture
def origin_and_clone(tmp_path):
    """A real origin plus a clone of it — no mocking of git itself."""
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-q", "--initial-branch=main")
    git(origin, "config", "user.email", "t@example.com")
    git(origin, "config", "user.name", "T")
    (origin / "code.py").write_text("VERSION = 1\n")
    git(origin, "add", "-A")
    git(origin, "commit", "-qm", "one")

    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
    git(clone, "config", "user.email", "t@example.com")
    git(clone, "config", "user.name", "T")
    return origin, clone


def advance(origin, text="VERSION = 2\n"):
    (origin / "code.py").write_text(text)
    git(origin, "add", "-A")
    git(origin, "commit", "-qm", "two")


def test_an_up_to_date_checkout_reports_current(origin_and_clone):
    _origin, clone = origin_and_clone
    assert freshness.ensure_fresh(clone) == "current (main)"


def test_a_dirty_checkout_is_never_pulled(origin_and_clone):
    """The operator has real work in the tree. Clobbering it to be helpful is
    the one outcome worse than running stale code."""
    origin, clone = origin_and_clone
    advance(origin)
    (clone / "code.py").write_text("VERSION = 1  # local edit\n")

    status = freshness.ensure_fresh(clone)

    assert status.startswith("stale:")
    assert "modified file(s)" in status
    assert (clone / "code.py").read_text() == "VERSION = 1  # local edit\n"
    assert git(clone, "rev-list", "--count", "HEAD..origin/main").stdout.strip() == "1"


def test_untracked_files_do_not_block_a_pull(origin_and_clone, monkeypatch):
    """The real checkout always has untracked files — the vault, caches,
    .wrangler. Treating those as 'dirty' would disable this permanently."""
    origin, clone = origin_and_clone
    advance(origin)
    (clone / "scratch.log").write_text("noise\n")
    monkeypatch.setattr(freshness.os, "execv", lambda *a: (_ for _ in ()).throw(OSError("no exec")))

    status = freshness.ensure_fresh(clone)

    assert "re-exec failed" in status
    assert (clone / "code.py").read_text() == "VERSION = 2\n", "the pull did not happen"


def test_a_pull_re_execs_so_the_new_code_is_what_runs(origin_and_clone, monkeypatch):
    """Modules are already imported by the time this runs. Without the re-exec
    the pull fixes the NEXT night while reporting success for tonight.

    The argv passed here is ARGPARSE-shaped — no script name — because that is
    what `main(argv=None)` receives and hands on. The original version of this
    test passed ["x.py", "--full"], a shape the real caller never produces, so
    it made a broken path look correct: execv would have run
    `python --full --deploy`, exit 2, and since execv has already replaced the
    process that exit IS the nightly.
    """
    origin, clone = origin_and_clone
    advance(origin)
    calls = []
    monkeypatch.setattr(freshness.os, "execv", lambda exe, argv: calls.append((exe, argv)))
    monkeypatch.setattr(freshness.sys, "argv", ["/abs/path/export.py", "--full"])

    with pytest.raises(AssertionError):        # execv is mocked, so we fall through
        freshness.ensure_fresh(clone, argv=["--full", "--deploy"])

    assert calls, "pulled without re-execing — tonight would run the old code"
    exe, argv = calls[0]
    # argv[0] must be the SCRIPT, or python is handed a flag as its program.
    assert argv[1] == "/abs/path/export.py", f"argv[0] is not the script: {argv}"
    assert argv[2:] == ["--full", "--deploy"], "re-exec lost the original arguments"
    # The interpreter must be sys.executable. The plist runs
    # /opt/homebrew/bin/python3 because the TCC grant for ~/Documents is
    # attributed to that exact binary; re-execing through another python loses
    # the grant and the run dies with EPERM.
    assert exe == freshness.sys.executable
    assert argv[0] == freshness.sys.executable


def test_no_argv_re_execs_with_the_real_sys_argv(origin_and_clone, monkeypatch):
    """The launchd path: main(argv=None), so ensure_fresh falls back to
    sys.argv entire — which already carries the script at [0]."""
    origin, clone = origin_and_clone
    advance(origin)
    calls = []
    monkeypatch.setattr(freshness.os, "execv", lambda exe, argv: calls.append((exe, argv)))
    monkeypatch.setattr(freshness.sys, "argv", ["/abs/export.py", "--full", "--deploy"])

    with pytest.raises(AssertionError):
        freshness.ensure_fresh(clone)

    assert calls[0][1][1:] == ["/abs/export.py", "--full", "--deploy"]


def test_a_re_exec_sets_the_one_shot_flag(origin_and_clone, monkeypatch):
    """The READER of the flag was tested; the SETTER was not, so removing the
    line that prevents the loop left the suite green."""
    origin, clone = origin_and_clone
    advance(origin)
    monkeypatch.setattr(freshness.os, "execv", lambda *a: None)
    monkeypatch.setattr(freshness.sys, "argv", ["/abs/export.py"])
    # execv is stubbed, so control falls through to the "unreachable" guard.
    calls = []
    monkeypatch.setattr(freshness.os, "execv", lambda e, a: calls.append(a))
    with pytest.raises(AssertionError, match="unreachable"):
        freshness.ensure_fresh(clone, argv=[])
    # `argv is not None`, not `if argv`: with an empty list the re-exec must
    # carry just the script, not fall back to the whole of sys.argv.
    assert calls[0][1:] == ["/abs/export.py"], calls[0]
    assert freshness.os.environ.get(freshness.RE_EXEC_ENV) == "1", (
        "the flag is never set, so the guard reading it can never fire")


def test_a_diverged_clean_tree_is_never_merged(origin_and_clone):
    """--ff-only is the guard against an unattended merge commit landing in
    Adam's working checkout at 04:45. A diverged tree can be perfectly CLEAN,
    so the dirty check does not cover this.
    """
    origin, clone = origin_and_clone
    advance(origin)                                   # origin +1
    (clone / "local.py").write_text("mine\n")
    git(clone, "add", "-A")
    git(clone, "commit", "-qm", "local work")         # clone +1, diverged

    before = git(clone, "rev-parse", "HEAD").stdout.strip()
    status = freshness.ensure_fresh(clone)

    assert status.startswith("stale:") and "ff-only" in status
    assert git(clone, "rev-parse", "HEAD").stdout.strip() == before, "HEAD moved"
    merges = git(clone, "log", "--merges", "--oneline").stdout.strip()
    assert merges == "", f"an unattended merge commit was created: {merges}"


def test_the_cli_opt_out_flag_gates_the_check():
    """ARCHIVE_SKIP_PULL was tested; --no-freshness-check was not."""
    import sys as _sys
    _sys.path.insert(0, str(REPO_ROOT / "scripts" / "core"))
    import export_matter_to_archive as cli
    assert cli.build_parser().parse_args([]).no_freshness_check is False
    assert cli.build_parser().parse_args(["--no-freshness-check"]).no_freshness_check is True


def test_the_freshness_check_runs_before_the_expensive_work():
    """The whole design depends on re-execing BEFORE the SMB walk. Moving the
    block to the end of main() leaves every other test green."""
    src = (REPO_ROOT / "scripts" / "core" / "export_matter_to_archive.py").read_text()
    body = src[src.index("def main("):]
    assert body.index("ensure_fresh") < body.index("run_sync(config)"), (
        "the freshness check no longer precedes the sync")


def test_the_subprocess_calls_carry_a_timeout():
    """Verified as present rather than exercised: a fetch that hangs rather
    than fails would hold the nightly open indefinitely."""
    import inspect
    src = inspect.getsource(freshness._git)
    assert "timeout=timeout" in src


def test_the_re_exec_is_one_shot(origin_and_clone, monkeypatch):
    monkeypatch.setenv(freshness.RE_EXEC_ENV, "1")
    _origin, clone = origin_and_clone
    assert freshness.ensure_fresh(clone) == "already re-execed this run"


def test_a_failed_re_exec_says_the_output_is_behind_the_disk(origin_and_clone, monkeypatch):
    """The pull already landed, so the code on disk is newer than the code
    running. Reporting that cleanly would be the silent-staleness bug again."""
    origin, clone = origin_and_clone
    advance(origin)
    monkeypatch.setattr(freshness.os, "execv",
                        lambda *a: (_ for _ in ()).throw(OSError("denied")))
    assert "re-exec failed" in freshness.ensure_fresh(clone)


def test_the_opt_out_works(origin_and_clone, monkeypatch):
    monkeypatch.setenv(freshness.SKIP_ENV, "1")
    origin, clone = origin_and_clone
    advance(origin)
    assert "skipped" in freshness.ensure_fresh(clone)
    assert (clone / "code.py").read_text() == "VERSION = 1\n"


# --- it must never cost a night -------------------------------------------

def test_a_directory_that_is_not_a_checkout_is_survivable(tmp_path):
    assert freshness.ensure_fresh(tmp_path) == "unknown (not a git checkout)"


def test_a_missing_directory_is_survivable(tmp_path):
    assert "unknown" in freshness.ensure_fresh(tmp_path / "nope")


def test_an_unreachable_origin_is_survivable(origin_and_clone):
    """A fetch that cannot reach the remote must not abort the run."""
    _origin, clone = origin_and_clone
    git(clone, "remote", "set-url", "origin", "/nonexistent/path.git")
    assert "unknown" in freshness.ensure_fresh(clone)


def test_a_detached_head_is_survivable(origin_and_clone):
    _origin, clone = origin_and_clone
    sha = git(clone, "rev-parse", "HEAD").stdout.strip()
    git(clone, "checkout", "-q", "--detach", sha)
    assert freshness.ensure_fresh(clone) == "unknown (detached HEAD)"


def test_a_branch_not_on_the_remote_says_so(origin_and_clone):
    """A nightly running from an unpushed feature branch is a real state, and
    it is not a network problem. The previous assertion accepted BOTH the
    correct message and 'cannot reach origin/...', so the test's stated purpose
    — not sending the operator after the wrong cause — was unmet by the message
    it actually received."""
    _origin, clone = origin_and_clone
    git(clone, "checkout", "-qb", "feature/local-only")
    status = freshness.ensure_fresh(clone)
    assert status == "unknown (feature/local-only is not on origin)", status


def test_a_missing_origin_remote_says_so_rather_than_blaming_the_network(origin_and_clone):
    _origin, clone = origin_and_clone
    git(clone, "remote", "rename", "origin", "upstream")
    assert freshness.ensure_fresh(clone) == "unknown (no remote named origin)"


def test_git_timing_out_is_survivable(origin_and_clone, monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="git", timeout=1)
    monkeypatch.setattr(freshness.subprocess, "run", boom)
    _origin, clone = origin_and_clone
    assert "unknown" in freshness.ensure_fresh(clone)


def test_git_missing_entirely_is_survivable(origin_and_clone, monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("git")
    monkeypatch.setattr(freshness.subprocess, "run", boom)
    _origin, clone = origin_and_clone
    assert "unknown" in freshness.ensure_fresh(clone)


# --- the module's actual thesis, exercised end to end --------------------

def test_after_a_re_exec_the_code_that_runs_is_the_code_that_was_pulled(tmp_path):
    """The one test that proves what this module claims.

    Every other execv here is stubbed, so none of them demonstrate the point:
    that a process which pulls new code goes on to RUN that code rather than
    the code it started with. This builds a throwaway origin and clone holding
    a script that calls the real ensure_fresh, advances origin with a changed
    source marker, and checks which marker the finishing process reports.

    No vault, no network, no launchd, ~1s. Adapted from the reviewer's harness,
    which closed a gap I could not close from inside the suite.
    """
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-q", "--initial-branch=main")
    git(origin, "config", "user.email", "t@example.com")
    git(origin, "config", "user.name", "T")

    runner = '''
import json, os, sys
sys.path.insert(0, {core!r})
import freshness
MARKER = "{marker}"
status = freshness.ensure_fresh(os.path.dirname(os.path.abspath(__file__)), sys.argv[1:])
print(json.dumps({{"marker": MARKER, "status": status, "argv": sys.argv,
                   "flag": os.environ.get(freshness.RE_EXEC_ENV)}}))
'''
    core = str(REPO_ROOT / "scripts" / "core")
    (origin / "run.py").write_text(runner.format(core=core, marker="OLD-CODE"))
    git(origin, "add", "-A")
    git(origin, "commit", "-qm", "one")

    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
    assert "OLD-CODE" in (clone / "run.py").read_text()

    (origin / "run.py").write_text(runner.format(core=core, marker="NEW-CODE-FROM-THE-PULL"))
    git(origin, "add", "-A")
    git(origin, "commit", "-qm", "two")

    proc = subprocess.run([sys.executable, str(clone / "run.py"), "--full", "--deploy"],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])

    # The process that FINISHED is running the pulled code, not the code it began with.
    assert out["marker"] == "NEW-CODE-FROM-THE-PULL", (
        "the re-exec did not happen, or ran the pre-pull code")
    # It re-execed exactly once and the flag stopped it looping.
    assert out["flag"] == "1"
    assert out["status"] == "already re-execed this run"
    # Arguments and the script path survived the replacement.
    assert out["argv"][1:] == ["--full", "--deploy"]
    assert out["argv"][0].endswith("run.py")


# --- the heartbeat wiring: the fix for the finding that mattered most -----

def _sync_result(**kw):
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from matter.state import utcnow
    from matter.sync import SyncResult
    return SyncResult(started_at=utcnow(), **kw)


def test_the_freshness_status_survives_the_heartbeat_json_round_trip():
    """The whole point of moving this out of the log. A field that can silently
    stop being written, in the feature built to eliminate silent staleness, is
    the last place to leave untested."""
    r = _sync_result(freshness="stale: 3 modified file(s) on main")
    assert json.loads(json.dumps(r.as_dict()))["freshness"] == \
        "stale: 3 modified file(s) on main"


def test_a_run_that_skipped_the_check_says_so_rather_than_nothing():
    assert _sync_result().as_dict()["freshness"] == "not checked"


def test_the_failure_heartbeat_carries_the_status_of_ITS_OWN_run(tmp_path, monkeypatch):
    """A module-level global made an early-failure heartbeat stamp the PREVIOUS
    run's status — a heartbeat asserting something false about the run it
    describes, in the feature built to stop exactly that."""
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "core"))
    import export_matter_to_archive as cli

    hb = tmp_path / "hb.json"
    args = cli.build_parser().parse_args(["--heartbeat", str(hb)])
    cli._record_failure(args, "boom", "stale: 2 modified file(s) on main")
    assert json.loads(hb.read_text())["freshness"] == "stale: 2 modified file(s) on main"

    cli._record_failure(args, "boom again")      # a later run that did not check
    assert json.loads(hb.read_text())["freshness"] == "not checked", (
        "this run reported a previous run's freshness")


# --- the gates, exercised rather than parsed -----------------------------

@pytest.mark.parametrize("argv, should_run", [
    ([], True),
    (["--no-freshness-check"], False),
    (["--dry-run"], False),
    (["--full", "--deploy"], True),
])
def test_the_check_is_gated_by_the_flags_that_claim_to_gate_it(argv, should_run, monkeypatch):
    """The previous version asserted argparse set an attribute, which left the
    BRANCH unexercised — mutating the condition to `if True:` stayed green."""
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "core"))
    import export_matter_to_archive as cli

    called = []
    monkeypatch.setattr(cli.freshness, "ensure_fresh",
                        lambda *a, **k: called.append(a) or "current (main)")
    monkeypatch.setattr(cli, "resolve_vault_path",
                        lambda *_a, **_k: (_ for _ in ()).throw(SystemExit(0)))
    with pytest.raises(SystemExit):
        cli.main(argv + ["--no-heartbeat"])
    assert bool(called) is should_run


def test_the_normal_heartbeat_carries_the_status_too(tmp_path, monkeypatch):
    """The success path, which the SyncResult-level tests above cannot see:
    they build the object directly, so removing `result.freshness = ...` from
    main() left every one of them green while the nightly's ordinary heartbeat
    silently lost the field."""
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "core"))
    import export_matter_to_archive as cli
    from matter.state import utcnow
    from matter.sync import SyncResult

    hb = tmp_path / "hb.json"
    monkeypatch.setattr(cli.freshness, "ensure_fresh",
                        lambda *a, **k: "stale: 4 modified file(s) on main")
    monkeypatch.setattr(cli, "resolve_vault_path", lambda *_a, **_k: tmp_path)
    monkeypatch.setattr(cli, "run_sync",
                        lambda _c: SyncResult(started_at=utcnow(), finished_at=utcnow(),
                                              outcome="ok"))
    monkeypatch.setattr(cli, "run_post_sync_legs", lambda *_a, **_k: {})

    cli.main(["--heartbeat", str(hb)])
    assert json.loads(hb.read_text())["freshness"] == "stale: 4 modified file(s) on main", (
        "the ordinary nightly heartbeat lost the freshness status")


# --- fail-open across the states a real checkout actually reaches ---------
#
# The tests above cover the states I thought of: detached HEAD, no origin,
# branch not on origin, unreachable origin, git missing, git timing out. This
# block covers the nine an actual working repo reaches that I did not think of
# — ported from a reviewer's sweep, which found zero raises across all of them
# and is the strongest evidence the fail-open claim holds.
#
# The claim under test is narrow and absolute: ensure_fresh RETURNS A STRING
# for every one of these. It sits at the head of a chain that walks an hour of
# SMB, so a convenience that can abort that is worse than no convenience.

def _base_repo(tmp):
    origin = tmp / "origin"
    origin.mkdir()
    git(origin, "init", "-q", "--initial-branch=main")
    git(origin, "config", "user.email", "t@example.com")
    git(origin, "config", "user.name", "T")
    (origin / "code.py").write_text("V=1\n")
    git(origin, "add", "-A")
    git(origin, "commit", "-qm", "one")
    clone = tmp / "clone"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
    git(clone, "config", "user.email", "t@example.com")
    git(clone, "config", "user.name", "T")
    return origin, clone


def _soft(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def _mid_merge(tmp):
    _o, c = _base_repo(tmp)
    git(c, "checkout", "-qb", "side")
    (c / "code.py").write_text("V=side\n")
    git(c, "commit", "-qam", "side")
    git(c, "checkout", "-q", "main")
    (c / "code.py").write_text("V=main\n")
    git(c, "commit", "-qam", "main2")
    _soft(c, "merge", "side")                      # leaves a conflict
    return c


def _mid_rebase(tmp):
    _o, c = _base_repo(tmp)
    git(c, "checkout", "-qb", "side")
    (c / "code.py").write_text("V=side\n")
    git(c, "commit", "-qam", "side")
    git(c, "checkout", "-q", "main")
    (c / "code.py").write_text("V=main\n")
    git(c, "commit", "-qam", "main2")
    _soft(c, "rebase", "side")                     # stops on conflict, detached
    return c


def _shallow(tmp):
    o, _c = _base_repo(tmp)
    advance(o)
    c = tmp / "shallow"
    subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{o}", str(c)], check=True)
    advance(o, "V=3\n")
    return c


def _worktree_detached(tmp):
    o, c = _base_repo(tmp)
    advance(o)
    git(c, "fetch", "-q", "origin")
    wt = tmp / "wt"
    git(c, "worktree", "add", "-q", "--detach", str(wt))
    return wt


def _worktree_branch(tmp):
    o, c = _base_repo(tmp)
    advance(o)
    wt = tmp / "wt2"
    git(c, "worktree", "add", "-q", "-b", "wtbranch", str(wt))
    return wt


def _stale_lock(tmp):
    o, c = _base_repo(tmp)
    advance(o)
    (c / ".git" / "index.lock").write_text("")
    return c


def _bare(tmp):
    b = tmp / "bare.git"
    b.mkdir()
    git(b, "init", "-q", "--bare", "--initial-branch=main")
    return b


def _unborn(tmp):
    e = tmp / "empty"
    e.mkdir()
    git(e, "init", "-q", "--initial-branch=main")
    return e


def _readonly_git(tmp):
    o, c = _base_repo(tmp)
    advance(o)
    for p in sorted((c / ".git").rglob("*"), reverse=True):
        try:
            p.chmod(0o500 if p.is_dir() else 0o400)
        except OSError:
            pass
    (c / ".git").chmod(0o500)
    return c


def _renamed_upstream(tmp):
    o, c = _base_repo(tmp)
    advance(o)
    git(c, "checkout", "-qb", "deploy")
    git(c, "branch", "--set-upstream-to=origin/main", "deploy")
    return c


@pytest.mark.parametrize("build", [
    _mid_merge, _mid_rebase, _shallow, _worktree_detached, _worktree_branch,
    _stale_lock, _bare, _unborn, _readonly_git, _renamed_upstream,
], ids=[
    "mid-merge-conflict", "mid-rebase-stopped", "shallow-clone",
    "git-worktree-detached", "git-worktree-on-branch", "stale-index-lock",
    "bare-repo", "unborn-branch", "read-only-dot-git", "renamed-upstream",
])
def test_no_repo_state_makes_the_check_raise(tmp_path, build):
    """Every one of these must ANSWER. A raise here costs the night."""
    repo = build(tmp_path)
    status = freshness.ensure_fresh(repo)
    assert isinstance(status, str) and status, f"empty status for {repo}"
    # And it must never silently claim to be current when it could not check.
    assert status.startswith(("current", "stale", "unknown", "pulled", "skipped")), status
