"""Reading the Matter API credential.

Contract (fixed):
  * The credential lives at ~/.secrets/matter.token, mode 0600.
  * A missing or empty file fails loudly, naming that exact path.
  * The sync NEVER writes to this file.

That last point is worth stating plainly, because it is a deliberate narrowing
of the original brief. Matter's public API v1 authenticates with a long-lived
personal access token (`mat_...`) and has no refresh endpoint -- the OpenAPI
spec at https://docs.getmatter.com/openapi.yaml declares exactly one security
scheme, `bearerAuth`, and no token/refresh path. Because there is nothing to
refresh, there is no rewrite, and therefore no window in which a crash mid-write
can destroy Adam's only credential. The safest way to survive that hazard is to
not have it.

(The older, undocumented v11 API behind Matter's Obsidian plugin does use
access + refresh tokens and does hand back a new refresh token on every
exchange. That surface has no incremental-sync parameter, which makes it a poor
fit for a nightly delta job; see docs/MATTER_SYNC.md for the full comparison.)
"""

import json
import os
import stat
from pathlib import Path

from .errors import MatterCredentialError

DEFAULT_TOKEN_PATH = Path("~/.secrets/matter.token")

# Keys accepted when the file holds JSON rather than a bare token, in priority
# order. `access_token` is last: it is the v11 spelling, and finding one there
# usually means the wrong credential was pasted in.
_JSON_TOKEN_KEYS = ("api_token", "token", "matter_token", "access_token")

_HOW_TO_GET_ONE = (
    "Generate one at https://web.getmatter.com/settings -> 'Generate API Token' "
    "(requires an active Matter Pro subscription), then:\n"
    "    mkdir -p ~/.secrets\n"
    "    printf '%s' 'mat_your_token_here' > ~/.secrets/matter.token\n"
    "    chmod 600 ~/.secrets/matter.token\n"
    "Note that Matter allows only one active token per account: generating a new "
    "one silently revokes the previous one (including the Matter CLI's)."
)


def token_path() -> Path:
    """Where the credential is read from.

    MATTER_TOKEN_FILE overrides the default. It exists so the tests can point at
    a fixture; the nightly job uses the default.
    """
    override = os.environ.get("MATTER_TOKEN_FILE")
    return Path(override).expanduser() if override else DEFAULT_TOKEN_PATH.expanduser()


def _check_permissions(path: Path, require_secure_perms: bool) -> None:
    if not require_secure_perms:
        return
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise MatterCredentialError(
            f"Matter token file {path} is readable by group or others "
            f"(mode {mode:04o}). This is an API credential; refusing to use it.\n"
            f"Fix it with:\n    chmod 600 {path}"
        )


def _extract_from_json(raw: str, path: Path) -> str:
    try:
        payload = json.loads(raw)
    except ValueError:
        # Not JSON, so treat the whole file as a bare token. That is the normal
        # case; only text that parses as a JSON object is handled below.
        return raw.strip()

    if not isinstance(payload, dict):
        return raw.strip()

    for key in _JSON_TOKEN_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    raise MatterCredentialError(
        f"Matter token file {path} contains JSON but none of the expected keys "
        f"({', '.join(_JSON_TOKEN_KEYS)}) held a token.\n{_HOW_TO_GET_ONE}"
    )


def load_token(path: Path | None = None, *, require_secure_perms: bool = True) -> str:
    """Return the bearer token, or raise MatterCredentialError explaining why not.

    Accepts either a bare token on one line or a JSON object -- Matter's own
    tooling hands the token over as plain text, but pasting a JSON blob is a
    natural mistake and it costs nothing to accept it.
    """
    path = Path(path).expanduser() if path is not None else token_path()

    if not path.exists():
        raise MatterCredentialError(
            f"Matter token file not found: {path}\n"
            f"The nightly sync cannot authenticate without it.\n{_HOW_TO_GET_ONE}"
        )
    if path.is_dir():
        raise MatterCredentialError(
            f"Matter token path {path} is a directory, not a file.\n{_HOW_TO_GET_ONE}"
        )

    _check_permissions(path, require_secure_perms)

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MatterCredentialError(f"Could not read Matter token file {path}: {exc}") from exc

    token = _extract_from_json(raw, path)

    if not token:
        raise MatterCredentialError(
            f"Matter token file {path} is empty.\n{_HOW_TO_GET_ONE}"
        )
    if any(ch.isspace() for ch in token):
        raise MatterCredentialError(
            f"Matter token file {path} contains whitespace inside the token, which "
            f"means the file holds something other than a single token "
            f"(a shell export line, or several tokens).\n{_HOW_TO_GET_ONE}"
        )

    return token


def looks_like_matter_token(token: str) -> bool:
    """Whether the token has the documented `mat_` prefix.

    Callers warn on False rather than refusing: the prefix is a documented
    convention, not something the API promises to keep forever, and refusing a
    working credential over a naming convention would be the worse failure.
    """
    return token.startswith("mat_")


def redact(token: str) -> str:
    """A form of the token that is safe to print in logs."""
    if len(token) <= 12:
        return "mat_***"
    return f"{token[:8]}...{token[-4:]}"
