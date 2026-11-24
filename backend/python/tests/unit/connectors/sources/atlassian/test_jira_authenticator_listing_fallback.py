"""The authenticator's link when Jira's bulk user read is not a full directory read.

On a Data Center release without /user/list the bulk read goes through /user/search, and
on either edition it may be refused. Neither can say who is missing, so the authenticator
is looked up on their own: found, they are listed; absent, the listing is complete; a
lookup that fails leaves the pass removal-only. Jira Cloud's user search answers a caller
without Browse users with no rows, so there "absent" counts only once the same search finds
the token's own account.
"""

from collections.abc import Callable
from functools import partial
from types import SimpleNamespace
from typing import NoReturn
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.sources.atlassian.jira_cloud.connector import JiraConnector
from app.connectors.sources.atlassian.jira_data_center.connector import (
    DcRestProfile,
    JiraDataCenterConnector,
)

OK = HttpStatusCode.OK.value
CREATOR = "Creator@Corp.com"


def _resp(status: int, body: object = None) -> SimpleNamespace:
    return SimpleNamespace(status=status, json=lambda: body, text=lambda: "")


def _bind(conn: MagicMock, cls: type, *names: str) -> None:
    for name in names:
        setattr(conn, name, partial(getattr(cls, name), conn))


def _raise(error: Exception) -> NoReturn:
    raise error


def _dc(search, *, user_list: bool = False, creator: str | None = CREATOR) -> MagicMock:
    conn = MagicMock(spec=JiraDataCenterConnector)
    conn.logger = MagicMock()
    conn.data_source = MagicMock()
    conn.connector_name = "JIRA"
    conn.connector_id = "c1"
    conn._dc_profile = DcRestProfile(supported=True, user_list=user_list)
    conn._user_bulk_forbidden = conn._user_bulk_refused = conn._user_bulk_incomplete = False
    conn._user_read_unfinished = False
    conn._listing_complete_for_link = False
    conn._listed_user_emails = set()
    conn._listed_user_keys = set()
    conn._seen_user_keys = set()
    conn._dc_name_to_source_id = {}
    conn._stored_app_users = []
    conn.data_entities_processor = MagicMock(
        org_id="org", get_all_app_users=AsyncMock(return_value=[]), get_all_active_users=AsyncMock(return_value=[]),
    )
    conn.authenticator_email = AsyncMock(return_value=creator)
    conn.datasource = MagicMock(get_user_search_v2=AsyncMock(side_effect=search))
    conn._get_fresh_datasource = AsyncMock(return_value=conn.datasource)
    conn.notify = AsyncMock()
    conn._notification_title = MagicMock(return_value="title")
    _bind(
        conn, JiraDataCenterConnector, "_fetch_users", "_fetch_all_jira_users_bulk", "_fetch_users_via_search",
        "_user_listing_complete_for_link", "_authenticator_is_listed", "_directory_read_complete", "_safe_json_parse",
    )
    return conn


def _row(key: str, email: str | None, active: bool = True) -> dict:
    row = {"key": key, "name": key, "active": active, "displayName": key}
    if email:
        row["emailAddress"] = email
    return row


def _search(listing: list[dict], lookup) -> Callable[..., object]:
    def run(*, username, **_) -> object:
        return lookup(username) if username != "." else _resp(OK, listing)
    return run


