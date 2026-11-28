"""What Jira's user read proves about the authenticator when it is not a full directory read.

On a Data Center release without /user/list the bulk read goes through /user/search, and on
either edition it may be refused or show an account whose email it cannot resolve. Then the
authenticator is looked up on their own: found, they are PRESENT; a lookup that fails leaves
them UNKNOWN. On Data Center the lookup includes inactive users, so not finding them settles
ABSENT. Jira Cloud's user search lists active users only, so there "not found" never does,
whatever the token's own account shows. A refused read lists no one, so it proves no absence.
"""

from collections.abc import Callable
from functools import partial
from types import SimpleNamespace
from typing import NoReturn
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.base.connector.source_user_listing import Presence
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
    conn._user_listing = None
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
        "_listing_of", "_settle_authenticator", "_directory_read_complete", "_safe_json_parse",
    )
    return conn


def _presence(conn: MagicMock) -> Presence:
    return conn._user_listing.presence(CREATOR)


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
    async def test_an_authenticator_the_lookup_does_not_find_is_absent(self) -> None:
        conn = _dc(_search([_row("k1", "other@corp.com")], lambda _: _resp(OK, [])))

        await conn._fetch_users()

        assert _presence(conn) is Presence.ABSENT

    async def test_a_found_authenticator_is_listed(self) -> None:
        conn = _dc(_search(
            [_row("k1", "other@corp.com")], lambda _: _resp(OK, [_row("k2", "creator@corp.com", active=False)]),
        ))

        await conn._fetch_users()

        assert _presence(conn) is Presence.PRESENT

    async def test_a_found_authenticator_is_listed_beside_a_malformed_row(self) -> None:
        conn = _dc(_search([_row("k1", "other@corp.com")], lambda _: _resp(OK, [None, _row("k2", "creator@corp.com")])))

        await conn._fetch_users()


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
        lambda _: _resp(OK, [None]),
        lambda _: _resp(OK, [_row("k1", "other@corp.com"), "k2"]),
    ])
    async def test_a_lookup_that_cannot_answer_leaves_them_unknown(self, lookup) -> None:
        conn = _dc(_search([_row("k1", "other@corp.com")], lookup))

        await conn._fetch_users()

        assert _presence(conn) is Presence.UNKNOWN

    async def test_no_authenticator_email_is_unknown(self) -> None:
        conn = _dc(_search([_row("k1", "other@corp.com")], lambda _: _resp(OK, [])), creator=None)

        await conn._fetch_users()

        assert _presence(conn) is Presence.UNKNOWN

    async def test_a_fallback_that_listed_no_one_proves_no_absence(self) -> None:
        conn = _dc(_search([], lambda _: _resp(OK, [])))

        await conn._fetch_users()

        assert _presence(conn) is Presence.UNKNOWN

    async def test_an_unfinished_read_proves_no_absence_whatever_the_lookup_says(self) -> None:
        conn = _dc(_search([_row("k1", "other@corp.com")], lambda _: _resp(OK, [])))
        conn._fetch_users_via_search = AsyncMock(
            side_effect=lambda: setattr(conn, "_user_read_unfinished", True) or [_row("k1", "other@corp.com")]
        )

        await conn._fetch_users()

        assert _presence(conn) is Presence.UNKNOWN

    async def test_a_refused_bulk_read_lists_no_one_so_proves_no_absence(self) -> None:
        def search(*, username, **_) -> object:
            return _resp(403) if username == "." else _resp(OK, [])
        conn = _dc(search)

        await conn._fetch_users()

        assert conn._user_bulk_refused is True
        assert _presence(conn) is Presence.UNKNOWN

    async def test_a_full_list_read_proves_absence_with_no_lookup(self) -> None:
        conn = _dc(lambda **_: pytest.fail("looked up"), user_list=True)
        conn._fetch_users_via_list = AsyncMock(return_value=[_row("k1", "other@corp.com")])

        await conn._fetch_users()

        assert _presence(conn) is Presence.ABSENT


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
    conn._user_listing = None
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
        conn, JiraConnector, "_fetch_users", "_listing_of", "_settle_authenticator",
        "_directory_read_complete", "_safe_json_parse", "_find_atlassian_accounts",
    )
    conn._names_email = JiraConnector._names_email
    return conn


