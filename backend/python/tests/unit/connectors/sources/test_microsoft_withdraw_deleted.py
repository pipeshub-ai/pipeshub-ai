"""SharePoint and Outlook: a deleted account loses the connector; an uncertain listing withdraws nobody."""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import Connectors
from app.connectors.sources.microsoft.common.msgraph_client import (
    MSGraphClient,
    UserListing,
)
from app.connectors.sources.microsoft.outlook.connector import OutlookConnector
from app.connectors.sources.microsoft.sharepoint_online.connector import (
    SharePointConnector,
)
from app.models.entities import AppUser
from app.sources.external.microsoft.users_groups.users_groups import (
    UsersGroupsDataSource,
)

AUTO = "auto"  # the page's count is the number of users on it


def _app_user(sid: str, active: bool = True) -> AppUser:
    return AppUser(
        app_name=Connectors.OUTLOOK, connector_id="c1", source_user_id=sid,
        email=f"{sid}@x.com", full_name=sid, is_active=active,
    )


def _processor(stored_ids: list[str]) -> tuple[MagicMock, dict]:
    stored = {sid: _app_user(sid) for sid in stored_ids}
    proc = MagicMock()
    proc.org_id = "org"
    proc.on_new_app_users = AsyncMock()
    proc.on_new_user_groups = AsyncMock()
    proc.get_all_active_users = AsyncMock(return_value=[])
    proc.get_all_app_users = AsyncMock(side_effect=lambda *a, **k: list(stored.values()))

    async def withdraw(_cid: str, users: list[AppUser]) -> None:
        for u in users:
            stored.pop(u.source_user_id)

    proc.withdraw_app_user_access = AsyncMock(side_effect=withdraw)
    return proc, stored


def _graph_user(uid: str, enabled: bool = True) -> MagicMock:
    u = MagicMock()
    u.id, u.display_name, u.mail, u.user_principal_name = uid, uid, f"{uid}@x.com", None
    u.given_name = u.surname = None
    u.account_enabled, u.user_type = enabled, "Member"
    u.job_title, u.created_date_time = None, None
    return u


def _page(values: list, next_link: str | None = None, success: bool = True, count: object = AUTO) -> MagicMock:
    data = MagicMock()
    data.value = values
    data.odata_next_link = next_link
    data.odata_count = len(values) if count == AUTO else count
    return MagicMock(success=success, data=data, error="boom")


# ---------------------------------------------------------------- Outlook

def _outlook(stored_ids: list[str], pages: list) -> tuple[OutlookConnector, dict]:
    proc, stored = _processor(stored_ids)
    c = OutlookConnector(logging.getLogger("t"), proc, MagicMock(), MagicMock(), "c1", "team", "creator")
    c.external_users_client = MagicMock()
    c.external_users_client.users_user_list_user = AsyncMock(side_effect=pages)
    return c, stored


@pytest.mark.asyncio
async def test_outlook_deleted_and_disabled_accounts_are_withdrawn() -> None:
    c, stored = _outlook(["a", "gone", "off"], [_page([_graph_user("a"), _graph_user("off", enabled=False)])])
    await c._sync_users()
    assert set(stored) == {"a"}


@pytest.mark.asyncio
async def test_outlook_asks_for_the_directory_count() -> None:
    c, _ = _outlook(["a"], [_page([_graph_user("a")])])
    await c._sync_users()
    assert c.external_users_client.users_user_list_user.await_args_list[0].kwargs["count"] is True


@pytest.mark.asyncio
async def test_outlook_restored_account_gets_access_back() -> None:
    c, stored = _outlook(["a", "b"], [_page([_graph_user("a")]), _page([_graph_user("a"), _graph_user("b")])])
    await c._sync_users()
    assert set(stored) == {"a"}
    await c._sync_users()
    saved = {u.source_user_id for u in c.data_entities_processor.on_new_app_users.await_args.args[0]}
    assert saved == {"a", "b"}


@pytest.mark.asyncio
@pytest.mark.parametrize("pages", [
    [_page([_graph_user("a")], "https://next", count=3), _page([], "https://next2"), _page([_graph_user("b")])],  # empty page with a token
    [_page([_graph_user("a")], "https://next", count=3), MagicMock(success=True, data=None)],  # page without data
    [_page([], "https://next", count=2), _page([_graph_user("a"), _graph_user("b")])],  # first page empty with a token
    [_page([_graph_user("a")], count=3)],  # the directory counts more users than the list holds (next link dropped)
    [_page([_graph_user("a")], count=None)],  # no count at all
])
async def test_outlook_uncertain_listing_syncs_but_withdraws_nobody(pages: list) -> None:
    c, stored = _outlook(["a", "b", "c"], pages)
    await c._sync_users()
    c.data_entities_processor.withdraw_app_user_access.assert_not_awaited()
    c.data_entities_processor.on_new_app_users.assert_awaited_once()
    assert set(stored) == {"a", "b", "c"}


