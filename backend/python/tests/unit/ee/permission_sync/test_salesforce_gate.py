"""Without a licence key a company-wide Salesforce connector reads no access from Salesforce and writes no access edge.

Salesforce wrote its record and account grants straight to the store, past the processor's licence gate. That writer
(``_sync_permissions_edges``) now asks the gate itself, and every other access write of the connector goes through a
processor method that asks it (``skips_permission_writes``)."""

import ast
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.core.registry.filters import FilterCollection
from app.connectors.sources.salesforce.connector import SalesforceConnector
from app.connectors.sources.salesforce.eligibility import READERS_SOQL
from app.ee.permission_sync.salesforce import SalesforcePermissionsMixin

SALESFORCE = Path(__file__).resolve().parents[4] / "app" / "connectors" / "sources" / "salesforce"


class _Pages:
    """A stand-in for ``_soql_query_paginated``: every query that is actually read (iterated) is recorded. A stream
    that is only created makes no call to Salesforce."""

    def __init__(self, rows_by_query: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.rows_by_query = rows_by_query or {}
        self.read: list[str] = []

    def __call__(self, api_version: str = "", q: str = "", **_: object) -> AsyncIterator[list[dict[str, Any]]]:
        async def pages() -> AsyncIterator[list[dict[str, Any]]]:
            self.read.append(q)
            rows = next((r for key, r in self.rows_by_query.items() if key in q), [])
            if rows:
                yield rows
        return pages()


def _connector(*, gated: bool) -> SalesforceConnector:
    processor = MagicMock()
    processor.org_id = "org-1"
    processor.skips_permission_writes = AsyncMock(return_value=gated)
    store = MagicMock()
    connector = SalesforceConnector(
        logger=MagicMock(), data_entities_processor=processor, data_store_provider=store,
        config_service=MagicMock(), connector_id="connector-1",
    )
    connector.data_source = MagicMock()
    connector.data_source.composite = AsyncMock()
    connector._soql_query_paginated = _Pages()
    return connector


def _assert_nothing_read_or_written(connector: SalesforceConnector) -> None:
    connector.data_entities_processor.skips_permission_writes.assert_awaited_with("connector-1")
    assert connector._soql_query_paginated.read == []
    assert connector.data_source.method_calls == []
    assert connector.data_store_provider.method_calls == []
    # Only the gate was asked: no role, group, grant or record group was handed to the processor either.
    assert [c[0] for c in connector.data_entities_processor.mock_calls] == ["skips_permission_writes"]


@pytest.mark.asyncio
async def test_gated_role_sync_reads_no_role() -> None:
    connector = _connector(gated=True)
    roles, members = connector._soql_query_paginated(q="FROM UserRole"), connector._soql_query_paginated(q="UserRoleId")

    assert await connector._sync_roles(roles, members) is None

    _assert_nothing_read_or_written(connector)


@pytest.mark.asyncio
async def test_gated_group_sync_reads_no_group_or_member() -> None:
    connector = _connector(gated=True)

    assert await connector._sync_user_groups("59.0", connector._soql_query_paginated(q="FROM Group")) is None

    _assert_nothing_read_or_written(connector)


@pytest.mark.asyncio
async def test_gated_permission_edges_read_and_write_nothing() -> None:
    connector = _connector(gated=True)

    with patch("app.ee.permission_sync.salesforce.notify_connector_permissions_changed", new=AsyncMock()) as notify:
        assert await connector._sync_permissions_edges("59.0") is None

    _assert_nothing_read_or_written(connector)
    notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_licensed_role_and_group_sync_still_read_salesforce() -> None:
    connector = _connector(gated=False)
    connector._soql_query_paginated = _Pages({
        "FROM UserRole": [{"Id": "role-1", "Name": "Sales"}],
        "UserRoleId": [{"Id": "005A", "Email": "a@x.com", "UserRoleId": "role-1"}],
        # The member query also names Group, so it is matched first.
        "GroupMembers": [{"Id": "grp-1", "GroupMembers": {"records": [
            {"UserOrGroupId": "005A", "UserOrGroup": {"Email": "a@x.com"}}]}}],
        "FROM Group": [{"Id": "grp-1", "Name": "Team", "Type": "Regular"}],
    })
    connector.data_entities_processor.on_new_app_roles = AsyncMock()
    connector.data_entities_processor.on_new_user_groups = AsyncMock()

    await connector._sync_roles(connector._soql_query_paginated(q="FROM UserRole"),
                                connector._soql_query_paginated(q="UserRoleId"))
    await connector._sync_user_groups("59.0", connector._soql_query_paginated(q="FROM Group"))

    [(role, role_members)] = connector.data_entities_processor.on_new_app_roles.await_args.args[0]
    assert role.source_role_id == "role-1" and [m.email for m in role_members] == ["a@x.com"]
    [(group, group_members)] = connector.data_entities_processor.on_new_user_groups.await_args.args[0]
    assert group.source_user_group_id == "grp-1" and [m.email for m in group_members] == ["a@x.com"]


@pytest.mark.asyncio
async def test_licensed_permission_edges_still_read_salesforce() -> None:
    connector = _connector(gated=False)
    connector.data_store_provider.transaction.side_effect = RuntimeError("stop after the read")

    with pytest.raises(RuntimeError, match="stop after the read"):
        await connector._sync_permissions_edges("59.0")

    assert connector._soql_query_paginated.read[0] == READERS_SOQL


@pytest.mark.asyncio
async def test_gated_full_run_reads_no_access_and_writes_no_permission_edge() -> None:
    """A whole run of the free connector without a licence key: content steps run, every access step is skipped, and
    the store is never asked to write or delete a PERMISSION edge."""
    connector = _connector(gated=True)
    connector._reinitialize_token_if_needed = AsyncMock()
    connector._get_api_version = AsyncMock(return_value="59.0")
    connector._register_authenticated_identity = AsyncMock()
    connector.user_sync_point = MagicMock(read_sync_point=AsyncMock(return_value={}), update_sync_point=AsyncMock())
    connector.records_sync_point = MagicMock(read_sync_point=AsyncMock(return_value={}), update_sync_point=AsyncMock())
    content_steps = ("_sync_users", "_withdraw_ineligible_users", "_sync_accounts", "_sync_contacts", "_sync_leads",
                     "_sync_products", "_sync_opportunities", "_sync_sold_in_edges", "_sync_cases", "_sync_tasks",
                     "_sync_files")
    for step in content_steps:
        setattr(connector, step, AsyncMock(return_value=set()))
    connector._deletions = MagicMock(return_value=MagicMock(run=AsyncMock()))
    connector.data_entities_processor.on_new_record_groups = AsyncMock()
    connector._file_links = MagicMock(return_value=MagicMock(run=AsyncMock()))

    with patch("app.connectors.sources.salesforce.connector.load_connector_filters", new_callable=AsyncMock,
               return_value=(FilterCollection(), FilterCollection())):
        await connector.run_sync()

    for step in content_steps:
        getattr(connector, step).assert_awaited()
    # Roles, groups, members, UserRecordAccess and product readers are never asked for.
    assert connector._soql_query_paginated.read == []
    connector.data_source.composite.assert_not_awaited()
    assert connector.data_store_provider.method_calls == []
    for name in ("on_new_app_roles", "on_new_user_groups", "upsert_permission_edge"):
        assert getattr(connector.data_entities_processor, name).mock_calls == []
    # The "Unlinked" groups are still handed to the processor with their org grant; it keeps what is stored.
    unlinked = [rg for c in connector.data_entities_processor.on_new_record_groups.await_args_list for rg, _ in c.args[0]]
    assert {rg.external_group_id for rg in unlinked} >= {"UNASSIGNED-CASE", "UNASSIGNED-TASK"}


def _free_salesforce_sources() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in SALESFORCE.rglob("*.py")}


