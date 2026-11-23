"""Without a licence key a company-wide Zendesk connector reads no access through these methods."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ee.permission_sync.zendesk import ZendeskPermissionsMixin
from app.models.entities import AppUser, AppUserGroup


def _connector(*, gated: bool) -> ZendeskPermissionsMixin:
    """The mixin alone, with every way it reaches Zendesk, a sync point or the graph replaced by a mock."""
    connector = type("Connector", (ZendeskPermissionsMixin,), {})()
    connector.connector_id = "connector-1"
    connector.logger = MagicMock()
    connector.data_entities_processor = MagicMock()
    connector.data_entities_processor.skips_permission_writes = AsyncMock(return_value=gated)
    connector.data_source = MagicMock()
    connector._get_fresh_datasource = AsyncMock(return_value=connector.data_source)
    connector._call_incremental = AsyncMock()
    connector._call_api_with_retry = AsyncMock()
    connector._fetch_paginated_list_checked = AsyncMock(return_value=([], True))
    connector.records_sync_point = MagicMock()
    connector.data_store_provider = MagicMock()
    connector._skipped_this_sync = []
    connector._roles_complete = True
    connector._role_ticket_access = {}
    connector._user_id_to_data = {}
    connector._user_id_to_app_user = {}
    connector._org_id_to_data = {}
    return connector


def _assert_nothing_read(connector: ZendeskPermissionsMixin) -> None:
    connector.data_entities_processor.skips_permission_writes.assert_awaited_with("connector-1")
    connector._get_fresh_datasource.assert_not_awaited()
    connector._call_incremental.assert_not_awaited()
    connector._call_api_with_retry.assert_not_awaited()
    connector._fetch_paginated_list_checked.assert_not_awaited()
    assert connector.data_source.method_calls == []
    assert connector.records_sync_point.method_calls == []
    assert connector.data_store_provider.method_calls == []
    # Only the gate was asked: nothing was written either, and no part of the run counts as skipped.
    assert [call[0] for call in connector.data_entities_processor.mock_calls] == ["skips_permission_writes"]
    assert connector._skipped_this_sync == []


_USER = AppUser(app_name="ZENDESK", connector_id="connector-1", source_user_id="1", org_id="org-1", email="a@acme.com",
                full_name="A")
_GROUP = AppUserGroup(app_name="ZENDESK", connector_id="connector-1", source_user_group_id="group_7", name="G",
                      org_id="org-1")
_TICKET_PAGE = {"users": [{"id": 5, "role": "end-user", "organization_id": 9, "email": "c@acme.com"}]}

# Every gated method the free connector calls, and what it gets back when the gate holds.
ENTRY_POINTS = [
    ("_fetch_custom_role_ticket_access", (), {}, ({}, True)),
    ("_ticket_group_members", (MagicMock(), {"1": _USER}), {}, ({}, [], True)),
    ("_sync_ticket_access_groups", ([(_GROUP, [_USER])],), {"users_complete": True, "memberships_complete": True}, None),
    ("_sync_organizations", (), {"users_complete": True}, (0, True)),
    ("_users_revoked_since_last_sync", (), {}, (False, None)),
    ("_add_sideloaded_org_members", (_TICKET_PAGE,), {}, None),
    ("_fetch_ticket_permissions", ("555",), {}, []),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "args", "kwargs", "expected"), ENTRY_POINTS, ids=[e[0] for e in ENTRY_POINTS])
async def test_gated_connector_reads_nothing_from_zendesk(name: str, args: tuple, kwargs: dict, expected: object) -> None:
    connector = _connector(gated=True)

    assert await getattr(connector, name)(*args, **kwargs) == expected

    _assert_nothing_read(connector)


@pytest.mark.asyncio
async def test_gated_ticket_members_read_no_membership_list() -> None:
    connector = _connector(gated=True)
    datasource = MagicMock()

    await connector._ticket_group_members(datasource, {"1": _USER})

    assert datasource.method_calls == []


@pytest.mark.asyncio
async def test_licensed_connector_still_reads_custom_roles() -> None:
    connector = _connector(gated=False)
    # A plan without custom roles answers 403: the same result as the gate, but read from Zendesk.
    connector._call_api = AsyncMock(return_value=MagicMock(status_code=403, success=False))

    assert await connector._fetch_custom_role_ticket_access() == ({}, True)

    connector._call_api.assert_awaited_once_with(connector.data_source.list_custom_roles)


@pytest.mark.asyncio
async def test_licensed_connector_still_reads_ticket_group_members() -> None:
    connector = _connector(gated=False)
    datasource = MagicMock()
    connector._fetch_paginated_list_checked = AsyncMock(return_value=([], False))

    assert await connector._ticket_group_members(datasource, {}) == ({}, [], False)

    connector._fetch_paginated_list_checked.assert_awaited_once_with(
        datasource.list_group_memberships, "group_memberships",
    )