@pytest.mark.asyncio
async def test_outlook_failed_page_still_raises_and_withdraws_nobody() -> None:
    c, stored = _outlook(["a", "b"], [_page([_graph_user("a")], "https://next", count=2), _page([], success=False)])
    with pytest.raises(Exception, match="Failed to fetch users"):
        await c._sync_users()
    c.data_entities_processor.withdraw_app_user_access.assert_not_awaited()
    assert set(stored) == {"a", "b"}


@pytest.mark.asyncio
async def test_outlook_empty_listing_withdraws_nobody() -> None:
    c, stored = _outlook(["a", "b"], [_page([])])
    await c._sync_users()
    assert set(stored) == {"a", "b"}


@pytest.mark.asyncio
async def test_outlook_user_without_source_id_is_not_withdrawn() -> None:
    c, stored = _outlook(["a"], [_page([_graph_user("a")])])
    odd = _app_user("x")
    odd.source_user_id = None
    stored["odd"] = odd
    await c._sync_users()
    assert "odd" in stored


@pytest.mark.asyncio
async def test_outlook_read_failure_of_stored_users_withdraws_nobody(caplog: pytest.LogCaptureFixture) -> None:
    c, stored = _outlook(["a", "gone"], [_page([_graph_user("a")])])
    c.data_entities_processor.get_all_app_users = AsyncMock(side_effect=RuntimeError("db down"))
    with caplog.at_level(logging.ERROR):
        await c._sync_users()
    c.data_entities_processor.withdraw_app_user_access.assert_not_awaited()
    assert any("db down" in r.message and r.levelno == logging.ERROR for r in caplog.records)
    assert set(stored) == {"a", "gone"}


@pytest.mark.asyncio
async def test_outlook_half_read_listing_cannot_withdraw_while_another_read_runs() -> None:
    """The cache refresh reads the users at the same time as the sync: the sets a withdrawal
    uses are only replaced once a read is complete."""
    c, stored = _outlook(["a", "b", "c"], [])
    reached_second_page, release = asyncio.Event(), asyncio.Event()

    async def pages(next_url: str | None = None, **_: object) -> MagicMock:
        if next_url is None:
            return _page([_graph_user("a")], "https://next", count=3)
        reached_second_page.set()
        await release.wait()
        return _page([_graph_user("b"), _graph_user("c")])

    c.external_users_client.users_user_list_user = AsyncMock(side_effect=pages)
    reading = asyncio.create_task(c._get_all_users_external())
    await reached_second_page.wait()
    await c._withdraw_disabled_app_users()  # runs while only "a" has been listed
    c.data_entities_processor.withdraw_app_user_access.assert_not_awaited()
    release.set()
    await reading
    assert set(stored) == {"a", "b", "c"}
    assert c._listed_user_ids == {"a", "b", "c"}


# ------------------------------------------------------------- SharePoint

def _sharepoint(stored_ids: list[str], listing: object, complete: bool = True) -> tuple[SharePointConnector, dict]:
    proc, stored = _processor(stored_ids)
    c = SharePointConnector(logging.getLogger("t"), proc, MagicMock(), MagicMock(), "c1", "team", "creator")
    c.msgraph_client = MagicMock()
    c.user_sync_point = MagicMock()
    c.user_sync_point.read_sync_point = AsyncMock(return_value=None)
    c.notify = AsyncMock()
    if isinstance(listing, Exception):
        c.msgraph_client.get_all_users = AsyncMock(side_effect=listing)
    else:
        users = UserListing(listing)
        users.complete = complete
        c.msgraph_client.get_all_users = AsyncMock(return_value=users)
    return c, stored


@pytest.mark.asyncio
async def test_sharepoint_deleted_account_is_withdrawn_blocked_one_listed_stays() -> None:
    c, stored = _sharepoint(["a", "gone", "blocked"], [_app_user("a"), _app_user("blocked", active=False)])
    await c._sync_users()
    assert set(stored) == {"a", "blocked"}


@pytest.mark.asyncio
async def test_sharepoint_restored_account_gets_access_back() -> None:
    c, stored = _sharepoint(["a", "b"], [_app_user("a")])
    await c._sync_users()
    assert set(stored) == {"a"}
    c.msgraph_client.get_all_users = AsyncMock(return_value=UserListing([_app_user("a"), _app_user("b")]))
    await c._sync_users()
    saved = {u.source_user_id for u in c.data_entities_processor.on_new_app_users.await_args.args[0]}
    assert saved == {"a", "b"}


@pytest.mark.asyncio
async def test_sharepoint_incomplete_listing_syncs_but_withdraws_nobody() -> None:
    c, stored = _sharepoint(["a", "b"], [_app_user("a")], complete=False)
    await c._sync_users()
    c.data_entities_processor.on_new_app_users.assert_awaited_once()
    c.data_entities_processor.withdraw_app_user_access.assert_not_awaited()
    assert set(stored) == {"a", "b"}


