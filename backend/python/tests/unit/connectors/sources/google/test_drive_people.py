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


# Hidden owner emails and direct shares.

from app.connectors.sources.google.common.drive_people import drive_shared_with  # noqa: E402


def _grant(pid: str, email: str | None, role: str, *, kind: str = "user", **extra: object) -> dict:
    grant: dict = {"id": pid, "type": kind, "role": role, **extra}
    if email:
        grant["emailAddress"] = email
    return grant


def test_owner_email_is_filled_from_the_permission_with_the_same_id() -> None:
    hidden = {"displayName": "test", "permissionId": "01448"}
    people = drive_file_people(
        {"id": "f1", "owners": [hidden]}, [_grant("01448", "test@ext.test", "owner")],
    )

    assert people.owners[0].email == "test@ext.test"
    assert people.owners[0].source_id == "01448"
    assert people.authored_by is not None and people.authored_by.email == "test@ext.test"


def test_last_modifier_email_is_filled_the_same_way() -> None:
    hidden = {"displayName": "abhishek", "permissionId": "0366"}
    people = drive_file_people({"id": "f1", "lastModifyingUser": hidden}, [_grant("0366", "ab@ext.test", "writer")])

    assert people.last_modified_by is not None and people.last_modified_by.email == "ab@ext.test"


def test_an_email_drive_did_give_is_never_replaced() -> None:
    people = drive_file_people({"id": "f1", "owners": [ALICE]}, [_grant(ALICE["permissionId"], "other@x.test", "owner")])

    assert people.owners[0].email == "alice@example.com"


def test_shared_drive_grants_follow_permission_details() -> None:
    direct = _grant("1", "bob@x.test", "writer", permissionDetails=[{"permissionType": "file", "inherited": False}])
    inherited = _grant("2", "cat@x.test", "reader", permissionDetails=[{"permissionType": "member", "inherited": True}])

    shared = drive_shared_with([direct, inherited], owner_ids=set(), parent_grants=None, has_details=True)

    assert [p.email for p in shared] == ["bob@x.test"]


def test_my_drive_grant_absent_from_the_parent_is_direct() -> None:
    grants = [_grant("o", "alice@x.test", "owner"), _grant("1", "bob@x.test", "writer"), _grant("2", "cat@x.test", "reader")]
    parent = [_grant("o", "alice@x.test", "owner"), _grant("2", "cat@x.test", "reader")]

    shared = drive_shared_with(grants, owner_ids={"o"}, parent_grants=parent, has_details=False)

    assert [(p.source_id, p.email) for p in shared] == [("1", "bob@x.test")]


def test_my_drive_role_ranking() -> None:
    def shared(file_role: str, parent_role: str) -> list[str]:
        return [p.email for p in drive_shared_with(
            [_grant("1", "bob@x.test", file_role)], owner_ids=set(),
            parent_grants=[_grant("1", "bob@x.test", parent_role)], has_details=False,
        )]

    assert shared("writer", "reader") == ["bob@x.test"]
    assert shared("reader", "writer") == []
    assert shared("writer", "writer") == []


def test_a_parent_grant_is_matched_by_email_when_ids_differ() -> None:
    shared = drive_shared_with(
        [_grant("1", "Bob@X.test", "reader")], owner_ids=set(),
        parent_grants=[_grant("9", "bob@x.test", "reader")], has_details=False,
    )

    assert shared == []


def test_no_parent_means_every_grant_is_direct() -> None:
    shared = drive_shared_with([_grant("1", "bob@x.test", "reader")], owner_ids=set(), parent_grants=[], has_details=False)

    assert [p.email for p in shared] == ["bob@x.test"]


def test_an_unknown_parent_leaves_my_drive_shares_unknown() -> None:
    assert drive_shared_with([_grant("1", "bob@x.test", "reader")], owner_ids=set(), parent_grants=None, has_details=False) is None


def test_owners_are_never_shared_with() -> None:
    grants = [_grant("o", "alice@x.test", "owner"), _grant("o2", "dan@x.test", "writer")]

    shared = drive_shared_with(grants, owner_ids={"o2"}, parent_grants=[], has_details=False)

    assert shared == []


def test_non_person_grants_are_ignored() -> None:
    grants = [
        _grant("g", "team@x.test", "reader", kind="group"),
        {"id": "d", "type": "domain", "role": "reader", "domain": "x.test"},
        {"id": "anyoneWithLink", "type": "anyone", "role": "reader"},
        _grant("del", "gone@x.test", "reader", deleted=True),
        {"type": "user", "role": "reader"},
    ]

    assert drive_shared_with(grants, owner_ids=set(), parent_grants=[], has_details=False) == []
