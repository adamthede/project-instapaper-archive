"""The nightly chain: leg ordering, heartbeat honesty, and publish safety.

The defect these pin: until 2026-08-21 the heartbeat was written BEFORE the
enrich and rebuild legs, so a failed index rebuild reported a green night.
Adding a deploy leg made that untenable - a silently stale website is exactly
what the cockpit exists to catch.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from matter import sync as sync_module  # noqa: E402


# ---- interpreter selection -------------------------------------------------

def test_venv_python_prefers_the_repo_venv(tmp_path):
    venv = tmp_path / ".venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "python").write_text("#!/bin/sh\n")
    assert sync_module._venv_python(tmp_path) == venv / "python"


def test_venv_python_returns_none_rather_than_the_tcc_interpreter(tmp_path):
    # The launchd interpreter has no pandas; falling back to it would fail
    # confusingly on an import rather than clearly on a missing venv.
    assert sync_module._venv_python(tmp_path) is None


def test_venv_python_allows_current_only_when_asked(tmp_path):
    assert sync_module._venv_python(tmp_path, allow_current=True) == Path(sys.executable)


def test_venv_python_honours_the_override(tmp_path, monkeypatch):
    override = tmp_path / "custom-python"
    override.write_text("#!/bin/sh\n")
    monkeypatch.setenv("MATTER_INDEX_PYTHON", str(override))
    assert sync_module._venv_python(tmp_path) == override


# ---- deploy refuses to publish the wrong thing -----------------------------

@pytest.fixture(autouse=True)
def _no_accidental_publishing(monkeypatch):
    """Belt and braces for the 2026-08-21 incident: no test in this file may
    publish unless it opts in the way the nightly does."""
    monkeypatch.delenv(sync_module.DEPLOY_OPT_IN_ENV, raising=False)


def _allow_deploy(monkeypatch):
    monkeypatch.setenv(sync_module.DEPLOY_OPT_IN_ENV, "1")


def _good_site(repo: Path) -> Path:
    site = repo / sync_module.SITE_DIR_NAME
    site.mkdir(parents=True)
    (site / "index.html").write_text("<!DOCTYPE html>")
    (site / sync_module.SITE_MARKER).write_text("generated\n")
    return site


def test_deploy_refuses_a_missing_site(tmp_path):
    assert sync_module.deploy_site(tmp_path) is False


def test_deploy_refuses_a_directory_without_the_generator_marker(tmp_path):
    site = tmp_path / sync_module.SITE_DIR_NAME
    site.mkdir()
    (site / "index.html").write_text("someone else's site")
    assert sync_module.deploy_site(tmp_path) is False


def test_deploy_refuses_a_marked_dir_with_no_index(tmp_path):
    site = tmp_path / sync_module.SITE_DIR_NAME
    site.mkdir()
    (site / sync_module.SITE_MARKER).write_text("generated\n")
    assert sync_module.deploy_site(tmp_path) is False


def test_deploy_fails_loudly_when_wrangler_is_absent(tmp_path, monkeypatch):
    _allow_deploy(monkeypatch)
    _good_site(tmp_path)
    monkeypatch.setattr(sync_module.shutil, "which", lambda _n: None)
    monkeypatch.setattr(sync_module.Path, "home", classmethod(lambda cls: tmp_path / "nohome"))
    assert sync_module.deploy_site(tmp_path) is False


def test_deploy_falls_back_to_volta_when_not_on_path(tmp_path, monkeypatch):
    # The launchd gotcha: minimal PATH, wrangler only under ~/.volta/bin.
    _allow_deploy(monkeypatch)
    _good_site(tmp_path)
    volta = tmp_path / "home" / ".volta" / "bin"
    volta.mkdir(parents=True)
    (volta / "wrangler").write_text("#!/bin/sh\n")
    monkeypatch.setattr(sync_module.shutil, "which", lambda _n: None)
    monkeypatch.setattr(sync_module.Path, "home", classmethod(lambda cls: tmp_path / "home"))
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "Deployment complete", "")

    monkeypatch.setattr(sync_module.subprocess, "run", fake_run)
    assert sync_module.deploy_site(tmp_path) is True
    assert seen["cmd"][0] == str(volta / "wrangler")
    assert "--project-name" in seen["cmd"]


def test_deploy_reports_failure_on_nonzero_exit(tmp_path, monkeypatch):
    _allow_deploy(monkeypatch)
    _good_site(tmp_path)
    monkeypatch.setattr(sync_module.shutil, "which", lambda _n: "/usr/bin/wrangler")
    monkeypatch.setattr(sync_module.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "auth error"))
    assert sync_module.deploy_site(tmp_path) is False


def test_deploy_survives_a_timeout_without_raising(tmp_path, monkeypatch):
    _allow_deploy(monkeypatch)
    _good_site(tmp_path)
    monkeypatch.setattr(sync_module.shutil, "which", lambda _n: "/usr/bin/wrangler")

    def boom(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1800)

    monkeypatch.setattr(sync_module.subprocess, "run", boom)
    assert sync_module.deploy_site(tmp_path) is False


# ---- rebuild_site ----------------------------------------------------------

def test_rebuild_site_needs_the_generator(tmp_path):
    assert sync_module.rebuild_site(tmp_path) is False


def test_rebuild_site_invokes_generate_with_the_site_dir(tmp_path, monkeypatch):
    (tmp_path / "site").mkdir()
    (tmp_path / "site" / "generate.py").write_text("")
    venv = tmp_path / ".venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "python").write_text("")
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "Rendered 826 week pages", "")

    monkeypatch.setattr(sync_module.subprocess, "run", fake_run)
    assert sync_module.rebuild_site(tmp_path) is True
    assert seen["cmd"][-2:] == ["--out", sync_module.SITE_DIR_NAME]


# ---- the heartbeat now covers the whole night ------------------------------
class _Args:
    """The flags run_post_sync_legs reads; the nightly's shape by default."""
    def __init__(self, **kw):
        self.dry_run = False
        self.enrich_local = True
        self.rebuild_index = True
        self.rebuild_site = True
        self.deploy = True
        self.__dict__.update(kw)


