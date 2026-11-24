"""SharePoint and Outlook: a deleted account loses the connector; an uncertain listing withdraws nobody."""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from msgraph.generated.models.o_data_errors.o_data_error import ODataError

from app.config.constants.arangodb import Connectors
from app.connectors.sources.microsoft.common.msgraph_client import (
    MSGraphClient,
    UserListing,
)
from app.connectors.sources.microsoft.outlook.connector import OutlookConnector
from app.connectors.sources.microsoft.sharepoint_online.connector import (
    SharePointConnector,
)
from app.connectors.sources.microsoft.sharepoint_online.sync_health import (
    SharePointSyncIncomplete,
)
from app.models.entities import AppUser
from app.sources.external.microsoft.users_groups.users_groups import (
    UsersGroupsDataSource,
)

AUTO = "auto"  # the page's count is the number of users on it


@pytest.fixture(autouse=True)
def sleeps(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """The retry waits of ``read_user_status`` are recorded, not slept."""
    from types import SimpleNamespace

    from app.connectors.sources.microsoft.common import msgraph_client

    sleep = AsyncMock()
    monkeypatch.setattr(msgraph_client, "asyncio", SimpleNamespace(sleep=sleep))
    return sleep


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
    proc.get_app_user_links = AsyncMock(side_effect=lambda *a, **k: list(stored.values()))
    proc.get_all_app_users = AsyncMock(side_effect=AssertionError("the member-only read must not pick candidates"))

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

def _odata_error(code: int) -> ODataError:
    err = ODataError()
    err.response_status_code = code
    return err


def _direct_read(status: object) -> AsyncMock:
    """What reading one user by id answers: gone (404), present, disabled, or an error."""
    if status == "gone":
        return AsyncMock(side_effect=_odata_error(404))
    if status in ("present", "disabled"):
        return AsyncMock(return_value=MagicMock(account_enabled=status == "present"))
    return AsyncMock(side_effect=status)


def _outlook(stored_ids: list[str], pages: list, status: object = "gone") -> tuple[OutlookConnector, dict]:
    proc, stored = _processor(stored_ids)
    c = OutlookConnector(logging.getLogger("t"), proc, MagicMock(), MagicMock(), "c1", "team", "creator")
    c.external_users_client = MagicMock()
    c.external_users_client.users_user_list_user = AsyncMock(side_effect=pages)
    c.external_users_client.client.users.by_user_id.return_value.get = _direct_read(status)
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
    [_page([], "https://next", count=3), _page([_graph_user("a"), _graph_user("b")])],  # first page empty with a token, users lost (2 of 3)
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
    c.data_entities_processor.get_app_user_links = AsyncMock(side_effect=RuntimeError("db down"))
    with caplog.at_level(logging.ERROR):
        await c._sync_users()
    c.data_entities_processor.withdraw_app_user_access.assert_not_awaited()
    assert any("db down" in r.message and r.levelno == logging.ERROR for r in caplog.records)
    assert set(stored) == {"a", "gone"}


@pytest.mark.asyncio
async def test_outlook_withdrawal_uses_the_syncs_own_listing_not_an_overlapping_refresh() -> None:
    """The user-cache refresh reads the users at the same time as the sync. An incomplete
    refresh that finishes before the withdrawal runs must not change what the sync withdraws."""
    c, stored = _outlook(["a", "gone"], [_page([_graph_user("a")]), _page([_graph_user("a")], count=5)])
    syncs_listing = await c._get_all_users_external()
    refresh = await c._get_all_users_external()  # incomplete: no user loses access on this one
    assert refresh.complete is False and not refresh.listed_ids
    await c._withdraw_disabled_app_users(syncs_listing)
    assert set(stored) == {"a"}


@pytest.mark.asyncio
async def test_outlook_an_incomplete_listing_hands_the_withdrawal_no_ids() -> None:
    c, stored = _outlook(["a", "gone"], [_page([_graph_user("a")], count=5)])
    listing = await c._get_all_users_external()
    assert not listing.listed_ids and not listing.disabled_ids
    await c._withdraw_disabled_app_users(listing)
    assert set(stored) == {"a", "gone"}


@pytest.mark.asyncio
async def test_outlook_stale_listing_with_a_matching_count_withdraws_nobody() -> None:
    c, stored = _outlook(["a", "new"], [_page([_graph_user("a")])], status="present")
    await c._sync_users()
    assert set(stored) == {"a", "new"}


@pytest.mark.asyncio
async def test_outlook_absent_account_found_disabled_is_withdrawn() -> None:
    c, stored = _outlook(["a", "off"], [_page([_graph_user("a")])], status="disabled")
    await c._sync_users()
    assert set(stored) == {"a"}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [_odata_error(429), _odata_error(500), RuntimeError("down")])
