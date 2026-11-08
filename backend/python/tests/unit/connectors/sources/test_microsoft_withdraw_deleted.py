"""SharePoint and Outlook: a deleted account loses the connector; a bad listing withdraws nobody."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import Connectors
from app.connectors.sources.microsoft.common.msgraph_client import MSGraphClient
from app.connectors.sources.microsoft.outlook.connector import OutlookConnector
from app.connectors.sources.microsoft.sharepoint_online.connector import SharePointConnector
from app.models.entities import AppUser


def _app_user(sid, active=True):
    return AppUser(
        app_name=Connectors.OUTLOOK, connector_id="c1", source_user_id=sid,
        email=f"{sid}@x.com", full_name=sid, is_active=active,
    )


def _processor(stored_ids):
    stored = {sid: _app_user(sid) for sid in stored_ids}
    proc = MagicMock()
    proc.org_id = "org"
    proc.on_new_app_users = AsyncMock()
    proc.on_new_user_groups = AsyncMock()
    proc.get_all_active_users = AsyncMock(return_value=[])
    proc.get_all_app_users = AsyncMock(side_effect=lambda *a, **k: list(stored.values()))

    async def withdraw(_cid, users):
        for u in users:
            stored.pop(u.source_user_id)

    proc.withdraw_app_user_access = AsyncMock(side_effect=withdraw)
    return proc, stored


def _graph_user(uid, enabled=True, user_type="Member"):
    u = MagicMock()
    u.id, u.display_name, u.mail, u.user_principal_name = uid, uid, f"{uid}@x.com", None
    u.given_name = u.surname = None
    u.account_enabled, u.user_type = enabled, user_type
    return u


def _page(values, next_link=None, success=True):
    data = MagicMock()
    data.value = values
    data.odata_next_link = next_link
    return MagicMock(success=success, data=data, error="boom")


# ---------------------------------------------------------------- Outlook

def _outlook(stored_ids, pages):
    proc, stored = _processor(stored_ids)
    c = OutlookConnector(logging.getLogger("t"), proc, MagicMock(), MagicMock(), "c1", "team", "creator")
    c.external_users_client = MagicMock()
    c.external_users_client.users_user_list_user = AsyncMock(side_effect=pages)
    return c, stored


@pytest.mark.asyncio
async def test_outlook_deleted_account_is_withdrawn_but_guest_and_disabled_handled():
    c, stored = _outlook(
        ["a", "gone", "guest", "off"],
        [_page([_graph_user("a"), _graph_user("guest", user_type="Guest"), _graph_user("off", enabled=False)])],
    )
    await c._sync_users()
    assert set(stored) == {"a", "guest"}  # deleted and disabled go; a guest the connector never syncs stays


@pytest.mark.asyncio
async def test_outlook_restored_account_gets_access_back():
    c, stored = _outlook(["a", "b"], [_page([_graph_user("a")]), _page([_graph_user("a"), _graph_user("b")])])
    await c._sync_users()
    assert set(stored) == {"a"}
    await c._sync_users()
    saved = {u.source_user_id for u in c.data_entities_processor.on_new_app_users.await_args.args[0]}
    assert saved == {"a", "b"}


@pytest.mark.asyncio
@pytest.mark.parametrize("pages", [
    [_page([_graph_user("a")], "https://next"), _page([], "https://next2"), _page([_graph_user("b")])],  # empty page with a token
    [_page([_graph_user("a")], "https://next"), _page([_graph_user("b")], success=False)],  # failed page
    [_page([_graph_user("a")], "https://next"), MagicMock(success=True, data=None), _page([_graph_user("b")])],  # page without data
    [_page([], "https://next"), _page([_graph_user("a"), _graph_user("b")])],  # first page empty with a token
])
async def test_outlook_partial_listing_withdraws_nobody(pages):
    c, stored = _outlook(["a", "b", "c"], pages)
    with pytest.raises(Exception):
        await c._sync_users()
    c.data_entities_processor.withdraw_app_user_access.assert_not_awaited()
    assert set(stored) == {"a", "b", "c"}


@pytest.mark.asyncio
async def test_outlook_empty_listing_withdraws_nobody():
    c, stored = _outlook(["a", "b"], [_page([])])
    await c._sync_users()
    assert set(stored) == {"a", "b"}


@pytest.mark.asyncio
async def test_outlook_user_without_source_id_is_not_withdrawn():
    c, stored = _outlook(["a"], [_page([_graph_user("a")])])
    odd = _app_user("x")
    odd.source_user_id = None
    stored["odd"] = odd
    await c._sync_users()
    assert "odd" in stored


# ------------------------------------------------------------- SharePoint

def _sharepoint(stored_ids, listing):
    proc, stored = _processor(stored_ids)
    c = SharePointConnector(logging.getLogger("t"), proc, MagicMock(), MagicMock(), "c1", "team", "creator")
    c.msgraph_client = MagicMock()
    c.user_sync_point = MagicMock()
    c.user_sync_point.read_sync_point = AsyncMock(return_value=None)
    c.notify = AsyncMock()
    c.msgraph_client.get_all_users = (
        AsyncMock(side_effect=listing) if isinstance(listing, Exception) else AsyncMock(return_value=listing)
    )
    return c, stored


@pytest.mark.asyncio
async def test_sharepoint_deleted_account_is_withdrawn_blocked_one_listed_stays():
    c, stored = _sharepoint(["a", "gone", "blocked"], [_app_user("a"), _app_user("blocked", active=False)])
    await c._sync_users()
    assert set(stored) == {"a", "blocked"}


@pytest.mark.asyncio
async def test_sharepoint_restored_account_gets_access_back():
    c, stored = _sharepoint(["a", "b"], [_app_user("a")])
    await c._sync_users()
    assert set(stored) == {"a"}
    c.msgraph_client.get_all_users = AsyncMock(return_value=[_app_user("a"), _app_user("b")])
    await c._sync_users()
    saved = {u.source_user_id for u in c.data_entities_processor.on_new_app_users.await_args.args[0]}
    assert saved == {"a", "b"}


@pytest.mark.asyncio
async def test_sharepoint_failed_listing_withdraws_nobody():
    c, stored = _sharepoint(["a", "b"], RuntimeError("throttled"))
    with pytest.raises(RuntimeError):
        await c._sync_users()
    c.data_entities_processor.withdraw_app_user_access.assert_not_awaited()
    assert set(stored) == {"a", "b"}


@pytest.mark.asyncio
async def test_sharepoint_empty_listing_withdraws_nobody():
    c, stored = _sharepoint(["a", "b"], [])
    await c._sync_users()
    assert set(stored) == {"a", "b"}


@pytest.mark.asyncio
async def test_sharepoint_user_without_source_id_is_not_withdrawn():
    c, stored = _sharepoint(["a"], [_app_user("a")])
    odd = _app_user("x")
    odd.source_user_id = None
    stored["odd"] = odd
    await c._sync_users()
    assert "odd" in stored


@pytest.mark.asyncio
async def test_graph_listing_with_empty_page_and_next_link_fails():
    g = MagicMock()
    first = MagicMock(value=[_graph_user("a")], odata_next_link="https://next")
    g.users.get = AsyncMock(return_value=first)
    nxt = MagicMock()
    nxt.get = AsyncMock(return_value=MagicMock(value=[], odata_next_link="https://next2"))
    g.users.with_url = MagicMock(return_value=nxt)
    g_user = first.value[0]
    g_user.job_title, g_user.created_date_time = None, None
    client = MSGraphClient("SHAREPOINT", "c1", g, logging.getLogger("t"), 1000)
    with pytest.raises(RuntimeError):
        await client.get_all_users()
