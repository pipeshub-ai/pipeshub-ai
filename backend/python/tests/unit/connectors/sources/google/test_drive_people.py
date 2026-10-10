"""Authorship fields mapped from Drive v3 file metadata, and the masks that request them."""

import pytest

from app.connectors.sources.google.common import drive_file_fields as fields
from app.connectors.sources.google.common.drive_people import drive_file_people
from app.models.entities import SourcePerson

ALICE = {
    "kind": "drive#user",
    "displayName": "Alice Smith",
    "emailAddress": "alice@example.com",
    "permissionId": "04577261180293727771",
    "me": True,
}
BOB = {
    "kind": "drive#user",
    "displayName": "Bob Jones",
    "emailAddress": "bob@example.com",
    "permissionId": "11223344556677889900",
    "me": False,
}


def test_my_drive_file_maps_owner_author_and_last_modifier() -> None:
    people = drive_file_people({"id": "f1", "owners": [ALICE], "lastModifyingUser": BOB})

    alice = SourcePerson(source_id="04577261180293727771", email="alice@example.com", display_name="Alice Smith")
    bob = SourcePerson(source_id="11223344556677889900", email="bob@example.com", display_name="Bob Jones")
    assert people.owners == [alice]
    assert people.authored_by == alice
    assert people.last_modified_by == bob


def test_shared_drive_file_has_no_author() -> None:
    people = drive_file_people({"id": "f1", "driveId": "sd-1", "owners": [], "lastModifyingUser": BOB})

    assert people.authored_by is None
    assert people.owners == []
    assert people.last_modified_by is not None
    assert people.last_modified_by.email == "bob@example.com"


def test_shared_drive_file_with_stray_owner_still_has_no_author() -> None:
    people = drive_file_people({"id": "f1", "driveId": "sd-1", "owners": [ALICE]})

    assert people.authored_by is None


def test_missing_people_fields_give_empty_values() -> None:
    people = drive_file_people({"id": "f1"})

    assert people.owners == []
    assert people.authored_by is None
    assert people.last_modified_by is None


def test_user_without_email_or_permission_id_is_dropped() -> None:
    people = drive_file_people({"id": "f1", "lastModifyingUser": {"displayName": "Someone"}, "owners": [{"displayName": "X"}]})

    assert people.last_modified_by is None
    assert people.owners == []
    assert people.authored_by is None


def test_service_account_is_marked() -> None:
    robot = {"displayName": "sync bot", "emailAddress": "bot@proj.iam.gserviceaccount.com", "permissionId": "999"}
    people = drive_file_people({"id": "f1", "owners": [robot], "lastModifyingUser": robot})

    assert people.authored_by is not None and people.authored_by.is_service_account
    assert people.last_modified_by is not None and people.last_modified_by.is_service_account


@pytest.mark.parametrize(
    "mask",
    [
        fields.DRIVE_PERSONAL_SYNC_FILE_RESOURCE_FIELDS,
        fields.DRIVE_PERSONAL_SYNC_FILES_LIST_FIELDS,
        fields.DRIVE_PERSONAL_SYNC_CHANGES_LIST_FIELDS,
        fields.DRIVE_WORKSPACE_SYNC_FILE_RESOURCE_FIELDS,
        fields.DRIVE_WORKSPACE_SYNC_FILES_LIST_FIELDS,
        fields.DRIVE_WORKSPACE_SYNC_CHANGES_LIST_FIELDS,
        fields.DRIVE_WORKSPACE_FILE_GET_FIELDS,
    ],
)
def test_sync_masks_request_people_fields(mask) -> None:
    requested = {f.strip() for f in mask.replace("(", ",").replace(")", ",").split(",")}
    assert "owners" in requested
    assert "lastModifyingUser" in requested
    assert "driveId" in requested
