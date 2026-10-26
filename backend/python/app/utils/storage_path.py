"""Shared hierarchical storage path building.

Both BlobStorage (the writer) and StorageCleanupHelper (the mover) must
produce identical paths for the same record.  This module is the single
source of truth for the path algorithm so the two can never drift.
"""

import hashlib
import re
from typing import Any
from urllib.parse import urlparse

_UNSAFE_CHARS = re.compile(r'[/\\:*?"<>|\x00-\x1f\x7f]')

# Filesystems cap one name at 255 bytes. Names that fit keep their old
# segment byte for byte: moves recompute a record's old path from its name.
_MAX_SEGMENT_BYTES = 255
# S3 and Azure cap a key at 1024; Node adds "<orgId>/PipesHub/" before and
# "/<docId>/record_<vrid>.json" after this path (~110 bytes).
_MAX_PATH_BYTES = 900
_HASH_CHARS = 16


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:_HASH_CHARS]


def _truncate_utf8(text: str, max_bytes: int) -> str:
    return text.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")


def sanitize_path_segment(name: str) -> str:
    """Sanitize a record/group name for use as a storage path segment.

    A name that is empty once cleaned becomes ``_`` rather than vanishing,
    which would file the record at its parent's path.
    """
    if not isinstance(name, str):
        return ""
    sanitized = _UNSAFE_CHARS.sub("_", name.strip())[:100]
    if sanitized in (".", ".."):
        sanitized = "_" * len(sanitized)
    if not sanitized:
        return "_"
    if len(sanitized.encode("utf-8")) > _MAX_SEGMENT_BYTES:
        suffix = "~" + _digest(name)
        sanitized = _truncate_utf8(sanitized, _MAX_SEGMENT_BYTES - len(suffix)) + suffix
    return sanitized


def _join_capped(parts: list[str]) -> str:
    """Join path parts, folding the tail into a hash when the path is too long.

    Leading segments are kept while they fit so a deep record still sits
    under its connector and outer folders. Limit: once a path is folded, only
    leading segments within ~882 bytes stay real folders, and where the fold
    falls depends on every ancestor's name length. A prefix move of a folder
    past that point, or an ancestor rename that shifts the fold, does not reach
    the folded content; it is relocated when the record is next re-indexed.
    """
    path = "/".join(parts)
    if len(path.encode("utf-8")) <= _MAX_PATH_BYTES:
        return path
    budget = _MAX_PATH_BYTES - _HASH_CHARS - 2
    kept = parts[:2]  # records/<connector_id>
    used = len("/".join(kept).encode("utf-8"))
    for part in parts[2:]:
        size = len(part.encode("utf-8")) + 1
        if used + size > budget:
            break
        kept.append(part)
        used += size
    tail = "/".join(parts[len(kept):])
    return "/".join([*kept, "~" + _digest(tail)])


def _build_web_storage_path(connector_id: str, weburl: str) -> str | None:
    """Build storage path for web connector records from the weburl.

    Web record names contain full URL path prefixes (e.g.
    ``www.example.com/assets/images/``), so the normal graph-traversal path
    produces garbled, redundant results.  Instead, parse the URL and use
    ``host/path`` directly: ``records/<connector_id>/<host>/<url_path>``.
    """
    parsed = urlparse(weburl)
    # Credentials in the URL must not become a directory name.
    host = parsed.netloc.rpartition("@")[2]
    if not host:
        return None
    parts: list[str] = ["records", connector_id, sanitize_path_segment(host)]
    url_path = parsed.path.strip("/")
    if url_path:
        url_segments = [s for s in url_path.split("/") if s]
        parts.extend(sanitize_path_segment(s) for s in url_segments)
    return _join_capped(parts)


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
    return _join_capped(["records", connector_id, sanitize_path_segment(group_name)])


def build_record_group_prefix_from_chain(
    connector_id: str,
    group_names: list[str],
) -> str | None:
    """Build a hierarchical storage prefix from a full ancestor chain.

    *group_names* is root-to-leaf, as returned by
    ``IGraphDBProvider.get_record_group_path``.  Returns
    ``records/<connector_id>/<root>/.../<leaf>`` or *None* when the
    chain is empty.
    """
    if not connector_id or not group_names:
        return None
    parts = ["records", connector_id]
    parts.extend(s for s in (sanitize_path_segment(n) for n in group_names) if s)
    if len(parts) == 2:
        return None
    return _join_capped(parts)


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
    kwargs: dict = {}
    if transaction is not None:
        kwargs["transaction"] = transaction
    # Path lookups must raise: a swallowed failure reads as "no ancestors" and
    # yields a plausible but wrong path.
    path_kwargs: dict = {**kwargs, "raise_on_error": True}

    record_group_id = getattr(record, "record_group_id", None)
    if record_group_id:
        # A failed lookup means unknown ancestry: a partial path could name a
        # different group's tree, so fall back as for any other failure.
        try:
            group_names = await graph_provider.get_record_group_path(
                record_group_id, **path_kwargs
            )
        except Exception as e:
            if logger:
                logger.warning("get_record_group_path failed: %s", str(e))
            return f"records/{virtual_record_id}" if virtual_record_id else None

        if group_names:
            parts.extend(s for s in (sanitize_path_segment(n) for n in group_names) if s)
        else:
            try:
                group = await graph_provider.get_record_group_by_id(
                    record_group_id, **kwargs
                )
            except Exception as e:
                if logger:
                    logger.warning("Could not fetch record group: %s", str(e))
                return f"records/{virtual_record_id}" if virtual_record_id else None
            if group:
                group_name = group.get("groupName") or group.get("name", "")
                if group_name:
                    parts.append(sanitize_path_segment(group_name))

    record_id = getattr(record, "id", None)
    record_path_added = False
    if record_id:
        try:
            segments = await graph_provider.get_record_path_segments(
                record_id, **path_kwargs
            )
            if segments:
                sanitized = [s for s in (sanitize_path_segment(v) for v in segments) if s]
                if sanitized:
                    parts.extend(sanitized)
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

    return _join_capped(parts)
