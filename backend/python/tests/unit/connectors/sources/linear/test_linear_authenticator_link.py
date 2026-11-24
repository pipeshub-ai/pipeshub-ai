"""Linear links the user who authenticated the connector only when its user listing proves them absent.

The removal pass runs before the user listing. The full pass runs once the users are written and the
absent ones withdrawn, with the listing the user sync built: every listed account, suspended ones and
guests too (their connector access is withdrawn).
"""

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.core.base.connector.source_user_listing import (
    Presence,
    SourceUserListing,
)
from app.connectors.core.registry.filters import FilterCollection
from app.connectors.sources.linear import connector as connector_module
from app.connectors.sources.linear.api_gateway import LinearPaginationError
from app.connectors.sources.linear.connector import LinearConnector
from tests.unit.connectors.sources.linear.test_linear_connector_behaviour import (
    _make_connector,
)


class _Stop(Exception):
    pass


def _listing(connector: LinearConnector, users: list[dict], error: Exception | None = None) -> None:
    connector.data_source = MagicMock()

    async def pages(*_: object, **__: object) -> AsyncIterator[tuple]:
        yield users, None, None
        if error:
            raise error

    connector._api.pages = pages


CREATOR = "creator@corp.com"


@pytest.mark.asyncio
async def test_a_listing_read_to_its_end_proves_an_unlisted_creator_absent_and_names_every_account() -> None:
    # Guests and suspended accounts are listed too, and are withdrawn from the connector like
    # other non-full members.
    connector = _make_connector()
    _listing(connector, [
        {"id": "u1", "email": "Live@Corp.com", "active": True},
        {"id": "u2", "email": "guest@corp.com", "active": True, "guest": True},
        {"id": "u4", "email": "Suspended@Corp.com", "active": False},
    ])

    users = await connector._fetch_users()

    assert connector._user_listing.presence(CREATOR) is Presence.ABSENT
    assert connector._user_listing.emails == {"live@corp.com", "guest@corp.com", "suspended@corp.com"}
    assert "suspended@corp.com" not in {u.email for u in users}


@pytest.mark.asyncio
async def test_a_suspended_creator_is_present() -> None:
    connector = _make_connector()
    _listing(connector, [{"id": "u1", "email": "a@corp.com"}, {"id": "u4", "email": "Creator@Corp.com", "active": False}])

    await connector._fetch_users()

    assert connector._user_listing.presence(CREATOR) is Presence.PRESENT


@pytest.mark.asyncio
async def test_the_listing_asks_for_suspended_accounts_too() -> None:
    connector = _make_connector()
    connector.data_source = MagicMock()
    fetches: list = []

    async def pages(*_: object, fetch: object, **__: object) -> AsyncIterator[tuple]:
        fetches.append(fetch)
        yield [{"id": "u1", "email": "a@corp.com", "active": True}], None, None

    connector._api.pages = pages
    await connector._fetch_users()
    datasource = MagicMock(users=AsyncMock())

    await fetches[0](datasource, 50, "cursor")

    datasource.users.assert_awaited_once_with(first=50, after="cursor", includeDisabled=True)


@pytest.mark.asyncio
async def test_the_users_query_passes_include_disabled_to_linear() -> None:
    from app.sources.client.graphql.response import GraphQLResponse
    from app.sources.external.linear.linear import LinearDataSource

    client = MagicMock()
    client.get_client.return_value.execute = AsyncMock(return_value=GraphQLResponse(success=True, data={}))

    await LinearDataSource(client).users(first=50, includeDisabled=True)

    sent = client.get_client.return_value.execute.await_args.kwargs
    assert sent["variables"]["includeDisabled"] is True
    assert "$includeDisabled: Boolean" in sent["query"]
    assert "includeDisabled: $includeDisabled" in sent["query"]


@pytest.mark.asyncio
async def test_a_listed_account_without_an_email_leaves_the_creator_unknown() -> None:
    # Linear's User.email is non-null: a row without one is malformed, and may be the authenticator.
    connector = _make_connector()
    _listing(connector, [{"id": "u1", "email": "live@corp.com", "active": True}, {"id": "u3", "active": True}])

    await connector._fetch_users()

    assert connector._user_listing.presence(CREATOR) is Presence.UNKNOWN
    assert connector._user_listing.presence("live@corp.com") is Presence.PRESENT


