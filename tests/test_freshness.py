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
    the pull fixes the NEXT night while reporting success for tonight."""
    origin, clone = origin_and_clone
    advance(origin)
    calls = []
    monkeypatch.setattr(freshness.os, "execv", lambda exe, argv: calls.append((exe, argv)))

    with pytest.raises(AssertionError):        # execv is mocked, so we fall through
        freshness.ensure_fresh(clone, argv=["x.py", "--full"])

    assert calls, "pulled without re-execing — tonight would run the old code"
    assert calls[0][1][1:] == ["x.py", "--full"], "re-exec lost the original arguments"


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


def test_a_branch_with_no_upstream_names_that_reason(origin_and_clone):
    """A nightly running from an unpushed feature branch is a real state. It
    must not be reported as 'not a git checkout', which sends the operator
    looking for a broken install."""
    _origin, clone = origin_and_clone
    git(clone, "checkout", "-qb", "feature/local-only")
    status = freshness.ensure_fresh(clone)
    assert "feature/local-only" in status and status.startswith("unknown")


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
