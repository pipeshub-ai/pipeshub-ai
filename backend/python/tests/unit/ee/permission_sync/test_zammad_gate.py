"""Without a licence key a company-wide Zammad connector reads no access through these methods."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.sources.zammad.read_outcome import ReadOutcome
from app.ee.permission_sync.zammad import ZammadPermissionsMixin


def _connector(*, gated: bool) -> ZammadPermissionsMixin:
    """The mixin alone, with every way it reaches Zammad or the graph replaced by a mock."""
    connector = type("Connector", (ZammadPermissionsMixin,), {})()
    connector.connector_id = "connector-1"
    connector.logger = MagicMock()
    connector.data_entities_processor = MagicMock()
    connector.data_entities_processor.skips_permission_writes = AsyncMock(return_value=gated)
    connector.data_source = MagicMock()
    connector._get_fresh_datasource = AsyncMock(return_value=connector.data_source)
    connector._user_id_to_data = {7: {"email": "a@acme.com", "role_ids": [1]}}
    connector._incomplete = []
    return connector


def _assert_nothing_read(connector: ZammadPermissionsMixin) -> None:
    connector.data_entities_processor.skips_permission_writes.assert_awaited_with("connector-1")
    connector._get_fresh_datasource.assert_not_awaited()
    assert connector.data_source.method_calls == []
    # Only the gate was asked: nothing was written either, and no part of the run counts as failed.
    assert [call[0] for call in connector.data_entities_processor.mock_calls] == ["skips_permission_writes"]
    assert connector._incomplete == []


_ROLES = [{"id": 1, "name": "Agent", "active": True}]

# Every gated method the free connector calls, and what it gets back when the gate holds.
ENTRY_POINTS = [
    ("_fetch_roles", lambda c: (), ([], ReadOutcome.COMPLETE)),
    ("_sync_roles", lambda c: (_ROLES, {"a@acme.com": MagicMock()}), None),
    ("_kb_category_access", lambda c: (c.data_source, 1, 5), ([], [], True)),
    ("_kb_category_editor_role_ids", lambda c: (c.data_source, 1, 5), []),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "args", "expected"), ENTRY_POINTS, ids=[e[0] for e in ENTRY_POINTS])
async def test_gated_connector_reads_nothing_from_zammad(name: str, args: object, expected: object) -> None:
    connector = _connector(gated=True)

    assert await getattr(connector, name)(*args(connector)) == expected

    _assert_nothing_read(connector)


@pytest.mark.asyncio
async def test_licensed_connector_still_reads_category_permissions() -> None:
    connector = _connector(gated=False)
    connector.data_source.get_kb_category_permissions = AsyncMock(return_value=MagicMock(
        success=True, data={"roles_editor": [{"id": 2}], "roles_reader": [{"id": 3}], "permissions": []},
    ))

    assert await connector._kb_category_access(connector.data_source, 1, 5) == ([2], [3], True)
    assert await connector._kb_category_editor_role_ids(connector.data_source, 1, 5) == [2]

    connector.data_source.get_kb_category_permissions.assert_awaited_with(kb_id=1, cat_id=5)


@pytest.mark.asyncio
async def test_licensed_connector_still_writes_roles() -> None:
    connector = _connector(gated=False)
    connector.data_entities_processor.org_id = "org-1"
    connector.data_entities_processor.on_new_app_roles = AsyncMock()
    connector._parse_zammad_datetime = lambda value: 0
    member = MagicMock()

    await connector._sync_roles(_ROLES, {"a@acme.com": member})

    (role, members), = connector.data_entities_processor.on_new_app_roles.await_args.args[0]
    assert role.source_role_id == "1" and members == [member]
