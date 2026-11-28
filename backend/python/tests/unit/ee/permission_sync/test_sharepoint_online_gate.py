# Copyright (c) 2026 PipesHub Inc.
# Licensed under the PipesHub Enterprise License. See the LICENSE file in this
# ee folder. Free for development, testing and evaluation. Production use needs
# a PipesHub licence key.

"""Without a licence key a company-wide SharePoint connector reads no access from SharePoint or Entra ID."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ee.permission_sync.sharepoint_online import SharePointPermissionsMixin


class _Connector(SharePointPermissionsMixin):
    def __init__(self, gated: bool) -> None:
        self.connector_id = "connector-1"
        self.data_entities_processor = MagicMock()
        self.data_entities_processor.skips_permission_writes = AsyncMock(return_value=gated)
        self.client = MagicMock()
        self._sharepoint_rest_pages = MagicMock()
        self._sharepoint_rest_get = AsyncMock()
        self._inheritance_when_unreadable = AsyncMock(return_value=False)
        self.data_entities_processor.get_record_group_by_external_id = AsyncMock(return_value=None)


# Every method the free connector calls, and what it gets back when the gate holds: no grants (the processor keeps the
# stored ones), never None (which would count the part as unreadable and fail the run).
ENTRY_POINTS = [
    ("_get_site_permissions", ("site",), []),
    ("_get_list_permissions", ("site", "list"), []),
    ("_get_list_item_permissions", ("site", "list", "item"), []),
    ("_get_page_permissions", ("site", "page"), []),
    ("_get_site_pages_scope", ("site",), None),
    ("_get_library_access", ("site", MagicMock(id="drive")), (False, [])),
    ("_get_page_access", ("site", "page", MagicMock()), (True, [])),
    ("_drive_item_access", (MagicMock(id="item"), "drive", "site"), (True, [])),
    ("_sync_user_groups", ([MagicMock()],), None),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "args", "expected"), ENTRY_POINTS, ids=[e[0] for e in ENTRY_POINTS])
async def test_gated_connector_reads_nothing_from_the_source(name: str, args: tuple, expected: object) -> None:
    connector = _Connector(gated=True)

    assert await getattr(connector, name)(*args) == expected

    connector.data_entities_processor.skips_permission_writes.assert_awaited_with("connector-1")
    assert connector.client.mock_calls == []
    connector._sharepoint_rest_pages.assert_not_called()
    connector._sharepoint_rest_get.assert_not_awaited()


@pytest.mark.asyncio
async def test_licensed_connector_still_reads_the_source() -> None:
    connector = _Connector(gated=False)
    connector._drive_item_has_unique_permissions = AsyncMock(return_value=True)
    connector._unique_drive_item_role_grants = AsyncMock(return_value=[])
    connector._get_item_permissions = AsyncMock(return_value=[])
    item = MagicMock(id="item")

    assert await connector._drive_item_access(item, "drive", "site") == (False, [])

    connector._drive_item_has_unique_permissions.assert_awaited_once_with(item, "drive")
    connector._get_item_permissions.assert_awaited_once_with("site", "drive", "item")


@pytest.mark.asyncio
async def test_licensed_connector_still_syncs_groups() -> None:
    connector = _Connector(gated=False)
    connector.logger = MagicMock()
    connector._health = MagicMock()
    connector._sync_azure_ad_groups = AsyncMock()
    connector.data_entities_processor.on_new_user_groups = AsyncMock()

    await connector._sync_user_groups([])

    connector._sync_azure_ad_groups.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("stored_inherits", [True, False])
async def test_gated_library_keeps_its_stored_inheritance(stored_inherits: bool) -> None:
    """The processor writes a library's inherit edge from this answer even while it skips grants, so a gated run
    hands back the edge that is stored, not "inherits"."""
    connector = _Connector(gated=True)
    connector.data_entities_processor.get_record_group_by_external_id = AsyncMock(return_value=MagicMock(id="rg-1"))
    connector._inheritance_when_unreadable = AsyncMock(return_value=stored_inherits)

    assert await connector._get_library_access("site", MagicMock(id="drive")) == (stored_inherits, [])

    connector._inheritance_when_unreadable.assert_awaited_once_with("recordGroups", "rg-1")
    assert connector.client.mock_calls == []
