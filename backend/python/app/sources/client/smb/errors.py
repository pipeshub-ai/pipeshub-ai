"""Plain-language descriptions of what smbprotocol raises, for notices and the connection check.

smbprotocol's own text ("Received unexpected status from the server: ... (3221225581)
STATUS_LOGON_FAILURE: 0xc000006d") names protocol codes a user cannot act on. The raw error
still goes to the logs; what is shown is one of the sentences below.
"""

from __future__ import annotations

import socket

# The server refused the user name or password.
_WRONG_CREDENTIALS = frozenset({
    0xC000006D,  # STATUS_LOGON_FAILURE
    0xC000006A,  # STATUS_WRONG_PASSWORD
    0xC0000064,  # STATUS_NO_SUCH_USER
})
# The credentials are right but the account may not sign in now.
_ACCOUNT_BLOCKED = frozenset({
    0xC000006E,  # STATUS_ACCOUNT_RESTRICTION
    0xC000006F,  # STATUS_INVALID_LOGON_HOURS
    0xC0000070,  # STATUS_INVALID_WORKSTATION
    0xC0000071,  # STATUS_PASSWORD_EXPIRED
    0xC0000072,  # STATUS_ACCOUNT_DISABLED
    0xC0000193,  # STATUS_ACCOUNT_EXPIRED
    0xC0000224,  # STATUS_PASSWORD_MUST_CHANGE
    0xC0000234,  # STATUS_ACCOUNT_LOCKED_OUT
})
SIGN_IN_REFUSED = _WRONG_CREDENTIALS | _ACCOUNT_BLOCKED
_BAD_NETWORK_NAME = 0xC00000CC
_ACCESS_DENIED = 0xC0000022


def nt_status(exc: BaseException) -> int | None:
    """The NT status smbclient (``ntstatus``) or smbprotocol (``status``) put on an error, if any."""
    for attr in ("ntstatus", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0xFFFF:
            return value
    return None


def is_sign_in_refusal(exc: BaseException) -> bool:
    """True when the server refused the account itself, not a file or a folder."""
    return nt_status(exc) in SIGN_IN_REFUSED


def describe(exc: BaseException, *, server: str, share: str | None = None, port: int = 445) -> str:
    """One sentence a user can act on."""
    status = nt_status(exc)
    if status in _WRONG_CREDENTIALS:
        return f"{server} refused the user name or password. Check them in the connector settings."
    if status in _ACCOUNT_BLOCKED:
        return (
            f"{server} accepted the password but the account may not sign in: it is disabled, locked, "
            "expired, or its password must be changed."
        )
    if status == _BAD_NETWORK_NAME or (share and isinstance(exc, FileNotFoundError)):
        return f'{server} has no share named "{share}", or the account cannot see it.'
    if status == _ACCESS_DENIED:
        return f'The account may not open the share "{share}" on {server}.' if share else (
            f"{server} denied the account access."
        )
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return f"{server} did not answer on port {port} in time."
    if isinstance(exc, (ConnectionError, socket.gaierror)) or _is_connect_failure(exc):
        return f"Could not reach {server} on port {port}. Check the server name and that the port is open."
    return f"{server} returned an error. The connector logs have the details."


def _is_connect_failure(exc: BaseException) -> bool:
    # smbprotocol reports a refused or unreachable address as ValueError("Failed to connect to ...").
    return isinstance(exc, ValueError) and str(exc).startswith("Failed to connect")
