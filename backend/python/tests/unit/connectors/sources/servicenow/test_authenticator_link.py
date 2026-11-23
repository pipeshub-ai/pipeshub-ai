"""ServiceNow links the user who authenticated the connector only after a complete user listing.

The removal pass runs before anything else in a sync. The full pass runs once the users are written,
told whether the listing was read in full and every listed user's email, an inactive one too (its
connector access is withdrawn, so no stored edge shows it as listed).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from servicenow_fakes import T0, FakeServiceNow, MemorySyncPoint, wire_listing

from app.sources.external.servicenow.models import TableAPIRecord


def _user(n: int, email: str | None = None, **fields: str) -> dict:
    return {
        "sys_id": f"u{n}", "user_name": f"user{n}", "email": f"user{n}@corp.com" if email is None else email,
        "active": "true", "sys_updated_on": T0, "company": None, "department": None, "location": None,
        "cost_center": None, **fields,
    }


def _wire_users(sc, rows: list[dict], *, delta: bool = False) -> FakeServiceNow:
    fake = FakeServiceNow({"sys_user": rows})
    wire_listing(sc, fake)
    stored = {"users/v2": {"done": True}, "users": {"last_sync_time": "2024-01-01 00:00:00"}} if delta else {}
    sc.user_sync_point = MemorySyncPoint(stored)
    sc._users_gone = AsyncMock(return_value=set())
    sc.data_entities_processor.withdraw_app_user_access = AsyncMock()
    return fake


class _Stop(Exception):
    pass


async def test_a_full_listing_is_complete_and_names_every_email_whatever_the_status(servicenow_connector) -> None:
    sc = servicenow_connector
    _wire_users(sc, [_user(1, "Live@Corp.com"), _user(2, "Gone@corp.com", active="false"), _user(3, email="")])
    sc._listed_user_emails = {"stale@corp.com"}

    await sc._sync_users()

    assert sc._users_listing_complete is True
    assert sc._listed_user_emails == {"live@corp.com", "gone@corp.com"}


async def test_an_empty_full_listing_is_incomplete(servicenow_connector) -> None:
    sc = servicenow_connector
    _wire_users(sc, [])
    sc._users_listing_complete = True

    await sc._sync_users()

    assert sc._users_listing_complete is False


async def test_a_delta_listing_that_finds_nothing_new_is_complete(servicenow_connector) -> None:
    sc = servicenow_connector
    _wire_users(sc, [], delta=True)

    await sc._sync_users()

    assert sc._users_listing_complete is True


async def test_a_user_row_that_fails_to_transform_makes_the_listing_incomplete(servicenow_connector) -> None:
    sc = servicenow_connector
    _wire_users(sc, [_user(1)])
    sc._sync_read_incomplete = False

    async def fail(_: object) -> None:
        sc._sync_read_incomplete = True

    with patch.object(sc, "_transform_to_app_user", AsyncMock(side_effect=fail)):
        await sc._sync_users()

    assert sc._users_listing_complete is False


async def test_a_listing_that_raises_never_stays_complete(servicenow_connector) -> None:
    sc = servicenow_connector
    _wire_users(sc, [_user(1)])
    sc._users_listing_complete = True
    sc._get_fresh_datasource = AsyncMock(side_effect=RuntimeError("down"))

    with pytest.raises(RuntimeError):
        await sc._sync_users()

    assert sc._users_listing_complete is False


class TestSyncOrder:
    def _wire(self, sc, order: list[str], users_error: Exception | None = None) -> None:
        def step(name: str, error: Exception | None = None, result: object = None) -> AsyncMock:
            async def _run(*_: object, **__: object) -> object:
                order.append(name)
                if error:
                    raise error
                return result
            return AsyncMock(side_effect=_run)

        sc.servicenow_client = MagicMock()
        sc.drop_authenticator_link_if_listed = step("drop")
        sc._load_filters = step("filters")
        sc._read_access_state = step("access", result=(None, True))
        sc._sync_users_and_groups = step("users", users_error)
        sc._register_authenticated_identity = step("register", _Stop())
        sc._users_listing_complete = True

    async def test_the_removal_pass_runs_first_and_the_full_pass_after_the_users(self, servicenow_connector) -> None:
        order: list[str] = []
        self._wire(servicenow_connector, order)

        with pytest.raises(_Stop):
            await servicenow_connector.run_sync()

        assert order == ["drop", "filters", "access", "users", "register"]

    async def test_the_removal_pass_runs_even_when_the_filters_cannot_be_read(self, servicenow_connector) -> None:
        order: list[str] = []
        self._wire(servicenow_connector, order)
        servicenow_connector._load_filters = AsyncMock(side_effect=RuntimeError("filters"))

        with pytest.raises(RuntimeError):
            await servicenow_connector.run_sync()

        assert order == ["drop"]

    async def test_no_full_pass_when_the_user_sync_raises(self, servicenow_connector) -> None:
        order: list[str] = []
        self._wire(servicenow_connector, order, users_error=RuntimeError("down"))

        with pytest.raises(RuntimeError):
            await servicenow_connector.run_sync()

        assert "register" not in order

    async def test_a_flag_left_by_an_earlier_run_is_reset_before_anything_is_read(self, servicenow_connector) -> None:
        sc = servicenow_connector
        order: list[str] = []
        self._wire(sc, order)
        sc._listed_user_emails = {"stale@corp.com"}
        sc._load_filters = AsyncMock(side_effect=RuntimeError("filters"))

        with pytest.raises(RuntimeError):
            await sc.run_sync()

        assert sc._users_listing_complete is False and sc._listed_user_emails == set()


async def test_the_full_pass_carries_the_listing_state(servicenow_connector) -> None:
    sc = servicenow_connector
    sc._users_listing_complete = True
    sc._listed_user_emails = {"creator@corp.com"}
    sc.register_authenticated_source_user = AsyncMock()
    datasource = MagicMock()
    datasource.get_now_table_tableName = AsyncMock(
        return_value=MagicMock(result=[TableAPIRecord(sys_id="a1", email="admin@corp.com")])
    )
    sc._get_fresh_datasource = AsyncMock(return_value=datasource)

    await sc._register_authenticated_identity()

    kwargs = sc.register_authenticated_source_user.await_args.kwargs
    assert kwargs == {"listing_complete": True, "extra_present_emails": {"creator@corp.com"}}