async def test_outlook_absent_account_that_cannot_be_confirmed_keeps_access(failure: Exception, caplog: pytest.LogCaptureFixture) -> None:
    c, stored = _outlook(["a", "x"], [_page([_graph_user("a")])], status=failure)
    with caplog.at_level(logging.ERROR):
        await c._sync_users()
    assert set(stored) == {"a", "x"}
    assert any("x" in r.message and r.levelno == logging.ERROR for r in caplog.records)


@pytest.mark.asyncio
async def test_outlook_direct_reads_are_bounded_to_the_absent_accounts() -> None:
    c, _ = _outlook(["a", "b", "c", "d"], [_page([_graph_user("a"), _graph_user("b", enabled=False)])])
    await c._sync_users()
    assert {call.args[0] for call in c.external_users_client.client.users.by_user_id.call_args_list} == {"c", "d"}  # no listed account is read


# ------------------------------------------------------------- SharePoint

def _sharepoint(stored_ids: list[str], listing: object, complete: bool = True, status: object = "gone") -> tuple[SharePointConnector, dict]:
    proc, stored = _processor(stored_ids)
    c = SharePointConnector(logging.getLogger("t"), proc, MagicMock(), MagicMock(), "c1", "team", "creator")
    c.msgraph_client = MagicMock()
    c.user_sync_point = MagicMock()
    c.user_sync_point.read_sync_point = AsyncMock(return_value=None)
    c.notify = AsyncMock()
    c.msgraph_client.get_user_status = AsyncMock(side_effect=status) if isinstance(status, Exception) else AsyncMock(return_value=status)
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
async def test_sharepoint_stale_listing_with_a_matching_count_withdraws_nobody() -> None:
    c, stored = _sharepoint(["a", "new"], [_app_user("a")], status="present")
    await c._sync_users()
    c.msgraph_client.get_user_status.assert_awaited_once_with("new")
    assert set(stored) == {"a", "new"}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [_odata_error(429), _odata_error(500)])
async def test_sharepoint_absent_account_that_cannot_be_confirmed_keeps_access(failure: Exception, caplog: pytest.LogCaptureFixture) -> None:
    c, stored = _sharepoint(["a", "x"], [_app_user("a")], status=failure)
    with caplog.at_level(logging.ERROR):
        await c._sync_users()
    assert set(stored) == {"a", "x"}
    assert any("x" in r.message and r.levelno == logging.ERROR for r in caplog.records)


@pytest.mark.asyncio
async def test_sharepoint_direct_reads_are_bounded_to_the_absent_accounts() -> None:
    c, _ = _sharepoint(["a", "b", "c", "d"], [_app_user("a"), _app_user("b", active=False)])
    await c._sync_users()
    assert c.msgraph_client.get_user_status.await_count == 2


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
    c.data_entities_processor.get_app_user_links = AsyncMock(side_effect=RuntimeError("db down"))
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
    (_result([_graph_user("a")], "https://next", count=3), [_result([], "https://next2"), _result([_graph_user("b")])]),  # empty page with a next link
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


@pytest.mark.asyncio
async def test_sharepoint_guest_accounts_follow_the_same_rule() -> None:
    listed_guest = _app_user("g1").model_copy(update={"is_guest": True})
    c, stored = _sharepoint(["a", "g1", "g2"], [_app_user("a"), listed_guest])  # g2 is a guest that was deleted
    await c._sync_users()
    assert set(stored) == {"a", "g1"}


