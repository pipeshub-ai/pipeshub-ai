"""The SMB helper's listing, against the errors smbprotocol really raises.

smbprotocol reports a missing path as ``SMBOSError``, an ``OSError`` with
errno 2 that is never Python's ``FileNotFoundError`` subclass. Catching only
``FileNotFoundError`` let a listing of a folder that does not exist yet (the
lifecycle's access check) escape as an error, so every SMB test errored at
setup against a share that was there all along.
"""

from __future__ import annotations

import pytest
from smbprotocol.exceptions import LogonFailure, SMBOSError
from smbprotocol.header import NtStatus

from connectors.smb import smb_storage_helper
from connectors.smb.smb_storage_helper import SmbStorageHelper

pytestmark = pytest.mark.unit


def _helper() -> SmbStorageHelper:
    helper = object.__new__(SmbStorageHelper)
    helper.server, helper.port, helper._cache = "smb.test", 445, {}
    return helper


def _scandir_raising(error: Exception):
    def scandir(path, **_):
        raise error

    return scandir


@pytest.mark.parametrize(
    "status",
    [NtStatus.STATUS_OBJECT_NAME_NOT_FOUND, NtStatus.STATUS_OBJECT_PATH_NOT_FOUND],
    ids=["missing folder", "missing parent folder"],
)
def test_a_folder_that_does_not_exist_lists_as_empty(monkeypatch, status) -> None:
    error = SMBOSError(status, r"\\smb.test\share\it-access-check")
    assert not isinstance(error, FileNotFoundError)
    monkeypatch.setattr(smb_storage_helper.smbclient, "scandir", _scandir_raising(error))
    assert _helper().list_objects("share", "it-access-check/") == []


@pytest.mark.parametrize(
    "error",
    [
        SMBOSError(NtStatus.STATUS_BAD_NETWORK_NAME, r"\\smb.test\missing-share\x"),
        SMBOSError(NtStatus.STATUS_ACCESS_DENIED, r"\\smb.test\share\x"),
        LogonFailure(),
    ],
    ids=["missing share", "access denied", "wrong password"],
)
def test_a_share_that_cannot_be_read_still_fails(monkeypatch, error) -> None:
    """The access check depends on these reaching it."""
    monkeypatch.setattr(smb_storage_helper.smbclient, "scandir", _scandir_raising(error))
    with pytest.raises(type(error)):
        _helper().list_objects("share", "it-access-check/")
