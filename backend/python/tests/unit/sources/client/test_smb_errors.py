import socket

import pytest
from smbprotocol.exceptions import SMBOSError

from app.sources.client.smb.errors import describe, is_sign_in_refusal, nt_status


class _Refused(Exception):
    """What smbprotocol raises at session set-up: the NT status on ``status``."""

    def __init__(self, status: int) -> None:
        super().__init__(f"Received unexpected status from the server: ({status}) 0x{status:08x}")
        self.status = status


@pytest.mark.parametrize("status", [0xC000006D, 0xC000006A, 0xC0000064])
def test_a_refused_password_names_the_server_and_the_fix(status: int) -> None:
    message = describe(_Refused(status), server="files.example", share="dept")
    assert message == "files.example refused the user name or password. Check them in the connector settings."
    assert is_sign_in_refusal(_Refused(status))


@pytest.mark.parametrize("status", [0xC0000072, 0xC0000234, 0xC0000071, 0xC0000193, 0xC0000224])
def test_a_blocked_account_says_so(status: int) -> None:
    assert "may not sign in" in describe(_Refused(status), server="files.example")
    assert is_sign_in_refusal(_Refused(status))


def test_smbclient_errors_carry_the_status_on_ntstatus() -> None:
    error = SMBOSError(0xC000006D, r"\\10.0.0.5\dept")
    assert nt_status(error) == 0xC000006D
    assert is_sign_in_refusal(error)


def test_a_missing_share_is_named() -> None:
    assert describe(_Refused(0xC00000CC), server="files.example", share="dept") == (
        'files.example has no share named "dept", or the account cannot see it.'
    )
    assert "dept" in describe(FileNotFoundError(r"\\10.0.0.5\dept"), server="files.example", share="dept")


def test_an_unreachable_server_names_the_port() -> None:
    assert describe(ValueError("Failed to connect to '10.0.0.5:445': refused"), server="fs", port=445) == (
        "Could not reach fs on port 445. Check the server name and that the port is open."
    )
    assert "did not answer" in describe(socket.timeout("timed out"), server="fs")


@pytest.mark.parametrize(
    "error",
    [
        _Refused(0xC000009A),
        SMBOSError(0xC00000C9, r"\\10.0.0.5\dept"),
        RuntimeError("Received unexpected status from the server: STATUS_FOO 0xc0001234"),
    ],
)
def test_no_protocol_internals_reach_the_user(error: Exception) -> None:
    message = describe(error, server="fs", share="dept")
    for internal in ("STATUS_", "0xc0", "NtStatus", "\\\\", "Received unexpected"):
        assert internal not in message


def test_access_denied_is_not_a_sign_in_refusal() -> None:
    # A folder the account may not list is not the account being refused.
    assert not is_sign_in_refusal(SMBOSError(0xC0000022, r"\\10.0.0.5\dept\hr"))
