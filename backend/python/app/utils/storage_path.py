"""Shared hierarchical storage path building.

Both BlobStorage (the writer) and StorageCleanupHelper (the mover) must
produce identical paths for the same record.  This module is the single
source of truth for the path algorithm so the two can never drift.
"""

import re
from typing import Any

_UNSAFE_CHARS = re.compile(r'[/\\:*?"<>|]')


def sanitize_path_segment(name: str) -> str:
    """Sanitize a record/group name for use as a storage path segment."""
    return _UNSAFE_CHARS.sub("_", name)[:100]


def build_record_group_path(
    connector_id: str | None,
    group_name: str | None,
) -> str | None:
    """Build the storage path prefix for a record group.

    Returns ``records/<connector_id>/<sanitized_group_name>`` or *None*
    when either input is missing.
    """
    if not connector_id or not isinstance(group_name, str) or not group_name:
        return None
    return f"records/{connector_id}/{sanitize_path_segment(group_name)}"


async def build_hierarchical_storage_path(
    record: Any,
    graph_provider: Any,
    *,
    virtual_record_id: str | None = None,
    transaction: str | None = None,
    logger: Any | None = None,
) -> str | None:
    """Build filesystem-like storage path for a record.

    Returns paths like ``records/<connector_id>/<group>/<ancestor_hierarchy>``.

    *virtual_record_id* is used only for the flat fallback path
    (``records/<vrid>``) when the hierarchy cannot be computed.  When it is
    ``None`` and the hierarchy fails, the function returns ``None`` so the
    caller can decide how to handle it (BlobStorage falls back to flat,
    StorageCleanupHelper skips the move).

    *transaction* is forwarded to graph-provider calls that support it
    (e.g. ArangoDB transactional reads inside ``DataSourceEntitiesProcessor``).
    """
    connector_id = getattr(record, "connector_id", None)
    if not graph_provider or not connector_id:
        return f"records/{virtual_record_id}" if virtual_record_id else None

    parts: list[str] = ["records", connector_id]

    record_group_id = getattr(record, "record_group_id", None)
    if record_group_id:
        try:
            kwargs: dict = {}
            if transaction is not None:
                kwargs["transaction"] = transaction
            group = await graph_provider.get_record_group_by_id(
                record_group_id, **kwargs
            )
            if group:
                group_name = group.get("groupName") or group.get("name", "")
                if group_name:
                    parts.append(sanitize_path_segment(group_name))
        except Exception as e:
            if logger:
                logger.warning("Could not fetch record group for path: %s", str(e))

    record_id = getattr(record, "id", None)
    record_path_added = False
    if record_id:
        try:
            kwargs = {}
            if transaction is not None:
                kwargs["transaction"] = transaction
            record_path = await graph_provider.get_record_path(record_id, **kwargs)
            if record_path:
                path_segments = [s for s in record_path.split("/") if s]
                if path_segments:
                    parts.extend(sanitize_path_segment(s) for s in path_segments)
                    record_path_added = True
        except Exception as e:
            if logger:
                logger.warning("Could not get record path hierarchy: %s", str(e))
            return f"records/{virtual_record_id}" if virtual_record_id else None

    if not record_path_added:
        record_name = getattr(record, "record_name", None)
        if not record_name:
            return f"records/{virtual_record_id}" if virtual_record_id else None
        parts.append(sanitize_path_segment(record_name))

    return "/".join(parts)
