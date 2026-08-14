"""The credential contract: ~/.secrets/matter.token, 0600, loud on failure."""

import json

import pytest

from matter.credentials import (
    DEFAULT_TOKEN_PATH,
    load_token,
    looks_like_matter_token,
    redact,
    token_path,
)
from matter.errors import MatterCredentialError


def test_default_path_is_the_documented_one():
    assert str(DEFAULT_TOKEN_PATH) == "~/.secrets/matter.token"


def test_env_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("MATTER_TOKEN_FILE", str(tmp_path / "elsewhere.token"))
    assert token_path() == tmp_path / "elsewhere.token"


def test_reads_a_bare_token(token_file):
    assert load_token(token_file) == "mat_testtoken0123456789abcdef"


def test_strips_the_trailing_newline_a_shell_redirect_leaves(tmp_path):
    path = tmp_path / "matter.token"
    path.write_text("mat_abc123\n")
    path.chmod(0o600)
    assert load_token(path) == "mat_abc123"


def test_accepts_json_with_an_api_token_key(tmp_path):
    path = tmp_path / "matter.token"
    path.write_text(json.dumps({"api_token": "mat_fromjson"}))
    path.chmod(0o600)
    assert load_token(path) == "mat_fromjson"


def test_missing_file_names_the_exact_path_and_how_to_fix_it(tmp_path):
    missing = tmp_path / "nope" / "matter.token"
    with pytest.raises(MatterCredentialError) as excinfo:
        load_token(missing)
    message = str(excinfo.value)
    assert str(missing) in message
    assert "web.getmatter.com/settings" in message
    assert "chmod 600" in message


def test_empty_file_fails_loudly(tmp_path):
    path = tmp_path / "matter.token"
    path.write_text("")
    path.chmod(0o600)
    with pytest.raises(MatterCredentialError, match="is empty"):
        load_token(path)


def test_whitespace_only_file_fails_loudly(tmp_path):
    path = tmp_path / "matter.token"
    path.write_text("   \n\t\n")
    path.chmod(0o600)
    with pytest.raises(MatterCredentialError, match="is empty"):
        load_token(path)


def test_group_or_world_readable_is_refused(tmp_path):
    path = tmp_path / "matter.token"
    path.write_text("mat_abc123")
    path.chmod(0o644)
    with pytest.raises(MatterCredentialError) as excinfo:
        load_token(path)
    assert "chmod 600" in str(excinfo.value)


def test_read_only_0400_is_accepted(tmp_path):
    """0400 is stricter than 0600, not looser; refusing it would be wrong."""
    path = tmp_path / "matter.token"
    path.write_text("mat_abc123")
    path.chmod(0o400)
    assert load_token(path) == "mat_abc123"


def test_permission_check_can_be_waived(tmp_path):
    path = tmp_path / "matter.token"
    path.write_text("mat_abc123")
    path.chmod(0o644)
    assert load_token(path, require_secure_perms=False) == "mat_abc123"


def test_a_shell_export_line_is_rejected_rather_than_used(tmp_path):
    """`export MATTER_API_TOKEN=mat_x` in the file would otherwise be sent as the token."""
    path = tmp_path / "matter.token"
    path.write_text("export MATTER_API_TOKEN=mat_abc123")
    path.chmod(0o600)
    with pytest.raises(MatterCredentialError, match="whitespace"):
        load_token(path)


def test_directory_instead_of_file_fails_loudly(tmp_path):
    path = tmp_path / "matter.token"
    path.mkdir()
    with pytest.raises(MatterCredentialError, match="is a directory"):
        load_token(path)


def test_json_without_a_recognised_key_fails_loudly(tmp_path):
    path = tmp_path / "matter.token"
    path.write_text(json.dumps({"unexpected": "mat_abc"}))
    path.chmod(0o600)
    with pytest.raises(MatterCredentialError, match="none of the expected keys"):
        load_token(path)


def test_prefix_check_is_advisory():
    assert looks_like_matter_token("mat_abc")
    assert not looks_like_matter_token("abc")


def test_redaction_never_prints_the_middle():
    token = "mat_abcdefghijklmnopqrstuvwxyz"
    redacted = redact(token)
    assert "ghijklmnop" not in redacted
    assert redacted.startswith("mat_abcd")


def test_the_sync_never_writes_the_token_file(token_file):
    """The whole no-refresh design rests on this: read-only, so no torn write."""
    before = token_file.read_bytes()
    before_mtime = token_file.stat().st_mtime_ns
    load_token(token_file)
    load_token(token_file)
    assert token_file.read_bytes() == before
    assert token_file.stat().st_mtime_ns == before_mtime
