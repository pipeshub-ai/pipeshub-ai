"""OneDrive: a disabled or deleted account loses the connector; an uncertain listing withdraws nobody."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import Connectors
from app.connectors.sources.microsoft.common.msgraph_client import (
    MSGraphClient,
    UserListing,
)
from app.connectors.sources.microsoft.onedrive.connector import OneDriveConnector
from app.models.entities import AppUser


def _user(sid: str, active: bool = True) -> AppUser:
    return AppUser(
        app_name=Connectors.ONEDRIVE, connector_id="c1", source_user_id=sid,
        email=f"{sid}@x.com", full_name=sid, is_active=active,
    )


def _connector(stored_ids: list[str], listing: object, complete: bool = True) -> tuple[OneDriveConnector, dict]:
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
        users = UserListing(listing)
        users.complete = complete
        c.msgraph_client.get_all_users = AsyncMock(return_value=users)
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


def _result(values: list, next_link: str | None = None, count: object = "auto") -> MagicMock:
    return MagicMock(value=values, odata_next_link=next_link, odata_count=len(values) if count == "auto" else count)


def _graph_user(uid: str) -> MagicMock:
    u = MagicMock()
    u.id, u.display_name, u.mail, u.user_principal_name = uid, uid, f"{uid}@x.com", None
    u.account_enabled, u.job_title, u.created_date_time, u.user_type = True, None, None, "Member"
    return u


def _client(first: MagicMock, *next_pages: MagicMock | None) -> tuple[MSGraphClient, MagicMock]:
    g = MagicMock()
    g.users.get = AsyncMock(return_value=first)
    nxt = MagicMock()
    nxt.get = AsyncMock(side_effect=list(next_pages))
    g.users.with_url = MagicMock(return_value=nxt)
    return MSGraphClient("ONEDRIVE", "c1", g, logging.getLogger("t"), 1000), g


@pytest.mark.asyncio
async def test_incomplete_listing_syncs_but_withdraws_nobody() -> None:
    c, stored = _connector(["a", "b"], [_user("a")], complete=False)
    await c._sync_users()
    c.data_entities_processor.on_new_app_users.assert_awaited_once()
    c.data_entities_processor.withdraw_app_user_access.assert_not_awaited()
    assert set(stored) == {"a", "b"}


@pytest.mark.asyncio
async def test_account_with_unknown_enabled_state_stays_saved() -> None:
    c, stored = _connector(["a", "b"], [_user("a"), _user("b").model_copy(update={"is_active": None})])
    await c._sync_users()
    assert set(stored) == {"a", "b"}
    saved = {u.source_user_id for u in c.data_entities_processor.on_new_app_users.await_args.args[0]}
    assert saved == {"a", "b"}


@pytest.mark.asyncio
async def test_read_failure_of_stored_users_withdraws_nobody(caplog: pytest.LogCaptureFixture) -> None:
    c, stored = _connector(["a", "gone"], [_user("a")])
    c.data_entities_processor.get_all_app_users = AsyncMock(side_effect=RuntimeError("db down"))
    with caplog.at_level(logging.ERROR):
        await c._sync_users()
    c.data_entities_processor.withdraw_app_user_access.assert_not_awaited()
    assert any("db down" in r.message and r.levelno == logging.ERROR for r in caplog.records)
    assert set(stored) == {"a", "gone"}


@pytest.mark.asyncio
async def test_listing_asks_for_the_count_with_the_consistency_header() -> None:
    client, g = _client(_result([_graph_user("a")]))
    await client.get_all_users()
    config = g.users.get.await_args.args[0]
    assert config.query_parameters.count is True
    assert config.headers.try_get("ConsistencyLevel") == {"eventual"}


@pytest.mark.asyncio
async def test_listing_matching_the_directory_count_is_complete() -> None:
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
async def test_listing_that_may_be_cut_is_incomplete_but_returned(first: MagicMock, rest: list) -> None:
    client, _ = _client(first, *rest)
    users = await client.get_all_users()
    assert users.complete is False
    assert users[0].source_user_id == "a"