@pytest.mark.asyncio
async def test_outlook_a_deleted_guest_loses_access_and_a_listed_one_keeps_it() -> None:
    guest = _graph_user("g1")
    guest.user_type = "Guest"
    c, stored = _outlook(["a", "g1", "g2"], [_page([_graph_user("a"), guest])])  # g2 is a guest that was deleted
    await c._sync_users()
    assert set(stored) == {"a", "g1"}


@pytest.mark.asyncio
async def test_status_read_is_a_definite_404_or_it_raises() -> None:
    from app.connectors.sources.microsoft.common.msgraph_client import read_user_status

    def graph_failing_with(code: int | None) -> MagicMock:
        g = MagicMock()
        g.users.by_user_id.return_value.get = AsyncMock(side_effect=_odata_error(code))
        return g

    assert await read_user_status(graph_failing_with(404), "u") == "gone"
    for code in (429, 500):
        with pytest.raises(ODataError):
            await read_user_status(graph_failing_with(code), "u")
    found = MagicMock()
    found.users.by_user_id.return_value.get = AsyncMock(return_value=MagicMock(account_enabled=True))
    assert await read_user_status(found, "u") == "present"
    found.users.by_user_id.return_value.get = AsyncMock(return_value=MagicMock(account_enabled=False))
    assert await read_user_status(found, "u") == "disabled"


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
async def test_outlook_a_404_then_found_keeps_access_and_three_404s_withdraw() -> None:
    c, stored = _outlook(["a", "new"], [_page([_graph_user("a")])])
    c.external_users_client.client.users.by_user_id.return_value.get = AsyncMock(side_effect=[_odata_error(404), MagicMock(account_enabled=True)])
    await c._sync_users()
    assert set(stored) == {"a", "new"}
    c, stored = _outlook(["a", "gone"], [_page([_graph_user("a")])])
    c.external_users_client.client.users.by_user_id.return_value.get = AsyncMock(side_effect=[_odata_error(404)] * 3)
    await c._sync_users()
    assert set(stored) == {"a"}


@pytest.mark.asyncio
async def test_outlook_a_404_then_a_server_error_keeps_access() -> None:
    c, stored = _outlook(["a", "x"], [_page([_graph_user("a")])])
    c.external_users_client.client.users.by_user_id.return_value.get = AsyncMock(side_effect=[_odata_error(404), _odata_error(500)])
    await c._sync_users()
    assert set(stored) == {"a", "x"}


# ----------------------------------- group writes need a trusted listing

def _sharepoint_sync_point(c: SharePointConnector, emails: list[str] | None) -> None:
    c.user_sync_point.read_sync_point = AsyncMock(return_value={} if emails is None else {"emails": emails})  # the sync point store answers {} for a key never saved


@pytest.mark.asyncio
async def test_sharepoint_trusted_listing_replaces_the_claim_groups_and_the_disabled_set() -> None:
    c, _ = _sharepoint(["a", "blocked"], [_app_user("a"), _app_user("blocked", active=False)])
    _sharepoint_sync_point(c, [])
    await c._sync_users()
    c.data_entities_processor.on_new_user_groups.assert_awaited_once()
    assert c._inactive_emails == {"blocked@x.com"} and c._refresh_all_groups is True


@pytest.mark.asyncio
async def test_sharepoint_incomplete_listing_leaves_the_groups_and_the_disabled_set_alone() -> None:
    c, _ = _sharepoint(["a", "b"], [_app_user("a"), _app_user("blocked", active=False)], complete=False)
    _sharepoint_sync_point(c, ["old@x.com"])
    await c._sync_users()
    c.data_entities_processor.on_new_user_groups.assert_not_awaited()
    c.data_entities_processor.on_new_app_users.assert_awaited_once()  # the listed users are still saved
    # the accounts the cut listing shows as disabled are used this run, on top of the last good set
    assert c._inactive_emails == {"old@x.com", "blocked@x.com"} and c._refresh_all_groups is False


