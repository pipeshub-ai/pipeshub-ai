"""Linear links the user who authenticated the connector only after a complete user listing.

The removal pass runs before the user listing. The full pass runs once the users are written and the
absent ones withdrawn, told whether the listing was read in full and every listed account's email,
a guest too (its connector access is withdrawn).
"""

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.core.registry.filters import FilterCollection
from app.connectors.sources.linear import connector as connector_module
from app.connectors.sources.linear.api_gateway import LinearPaginationError
from tests.unit.connectors.sources.linear.test_linear_connector_behaviour import (
    _make_connector,
)


class _Stop(Exception):
    pass


def _listing(connector: Any, users: list[dict], error: Exception | None = None) -> None:
    connector.data_source = MagicMock()

    async def pages(*_: object, **__: object) -> AsyncIterator[tuple]:
        yield users, None, None
        if error:
            raise error

    connector._api.pages = pages


@pytest.mark.asyncio
async def test_a_listing_read_to_its_end_is_complete_and_names_every_listed_account() -> None:
    # Linear's users query does not pass includeDisabled, so a suspended account is never in the
    # listing; guests are, and are withdrawn from the connector like other non-full members.
    connector = _make_connector()
    _listing(connector, [
        {"id": "u1", "email": "Live@Corp.com", "active": True},
        {"id": "u2", "email": "guest@corp.com", "active": True, "guest": True},
        {"id": "u3", "active": True},
    ])
    connector._listed_user_emails = {"stale@corp.com"}

    await connector._fetch_users()

    assert connector._users_listing_complete is True
    assert connector._listed_user_emails == {"live@corp.com", "guest@corp.com"}


@pytest.mark.asyncio
async def test_an_empty_listing_is_incomplete() -> None:
    connector = _make_connector()
    _listing(connector, [])
    connector._users_listing_complete = True

    await connector._fetch_users()

    assert connector._users_listing_complete is False


@pytest.mark.asyncio
async def test_a_listing_that_cannot_continue_is_incomplete() -> None:
    connector = _make_connector()
    _listing(connector, [{"id": "u1", "email": "a@corp.com"}], error=LinearPaginationError("more without a cursor"))
    connector._users_listing_complete = True

    with pytest.raises(RuntimeError):
        await connector._fetch_users()

    assert connector._users_listing_complete is False


class TestRunOrder:
    def _conn(self, order: list[str], *, active: list | None = None, fetch_error: Exception | None = None):
        def step(name: str, result: Any = None, error: Exception | None = None) -> AsyncMock:
            async def _run(*_a: object, **_k: object) -> Any:
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

    async def _run(self, connector: Any) -> None:
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
        connector._users_listing_complete, connector._listed_user_emails = True, {"stale@corp.com"}

        await self._run(connector)

        assert connector._users_listing_complete is False and connector._listed_user_emails == set()


@pytest.mark.asyncio
async def test_the_full_pass_carries_the_listing_state() -> None:
    connector = _make_connector()
    connector.data_source = MagicMock()
    connector._users_listing_complete = True
    connector._listed_user_emails = {"creator@corp.com"}
    connector.register_authenticated_source_user = AsyncMock()
    datasource = MagicMock()
    datasource.viewer = AsyncMock(return_value=MagicMock(success=True, data={"viewer": {"id": "v1", "email": "a@corp.com"}}))
    connector._get_fresh_datasource = AsyncMock(return_value=datasource)

    await connector._register_authenticated_identity()

    assert connector.register_authenticated_source_user.await_args.kwargs == {
        "listing_complete": True, "extra_present_emails": {"creator@corp.com"},
    }