class TestJiraDataCenterSearchFallback:
    async def test_an_unlisted_authenticator_gets_a_complete_listing(self) -> None:
        conn = _dc(_search([_row("k1", "other@corp.com")], lambda _: _resp(OK, [])))

        await conn._fetch_users()

        assert conn._listing_complete_for_link is True
        assert CREATOR.lower() not in conn._listed_user_emails

    async def test_a_found_authenticator_is_listed(self) -> None:
        conn = _dc(_search(
            [_row("k1", "other@corp.com")], lambda _: _resp(OK, [_row("k2", "creator@corp.com", active=False)]),
        ))

        await conn._fetch_users()

        assert conn._listing_complete_for_link is True
        assert "creator@corp.com" in conn._listed_user_emails

    async def test_the_lookup_asks_for_inactive_users_too(self) -> None:
        conn = _dc(_search([_row("k1", "other@corp.com")], lambda _: _resp(OK, [])))

        await conn._fetch_users()

        lookup = [c for c in conn.datasource.get_user_search_v2.await_args_list if c.kwargs["username"] == CREATOR]
        assert lookup and lookup[0].kwargs["includeInactive"] is True

    @pytest.mark.parametrize("lookup", [
        lambda _: _resp(500),
        lambda _: _resp(403),
        lambda _: _resp(OK, {"not": "a list"}),
        lambda _: _raise(OSError("reset")),
        lambda _: _resp(OK, [_row("k9", None)]),
    ])
    async def test_a_lookup_that_cannot_answer_is_incomplete(self, lookup) -> None:
        conn = _dc(_search([_row("k1", "other@corp.com")], lookup))

        await conn._fetch_users()

        assert conn._listing_complete_for_link is False

    async def test_no_authenticator_email_is_incomplete(self) -> None:
        conn = _dc(_search([_row("k1", "other@corp.com")], lambda _: _resp(OK, [])), creator=None)

        await conn._fetch_users()

        assert conn._listing_complete_for_link is False

    async def test_a_fallback_that_listed_no_one_is_incomplete(self) -> None:
        conn = _dc(_search([], lambda _: _resp(OK, [])))

        await conn._fetch_users()

        assert conn._listing_complete_for_link is False

    async def test_an_unfinished_read_is_incomplete_whatever_the_lookup_says(self) -> None:
        conn = _dc(_search([_row("k1", "other@corp.com")], lambda _: _resp(OK, [])))
        conn._fetch_users_via_search = AsyncMock(
            side_effect=lambda: setattr(conn, "_user_read_unfinished", True) or [_row("k1", "other@corp.com")]
        )

        await conn._fetch_users()

        assert conn._listing_complete_for_link is False

    async def test_a_refused_bulk_read_looks_the_authenticator_up(self) -> None:
        def search(*, username, **_) -> object:
            return _resp(403) if username == "." else _resp(OK, [])
        conn = _dc(search)

        await conn._fetch_users()

        assert conn._user_bulk_refused is True
        assert conn._listing_complete_for_link is True

    async def test_a_full_list_read_needs_no_lookup(self) -> None:
        conn = _dc(lambda **_: pytest.fail("looked up"), user_list=True)
        conn._fetch_users_via_list = AsyncMock(return_value=[_row("k1", "other@corp.com")])

        await conn._fetch_users()

        assert conn._listing_complete_for_link is True


def _acct(account_id: str, email: str | None, *, kind: str = "atlassian") -> dict:
    row = {"accountId": account_id, "accountType": kind, "active": True, "displayName": account_id}
    if email:
        row["emailAddress"] = email
    return row


TOKEN = "Token@Corp.com"


def _by_query(creator_rows: list[dict], token_rows: list[dict] | None = None) -> Callable[..., SimpleNamespace]:
    """find_users answering the authenticator's lookup and the token account's own one."""
    token = [_acct("tok", TOKEN.lower())] if token_rows is None else token_rows

    def run(*, query: str, **_: object) -> SimpleNamespace:
        return _resp(OK, token if query.lower() == TOKEN.lower() else creator_rows)
    return run


