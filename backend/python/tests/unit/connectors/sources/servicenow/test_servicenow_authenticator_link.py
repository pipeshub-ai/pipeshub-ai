"""ServiceNow links the user who authenticated the connector only when its user listing proves them absent.

The removal pass runs before anything else in a sync. The full pass runs once the users are written,
with the listing the user sync built: every listed user, an inactive one too. A full read lists every
user; a delta, or a full read with a user that has no email, is settled by looking the authenticator up.
"""

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from servicenow_fakes import T0, FakeServiceNow, MemorySyncPoint, wire_listing

from app.connectors.core.base.connector.source_user_listing import (
    Presence,
    SourceUserListing,
)
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


CREATOR = "creator@corp.com"


def _presence(sc) -> Presence:
    return sc._user_listing.presence(CREATOR)


_VISIBLE = object()


def _lookup(
    sc, rows: list | None = None, error: Exception | None = None, email: str | None = CREATOR,
    total: object = _VISIBLE,
) -> AsyncMock:
    """The authenticator's lookup answers ``rows``, counted ``total`` before ACLs (by default, every row shows)."""
    sc.authenticator_email = AsyncMock(return_value=email)
    listing_get = sc._table_get
    rows = rows or []
    lookup = AsyncMock(side_effect=error, return_value=MagicMock(
        result=rows, total_count=len(rows) if total is _VISIBLE else total))

    async def table_get(table: str, **kwargs: object) -> object:
        if str(kwargs.get("sysparm_query", "")).startswith("emailLIKE"):
            return await lookup(table, **kwargs)
        return await listing_get(table, **kwargs)

    sc._table_get = table_get
    return lookup


async def test_a_full_listing_proves_an_unlisted_creator_absent_and_names_every_email_whatever_the_status(
    servicenow_connector,
) -> None:
    sc = servicenow_connector
    _wire_users(sc, [_user(1, "Live@Corp.com"), _user(2, "Gone@corp.com", active="false")])
    lookup = _lookup(sc)

    await sc._sync_users()

    assert _presence(sc) is Presence.ABSENT
    assert sc._user_listing.emails == {"live@corp.com", "gone@corp.com"}
    lookup.assert_not_awaited()


async def test_an_inactive_creator_in_a_full_listing_is_present(servicenow_connector) -> None:
    sc = servicenow_connector
    _wire_users(sc, [_user(1), _user(2, "Creator@corp.com", active="false")])

    await sc._sync_users()

    assert _presence(sc) is Presence.PRESENT


async def test_a_user_without_an_email_in_a_full_listing_is_settled_by_the_lookup(servicenow_connector) -> None:
    sc = servicenow_connector
    _wire_users(sc, [_user(1), _user(3, email="")])
    lookup = _lookup(sc, [])

    await sc._sync_users()

    lookup.assert_awaited_once()
    assert _presence(sc) is Presence.ABSENT


async def test_a_user_without_an_email_and_a_failed_lookup_leave_the_creator_unknown(servicenow_connector) -> None:
    sc = servicenow_connector
    _wire_users(sc, [_user(1), _user(3, email="")])
    _lookup(sc, error=RuntimeError("down"))

    await sc._sync_users()

    assert _presence(sc) is Presence.UNKNOWN


async def test_an_empty_full_listing_leaves_the_creator_unknown(servicenow_connector) -> None:
    sc = servicenow_connector
    _wire_users(sc, [])
    _lookup(sc, [])

    await sc._sync_users()

    assert _presence(sc) is Presence.UNKNOWN


async def test_a_delta_whose_lookup_finds_the_authenticator_in_any_status_lists_them(servicenow_connector) -> None:
    sc = servicenow_connector
    _wire_users(sc, [_user(1)], delta=True)
    table_get = _lookup(sc, [TableAPIRecord(sys_id="a1", email="Creator@Corp.com")])

    await sc._sync_users()

    assert _presence(sc) is Presence.PRESENT
    query = table_get.await_args.kwargs["sysparm_query"]
    assert query == "emailLIKEcreator@corp.com" and "active" not in query


async def test_a_delta_whose_lookup_finds_nothing_proves_absence(servicenow_connector) -> None:
    sc = servicenow_connector
    _wire_users(sc, [_user(1)], delta=True)
    _lookup(sc, [])

    await sc._sync_users()

    assert _presence(sc) is Presence.ABSENT


async def test_a_delta_that_lists_nobody_leaves_the_creator_unknown(servicenow_connector) -> None:
    sc = servicenow_connector
    _wire_users(sc, [], delta=True)
    _lookup(sc, [])

    await sc._sync_users()

    assert _presence(sc) is Presence.UNKNOWN


