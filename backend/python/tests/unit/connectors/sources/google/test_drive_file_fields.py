"""Field masks ask Drive for the metadata both connectors now depend on."""

from app.connectors.sources.google.common.drive_file_fields import (
    DRIVE_DRIVES_LIST_FIELDS,
    DRIVE_PERSONAL_SYNC_CHANGES_LIST_FIELDS,
    DRIVE_PERSONAL_SYNC_FILES_LIST_FIELDS,
    DRIVE_WORKSPACE_SYNC_FILES_LIST_FIELDS,
)


def test_file_masks_include_access_and_shortcut_fields() -> None:
    for mask in (DRIVE_PERSONAL_SYNC_FILES_LIST_FIELDS, DRIVE_WORKSPACE_SYNC_FILES_LIST_FIELDS):
        assert "shortcutDetails/targetId" in mask
        assert "shortcutDetails/targetMimeType" in mask
        assert "ownedByMe" in mask
        assert "trashed" in mask
        assert "capabilities/canEdit" in mask
        assert "capabilities/canDownload" in mask
        assert "incompleteSearch" in mask


def test_personal_changes_and_drives_masks() -> None:
    changes = DRIVE_PERSONAL_SYNC_CHANGES_LIST_FIELDS
    assert "changeType" in changes
    assert "driveId" in changes
    assert "removed" in changes
    assert "drive(id,name)" in changes
    assert "incompleteSearch" in changes

    drives = DRIVE_DRIVES_LIST_FIELDS
    assert "drives(" in drives
    assert "capabilities/canEdit" in drives
    assert "capabilities/canManageMembers" in drives
    assert "createdTime" in drives
