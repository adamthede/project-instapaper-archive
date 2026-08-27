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
    with pytest.raises(AssertionError, match="unreachable"):
        freshness.ensure_fresh(clone, argv=[])
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
