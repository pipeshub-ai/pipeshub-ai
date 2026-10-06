"""Export format, extension and permission decisions for a Drive file."""

import pytest

from app.connectors.sources.google.drive.utils.drive_mime import (
    DriveNativeKind,
    classify_drive_mime,
    download_filename,
    drive_content_changed,
    is_not_exportable,
    permission_for_drive_item,
    permission_for_shared_drive,
    resolve_extension,
    shared_for_indexing,
)
from app.models.permission import PermissionType

DOC = "application/vnd.google-apps.document"
SHEET = "application/vnd.google-apps.spreadsheet"
SLIDES = "application/vnd.google-apps.presentation"
SHORTCUT = "application/vnd.google-apps.shortcut"
FORM = "application/vnd.google-apps.form"


@pytest.mark.parametrize(
    ("metadata", "extension"),
    [
        ({"mimeType": DOC, "name": "x.py"}, "docx"),
        ({"mimeType": SHEET, "name": "budget"}, "xlsx"),
        ({"mimeType": SLIDES, "name": "deck.pptx"}, "pptx"),
        ({"mimeType": "text/plain", "fileExtension": "TXT"}, "txt"),
        ({"mimeType": "text/plain", "name": "Notes.PDF"}, "pdf"),
        ({"mimeType": "image/png", "name": "photo"}, "png"),
        ({"mimeType": FORM, "name": "Survey"}, None),
        ({"mimeType": SHORTCUT, "name": "link.docx"}, None),
        ({"mimeType": "application/vnd.google-apps.folder", "name": "dir"}, None),
    ],
)
def test_resolve_extension(metadata: dict, extension: str | None) -> None:
    assert resolve_extension(metadata) == extension


def test_native_types_are_classified() -> None:
    assert classify_drive_mime(DOC) is DriveNativeKind.EXPORTABLE
    assert classify_drive_mime(SHORTCUT) is DriveNativeKind.SHORTCUT
    assert classify_drive_mime(FORM) is DriveNativeKind.NOT_EXPORTABLE
    assert classify_drive_mime("application/vnd.google-apps.site") is DriveNativeKind.NOT_EXPORTABLE
    assert is_not_exportable("application/vnd.google-apps.map") is True
    assert is_not_exportable("text/plain") is False


def test_download_filename_adds_the_extension_once() -> None:
    assert download_filename("Notes", "docx") == "Notes.docx"
    assert download_filename("Notes.docx", "docx") == "Notes.docx"
    assert download_filename("Notes.DOCX", ".docx") == "Notes.DOCX"


def test_permissions_follow_owned_by_me_and_drive_capabilities() -> None:
    assert permission_for_drive_item({}, PermissionType.OWNER) is PermissionType.OWNER
    assert permission_for_drive_item({"ownedByMe": True}, PermissionType.READ) is PermissionType.OWNER
    assert permission_for_drive_item(
        {"ownedByMe": False, "capabilities": {"canEdit": True}}, PermissionType.OWNER
    ) is PermissionType.WRITE
    assert permission_for_drive_item({"ownedByMe": False}, PermissionType.OWNER) is PermissionType.READ
    assert permission_for_shared_drive({"canManageMembers": True}) is PermissionType.OWNER
    assert permission_for_shared_drive({"canEdit": True}) is PermissionType.WRITE
    assert permission_for_shared_drive({}) is PermissionType.READ


def test_sharing_a_file_out_does_not_make_it_shared_with_the_owner() -> None:
    owned = {"ownedByMe": True, "shared": True}
    incoming = {"ownedByMe": False, "shared": True}
    legacy = {"shared": True}

    assert shared_for_indexing(owned) is False
    assert shared_for_indexing(incoming) is True
    assert shared_for_indexing(legacy) is True


def test_content_changed_compares_revision_and_checksum() -> None:
    class Existing:
        external_revision_id = "rev-1"
        md5_hash = "aaa"

    same = Existing()
    assert drive_content_changed(same, {"headRevisionId": "rev-1", "md5Checksum": "aaa"}) is False
    assert drive_content_changed(same, {"headRevisionId": "rev-2", "md5Checksum": "aaa"}) is True
    assert drive_content_changed(same, {"headRevisionId": "rev-1", "md5Checksum": "bbb"}) is True
    assert drive_content_changed(same, {"version": "9"}) is True