@pytest.mark.asyncio
async def test_sharepoint_stale_listing_with_a_matching_count_leaves_the_groups_alone() -> None:
    c, _ = _sharepoint(["a", "new"], [_app_user("a"), _app_user("blocked", active=False)], status="present")
    _sharepoint_sync_point(c, [])
    await c._sync_users()
    c.data_entities_processor.on_new_user_groups.assert_not_awaited()
    assert c._inactive_emails == {"blocked@x.com"} and c._refresh_all_groups is False


@pytest.mark.asyncio
async def test_sharepoint_a_direct_read_that_raised_leaves_the_groups_alone() -> None:
    c, _ = _sharepoint(["a", "x"], [_app_user("a")], status=_odata_error(429))
    await c._sync_users()
    c.data_entities_processor.on_new_user_groups.assert_not_awaited()


@pytest.mark.asyncio
async def test_sharepoint_failed_read_of_the_stored_users_leaves_the_groups_alone() -> None:
    c, _ = _sharepoint(["a"], [_app_user("a")])
    c.data_entities_processor.get_app_user_links = AsyncMock(side_effect=RuntimeError("db down"))
    await c._sync_users()
    c.data_entities_processor.on_new_user_groups.assert_not_awaited()


@pytest.mark.asyncio
async def test_sharepoint_a_listing_that_never_becomes_trusted_does_not_stop_the_sync_and_a_good_one_catches_up() -> None:
    c, _ = _sharepoint(["a", "b"], [_app_user("a")], complete=False)
    for _ in range(3):
        await c._sync_users()
    c.data_entities_processor.on_new_user_groups.assert_not_awaited()
    c.msgraph_client.get_all_users = AsyncMock(return_value=UserListing([_app_user("a"), _app_user("b"), _app_user("new")]))
    await c._sync_users()
    groups = c.data_entities_processor.on_new_user_groups.await_args.args[0]
    assert groups  # the claim groups are written once a trusted listing arrives


def _outlook_run(c: OutlookConnector) -> tuple[AsyncMock, AsyncMock]:
    """run_sync with everything but the user sync and the group sync stubbed; returns the two group mocks."""
    from contextlib import ExitStack
    from unittest.mock import patch

    groups, conversations = AsyncMock(return_value=[]), AsyncMock()
    c._sync_user_groups, c._sync_group_conversations = groups, conversations
    c._reinitialize_client_if_needed = AsyncMock()
    c.external_outlook_client = MagicMock()

    async def no_mail(*_a: object, **_k: object):  # noqa: ANN202
        return
        yield

    c._process_users = no_mail
    c.data_entities_processor.get_all_active_users = AsyncMock(return_value=[])
    stack = ExitStack()
    stack.enter_context(patch("app.connectors.sources.microsoft.outlook.connector.load_connector_filters",
                              AsyncMock(return_value=(MagicMock(get=MagicMock(return_value=None)), MagicMock()))))
    stack.enter_context(patch("app.connectors.sources.microsoft.outlook.connector.notify_sync_outcome", AsyncMock()))
    c._stack = stack
    return groups, conversations


@pytest.mark.asyncio
async def test_outlook_trusted_listing_rewrites_the_groups_and_fills_the_cache() -> None:
    c, _ = _outlook(["a"], [_page([_graph_user("a")])])
    groups, _ = _outlook_run(c)
    with c._stack:
        await c.run_sync()
    groups.assert_awaited_once()
    assert c._user_cache == {"a@x.com": "a"}


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["incomplete", "stale", "read_raised", "stored_read_failed"])
async def test_outlook_untrusted_listing_rewrites_no_group_and_keeps_the_last_good_cache(scenario: str) -> None:
    pages = [_page([_graph_user("a")], count=3)] if scenario == "incomplete" else [_page([_graph_user("a")])]
    status = {"stale": "present", "read_raised": _odata_error(429)}.get(scenario, "gone")
    c, stored = _outlook(["a", "x"], pages, status=status)
    if scenario == "stored_read_failed":
        c.data_entities_processor.get_app_user_links = AsyncMock(side_effect=RuntimeError("db down"))
    c._user_cache = {"old@x.com": "old"}
    groups, conversations = _outlook_run(c)
    with c._stack:
        await c.run_sync()  # the sync goes on: it does not raise
    groups.assert_not_awaited()
    conversations.assert_not_awaited()
    assert c._user_cache == {"old@x.com": "old"}
    c.data_entities_processor.on_new_app_users.assert_awaited_once()  # the listed users are still saved
    assert c._full_sync_incomplete is True  # the skipped groups are owed, as after a failed group sync


