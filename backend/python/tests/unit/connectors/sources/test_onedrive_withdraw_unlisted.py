"""OneDrive: a disabled or deleted account loses the connector; a bad listing withdraws nobody."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import Connectors
from app.connectors.sources.microsoft.common.msgraph_client import MSGraphClient
from app.connectors.sources.microsoft.onedrive.connector import OneDriveConnector
from app.models.entities import AppUser


def _user(sid: str, active: bool = True) -> AppUser:
    return AppUser(
        app_name=Connectors.ONEDRIVE, connector_id="c1", source_user_id=sid,
        email=f"{sid}@x.com", full_name=sid, is_active=active,
    )


def _connector(stored_ids: list[str], listing: object) -> tuple[OneDriveConnector, dict]:
    """A connector whose store holds ``stored_ids`` and whose listing is ``listing``
    (a list of AppUser, or an exception to raise)."""
    stored = {sid: _user(sid) for sid in stored_ids}
    proc = MagicMock()
    proc.org_id = "org"
    proc.on_new_app_users = AsyncMock()
    proc.get_all_app_users = AsyncMock(side_effect=lambda *a, **k: list(stored.values()))

    async def withdraw(_cid: str, users: list[AppUser]) -> None:
        for u in users:
            stored.pop(u.source_user_id)

    proc.withdraw_app_user_access = AsyncMock(side_effect=withdraw)
    c = OneDriveConnector(logging.getLogger("t"), proc, MagicMock(), MagicMock(), "c1", "team", "creator")
    c.msgraph_client = MagicMock()
    if isinstance(listing, Exception):
        c.msgraph_client.get_all_users = AsyncMock(side_effect=listing)
    else:
        c.msgraph_client.get_all_users = AsyncMock(return_value=listing)
    return c, stored


@pytest.mark.asyncio
async def test_disabled_account_is_withdrawn() -> None:
    c, stored = _connector(["a", "b"], [_user("a"), _user("b", active=False)])
    users = await c._sync_users()
    assert set(stored) == {"a"}
    saved = {u.source_user_id for u in c.data_entities_processor.on_new_app_users.await_args.args[0]}
    assert saved == {"a"}  # the disabled account is not saved again...
    assert {u.source_user_id for u in users} == {"a", "b"}  # ...but its drive is still synced


@pytest.mark.asyncio
async def test_deleted_account_is_withdrawn() -> None:
    c, stored = _connector(["a", "b"], [_user("a")])
    await c._sync_users()
    assert set(stored) == {"a"}


@pytest.mark.asyncio
async def test_reenabled_or_restored_account_gets_access_back() -> None:
    c, stored = _connector(["a", "b"], [_user("a")])
    await c._sync_users()
    assert set(stored) == {"a"}
    # The next listing has it again: it is upserted (which saves the user-app link).
    c.msgraph_client.get_all_users = AsyncMock(return_value=[_user("a"), _user("b")])
    await c._sync_users()
    saved = {u.source_user_id for u in c.data_entities_processor.on_new_app_users.await_args.args[0]}
    assert saved == {"a", "b"}


@pytest.mark.asyncio
async def test_failed_listing_withdraws_nobody() -> None:
    c, stored = _connector(["a", "b"], RuntimeError("throttled"))
    with pytest.raises(RuntimeError):
        await c._sync_users()
    c.data_entities_processor.withdraw_app_user_access.assert_not_awaited()
    assert set(stored) == {"a", "b"}


@pytest.mark.asyncio
async def test_empty_listing_withdraws_nobody() -> None:
    c, stored = _connector(["a", "b"], [])
    await c._sync_users()
    assert set(stored) == {"a", "b"}


@pytest.mark.asyncio
async def test_user_without_source_id_is_not_withdrawn() -> None:
    c, stored = _connector(["a"], [_user("a")])
    stored[""] = _user("", True)
    stored[""].source_user_id = None
    await c._sync_users()
    assert "" in stored


def _page(values: list, next_link: str | None = None) -> MagicMock:
    r = MagicMock()
    r.value = values
    r.odata_next_link = next_link
    return r


def _graph_user(uid: str) -> MagicMock:
    u = MagicMock()
    u.id, u.display_name, u.mail, u.user_principal_name = uid, uid, f"{uid}@x.com", None
    u.account_enabled, u.job_title, u.created_date_time, u.user_type = True, None, None, "Member"
    return u


@pytest.mark.asyncio
async def test_empty_page_with_next_link_is_a_failed_listing() -> None:
    g = MagicMock()
    g.users.get = AsyncMock(return_value=_page([_graph_user("a")], "https://next"))
    nxt = MagicMock()
    nxt.get = AsyncMock(return_value=_page([], "https://next2"))
    g.users.with_url = MagicMock(return_value=nxt)
    client = MSGraphClient("ONEDRIVE", "c1", g, logging.getLogger("t"), 1000)
    with pytest.raises(RuntimeError):
        await client.get_all_users()
