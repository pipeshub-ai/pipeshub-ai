# Copyright (c) 2026 PipesHub Inc.
# Licensed under the PipesHub Enterprise License. See the LICENSE file in this
# ee folder. Free for development, testing and evaluation. Production use needs
# a PipesHub licence key.

"""Without a licence key a company-wide OneDrive connector reads no access from OneDrive, SharePoint or Entra ID."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.sources.microsoft.onedrive.connector import OneDriveConnector
from app.ee.permission_sync.onedrive import OneDrivePermissionsMixin, _FolderWalk


def _connector(*, gated: bool) -> OneDriveConnector:
    """A OneDrive connector whose every way to the source, a checkpoint or the graph is a mock."""
    processor = MagicMock()
    processor.org_id = "org-1"
    processor.skips_permission_writes = AsyncMock(return_value=gated)
    connector = OneDriveConnector(MagicMock(), processor, MagicMock(), MagicMock(), "connector-1", "team", "creator")
    connector.msgraph_client = MagicMock()
    connector.client = MagicMock()
    connector.scope_reader = MagicMock()
    connector.drive_delta_sync_point = MagicMock()
    connector.user_group_sync_point = MagicMock()
    connector._reset_permission_scopes()
    connector._items_without_known_inheritance = {}
    connector._inheriting_from_full_read = {}
    return connector


def _assert_nothing_read(connector: OneDriveConnector) -> None:
    for source in (connector.msgraph_client, connector.client, connector.scope_reader):
        assert source.method_calls == []
    assert connector.drive_delta_sync_point.method_calls == []
    assert connector.user_group_sync_point.method_calls == []
    assert [call[0] for call in connector.data_entities_processor.mock_calls] == ["skips_permission_writes"]
    assert connector._items_without_known_inheritance == {}


def _item() -> MagicMock:
    item = MagicMock()
    item.id = "item-1"
    item.parent_reference.drive_id = "drive-1"
    item.parent_reference.id = "folder-1"
    item.root = None
    return item


# Every gated method the free connector calls, and what it gets back when the gate holds.
ENTRY_POINTS = [
    # Taken as inheriting its folder's access: nothing of its own to read, so no grants.
    ("_inherits_parent_access", (_item(),), True),
    ("_update_folder_children_permissions", ("drive-1", "folder-1"), _FolderWalk()),
    # Still owed: the first licensed run reads them.
    ("_retry_pending_access_reads", (["item-1"], "sync-key"), ["item-1"]),
    ("_reevaluate_item_access", ("drive-1", ["item-1"]), ["item-1"]),
    ("_sync_user_groups", (), None),
    # Free, with its own gate: the drive's permission mode is not settled from a run that read no access.
    ("_settle_permission_mode", ("user-1", "drive-1", {"enumerating": True}, []), None),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "args", "expected"), ENTRY_POINTS, ids=[e[0] for e in ENTRY_POINTS])
async def test_gated_connector_reads_nothing_from_the_source(name: str, args: tuple, expected: object) -> None:
    connector = _connector(gated=True)

    assert await getattr(connector, name)(*args) == expected

    _assert_nothing_read(connector)


@pytest.mark.asyncio
async def test_gated_item_is_written_with_no_grants_and_no_permission_read() -> None:
    connector = _connector(gated=True)
    processor = connector.data_entities_processor
    processor.get_record_by_external_id = AsyncMock(return_value=None)
    connector.msgraph_client.get_signed_url = AsyncMock(return_value="https://signed")
    connector.msgraph_client.get_file_permission = AsyncMock()
    item = _item()
    item.deleted = item.shared = item.folder = None
    item.name, item.e_tag, item.c_tag, item.size, item.web_url = "plan.pdf", "etag", "ctag", 10, "https://item"
    item.created_date_time = item.last_modified_date_time = datetime.now(timezone.utc)
    item.file.mime_type = "application/pdf"
    item.file.hashes = None
    item.parent_reference.path = "/drive/root:"
    connector._pass_date_filters = MagicMock(return_value=True)
    connector._pass_extension_filter = MagicMock(return_value=True)

    update = await connector._process_delta_item(item)

    # The content is still synced; the processor keeps whatever access is stored.
    assert update is not None and update.is_new and update.record.record_name == "plan.pdf"
    assert update.new_permissions == []
    connector.msgraph_client.get_file_permission.assert_not_called()
    assert connector.scope_reader.method_calls == []


@pytest.mark.asyncio
async def test_licensed_connector_still_reads_pending_access() -> None:
    connector = _connector(gated=False)
    connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
    connector.drive_delta_sync_point.update_sync_point = AsyncMock()

    assert await connector._retry_pending_access_reads(["item-1"], "sync-key") == []

    connector.data_entities_processor.get_record_by_external_id.assert_awaited_once_with("connector-1", "item-1")
    connector.drive_delta_sync_point.update_sync_point.assert_awaited_once_with(
        "sync-key", sync_point_data={"pendingAccessReads": []}
    )


def test_the_connector_takes_its_permission_reading_from_ee() -> None:
    assert OneDrivePermissionsMixin in OneDriveConnector.__mro__