@pytest.fixture
def exporter(monkeypatch):
    """The real CLI module, with each leg swappable."""
    import importlib.util
    path = Path(__file__).resolve().parents[1] / "scripts" / "core" / "export_matter_to_archive.py"
    spec = importlib.util.spec_from_file_location("exporter_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _result(**kw):
    from matter.state import utcnow
    r = sync_module.SyncResult(started_at=utcnow(), outcome="ok")
    for k, v in kw.items():
        setattr(r, k, v)
    return r


def _stub(exporter, monkeypatch, **outcomes):
    for name, default in (("enrich_local", False), ("rebuild_index", True),
                          ("rebuild_site", True), ("deploy_site", True)):
        monkeypatch.setattr(exporter.sync_module, name,
                            lambda _r, _v=outcomes.get(name, default): _v)


def test_all_legs_green_reports_every_leg(exporter, monkeypatch):
    _stub(exporter, monkeypatch, enrich_local=True)
    legs = exporter.run_post_sync_legs(_Args(), _result(new=3))
    assert legs == {"enrich": "wrote", "rebuild_index": "ok",
                    "rebuild_site": "ok", "deploy": "ok"}


def test_a_failed_deploy_is_recorded_as_fail(exporter, monkeypatch):
    _stub(exporter, monkeypatch, deploy_site=False)
    legs = exporter.run_post_sync_legs(_Args(), _result(new=1))
    assert legs["deploy"] == "fail"
    assert any(v == "fail" for v in legs.values())


def test_lm_studio_down_never_fails_the_night(exporter, monkeypatch):
    # The deliberate exception: enrichment is non-fatal.
    _stub(exporter, monkeypatch, enrich_local=False)
    legs = exporter.run_post_sync_legs(_Args(), _result(new=1))
    assert legs["enrich"] == "no-writes"
    assert not any(v == "fail" for v in legs.values())


def test_a_failed_build_skips_the_deploy_rather_than_publishing_the_old_one(exporter, monkeypatch):
    published = []
    monkeypatch.setattr(exporter.sync_module, "enrich_local", lambda _r: False)
    monkeypatch.setattr(exporter.sync_module, "rebuild_index", lambda _r: True)
    monkeypatch.setattr(exporter.sync_module, "rebuild_site", lambda _r: False)
    monkeypatch.setattr(exporter.sync_module, "deploy_site",
                        lambda _r: published.append(1) or True)
    legs = exporter.run_post_sync_legs(_Args(), _result(new=1))
    assert published == [], "a stale build must never be published"
    assert legs["deploy"] == "skipped-build-failed"
    assert legs["rebuild_site"] == "fail"


def test_site_legs_run_even_when_the_sync_wrote_nothing(exporter, monkeypatch):
    # Sunday's weekly synthesis writes week files this sync never counts, so
    # gating the site on result.new would leave Monday's site missing them.
    _stub(exporter, monkeypatch)
    legs = exporter.run_post_sync_legs(_Args(), _result(new=0, updated=0))
    assert legs["rebuild_site"] == "ok" and legs["deploy"] == "ok"
    assert "rebuild_index" not in legs, "the index rebuild stays gated on real changes"


def test_dry_run_touches_nothing(exporter, monkeypatch):
    calls = []
    for name in ("enrich_local", "rebuild_index", "rebuild_site", "deploy_site"):
        monkeypatch.setattr(exporter.sync_module, name,
                            lambda _r, _n=name: calls.append(_n) or True)
    assert exporter.run_post_sync_legs(_Args(dry_run=True), _result(new=5)) == {}
    assert calls == []


def test_legs_not_requested_are_absent(exporter, monkeypatch):
    _stub(exporter, monkeypatch)
    legs = exporter.run_post_sync_legs(
        _Args(rebuild_site=False, deploy=False), _result(new=1))
    assert "rebuild_site" not in legs and "deploy" not in legs


# ---- main() itself: the claim "the heartbeat covers the whole night" -------
# Round-1 review: FOUR mutants survived because no test ever called main() -
# leg_failed detection, outcome="fail", the exit-code term and finished_at
# could all be reverted with the suite green. These call it for real.

@pytest.fixture
def main_harness(exporter, tmp_path, monkeypatch):
    """main() driven past the network, with the legs swappable."""
    from matter.state import utcnow
    hb = tmp_path / "hb.json"
    vault = tmp_path / "vault"
    (vault / "matter").mkdir(parents=True)
    monkeypatch.setenv("INSTAPAPER_VAULT_PATH", str(vault))

    state = {"result": None}

    def fake_run_sync(config, client=None):
        r = sync_module.SyncResult(started_at=utcnow(), outcome="ok")
        r.finished_at = utcnow()
        r.new = 1
        state["result"] = r
        return r

    monkeypatch.setattr(exporter, "run_sync", fake_run_sync)
    monkeypatch.setattr(exporter, "load_token", lambda *a, **k: "mat_x")
    monkeypatch.setattr(exporter, "looks_like_matter_token", lambda _t: True)
    monkeypatch.setattr(exporter, "MatterClient", lambda *a, **k: object())
    monkeypatch.setattr(sys, "argv", [
        "export_matter_to_archive.py", "--full", "--enrich-local",
        "--rebuild-index", "--rebuild-site", "--deploy",
        "--heartbeat", str(hb), "--quiet"])
    return exporter, hb, state


def test_main_writes_fail_to_the_heartbeat_when_the_deploy_fails(main_harness, monkeypatch):
    exporter, hb, _ = main_harness
    monkeypatch.setattr(exporter.sync_module, "enrich_local", lambda _r: False)
    monkeypatch.setattr(exporter.sync_module, "rebuild_index", lambda _r: True)
    monkeypatch.setattr(exporter.sync_module, "rebuild_site", lambda _r: True)
    monkeypatch.setattr(exporter.sync_module, "deploy_site", lambda _r: False)

    code = exporter.main()

    written = json.loads(hb.read_text())
    assert code == 1, "a failed publish must fail the process"
    assert written["outcome"] == "fail"
    assert written["legs"]["deploy"] == "fail"
    assert written["errors"] == 0, "the sync itself was clean; the night still failed"


def test_main_reports_ok_and_zero_when_every_leg_passes(main_harness, monkeypatch):
    exporter, hb, _ = main_harness
    for name in ("rebuild_index", "rebuild_site", "deploy_site"):
        monkeypatch.setattr(exporter.sync_module, name, lambda _r: True)
    monkeypatch.setattr(exporter.sync_module, "enrich_local", lambda _r: True)

    assert exporter.main() == 0
    written = json.loads(hb.read_text())
    assert written["outcome"] == "ok"
    assert written["legs"] == {"enrich": "wrote", "rebuild_index": "ok",
                               "rebuild_site": "ok", "deploy": "ok"}


def test_main_finished_at_covers_the_legs_not_just_the_sync(main_harness, monkeypatch):
    exporter, hb, state = main_harness
    import time as _t
    monkeypatch.setattr(exporter.sync_module, "enrich_local", lambda _r: False)
    monkeypatch.setattr(exporter.sync_module, "rebuild_index", lambda _r: True)
    monkeypatch.setattr(exporter.sync_module, "rebuild_site", lambda _r: True)
    monkeypatch.setattr(exporter.sync_module, "deploy_site",
                        lambda _r: (_t.sleep(1.1), True)[1])

    sync_finished = None
    exporter.main()
    sync_finished = state["result"]
    written = json.loads(hb.read_text())
    # A slow publish must show up in the night's duration, or the cockpit
    # reports a 20-minute night as a 90-second one.
    from datetime import datetime
    started = datetime.fromisoformat(written["started_at"].replace("Z", "+00:00"))
    finished = datetime.fromisoformat(written["finished_at"].replace("Z", "+00:00"))
    assert (finished - started).total_seconds() >= 1.0
    assert sync_finished is not None


def test_main_heartbeats_fail_when_a_leg_RAISES(main_harness, monkeypatch):
    """Round-1 review finding (a): an exception escaping a leg skipped the
    heartbeat write entirely, leaving yesterday's 'ok' on disk."""
    exporter, hb, _ = main_harness
    hb.write_text(json.dumps({"started_at": "2026-08-20T09:45:00Z",
                              "finished_at": "2026-08-20T10:45:00Z",
                              "outcome": "ok", "legs": {"deploy": "ok"}}))
    monkeypatch.setattr(exporter.sync_module, "enrich_local", lambda _r: False)

    def boom(_r):
        raise PermissionError("NAS went away mid-rebuild")

    monkeypatch.setattr(exporter.sync_module, "rebuild_index", boom)

    with pytest.raises(PermissionError):
        exporter.main()

    written = json.loads(hb.read_text())
    assert written["outcome"] == "fail", "yesterday's green must not survive a crash"
    assert "PermissionError" in json.dumps(written["legs"])
    assert "NAS went away" in (written["error"] or "")


# ---- the detection the skip-on-failed-build logic depends on ---------------

def test_rebuild_site_treats_a_nonzero_exit_as_failure(tmp_path, monkeypatch):
    """The surviving mutant that would cause a genuine silent stale publish:
    ignore generate.py's exit code and deploy ships the OLD site under a
    green heartbeat."""
    (tmp_path / "site").mkdir()
    (tmp_path / "site" / "generate.py").write_text("")
    venv = tmp_path / ".venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "python").write_text("")
    monkeypatch.setattr(sync_module.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 2, "", "boom"))
    assert sync_module.rebuild_site(tmp_path) is False