class TestJiraCloudRefusedBulkRead:
    async def test_an_authenticator_the_active_only_search_does_not_find_is_unknown(self) -> None:
        # The token's own account is found, so the search can see users: a deactivated creator still could not be.
        conn = _cloud(_by_query([_acct("a1", "someone.else@corp.com")]))

        await conn._fetch_users(active_pipeshub_users=[])

        assert _presence(conn) is Presence.UNKNOWN

    async def test_a_search_that_cannot_find_the_tokens_own_account_leaves_them_unknown(self) -> None:
        # Without Browse users the search answers 200 with no rows: not found may mean cannot see.
        conn = _cloud(_by_query([], token_rows=[]))

        await conn._fetch_users(active_pipeshub_users=[])

        assert _presence(conn) is Presence.UNKNOWN

    async def test_an_unknown_token_account_email_leaves_them_unknown(self) -> None:
        conn = _cloud(_by_query([]), token=None)

        await conn._fetch_users(active_pipeshub_users=[])

        assert _presence(conn) is Presence.UNKNOWN

    async def test_an_authenticator_who_is_the_token_account_and_is_not_found_is_unknown(self) -> None:
        conn = _cloud(_by_query([], token_rows=[]), creator=TOKEN)

        await conn._fetch_users(active_pipeshub_users=[])

        assert _presence(conn) is Presence.UNKNOWN

    async def test_the_token_account_is_never_probed(self) -> None:
        def find_users(*, query: str, **_: object) -> SimpleNamespace:
            if query.lower() == TOKEN.lower():  # not an assert: the lookup swallows any Exception
                pytest.fail("probed the token account")
            return _resp(OK, [])
        conn = _cloud(find_users)

        await conn._fetch_users(active_pipeshub_users=[])

        assert _presence(conn) is Presence.UNKNOWN

    async def test_a_found_authenticator_needs_no_probe_of_the_token_account(self) -> None:
        def find_users(*, query: str, **_: object) -> SimpleNamespace:
            if query.lower() == TOKEN.lower():  # not an assert: the lookup swallows any Exception
                pytest.fail("probed the token account")
            return _resp(OK, [_acct("a1", "creator@corp.com")])
        conn = _cloud(find_users)

        await conn._fetch_users(active_pipeshub_users=[])

        assert _presence(conn) is Presence.PRESENT

    async def test_a_found_authenticator_is_listed(self) -> None:
        conn = _cloud(lambda **_: _resp(OK, [_acct("a1", "creator@corp.com")]))

        await conn._fetch_users(active_pipeshub_users=[])

        assert _presence(conn) is Presence.PRESENT

    @pytest.mark.parametrize("answer", [
        lambda **_: _resp(500),
        lambda **_: _resp(OK, {"not": "a list"}),
        lambda **_: _raise(OSError("reset")),
        lambda **_: _resp(OK, [_acct("a9", None)]),
    ])
    async def test_a_lookup_that_cannot_answer_leaves_them_unknown(self, answer) -> None:
        conn = _cloud(answer)

        await conn._fetch_users(active_pipeshub_users=[])

        assert _presence(conn) is Presence.UNKNOWN

    async def test_no_authenticator_email_is_unknown(self) -> None:
        conn = _cloud(lambda **_: _resp(OK, []), creator=None)

        await conn._fetch_users(active_pipeshub_users=[])

        assert _presence(conn) is Presence.UNKNOWN

    async def test_an_app_account_beside_the_found_authenticator_does_not_hide_the_match(self) -> None:
        conn = _cloud(_by_query([_acct("bot", None, kind="app"), _acct("a1", "creator@corp.com")]))

        await conn._fetch_users(active_pipeshub_users=[])

        assert _presence(conn) is Presence.PRESENT


def _cloud_bulk(rows: list[dict], find_users: Callable[..., object], **kwargs: object) -> MagicMock:
    """A Cloud connector whose bulk read succeeds with these rows."""
    conn = _cloud(find_users, **kwargs)
    conn._fetch_all_jira_users_bulk = AsyncMock(return_value=rows)
    return conn


def _inactive(account_id: str, email: str | None = None) -> dict:
    return {**_acct(account_id, email), "active": False}


