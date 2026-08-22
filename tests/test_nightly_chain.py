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
    _good_site(tmp_path)
    monkeypatch.setattr(sync_module.shutil, "which", lambda _n: None)
    monkeypatch.setattr(sync_module.Path, "home", classmethod(lambda cls: tmp_path / "nohome"))
    assert sync_module.deploy_site(tmp_path) is False


def test_deploy_falls_back_to_volta_when_not_on_path(tmp_path, monkeypatch):
    # The launchd gotcha: minimal PATH, wrangler only under ~/.volta/bin.
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
    _good_site(tmp_path)
    monkeypatch.setattr(sync_module.shutil, "which", lambda _n: "/usr/bin/wrangler")
    monkeypatch.setattr(sync_module.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "auth error"))
    assert sync_module.deploy_site(tmp_path) is False


def test_deploy_survives_a_timeout_without_raising(tmp_path, monkeypatch):
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
