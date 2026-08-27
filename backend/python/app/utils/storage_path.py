"""Shared hierarchical storage path building.

Both BlobStorage (the writer) and StorageCleanupHelper (the mover) must
produce identical paths for the same record.  This module is the single
source of truth for the path algorithm so the two can never drift.
"""

import re
from typing import Any
from urllib.parse import urlparse

_UNSAFE_CHARS = re.compile(r'[/\\:*?"<>|]')


def sanitize_path_segment(name: str) -> str:
    """Sanitize a record/group name for use as a storage path segment."""
    return _UNSAFE_CHARS.sub("_", name)[:100]


def _build_web_storage_path(connector_id: str, weburl: str) -> str | None:
    """Build storage path for web connector records from the weburl.

    Web record names contain full URL path prefixes (e.g.
    ``www.example.com/assets/images/``), so the normal graph-traversal path
    produces garbled, redundant results.  Instead, parse the URL and use
    ``host/path`` directly: ``records/<connector_id>/<host>/<url_path>``.
    """
    parsed = urlparse(weburl)
    host = parsed.netloc
    if not host:
        return None
    parts: list[str] = ["records", connector_id, sanitize_path_segment(host)]
    url_path = parsed.path.strip("/")
    if url_path:
        url_segments = [s for s in url_path.split("/") if s]
        parts.extend(sanitize_path_segment(s) for s in url_segments)
    return "/".join(parts)


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

    connector_name = getattr(record, "connector_name", None)
    cn_val = getattr(connector_name, "value", connector_name) if connector_name else None
    if cn_val == "WEB":
        weburl = getattr(record, "weburl", None)
        if weburl:
            web_path = _build_web_storage_path(connector_id, weburl)
            if web_path:
                return web_path

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
            kwargs: dict = {}
            if transaction is not None:
                kwargs["transaction"] = transaction
            record_path = await graph_provider.get_record_path(record_id, **kwargs)
            if record_path:
                record_name = getattr(record, "record_name", None)
                if record_name and record_path.endswith(record_name):
                    ancestor_part = record_path[: -len(record_name)].rstrip("/")
                    ancestors = [s for s in ancestor_part.split("/") if s]
                    segments = ancestors + [record_name]
                else:
                    segments = [s for s in record_path.split("/") if s]
                if segments:
                    parts.extend(sanitize_path_segment(s) for s in segments)
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
