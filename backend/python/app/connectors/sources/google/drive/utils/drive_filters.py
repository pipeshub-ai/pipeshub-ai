"""Date and extension filters shared by the personal and workspace Drive connectors.

Drive's ``files.list`` query language has no created/modified range that matches
the connector filters, so both connectors apply them to each item after listing.
"""

from __future__ import annotations

from datetime import datetime

from app.config.constants.arangodb import MimeTypes
from app.connectors.core.registry.filters import (
    FilterCollection,
    FilterOperator,
    SyncFilterKey,
)

_GOOGLE_DOC_MIME_TYPES = (
    MimeTypes.GOOGLE_DOCS.value,
    MimeTypes.GOOGLE_SHEETS.value,
    MimeTypes.GOOGLE_SLIDES.value,
)


def parse_drive_datetime(dt_obj: object) -> int | None:
    """Parse a Drive timestamp or datetime to epoch milliseconds."""
    if not dt_obj:
        return None
    try:
        if isinstance(dt_obj, str):
            parsed = datetime.fromisoformat(dt_obj.replace("Z", "+00:00"))
        else:
            parsed = dt_obj
        return int(parsed.timestamp() * 1000)
    except Exception:
        return None


def _operator_value(operator: object) -> str:
    return operator.value if hasattr(operator, "value") else str(operator)


def passes_date_filters(metadata: dict, sync_filters: FilterCollection) -> bool:
    """True when the item is inside the configured created and modified windows.

    Folders always pass: a file inside the window still needs its parent folder
    even when the folder itself was last touched outside the window.
    """
    mime_type = metadata.get("mimeType", "")
    if mime_type == MimeTypes.GOOGLE_DRIVE_FOLDER.value:
        return True

    created_filter = sync_filters.get(SyncFilterKey.CREATED)
    if created_filter and not _in_window(
        metadata.get("createdTime"), created_filter.get_datetime_iso()
    ):
        return False

    modified_filter = sync_filters.get(SyncFilterKey.MODIFIED)
    if modified_filter and not _in_window(
        metadata.get("modifiedTime"), modified_filter.get_datetime_iso()
    ):
        return False

    return True


def _in_window(raw_time: object, bounds: tuple[object, object]) -> bool:
    item_ts = parse_drive_datetime(raw_time) if raw_time else None
    if item_ts is None:
        return True
    start_ts = parse_drive_datetime(bounds[0])
    end_ts = parse_drive_datetime(bounds[1])
    if start_ts and item_ts < start_ts:
        return False
    if end_ts and item_ts > end_ts:
        return False
    return True


def passes_extension_filter(metadata: dict, sync_filters: FilterCollection) -> bool:
    """True when the item matches the file-extension sync filter.

    Docs, Sheets and Slides are matched on mime type. Everything else is matched
    on ``fileExtension``, then on the suffix of the name. Folders always pass.
    """
    mime_type = metadata.get("mimeType", "")
    if mime_type == MimeTypes.GOOGLE_DRIVE_FOLDER.value:
        return True

    extensions_filter = sync_filters.get(SyncFilterKey.FILE_EXTENSIONS)
    if extensions_filter is None or extensions_filter.is_empty():
        return True

    allowed_values = extensions_filter.value
    if not isinstance(allowed_values, list):
        return True

    operator = _operator_value(extensions_filter.get_operator())
    if mime_type in _GOOGLE_DOC_MIME_TYPES:
        if operator == FilterOperator.IN:
            return mime_type in allowed_values
        if operator == FilterOperator.NOT_IN:
            return mime_type not in allowed_values
        return True

    file_extension = _raw_extension(metadata)
    if file_extension is None:
        return operator == FilterOperator.NOT_IN

    normalized = [
        ext.lower().lstrip(".")
        for ext in allowed_values
        if not str(ext).startswith("application/vnd.google-apps")
    ]
    if operator == FilterOperator.IN:
        return file_extension in normalized
    if operator == FilterOperator.NOT_IN:
        return file_extension not in normalized
    return True


def _raw_extension(metadata: dict) -> str | None:
    file_extension = metadata.get("fileExtension")
    if not file_extension:
        file_name = metadata.get("name", "")
        if "." in file_name:
            file_extension = file_name.rsplit(".", 1)[-1]
        else:
            return None
    return str(file_extension).lower().lstrip(".")