@pytest.mark.asyncio
async def test_outlook_a_cut_refresh_does_not_replace_the_last_good_cache() -> None:
    c, _ = _outlook([], [_page([_graph_user("a")], count=3)])
    c._user_cache = {"old@x.com": "old"}
    await c._populate_user_cache()
    assert c._user_cache == {"old@x.com": "old"}


@pytest.mark.asyncio
async def test_sharepoint_untrusted_listing_without_an_earlier_disabled_set_reads_no_group() -> None:
    """No earlier set means nobody is known to be enabled: the Entra and site groups wait for a trusted listing."""
    c, _ = _sharepoint(["a", "b"], [_app_user("a")], complete=False)
    _sharepoint_sync_point(c, None)
    await c._sync_users()
    assert c._disabled_set_unknown is True and not c._health.complete
    c.msgraph_client.get_all_users = AsyncMock(return_value=UserListing([_app_user("a"), _app_user("b")]))
    await c._sync_users()
    assert c._disabled_set_unknown is False  # a trusted listing clears it


@pytest.mark.asyncio
async def test_sharepoint_untrusted_listing_with_an_earlier_set_keeps_reading_groups_with_it() -> None:
    c, _ = _sharepoint(["a", "b"], [_app_user("a")], complete=False)
    _sharepoint_sync_point(c, ["old@x.com"])
    await c._sync_users()
    assert c._disabled_set_unknown is False and c._inactive_emails == {"old@x.com"}


@pytest.mark.asyncio
@pytest.mark.parametrize(("sync_point", "reads_groups"), [(None, False), (["old@x.com"], True)])
async def test_sharepoint_run_reads_no_group_while_the_disabled_set_is_unknown(sync_point: list[str] | None, reads_groups: bool) -> None:
    from unittest.mock import patch

    c, _ = _sharepoint(["a", "b"], [_app_user("a")], complete=False)
    _sharepoint_sync_point(c, sync_point)
    for name in ("_reinitialize_credential_if_needed", "_report_missing_graph_permission", "_remove_deleted_sites", "_remove_filtered_out"):
        setattr(c, name, AsyncMock())
    c._reset_permission_caches = MagicMock()
    c._get_all_sites = AsyncMock(return_value=[])
    c._sync_user_groups = AsyncMock()
    c.drive_delta_sync_point = MagicMock(read_sync_point=AsyncMock(return_value=None), update_sync_point=AsyncMock())
    with patch("app.connectors.sources.microsoft.sharepoint_online.connector.load_connector_filters",
               AsyncMock(return_value=(MagicMock(), MagicMock()))):
        with pytest.raises(SharePointSyncIncomplete):  # an untrusted listing leaves the run incomplete, which a sync reports
            await c.run_sync()
    assert c._sync_user_groups.await_count == (1 if reads_groups else 0)


@pytest.mark.asyncio
@pytest.mark.parametrize(("complete", "saved"), [(True, True), (False, False)])
async def test_sharepoint_the_disabled_set_is_saved_only_from_a_trusted_listing(complete: bool, saved: bool) -> None:
    c, _ = _sharepoint(["a"], [_app_user("a"), _app_user("blocked", active=False)], complete=complete)
    _sharepoint_sync_point(c, [])
    c.user_group_sync_point = MagicMock(read_sync_point=AsyncMock(return_value=None), update_sync_point=AsyncMock())
    c.user_sync_point.update_sync_point = AsyncMock()
    c._get_initial_delta_link = AsyncMock(return_value="delta")
    c._perform_initial_full_sync = AsyncMock(return_value=True)
    await c._sync_users()
    await c._sync_azure_ad_groups()
    assert c.user_sync_point.update_sync_point.await_count == (1 if saved else 0)


