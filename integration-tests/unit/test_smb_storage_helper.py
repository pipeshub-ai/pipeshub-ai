"""The SMB helper's listing, against the errors smbprotocol really raises.

smbprotocol reports a missing path as ``SMBOSError``, an ``OSError`` with
errno 2 that is never Python's ``FileNotFoundError`` subclass. Catching only
``FileNotFoundError`` let a listing of a folder that does not exist yet (the
lifecycle's access check) escape as an error, so every SMB test errored at
setup against a share that was there all along.
"""

from __future__ import annotations

import errno

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


SHARE_ROOT = r"\\smb.test\share"


class _EmptyScan:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(())


def _scandir_raising(error: Exception, share_root: Exception | None = None):
    """``scandir`` that fails for the folder, and for the share root only when told to."""
    calls: list[str] = []

    def scandir(path, **_):
        calls.append(path)
        if path == SHARE_ROOT:
            if share_root is not None:
                raise share_root
            return _EmptyScan()
        raise error

    scandir.calls = calls
    return scandir


@pytest.mark.parametrize(
    "status",
    [NtStatus.STATUS_OBJECT_NAME_NOT_FOUND, NtStatus.STATUS_OBJECT_PATH_NOT_FOUND],
    ids=["missing folder", "missing parent folder"],
)
def test_a_folder_that_does_not_exist_lists_as_empty(monkeypatch, status) -> None:
    error = SMBOSError(status, SHARE_ROOT + r"\it-access-check")
    assert not isinstance(error, FileNotFoundError)
    scandir = _scandir_raising(error)
    monkeypatch.setattr(smb_storage_helper.smbclient, "scandir", scandir)
    assert _helper().list_objects("share", "it-access-check/") == []
    assert scandir.calls[-1] == SHARE_ROOT, "the share must be read before a folder is called missing"


@pytest.mark.parametrize(
    "share_error",
    [
        # How smbprotocol reports a missing share when the server offers DFS
        # and has no referral for it: the same errno as a missing folder.
        SMBOSError(NtStatus.STATUS_NOT_FOUND, SHARE_ROOT),
        SMBOSError(NtStatus.STATUS_OBJECT_PATH_NOT_FOUND, SHARE_ROOT),
    ],
    ids=["DFS lookup not found", "DFS path not found"],
)
def test_a_missing_share_is_not_an_empty_folder(monkeypatch, share_error) -> None:
    folder_error = SMBOSError(NtStatus.STATUS_NOT_FOUND, SHARE_ROOT + r"\it-access-check")
    assert folder_error.errno == share_error.errno == errno.ENOENT
    monkeypatch.setattr(
        smb_storage_helper.smbclient, "scandir", _scandir_raising(folder_error, share_root=share_error)
    )
    with pytest.raises(SMBOSError) as raised:
        _helper().list_objects("share", "it-access-check/")
    assert raised.value is share_error


def test_listing_the_share_root_itself_never_calls_a_missing_share_empty(monkeypatch) -> None:
    error = SMBOSError(NtStatus.STATUS_NOT_FOUND, SHARE_ROOT)
    monkeypatch.setattr(smb_storage_helper.smbclient, "scandir", _scandir_raising(error, share_root=error))
    with pytest.raises(SMBOSError):
        _helper().list_objects("share", "")


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