async def test_the_lookup_asks_for_the_count_before_acls(servicenow_connector) -> None:
    sc = servicenow_connector
    _wire_users(sc, [_user(1)], delta=True)
    table_get = _lookup(sc, [])

    await sc._sync_users()

    assert "sysparm_no_count" in table_get.await_args.kwargs
    assert table_get.await_args.kwargs["sysparm_no_count"] is None


@pytest.mark.parametrize(("rows", "total"), [
    ([], 1),  # an ACL hid a matching row: it may be the authenticator
    ([TableAPIRecord(sys_id="a2", email="creator@corp.com.au")], 2),
    ([], None),  # no count: nothing says a row was not hidden
    ([TableAPIRecord(sys_id="a3", email="")], 1),  # the row's email field is hidden
])
async def test_a_delta_whose_lookup_may_hide_the_authenticator_leaves_them_unknown(
    servicenow_connector, rows, total,
) -> None:
    sc = servicenow_connector
    _wire_users(sc, [_user(1)], delta=True)
    _lookup(sc, rows, total=total)

    await sc._sync_users()

    assert _presence(sc) is Presence.UNKNOWN


async def test_a_delta_whose_lookup_fails_leaves_the_creator_unknown(servicenow_connector) -> None:
    sc = servicenow_connector
    _wire_users(sc, [_user(1)], delta=True)
    _lookup(sc, error=RuntimeError("down"))

    await sc._sync_users()

    assert _presence(sc) is Presence.UNKNOWN


async def test_a_delta_with_no_authenticator_email_looks_nobody_up(servicenow_connector) -> None:
    sc = servicenow_connector
    _wire_users(sc, [_user(1)], delta=True)
    lookup = _lookup(sc, email=None)

    await sc._sync_users()

    lookup.assert_not_awaited()
    assert _presence(sc) is Presence.UNKNOWN


async def test_a_literal_caret_in_the_email_cannot_add_a_condition(servicenow_connector) -> None:
    sc = servicenow_connector
    _wire_users(sc, [_user(1)], delta=True)
    table_get = _lookup(sc, [], email="a^active=true@corp.com")

    await sc._sync_users()

    assert table_get.await_args.kwargs["sysparm_query"] == "emailLIKEa^^active=true@corp.com"


async def test_a_listing_that_shows_fewer_users_than_it_counts_leaves_the_creator_unknown(
    servicenow_connector,
) -> None:
    sc = servicenow_connector
    _wire_users(sc, [_user(1)])
    lookup = _lookup(sc, [])

    async def iter_table(*_a: object, listing=None, **_k: object) -> AsyncIterator[list[TableAPIRecord]]:
        listing.total, listing.seen = 5, 1  # an ACL or domain separation hid four rows
        yield [TableAPIRecord(**_user(1))]

    sc._iter_table = iter_table

    await sc._sync_users()

    assert _presence(sc) is Presence.UNKNOWN
    lookup.assert_not_awaited()


async def test_a_user_row_that_fails_to_transform_leaves_the_creator_unknown(servicenow_connector) -> None:
    sc = servicenow_connector
    _wire_users(sc, [_user(1)])
    _lookup(sc, [])
    sc._sync_read_incomplete = False

    async def fail(_: object) -> None:
        sc._sync_read_incomplete = True

    with patch.object(sc, "_transform_to_app_user", AsyncMock(side_effect=fail)):
        await sc._sync_users()

    assert _presence(sc) is Presence.UNKNOWN


async def test_a_listing_that_raises_never_proves_absence(servicenow_connector) -> None:
    sc = servicenow_connector
    _wire_users(sc, [_user(1)])
    sc._get_fresh_datasource = AsyncMock(side_effect=RuntimeError("down"))

    with pytest.raises(RuntimeError):
        await sc._sync_users()

    assert _presence(sc) is Presence.UNKNOWN


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
        sc._user_listing = SourceUserListing(includes_inactive=True)
        sc._load_filters = AsyncMock(side_effect=RuntimeError("filters"))

        with pytest.raises(RuntimeError):
            await sc.run_sync()

        assert sc._user_listing is None


async def test_the_full_pass_carries_the_listing_state(servicenow_connector) -> None:
    sc = servicenow_connector
    listing = sc._user_listing = SourceUserListing(includes_inactive=True)
    sc.register_authenticated_source_user = AsyncMock()
    datasource = MagicMock()
    datasource.get_now_table_tableName = AsyncMock(
        return_value=MagicMock(result=[TableAPIRecord(sys_id="a1", email="admin@corp.com")])
    )
    sc._get_fresh_datasource = AsyncMock(return_value=datasource)

    await sc._register_authenticated_identity()

    kwargs = sc.register_authenticated_source_user.await_args.kwargs
    assert kwargs == {"listing": listing}