def _cloud(find_users, *, creator: str | None = CREATOR, token: str | None = TOKEN) -> MagicMock:
    conn = MagicMock(spec=JiraConnector)
    conn._authenticated_jira_email = token
    conn.logger = MagicMock()
    conn.data_source = MagicMock()
    conn.connector_name = "JIRA"
    conn.connector_id = "c1"
    conn._user_bulk_forbidden = conn._user_bulk_refused = False
    conn._user_read_unfinished = False
    conn._listing_complete_for_link = False
    conn._listed_user_emails = set()
    conn._listed_account_ids = set()
    conn._stored_app_users = []
    conn.data_entities_processor = MagicMock(org_id="org", get_all_app_users=AsyncMock(return_value=[]))
    conn.authenticator_email = AsyncMock(return_value=creator)
    ds = MagicMock(find_users=AsyncMock(side_effect=find_users))
    async def call_with_retry(call, ctx, **_) -> object:
        return await call(ds)

    conn._call_with_retry = call_with_retry

    async def refused() -> list:
        conn._user_bulk_forbidden = conn._user_bulk_refused = True
        return []

    conn._fetch_all_jira_users_bulk = AsyncMock(side_effect=refused)
    _bind(
        conn, JiraConnector, "_fetch_users", "_user_listing_complete_for_link", "_authenticator_is_listed",
        "_directory_read_complete", "_safe_json_parse", "_find_atlassian_accounts",
    )
    conn._names_email = JiraConnector._names_email
    return conn


class TestJiraCloudRefusedBulkRead:
    async def test_an_unlisted_authenticator_gets_a_complete_listing(self) -> None:
        conn = _cloud(_by_query([_acct("a1", "someone.else@corp.com")]))

        await conn._fetch_users(active_pipeshub_users=[])

        assert conn._listing_complete_for_link is True
        assert "creator@corp.com" not in conn._listed_user_emails

    async def test_a_search_that_cannot_find_the_tokens_own_account_is_incomplete(self) -> None:
        # Without Browse users the search answers 200 with no rows: not found may mean cannot see.
        conn = _cloud(_by_query([], token_rows=[]))

        await conn._fetch_users(active_pipeshub_users=[])

        assert conn._listing_complete_for_link is False
        assert "creator@corp.com" not in conn._listed_user_emails

    async def test_an_unknown_token_account_email_is_incomplete(self) -> None:
        conn = _cloud(_by_query([]), token=None)

        await conn._fetch_users(active_pipeshub_users=[])

        assert conn._listing_complete_for_link is False

    async def test_an_authenticator_who_is_the_token_account_and_is_not_found_is_incomplete(self) -> None:
        conn = _cloud(_by_query([], token_rows=[]), creator=TOKEN)

        await conn._fetch_users(active_pipeshub_users=[])

        assert conn._listing_complete_for_link is False

    async def test_a_found_authenticator_needs_no_probe_of_the_token_account(self) -> None:
        def find_users(*, query: str, **_: object) -> SimpleNamespace:
            assert query.lower() != TOKEN.lower(), "probed the token account"
            return _resp(OK, [_acct("a1", "creator@corp.com")])
        conn = _cloud(find_users)

        await conn._fetch_users(active_pipeshub_users=[])

        assert conn._listing_complete_for_link is True
        assert "creator@corp.com" in conn._listed_user_emails

    async def test_a_found_authenticator_is_listed(self) -> None:
        conn = _cloud(lambda **_: _resp(OK, [_acct("a1", "creator@corp.com")]))

        await conn._fetch_users(active_pipeshub_users=[])

        assert conn._listing_complete_for_link is True
        assert "creator@corp.com" in conn._listed_user_emails

    @pytest.mark.parametrize("answer", [
        lambda **_: _resp(500),
        lambda **_: _resp(OK, {"not": "a list"}),
        lambda **_: _raise(OSError("reset")),
        lambda **_: _resp(OK, [_acct("a9", None)]),
    ])
    async def test_a_lookup_that_cannot_answer_is_incomplete(self, answer) -> None:
        conn = _cloud(answer)

        await conn._fetch_users(active_pipeshub_users=[])

        assert conn._listing_complete_for_link is False

    async def test_no_authenticator_email_is_incomplete(self) -> None:
        conn = _cloud(lambda **_: _resp(OK, []), creator=None)

        await conn._fetch_users(active_pipeshub_users=[])

        assert conn._listing_complete_for_link is False

    async def test_an_app_account_with_no_email_does_not_hide_an_answer(self) -> None:
        conn = _cloud(_by_query([_acct("bot", None, kind="app")]))

        await conn._fetch_users(active_pipeshub_users=[])

        assert conn._listing_complete_for_link is True
