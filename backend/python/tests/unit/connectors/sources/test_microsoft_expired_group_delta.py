"""Graph refuses a groups delta link older than 30 days. Kept, the link failed every later run of the connector."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from msgraph.generated.models.o_data_errors.main_error import MainError
from msgraph.generated.models.o_data_errors.o_data_error import ODataError

from app.connectors.sources.microsoft.common.expired_group_delta import (
    remove_groups_gone_from_directory,
)
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
    c.data_entities_processor.get_all_user_groups = AsyncMock(return_value=[])
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
    assert "read again" in c.notify.await_args.kwargs["title"]


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


ID = "00000000-0000-4000-8000-00000000000{}".format
MS_SITE_GROUP, EVERYONE = "host,site,web-5", "sharepoint-everyone"


async def _remove(lookups: dict[str, int | None], *, listed: tuple[str, ...] = (), deleted: bool = True) -> tuple[int, list[str]]:
    """``lookups``: the status Graph answers for a group that is stored and not listed; None is found."""
    async def look_up(group_id: str) -> object:
        status = lookups[group_id]
        if status:
            raise _error("no", status)
        return MagicMock()

    stored = [*listed, *lookups, MS_SITE_GROUP, EVERYONE]
    processor, graph = MagicMock(), MagicMock()
    processor.get_all_user_groups = AsyncMock(return_value=[MagicMock(source_user_group_id=g) for g in stored])
    graph.get_all_user_groups = AsyncMock(return_value=[MagicMock(id=g) for g in listed])
    graph.rate_limiter = asyncio.Semaphore(1)
    graph.client.groups.by_group_id = lambda g: MagicMock(get=lambda: look_up(g))
    gone: list[str] = []

    async def delete(group_id: str) -> bool:
        gone.append(group_id)
        return deleted

    return await remove_groups_gone_from_directory(processor, "c1", graph, delete, MagicMock()), gone


async def test_only_a_group_the_directory_confirms_is_gone_is_removed() -> None:
    lookups = {ID(1): 404, ID(2): None, ID(3): 403, ID(4): 503}
    assert await _remove(lookups, listed=(ID(9),)) == (1, [ID(1)])


async def test_a_removal_that_fails_raises() -> None:
    with pytest.raises(RuntimeError, match="could not be removed"):
        await _remove({ID(1): 404}, deleted=False)


@pytest.mark.parametrize("cls", [OneDriveConnector, SharePointConnector])
async def test_the_expired_link_stays_when_the_deleted_groups_could_not_be_found(cls: type) -> None:
    c = _connector(cls, {"deltaLink": OLD}, _expired())
    c.data_entities_processor.get_all_user_groups = AsyncMock(side_effect=ConnectionError("graph down"))
    if cls is OneDriveConnector:
        with pytest.raises(ConnectionError):
            await _sync(cls, c)
    else:
        await _sync(cls, c)
        c._health.skipped.assert_called()
    assert NEW not in _saved_links(c)
    c.notify.assert_not_awaited()