@pytest.mark.asyncio
async def test_sharepoint_failed_listing_withdraws_nobody() -> None:
    c, stored = _sharepoint(["a", "b"], RuntimeError("throttled"))
    with pytest.raises(RuntimeError):
        await c._sync_users()
    c.data_entities_processor.withdraw_app_user_access.assert_not_awaited()
    assert set(stored) == {"a", "b"}


@pytest.mark.asyncio
async def test_sharepoint_empty_listing_withdraws_nobody() -> None:
    c, stored = _sharepoint(["a", "b"], [])
    await c._sync_users()
    assert set(stored) == {"a", "b"}


@pytest.mark.asyncio
async def test_sharepoint_user_without_source_id_is_not_withdrawn() -> None:
    c, stored = _sharepoint(["a"], [_app_user("a")])
    odd = _app_user("x")
    odd.source_user_id = None
    stored["odd"] = odd
    await c._sync_users()
    assert "odd" in stored


@pytest.mark.asyncio
async def test_sharepoint_read_failure_of_stored_users_withdraws_nobody(caplog: pytest.LogCaptureFixture) -> None:
    c, stored = _sharepoint(["a", "gone"], [_app_user("a")])
    c.data_entities_processor.get_all_app_users = AsyncMock(side_effect=RuntimeError("db down"))
    with caplog.at_level(logging.ERROR):
        await c._sync_users()
    c.data_entities_processor.withdraw_app_user_access.assert_not_awaited()
    assert any("db down" in r.message and r.levelno == logging.ERROR for r in caplog.records)
    assert set(stored) == {"a", "gone"}


# --------------------------------------------------- the shared listing

def _client(first: MagicMock, *next_pages: MagicMock) -> tuple[MSGraphClient, MagicMock]:
    g = MagicMock()
    g.users.get = AsyncMock(return_value=first)
    nxt = MagicMock()
    nxt.get = AsyncMock(side_effect=list(next_pages))
    g.users.with_url = MagicMock(return_value=nxt)
    return MSGraphClient("SHAREPOINT ONLINE", "c1", g, logging.getLogger("t"), 1000), g


def _result(values: list, next_link: str | None = None, count: object = AUTO) -> MagicMock:
    return MagicMock(value=values, odata_next_link=next_link, odata_count=len(values) if count == AUTO else count)


@pytest.mark.asyncio
async def test_graph_listing_asks_for_the_count_with_the_consistency_header() -> None:
    client, g = _client(_result([_graph_user("a")]))
    await client.get_all_users()
    config = g.users.get.await_args.args[0]
    assert config.query_parameters.count is True
    assert config.headers.try_get("ConsistencyLevel") == {"eventual"}


@pytest.mark.asyncio
async def test_graph_listing_matching_the_directory_count_is_complete() -> None:
    client, _ = _client(_result([_graph_user("a")], "https://next", count=2), _result([_graph_user("b")]))
    users = await client.get_all_users()
    assert [u.source_user_id for u in users] == ["a", "b"]
    assert users.complete is True


@pytest.mark.asyncio
@pytest.mark.parametrize("first, rest", [
    (_result([_graph_user("a")], count=3), []),  # shorter than the count
    (_result([_graph_user("a")], count=None), []),  # no count
    (_result([_graph_user("a")], "https://next", count=2), [_result([], "https://next2"), _result([_graph_user("b")])]),  # empty page with a next link
    (_result([_graph_user("a")], "https://next", count=2), [None]),  # next page with no data
])
async def test_graph_listing_that_may_be_cut_is_incomplete_but_returned(first: MagicMock, rest: list) -> None:
    client, _ = _client(first, *rest)
    users = await client.get_all_users()
    assert users.complete is False
    assert users[0].source_user_id == "a"


# ------------------------------------------------------ the datasource

@pytest.mark.asyncio
async def test_datasource_turns_count_into_the_query_parameter_and_header() -> None:
    graph = MagicMock()
    graph.users.get = AsyncMock(return_value=SimpleNamespace(value=[]))
    wrapper = MagicMock()
    wrapper.get_client.return_value.get_ms_graph_service_client.return_value = graph
    await UsersGroupsDataSource(wrapper).users_user_list_user(select=["id"], count=True)
    config = graph.users.get.await_args.kwargs["request_configuration"]
    assert config.query_parameters.count is True
    assert config.headers.try_get("ConsistencyLevel") == {"eventual"}


@pytest.mark.asyncio
async def test_datasource_sends_no_count_unless_asked() -> None:
    graph = MagicMock()
    graph.users.get = AsyncMock(return_value=SimpleNamespace(value=[]))
    wrapper = MagicMock()
    wrapper.get_client.return_value.get_ms_graph_service_client.return_value = graph
    await UsersGroupsDataSource(wrapper).users_user_list_user(select=["id"])
    config = graph.users.get.await_args.kwargs["request_configuration"]
    assert not config.query_parameters.count