@pytest.mark.asyncio
async def test_graph_listing_with_an_empty_page_then_users_and_a_matching_count_is_complete() -> None:
    client, _ = _client(_result([_graph_user("a")], "https://next", count=2), _result([], "https://next2"), _result([_graph_user("b")]))
    users = await client.get_all_users()
    assert [u.source_user_id for u in users] == ["a", "b"] and users.complete is True


@pytest.mark.asyncio
async def test_graph_listing_with_an_empty_page_and_lost_users_is_incomplete_through_the_count() -> None:
    client, _ = _client(_result([_graph_user("a")], "https://next", count=3), _result([], "https://next2"), _result([_graph_user("b")]))
    assert (await client.get_all_users()).complete is False


@pytest.mark.asyncio
async def test_outlook_empty_page_then_users_with_a_matching_count_is_complete_and_withdrawals_proceed() -> None:
    c, stored = _outlook(["a", "gone"], [_page([_graph_user("a")], "https://next", count=2), _page([], "https://next2"), _page([_graph_user("b")])])
    await c._sync_users()
    assert set(stored) == {"a"} and c._users_trusted is True


@pytest.mark.asyncio
async def test_outlook_empty_page_with_lost_users_is_untrusted_through_the_count() -> None:
    c, stored = _outlook(["a", "gone"], [_page([_graph_user("a")], "https://next", count=3), _page([], "https://next2"), _page([_graph_user("b")])])
    await c._sync_users()
    assert set(stored) == {"a", "gone"} and c._users_trusted is False


@pytest.mark.asyncio
async def test_sharepoint_a_run_that_reads_no_group_cannot_grant_from_an_earlier_runs_site_groups() -> None:
    from unittest.mock import patch

    from app.models.permission import PermissionType

    c, _ = _sharepoint(["a", "b"], [_app_user("a")], complete=False)
    _sharepoint_sync_point(c, None)
    c._site_groups_read = {"s"}
    c._org_wide_site_groups = {"s": {"1": {"everyone"}}}
    c._unreadable_site_groups = {"s": {"2"}}
    for name in ("_reinitialize_credential_if_needed", "_report_missing_graph_permission", "_remove_deleted_sites", "_remove_filtered_out"):
        setattr(c, name, AsyncMock())
    c._get_all_sites = AsyncMock(return_value=[])
    c._sync_user_groups = AsyncMock()
    c.drive_delta_sync_point = MagicMock(read_sync_point=AsyncMock(return_value=None), update_sync_point=AsyncMock())
    with patch("app.connectors.sources.microsoft.sharepoint_online.connector.load_connector_filters",
               AsyncMock(return_value=(MagicMock(), MagicMock()))):
        with pytest.raises(SharePointSyncIncomplete):
            await c.run_sync()
    assert c._site_groups_read == set() and c._org_wide_site_groups == {} and c._unreadable_site_groups == {}
    with pytest.raises(RuntimeError, match="were not read"):
        c._site_group_permissions("s", "1", PermissionType.READ)


