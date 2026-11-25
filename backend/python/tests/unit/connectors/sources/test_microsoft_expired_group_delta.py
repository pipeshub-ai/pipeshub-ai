"""Graph refuses a groups delta link older than 30 days. Kept, the link failed every later run of the connector."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from msgraph.generated.models.o_data_errors.main_error import MainError
from msgraph.generated.models.o_data_errors.o_data_error import ODataError

from app.connectors.sources.microsoft.common.outlook_constants import (
    is_expired_delta_link_error,
)
from app.connectors.sources.microsoft.onedrive.connector import OneDriveConnector
from app.connectors.sources.microsoft.sharepoint_online.connector import (
    SharePointConnector,
)

pytestmark = pytest.mark.anyio

OLD, NEW = "https://graph.microsoft.com/old", "https://graph.microsoft.com/new"


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


def _error(message: str, status: int) -> ODataError:
    error = ODataError()
    error.error = MainError(code="Request_BadRequest", message=message)
    error.response_status_code = status
    return error


def _expired() -> ODataError:
    return _error("DeltaLink older than 30 days is not supported.", 400)


def test_an_expired_link_is_told_from_another_bad_request() -> None:
    assert is_expired_delta_link_error(_expired())
    assert is_expired_delta_link_error("DeltaLink older than 30 days is not supported. (status 400)")
    assert not is_expired_delta_link_error(_error("Invalid filter clause", 400))


def _connector(cls: type, sync_point: dict, delta: object) -> MagicMock:
    c = MagicMock()
    c.user_group_sync_point.read_sync_point = AsyncMock(return_value=sync_point)
    c.user_group_sync_point.update_sync_point = AsyncMock()
    c.user_sync_point.update_sync_point = AsyncMock()
    c.notify = AsyncMock()
    c._perform_delta_sync = AsyncMock(side_effect=[delta])
    c._get_initial_delta_link = AsyncMock(return_value=NEW)
    c._perform_initial_full_sync = AsyncMock(return_value=True)
    c._refresh_all_groups = False
    c._inactive_emails = set()
    for name in ("_apply_group_changes", "_start_group_sync_over"):
        if hasattr(cls, name):
            setattr(c, name, getattr(cls, name).__get__(c))
    return c


async def _sync(cls: type, c: MagicMock) -> None:
    await (cls._sync_user_groups(c) if cls is OneDriveConnector else cls._sync_azure_ad_groups(c))


def _saved_links(c: MagicMock) -> list:
    return [call.args[1].get("deltaLink") for call in c.user_group_sync_point.update_sync_point.await_args_list]


@pytest.mark.parametrize("cls", [OneDriveConnector, SharePointConnector])
async def test_an_expired_link_is_replaced_and_every_group_read(cls: type) -> None:
    c = _connector(cls, {"deltaLink": OLD}, _expired())
    await _sync(cls, c)
    c._perform_initial_full_sync.assert_awaited_once()
    assert NEW in _saved_links(c)


@pytest.mark.parametrize("cls", [OneDriveConnector, SharePointConnector])
async def test_another_failure_keeps_the_link(cls: type) -> None:
    c = _connector(cls, {"deltaLink": OLD}, _error("Service unavailable", 503))
    with pytest.raises(ODataError):
        await _sync(cls, c)
    c._perform_initial_full_sync.assert_not_awaited()
    assert NEW not in _saved_links(c)


@pytest.mark.parametrize("cls", [OneDriveConnector, SharePointConnector])
async def test_a_link_that_works_is_not_replaced(cls: type) -> None:
    c = _connector(cls, {"deltaLink": OLD}, True)
    await _sync(cls, c)
    c._perform_initial_full_sync.assert_not_awaited()


async def test_onedrive_replaces_a_link_that_expired_while_a_full_read_was_incomplete() -> None:
    c = _connector(OneDriveConnector, {"deltaLink": OLD, "fullSyncIncomplete": True}, _expired())
    await _sync(OneDriveConnector, c)
    assert NEW in _saved_links(c)


@pytest.mark.parametrize(("error", "saved"), [(_expired(), True), (_error("Service unavailable", 503), False)])
async def test_sharepoint_leaves_an_expired_link_when_every_group_is_read_anyway(error: ODataError, saved: bool) -> None:
    c = _connector(SharePointConnector, {"deltaLink": OLD}, error)
    c._refresh_all_groups = True
    await _sync(SharePointConnector, c)
    assert (NEW in _saved_links(c)) is saved
    assert c._health.skipped.called is not saved
