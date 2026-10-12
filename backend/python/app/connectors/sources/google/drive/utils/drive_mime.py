"""How a Drive file is stored, exported, and named.

Google-native files keep whatever the user typed as the name, including a
suffix like ``.py``. That suffix must not become the record extension: the
parser would treat a Doc named ``main.py`` as source code.
"""

from __future__ import annotations

from enum import Enum

from app.config.constants.arangodb import MimeTypes
from app.models.permission import PermissionType
from app.utils.image_utils import get_extension_from_mimetype

GOOGLE_APPS_PREFIX = "application/vnd.google-apps."
SHORTCUT_MIME = "application/vnd.google-apps.shortcut"

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
PDF_MIME = "application/pdf"

# mime type -> (export mime, extension without a dot)
GOOGLE_EXPORT_FORMATS: dict[str, tuple[str, str]] = {
    MimeTypes.GOOGLE_DOCS.value: (DOCX_MIME, "docx"),
    MimeTypes.GOOGLE_SHEETS.value: (XLSX_MIME, "xlsx"),
    MimeTypes.GOOGLE_SLIDES.value: (PPTX_MIME, "pptx"),
}

# Native types Drive will not export to an indexable file. Other google-apps
# types that are not folders, shortcuts, or the three above are treated the same
# way: they stay visible and are not sent to the indexer.
_KNOWN_NON_EXPORTABLE = frozenset(
    {
        "application/vnd.google-apps.form",
        "application/vnd.google-apps.site",
        "application/vnd.google-apps.map",
        "application/vnd.google-apps.jam",
    }
)


class DriveNativeKind(str, Enum):
    FOLDER = "folder"
    SHORTCUT = "shortcut"
    EXPORTABLE = "exportable"
    NOT_EXPORTABLE = "not_exportable"
    BINARY = "binary"


def classify_drive_mime(mime_type: str | None) -> DriveNativeKind:
    mime = mime_type or ""
    if mime == MimeTypes.GOOGLE_DRIVE_FOLDER.value:
        return DriveNativeKind.FOLDER
    if mime == SHORTCUT_MIME:
        return DriveNativeKind.SHORTCUT
    if mime in GOOGLE_EXPORT_FORMATS:
        return DriveNativeKind.EXPORTABLE
    if mime in _KNOWN_NON_EXPORTABLE or mime.startswith(GOOGLE_APPS_PREFIX):
        return DriveNativeKind.NOT_EXPORTABLE
    return DriveNativeKind.BINARY


def is_not_exportable(mime_type: str | None) -> bool:
    return classify_drive_mime(mime_type) is DriveNativeKind.NOT_EXPORTABLE


def export_mime_for(mime_type: str | None, *, pdf: bool = False) -> str | None:
    """Export mime for a Google-native file, or None when it cannot be exported."""
    if classify_drive_mime(mime_type) is not DriveNativeKind.EXPORTABLE:
        return None
    if pdf:
        return PDF_MIME
    return GOOGLE_EXPORT_FORMATS[mime_type or ""][0]


def export_extension_for(mime_type: str | None, *, pdf: bool = False) -> str | None:
    if classify_drive_mime(mime_type) is not DriveNativeKind.EXPORTABLE:
        return None
    if pdf:
        return "pdf"
    return GOOGLE_EXPORT_FORMATS[mime_type or ""][1]


def resolve_extension(metadata: dict) -> str | None:
    """Extension to store on the record, without a leading dot.

    Exportable Google files always get the export extension. Their name is not
    consulted. Binary files use Drive's ``fileExtension``, then the name suffix,
    then the mime type.
    """
    mime = metadata.get("mimeType") or ""
    kind = classify_drive_mime(mime)
    if kind is DriveNativeKind.EXPORTABLE:
        return GOOGLE_EXPORT_FORMATS[mime][1]
    if kind is not DriveNativeKind.BINARY:
        return None

    file_extension = metadata.get("fileExtension")
    if file_extension:
        return str(file_extension).lower().lstrip(".")

    name = metadata.get("name") or ""
    if "." in name:
        return name.rsplit(".", 1)[-1].lower().lstrip(".")

    from_mime = get_extension_from_mimetype(mime)
    if from_mime:
        return str(from_mime).lower().lstrip(".")
    return None


def download_filename(name: str | None, extension: str | None) -> str:
    """File name for Content-Disposition, with the export extension applied once."""
    base = name or "download"
    if not extension:
        return base
    suffix = extension if extension.startswith(".") else f".{extension}"
    if base.lower().endswith(suffix.lower()):
        return base
    return f"{base}{suffix}"


def permission_for_drive_item(metadata: dict, fallback: PermissionType) -> PermissionType:
    """Access to record for the syncing user.

    ``ownedByMe`` is only present when the listing asked for it. Without it, the
    caller's fallback stands so existing callers that pass an explicit grant
    (shared-with-me READ, My Drive OWNER) keep that grant.
    """
    if metadata.get("ownedByMe") is True:
        return PermissionType.OWNER
    if metadata.get("ownedByMe") is False:
        if (metadata.get("capabilities") or {}).get("canEdit"):
            return PermissionType.WRITE
        return PermissionType.READ
    return fallback


def shared_for_indexing(metadata: dict) -> bool:
    """Whether the indexing "shared" filter treats this item as shared with the user.

    A file the user owns and has shared with others is still their file. Drive's
    ``shared`` flag is true for that case; ``ownedByMe`` is what distinguishes it
    from a file someone else shared in.
    """
    if "ownedByMe" in metadata:
        return not bool(metadata.get("ownedByMe"))
    return bool(metadata.get("shared", False))


def permission_for_shared_drive(capabilities: dict | None) -> PermissionType:
    """The syncing user's grant on a shared drive, from the drive ``capabilities``."""
    caps = capabilities or {}
    if caps.get("canManageMembers"):
        return PermissionType.OWNER
    if caps.get("canEdit"):
        return PermissionType.WRITE
    return PermissionType.READ


def drive_content_changed(existing: object, metadata: dict) -> bool:
    """True when the bytes (or Drive revision) changed since the stored record.

    ``headRevisionId`` wins over Drive's ``version`` field, matching what is
    stored as ``external_revision_id``. ``version`` is the comparison when
    ``headRevisionId`` is absent. A checksum change counts even if the revision
    id did not move.
    """
    def _text(value: object) -> str | None:
        return value if isinstance(value, str) and value else None

    new_revision = _text(metadata.get("headRevisionId")) or _text(metadata.get("version"))
    old_revision = _text(getattr(existing, "external_revision_id", None))
    if old_revision != new_revision:
        return True
    new_md5 = _text(metadata.get("md5Checksum"))
    old_md5 = _text(getattr(existing, "md5_hash", None))
    return bool(new_md5 or old_md5) and new_md5 != old_md5
