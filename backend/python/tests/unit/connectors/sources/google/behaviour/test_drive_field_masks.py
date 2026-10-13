"""Every list mask names only properties Drive returns; anything else is a 400."""

import pytest
from drive_world import LIST_RESPONSE_FIELDS, top_level_fields

from app.connectors.sources.google.common.drive_file_fields import (
    DRIVE_DRIVES_LIST_FIELDS,
    DRIVE_FOLDER_EXPANSION_LIST_FIELDS,
    DRIVE_PERSONAL_SYNC_CHANGES_LIST_FIELDS,
    DRIVE_PERSONAL_SYNC_FILES_LIST_FIELDS,
    DRIVE_WORKSPACE_SYNC_CHANGES_LIST_FIELDS,
    DRIVE_WORKSPACE_SYNC_FILES_LIST_FIELDS,
)


@pytest.mark.parametrize(
    ("mask", "resource"),
    [
        (DRIVE_PERSONAL_SYNC_FILES_LIST_FIELDS, "files"),
        (DRIVE_WORKSPACE_SYNC_FILES_LIST_FIELDS, "files"),
        (DRIVE_FOLDER_EXPANSION_LIST_FIELDS, "files"),
        (DRIVE_PERSONAL_SYNC_CHANGES_LIST_FIELDS, "changes"),
        (DRIVE_WORKSPACE_SYNC_CHANGES_LIST_FIELDS, "changes"),
        (DRIVE_DRIVES_LIST_FIELDS, "drives"),
    ],
)
def test_list_masks_name_only_properties_drive_returns(mask: str, resource: str) -> None:
    unknown = set(top_level_fields(mask)) - LIST_RESPONSE_FIELDS[resource]
    assert not unknown, f"Drive rejects {sorted(unknown)} on {resource}.list with 400"


def test_the_fake_drive_reads_nested_masks_as_one_top_level_field() -> None:
    assert top_level_fields("nextPageToken, changes(fileId, file(id, name)), drive/id") == [
        "nextPageToken",
        "changes",
        "drive",
    ]
