"""Without a licence key a company-wide SharePoint connector reads no access from SharePoint or Entra ID."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ee.permission_sync.sharepoint_online import SharePointPermissionsMixin


class _Connector(SharePointPermissionsMixin):
    def __init__(self, gated: bool) -> None:
        self.connector_id = "connector-1"
        self.data_entities_processor = MagicMock()
        self.data_entities_processor.permission_sync_gated = AsyncMock(return_value=gated)
        self.client = MagicMock()
        self._sharepoint_rest_pages = MagicMock()
        self._sharepoint_rest_get = AsyncMock()


# Every method the free connector calls, and what it gets back when the gate holds: no grants, inherit.
ENTRY_POINTS = [
    ("_get_site_permissions", ("site",), []),
    ("_get_list_permissions", ("site", "list"), []),
    ("_get_list_item_permissions", ("site", "list", "item"), []),
    ("_get_page_permissions", ("site", "page"), []),
    ("_get_site_pages_scope", ("site",), None),
    ("_get_library_access", ("site", MagicMock(id="drive")), (True, [])),
    ("_get_page_access", ("site", "page", MagicMock()), (True, [])),
    ("_drive_item_access", (MagicMock(id="item"), "drive", "site"), (True, [])),
    ("_sync_user_groups", ([MagicMock()],), None),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "args", "expected"), ENTRY_POINTS, ids=[e[0] for e in ENTRY_POINTS])
async def test_gated_connector_reads_nothing_from_the_source(name: str, args: tuple, expected: object) -> None:
    connector = _Connector(gated=True)

    assert await getattr(connector, name)(*args) == expected

    connector.data_entities_processor.permission_sync_gated.assert_awaited_with("connector-1")
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