@pytest.mark.parametrize("name", ["PERMISSION", "USER_APP_RELATION", "INHERIT_PERMISSIONS"])
def test_free_connector_names_no_access_edge_collection(name: str) -> None:
    """The free connector never writes an access edge to the store itself: every grant it hands over goes through a
    processor method, and each of those asks the licence gate."""
    offenders = [
        f"{file}:{node.lineno}"
        for file, source in _free_salesforce_sources().items()
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Attribute) and node.attr == name
        and isinstance(node.value, ast.Name) and node.value.id == "CollectionNames"
    ]
    assert offenders == []


@pytest.mark.parametrize(
    "method", ["upsert_permission_edge", "on_new_app_roles", "on_new_user_groups", "on_updated_record_permissions"],
)
def test_free_connector_calls_no_source_access_writer(method: str) -> None:
    offenders = [
        f"{file}:{node.lineno}"
        for file, source in _free_salesforce_sources().items()
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Attribute) and node.attr == method
    ]
    assert offenders == []


def test_moved_writer_is_the_one_that_names_permission_edges() -> None:
    """The direct writer did move here, so the check above is not passing because it was renamed."""
    source = Path(__file__).resolve().parents[4].joinpath("app/ee/permission_sync/salesforce.py").read_text()
    assert "collection=CollectionNames.PERMISSION.value" in source


def test_salesforce_inherits_the_permission_mixin() -> None:
    assert SalesforcePermissionsMixin in SalesforceConnector.__mro__
