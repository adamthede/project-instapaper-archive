"""Exceptions for the Matter sync.

Every one of these carries a message that names the exact thing to fix. The
nightly job is unattended, so a failure that does not say what to do is a
failure that gets rediscovered from scratch weeks later.
"""


class MatterError(Exception):
    """Base for every failure this package raises deliberately."""


class MatterCredentialError(MatterError):
    """The token file is missing, empty, unreadable, or world-readable."""


class MatterAuthError(MatterError):
    """The API rejected the token (HTTP 401)."""


class MatterForbiddenError(MatterError):
    """The API accepted the token but refused the request (HTTP 403).

    Most commonly: the account has no active Matter Pro subscription, which the
    public API requires.
    """


class MatterAPIError(MatterError):
    """An API call failed and retries did not recover it."""


class VaultNotFoundError(MatterError):
    """The vault directory does not exist.

    Usually the external SSD holding the archive is not mounted. We never create
    the vault: doing so would silently write a second, empty archive onto the
    mount point and the real one would appear to have lost everything.
    """