@pytest.mark.asyncio
async def test_sharepoint_group_members_are_not_expanded_while_the_disabled_set_is_unknown() -> None:
    from app.models.permission import PermissionType

    c, _ = _sharepoint([], [])
    c.client = MagicMock()
    c.client.groups.by_group_id.return_value.owners.get = AsyncMock(return_value=MagicMock(value=[], odata_next_link=None))
    c._disabled_set_unknown = True
    with pytest.raises(RuntimeError, match="not known"):
        await c._m365_owner_permissions("g1", PermissionType.READ)
    with pytest.raises(RuntimeError, match="not known"):
        await c._get_directory_role_member_users("role1")
    c._disabled_set_unknown = False
    assert await c._m365_owner_permissions("g1", PermissionType.READ) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("unknown", "granted"), [
    (True, {"active@x.com"}),                       # only users the listing showed as active
    (False, {"active@x.com", "unlisted@x.com"}),    # a known set: everyone but the disabled
])
async def test_sharepoint_direct_user_grants_while_the_disabled_set_is_unknown(unknown: bool, granted: set[str]) -> None:
    from app.models.permission import PermissionType

    c, _ = _sharepoint([], [])
    c._disabled_set_unknown = unknown
    c._confirmed_active_emails = {"active@x.com"}
    c._inactive_emails = {"blocked@x.com"}
    got = set()
    for email in ("active@x.com", "unlisted@x.com", "blocked@x.com"):
        principal = {"LoginName": f"i:0#.f|membership|{email}", "PrincipalType": 1, "Email": email, "Id": 7}
        got |= {p.email for p in await c._sharepoint_principal_permissions(principal, PermissionType.READ, "s1")}
    assert got == granted
    assert c._withheld_unknown_user is unknown


@pytest.mark.asyncio
async def test_sharepoint_a_run_that_withheld_a_direct_grant_has_every_item_read_again_next_run() -> None:
    from unittest.mock import patch

    c, _ = _sharepoint(["a", "b"], [_app_user("a")], complete=False)
    _sharepoint_sync_point(c, None)
    for name in ("_reinitialize_credential_if_needed", "_report_missing_graph_permission", "_remove_deleted_sites", "_remove_filtered_out"):
        setattr(c, name, AsyncMock())
    c._reset_permission_caches = MagicMock()
    c._withheld_unknown_user = True
    c._get_all_sites = AsyncMock(return_value=[])
    c._sync_user_groups = AsyncMock()
    c.drive_delta_sync_point = MagicMock(read_sync_point=AsyncMock(return_value=None), update_sync_point=AsyncMock())
    with patch("app.connectors.sources.microsoft.sharepoint_online.connector.load_connector_filters",
               AsyncMock(return_value=(MagicMock(), MagicMock()))):
        with pytest.raises(SharePointSyncIncomplete):
            await c.run_sync()
    c.drive_delta_sync_point.update_sync_point.assert_awaited_with("sharepoint-permission-model", sync_point_data={"version": 0})


@pytest.mark.asyncio
async def test_sharepoint_every_item_is_marked_for_reading_again_before_any_site_is_read_while_the_disabled_set_is_unknown() -> None:
    from unittest.mock import patch

    c, _ = _sharepoint(["a", "b"], [_app_user("a")], complete=False)
    _sharepoint_sync_point(c, None)
    for name in ("_reinitialize_credential_if_needed", "_report_missing_graph_permission", "_remove_deleted_sites", "_remove_filtered_out"):
        setattr(c, name, AsyncMock())
    c._reset_permission_caches = MagicMock()
    order: list[str] = []
    c.drive_delta_sync_point = MagicMock(
        read_sync_point=AsyncMock(return_value=None),
        update_sync_point=AsyncMock(side_effect=lambda key, sync_point_data: order.append(f"{key}={sync_point_data['version']}")),
    )

    async def sites() -> list:
        order.append("sites")
        raise RuntimeError("the run stops while reading sites")
    c._get_all_sites = sites
    with patch("app.connectors.sources.microsoft.sharepoint_online.connector.load_connector_filters",
               AsyncMock(return_value=(MagicMock(), MagicMock()))):
        with pytest.raises(RuntimeError):
            await c.run_sync()

    # Written before any item's grants or delta token could move, so a run that stops later still re-reads everything.
    assert order == ["sharepoint-permission-model=0", "sites"]


@pytest.mark.parametrize(("unknown", "withheld"), [(True, True), (False, False)])
def test_sharepoint_a_graph_identity_with_no_email_is_withheld_only_while_the_disabled_set_is_unknown(unknown: bool, withheld: bool) -> None:
    c, _ = _sharepoint([], [])
    c._disabled_set_unknown = unknown

    assert c._is_inactive_identity(SimpleNamespace(additional_data={})) is withheld
    assert c._withheld_unknown_user is withheld