def test_rebuild_site_survives_a_timeout(tmp_path, monkeypatch):
    (tmp_path / "site").mkdir()
    (tmp_path / "site" / "generate.py").write_text("")
    venv = tmp_path / ".venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "python").write_text("")

    def boom(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 3600)

    monkeypatch.setattr(sync_module.subprocess, "run", boom)
    assert sync_module.rebuild_site(tmp_path) is False


def test_rebuild_index_survives_a_timeout(tmp_path, monkeypatch):
    """The one leg that let TimeoutExpired escape - and the most reachable
    trigger, since the 2026-08-21 run spent 53 minutes scanning the NAS."""
    (tmp_path / "scripts" / "core").mkdir(parents=True)
    (tmp_path / "scripts" / "core" / "build_index.py").write_text("")
    monkeypatch.setattr(sync_module, "resolve_vault_path", lambda: tmp_path)

    def boom(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 3600)

    monkeypatch.setattr(sync_module.subprocess, "run", boom)
    assert sync_module.rebuild_index(tmp_path) is False


def test_deploy_targets_the_configured_pages_project(tmp_path, monkeypatch):
    # The old test asserted "--project-name" was present, not its value, so a
    # typo'd destination passed.
    _allow_deploy(monkeypatch)
    _good_site(tmp_path)
    monkeypatch.setattr(sync_module.shutil, "which", lambda _n: "/usr/bin/wrangler")
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "ok", "")

    monkeypatch.setattr(sync_module.subprocess, "run", fake_run)
    assert sync_module.deploy_site(tmp_path) is True
    i = seen["cmd"].index("--project-name")
    assert seen["cmd"][i + 1] == sync_module.PAGES_PROJECT == "reading-adamthede"
    assert seen["cmd"][seen["cmd"].index("--branch") + 1] == "main"


def test_deploy_refuses_without_the_opt_in(tmp_path, monkeypatch):
    """The 2026-08-21 incident: an adversarial review probing the guards
    published its fixtures to the live site seven times, because reaching
    deploy_site at all is enough to publish."""
    _good_site(tmp_path)
    invoked = []
    monkeypatch.setattr(sync_module.shutil, "which", lambda _n: "/usr/bin/wrangler")
    monkeypatch.setattr(sync_module.subprocess, "run",
                        lambda cmd, **kw: invoked.append(cmd) or None)
    assert sync_module.deploy_site(tmp_path) is False
    assert invoked == [], "no wrangler call may happen without the opt-in"


def test_the_opt_in_must_be_exactly_one(tmp_path, monkeypatch):
    _good_site(tmp_path)
    monkeypatch.setattr(sync_module.shutil, "which", lambda _n: "/usr/bin/wrangler")
    monkeypatch.setattr(sync_module.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", ""))
    for value in ("", "0", "true", "yes"):
        monkeypatch.setenv(sync_module.DEPLOY_OPT_IN_ENV, value)
        assert sync_module.deploy_site(tmp_path) is False, value