class TestJiraCloudBulkReadWithHiddenEmails:
    """A listed account whose email the read cannot resolve may be the authenticator, so a complete bulk
    read settles the link only once the authenticator's own lookup finds them."""

    async def test_a_read_where_every_email_shows_proves_absence_with_no_lookup(self) -> None:
        conn = _cloud_bulk([_acct("a1", "other@corp.com"), _inactive("a2", "gone@corp.com")],
                           lambda **_: pytest.fail("looked up"))

        await conn._fetch_users(active_pipeshub_users=[])

        assert _presence(conn) is Presence.ABSENT

    @pytest.mark.parametrize("hidden", [_inactive("a2"), _acct("a2", None)])
    async def test_a_hidden_email_and_an_authenticator_the_lookup_does_not_find_is_unknown(
        self, hidden: dict,
    ) -> None:
        # The active-only search cannot find a deactivated account, so the hidden one may still be them.
        conn = _cloud_bulk([_acct("a1", "other@corp.com"), hidden], _by_query([]))

        await conn._fetch_users(active_pipeshub_users=[])

        assert _presence(conn) is Presence.UNKNOWN

    async def test_a_hidden_email_and_an_authenticator_the_lookup_finds_is_listed(self) -> None:
        conn = _cloud_bulk([_acct("a1", "other@corp.com"), _inactive("a2")],
                           _by_query([_acct("a2", "creator@corp.com")]))

        await conn._fetch_users(active_pipeshub_users=[])

        assert _presence(conn) is Presence.PRESENT

    async def test_a_hidden_email_beside_the_authenticators_own_listed_email_needs_no_lookup(self) -> None:
        conn = _cloud_bulk([_acct("a1", "creator@corp.com"), _inactive("a2")], lambda **_: pytest.fail("looked up"))

        await conn._fetch_users(active_pipeshub_users=[])

        assert _presence(conn) is Presence.PRESENT

    @pytest.mark.parametrize("find_users", [
        _by_query([_inactive("a2")]),
        _by_query([], token_rows=[]),
        lambda **_: _resp(500),
    ])
    async def test_a_hidden_email_the_lookup_cannot_settle_leaves_them_unknown(self, find_users: Callable[..., object]) -> None:
        conn = _cloud_bulk([_acct("a1", "other@corp.com"), _inactive("a2")], find_users)

        await conn._fetch_users(active_pipeshub_users=[])

        assert _presence(conn) is Presence.UNKNOWN


class TestJiraDataCenterListReadWithHiddenEmails:
    def _conn(self, rows: list[dict], lookup: Callable[..., object]) -> MagicMock:
        conn = _dc(_search([], lookup), user_list=True)
        conn._fetch_users_via_list = AsyncMock(return_value=rows)
        return conn

    async def test_a_read_where_every_email_shows_proves_absence_with_no_lookup(self) -> None:
        conn = self._conn([_row("k1", "other@corp.com"), _row("k2", "gone@corp.com", active=False)],
                          lambda _: pytest.fail("looked up"))

        await conn._fetch_users()

        assert _presence(conn) is Presence.ABSENT

    @pytest.mark.parametrize("hidden", [_row("k2", None, active=False), _row("k2", None)])
    async def test_a_hidden_email_and_an_authenticator_the_lookup_does_not_find_is_absent(self, hidden: dict) -> None:
        conn = self._conn([_row("k1", "other@corp.com"), hidden], lambda _: _resp(OK, []))

        await conn._fetch_users()

        assert _presence(conn) is Presence.ABSENT

    async def test_a_hidden_email_and_an_authenticator_the_lookup_finds_is_listed(self) -> None:
        conn = self._conn([_row("k1", "other@corp.com"), _row("k2", None, active=False)],
                          lambda _: _resp(OK, [_row("k2", "creator@corp.com", active=False)]))

        await conn._fetch_users()


    @pytest.mark.parametrize("lookup", [lambda _: _resp(OK, [_row("k2", None)]), lambda _: _resp(500)])
    async def test_a_hidden_email_the_lookup_cannot_settle_leaves_them_unknown(self, lookup: Callable[..., object]) -> None:
        conn = self._conn([_row("k1", "other@corp.com"), _row("k2", None, active=False)], lookup)

        await conn._fetch_users()

        assert _presence(conn) is Presence.UNKNOWN
