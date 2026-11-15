"""OneDrive: a disabled or deleted account loses the connector; an uncertain listing withdraws nobody."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from msgraph.generated.models.o_data_errors.o_data_error import ODataError

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


def _connector(stored_ids: list[str], listing: object, complete: bool = True, status: object = "gone") -> tuple[OneDriveConnector, dict]:
    """A connector whose store holds ``stored_ids`` and whose listing is ``listing``
    (a list of AppUser, or an exception to raise)."""
    stored = {sid: _user(sid) for sid in stored_ids}
    proc = MagicMock()
    proc.org_id = "org"
    proc.on_new_app_users = AsyncMock()
    proc.get_app_user_links = AsyncMock(side_effect=lambda *a, **k: list(stored.values()))
    proc.get_all_app_users = AsyncMock(side_effect=AssertionError("the member-only read must not pick candidates"))

    async def withdraw(_cid: str, users: list[AppUser]) -> None:
        for u in users:
            stored.pop(u.source_user_id)

    proc.withdraw_app_user_access = AsyncMock(side_effect=withdraw)
    c = OneDriveConnector(logging.getLogger("t"), proc, MagicMock(), MagicMock(), "c1", "team", "creator")
    c.msgraph_client = MagicMock()
    c.msgraph_client.get_user_status = AsyncMock(side_effect=status) if isinstance(status, Exception) else AsyncMock(return_value=status)
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
    c.data_entities_processor.get_app_user_links = AsyncMock(side_effect=RuntimeError("db down"))
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
    (_result([_graph_user("a")], "https://next", count=3), [_result([], "https://next2"), _result([_graph_user("b")])]),  # empty page with a next link
    (_result([_graph_user("a")], "https://next", count=2), [None]),  # next page with no data
])
async def test_listing_that_may_be_cut_is_incomplete_but_returned(first: MagicMock, rest: list) -> None:
    client, _ = _client(first, *rest)
    users = await client.get_all_users()
    assert users.complete is False
    assert users[0].source_user_id == "a"


@pytest.mark.asyncio
async def test_listed_disabled_account_is_withdrawn_without_a_direct_read() -> None:
    c, stored = _connector(["a", "b"], [_user("a"), _user("b", active=False)])
    await c._sync_users()
    assert set(stored) == {"a"}
    c.msgraph_client.get_user_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_listing_with_a_matching_count_withdraws_nobody() -> None:
    """A user created after the listing was served is absent from it, and Microsoft still finds it."""
    c, stored = _connector(["a", "new"], [_user("a")], status="present")
    await c._sync_users()
    c.msgraph_client.get_user_status.assert_awaited_once_with("new")
    c.data_entities_processor.withdraw_app_user_access.assert_not_awaited()
    assert set(stored) == {"a", "new"}


@pytest.mark.asyncio
async def test_absent_account_found_disabled_is_withdrawn() -> None:
    c, stored = _connector(["a", "off"], [_user("a")], status="disabled")
    await c._sync_users()
    assert set(stored) == {"a"}


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [429, 500])
async def test_absent_account_that_cannot_be_confirmed_keeps_access(status_code: int, caplog: pytest.LogCaptureFixture) -> None:
    c, stored = _connector(["a", "x"], [_user("a")], status=RuntimeError(f"status {status_code}"))
    with caplog.at_level(logging.ERROR):
        await c._sync_users()
    c.data_entities_processor.withdraw_app_user_access.assert_not_awaited()
    assert any("x" in r.message and r.levelno == logging.ERROR for r in caplog.records)
    assert set(stored) == {"a", "x"}


@pytest.mark.asyncio
async def test_direct_reads_are_bounded_to_the_absent_accounts() -> None:
    c, _ = _connector(["a", "b", "c", "d"], [_user("a"), _user("b", active=False)])
    await c._sync_users()
    assert c.msgraph_client.get_user_status.await_count == 2  # c and d only


@pytest.mark.asyncio
async def test_status_read_is_a_definite_404_or_it_raises() -> None:
    from app.connectors.sources.microsoft.common.msgraph_client import read_user_status

    def graph_failing_with(code: int | None) -> MagicMock:
        err = ODataError()
        err.response_status_code = code
        g = MagicMock()
        g.users.by_user_id.return_value.get = AsyncMock(side_effect=err)
        return g

    assert await read_user_status(graph_failing_with(404), "u") == "gone"
    for code in (429, 500, None):
        with pytest.raises(ODataError):
            await read_user_status(graph_failing_with(code), "u")
    found = MagicMock()
    found.users.by_user_id.return_value.get = AsyncMock(return_value=MagicMock(account_enabled=True))
    assert await read_user_status(found, "u") == "present"
    found.users.by_user_id.return_value.get = AsyncMock(return_value=MagicMock(account_enabled=False))
    assert await read_user_status(found, "u") == "disabled"


@pytest.mark.asyncio
async def test_guest_accounts_follow_the_same_rule() -> None:
    listed_guest = _user("g1").model_copy(update={"is_guest": True})
    c, stored = _connector(["a", "g1", "g2"], [_user("a"), listed_guest])  # g2 is a guest that was deleted
    await c._sync_users()
    assert set(stored) == {"a", "g1"}


@pytest.fixture(autouse=True)
def sleeps(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """The retry waits of ``read_user_status`` are recorded, not slept."""
    from types import SimpleNamespace

    from app.connectors.sources.microsoft.common import msgraph_client

    sleep = AsyncMock()
    monkeypatch.setattr(msgraph_client, "asyncio", SimpleNamespace(sleep=sleep))
    return sleep


def _odata_error(code: int) -> ODataError:
    err = ODataError()
    err.response_status_code = code
    return err


def _graph_answering(*answers: object) -> MagicMock:
    """A Graph client whose reads of one user answer in turn: an int is an ODataError with that status, anything else is returned."""
    g = MagicMock()
    g.users.by_user_id.return_value.get = AsyncMock(side_effect=[_odata_error(a) if isinstance(a, int) else a for a in answers])
    return g


@pytest.mark.asyncio
async def test_a_404_is_read_again_after_2_then_4_seconds_before_it_is_gone(sleeps: AsyncMock) -> None:
    from unittest.mock import call

    from app.connectors.sources.microsoft.common.msgraph_client import read_user_status

    g = _graph_answering(404, 404, 404)
    assert await read_user_status(g, "u") == "gone"
    assert g.users.by_user_id.return_value.get.await_count == 3
    assert sleeps.await_args_list == [call(2), call(4)]


@pytest.mark.asyncio
@pytest.mark.parametrize("answers", [(404, MagicMock(account_enabled=True)), (404, 404, MagicMock(account_enabled=True))])
async def test_a_user_found_on_a_later_read_is_present(answers: tuple) -> None:
    from app.connectors.sources.microsoft.common.msgraph_client import read_user_status

    assert await read_user_status(_graph_answering(*answers), "u") == "present"


@pytest.mark.asyncio
async def test_a_404_then_a_server_error_raises() -> None:
    from app.connectors.sources.microsoft.common.msgraph_client import read_user_status

    with pytest.raises(ODataError):
        await read_user_status(_graph_answering(404, 500), "u")


@pytest.mark.asyncio
async def test_listing_with_an_empty_page_then_users_and_a_matching_count_is_complete() -> None:
    client, _ = _client(_result([_graph_user("a")], "https://next", count=2), _result([], "https://next2"), _result([_graph_user("b")]))
    users = await client.get_all_users()
    assert [u.source_user_id for u in users] == ["a", "b"] and users.complete is True


@pytest.mark.asyncio
async def test_listing_with_an_empty_page_and_lost_users_is_incomplete_through_the_count() -> None:
    client, _ = _client(_result([_graph_user("a")], "https://next", count=3), _result([], "https://next2"), _result([_graph_user("b")]))
    assert (await client.get_all_users()).complete is False
