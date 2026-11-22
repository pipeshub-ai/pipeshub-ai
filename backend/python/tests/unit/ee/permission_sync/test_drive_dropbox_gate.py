"""Without a licence key a company-wide Dropbox or Google Drive connector reads no access through these methods."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.sources.dropbox_individual.connector import (
    DropboxIndividualConnector,
)
from app.connectors.sources.google.drive.individual.connector import (
    GoogleDriveIndividualConnector,
)
from app.ee.permission_sync.dropbox_team import DropboxPermissionsMixin
from app.ee.permission_sync.google_drive_workspace import (
    GoogleDriveWorkspacePermissionsMixin,
)


def _connector(mixin: type, *, gated: bool) -> object:
    """The mixin alone, with every way it reaches the source, a checkpoint or the graph replaced by a mock."""
    connector = type("Connector", (mixin,), {})()
    connector.connector_id = "connector-1"
    connector.logger = MagicMock()
    connector.data_entities_processor = MagicMock()
    connector.data_entities_processor.skips_permission_writes = AsyncMock(return_value=gated)
    connector.data_source = MagicMock()
    connector.admin_data_source = MagicMock()
    connector.dropbox_cursor_sync_point = MagicMock()
    connector._load_limited_pending = AsyncMock(return_value={})
    connector._save_limited_pending = AsyncMock()
    connector._upgrade_drives = set()
    return connector


def _assert_nothing_read(connector: object) -> None:
    connector.data_entities_processor.skips_permission_writes.assert_awaited_with("connector-1")
    assert connector.data_source.method_calls == []
    assert connector.admin_data_source.method_calls == []
    assert connector.dropbox_cursor_sync_point.method_calls == []
    connector._load_limited_pending.assert_not_awaited()
    # Only the gate was asked: nothing was written either.
    assert [call[0] for call in connector.data_entities_processor.mock_calls] == ["skips_permission_writes"]


# Every gated method the free connector calls, and what it gets back when the gate holds.
ENTRY_POINTS = [
    (DropboxPermissionsMixin, "_convert_dropbox_permissions_to_permissions", ("id:1", True, "dbmid:a"), []),
    (DropboxPermissionsMixin, "_shared_folder_members", ("sf-1", "dbmid:a"), []),
    (DropboxPermissionsMixin, "_sync_user_groups", (), None),
    (DropboxPermissionsMixin, "_sync_group_changes_with_cursor", (), None),
    (DropboxPermissionsMixin, "_sync_sharing_changes_with_cursor", ("sharing-key", "cursor-1"), None),
    (GoogleDriveWorkspacePermissionsMixin, "_sync_user_groups", (), None),
    (GoogleDriveWorkspacePermissionsMixin, "_clear_cut_out_grants_below_limited_folders", (), None),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mixin", "name", "args", "expected"), ENTRY_POINTS, ids=[f"{e[0].__name__}.{e[1]}" for e in ENTRY_POINTS],
)
async def test_gated_connector_reads_nothing_from_the_source(
    mixin: type, name: str, args: tuple, expected: object,
) -> None:
    connector = _connector(mixin, gated=True)

    assert await getattr(connector, name)(*args) == expected

    _assert_nothing_read(connector)


@pytest.mark.asyncio
async def test_gated_dropbox_file_grants_cover_nobody() -> None:
    connector = _connector(DropboxPermissionsMixin, gated=True)

    grants = await connector._convert_dropbox_permissions_to_permissions("id:1", True, "dbmid:a")

    # The same answer as a file Dropbox says has no members of its own: the record is written and inherits.
    assert grants == [] and grants.covered_emails == set()


@pytest.mark.asyncio
async def test_licensed_dropbox_still_reads_shared_folder_members() -> None:
    connector = _connector(DropboxPermissionsMixin, gated=False)
    connector.data_source.sharing_list_folder_members = AsyncMock(return_value=MagicMock(success=False, error="down"))

    assert await connector._shared_folder_members("sf-1", "dbmid:a") is None

    connector.data_source.sharing_list_folder_members.assert_awaited_once_with(
        shared_folder_id="sf-1", team_member_id="dbmid:a",
    )


@pytest.mark.asyncio
async def test_licensed_drive_still_settles_limited_folders() -> None:
    connector = _connector(GoogleDriveWorkspacePermissionsMixin, gated=False)

    await connector._clear_cut_out_grants_below_limited_folders()

    connector._load_limited_pending.assert_awaited_once()


@pytest.mark.parametrize(
    ("personal", "mixin"),
    [
        (DropboxIndividualConnector, DropboxPermissionsMixin),
        (GoogleDriveIndividualConnector, GoogleDriveWorkspacePermissionsMixin),
    ],
)
def test_personal_connectors_share_no_permission_reading(personal: type, mixin: type) -> None:
    assert mixin not in personal.__mro__