@pytest.mark.asyncio
async def test_an_empty_listing_leaves_the_creator_unknown() -> None:
    connector = _make_connector()
    _listing(connector, [])

    await connector._fetch_users()

    assert connector._user_listing.presence(CREATOR) is Presence.UNKNOWN


@pytest.mark.asyncio
async def test_a_listing_that_cannot_continue_leaves_the_creator_unknown() -> None:
    connector = _make_connector()
    _listing(connector, [{"id": "u1", "email": "a@corp.com"}], error=LinearPaginationError("more without a cursor"))

    with pytest.raises(RuntimeError):
        await connector._fetch_users()

    assert connector._user_listing.presence(CREATOR) is Presence.UNKNOWN


class TestRunOrder:
    def _conn(self, order: list[str], *, active: list | None = None, fetch_error: Exception | None = None) -> LinearConnector:
        def step(name: str, result: object = None, error: Exception | None = None) -> AsyncMock:
            async def _run(*_a: object, **_k: object) -> object:
                order.append(name)
                if error:
                    raise error
                return result
            return AsyncMock(side_effect=_run)

        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.drop_authenticator_link_if_listed = step("drop")
        connector.data_entities_processor.get_all_active_users = step(
            "active", [MagicMock()] if active is None else active
        )
        connector.data_entities_processor.on_new_app_users = step("write")
        connector._fetch_users = step("fetch", [MagicMock(email="a@corp.com")], fetch_error)
        connector._withdraw_absent_app_users = step("withdraw")
        connector._register_authenticated_identity = step("register", error=_Stop())
        return connector

    async def _run(self, connector: LinearConnector) -> None:
        with patch.object(
            connector_module, "load_connector_filters",
            new=AsyncMock(return_value=(FilterCollection(), FilterCollection())),
        ):
            await connector.run_sync()

    @pytest.mark.asyncio
    async def test_the_removal_pass_runs_first_and_the_full_pass_after_the_users_are_written_and_withdrawn(self) -> None:
        order: list[str] = []
        connector = self._conn(order)

        with pytest.raises(_Stop):
            await self._run(connector)

        assert order == ["drop", "active", "fetch", "write", "withdraw", "register"]

    @pytest.mark.asyncio
    async def test_the_removal_pass_runs_even_when_the_run_stops_for_no_active_users(self) -> None:
        order: list[str] = []
        connector = self._conn(order, active=[])

        await self._run(connector)

        assert order == ["drop", "active"]

    @pytest.mark.asyncio
    async def test_the_removal_pass_runs_even_when_the_connector_is_not_initialized(self) -> None:
        order: list[str] = []
        connector = self._conn(order)
        connector.data_source = None

        with pytest.raises(RuntimeError, match="not initialized"):
            await self._run(connector)

        assert order == ["drop"]

    @pytest.mark.asyncio
    async def test_no_full_pass_when_the_user_listing_raises(self) -> None:
        order: list[str] = []
        connector = self._conn(order, fetch_error=RuntimeError("down"))

        with pytest.raises(RuntimeError):
            await self._run(connector)

        assert order == ["drop", "active", "fetch"]

    @pytest.mark.asyncio
    async def test_a_flag_left_by_an_earlier_run_is_reset_before_the_removal_pass(self) -> None:
        order: list[str] = []
        connector = self._conn(order, active=[])
        connector._user_listing = SourceUserListing(includes_inactive=True)

        await self._run(connector)

        assert connector._user_listing is None


@pytest.mark.asyncio
async def test_the_full_pass_carries_the_listing_state() -> None:
    connector = _make_connector()
    connector.data_source = MagicMock()
    listing = connector._user_listing = SourceUserListing(includes_inactive=True)
    connector.register_authenticated_source_user = AsyncMock()
    datasource = MagicMock()
    datasource.viewer = AsyncMock(return_value=MagicMock(success=True, data={"viewer": {"id": "v1", "email": "a@corp.com"}}))
    connector._get_fresh_datasource = AsyncMock(return_value=datasource)

    await connector._register_authenticated_identity()

    assert connector.register_authenticated_source_user.await_args.kwargs == {"listing": listing}
