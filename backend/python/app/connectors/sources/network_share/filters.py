"""Sync-filter helpers for network-share walks. Pure against FilterCollection."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from app.connectors.core.registry.filters import (
    FilterCollection,
    FilterOperator,
    SyncFilterKey,
)
from app.connectors.sources.network_share.pathing import file_extension

if TYPE_CHECKING:
    from app.connectors.sources.network_share.entry import DirectoryEntry


def date_bounds(
    sync_filters: FilterCollection,
) -> tuple[int | None, int | None, int | None, int | None]:
    modified_after = modified_before = created_after = created_before = None
    modified = sync_filters.get(SyncFilterKey.MODIFIED)
    if modified and not modified.is_empty():
        after_iso, before_iso = modified.get_datetime_iso()
        if after_iso:
            modified_after = _iso_ms(after_iso)
        if before_iso:
            modified_before = _iso_ms(before_iso)
    created = sync_filters.get(SyncFilterKey.CREATED)
    if created and not created.is_empty():
        after_iso, before_iso = created.get_datetime_iso()
        if after_iso:
            created_after = _iso_ms(after_iso)
        if before_iso:
            created_before = _iso_ms(before_iso)
    return modified_after, modified_before, created_after, created_before


def _iso_ms(iso: str) -> int:
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _epoch_ms(value: datetime | None) -> int | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp() * 1000)


def passes_date_filters(
    entry: DirectoryEntry,
    modified_after: int | None,
    modified_before: int | None,
    created_after: int | None,
    created_before: int | None,
) -> bool:
    if entry.is_directory:
        return True
    if not any([modified_after, modified_before, created_after, created_before]):
        return True
    write_ms = _epoch_ms(entry.last_write_time)
    if write_ms is None:
        return True
    if modified_after and write_ms < modified_after:
        return False
    if modified_before and write_ms > modified_before:
        return False
    created_ms = _epoch_ms(entry.created_time) or write_ms
    if created_after and created_ms < created_after:
        return False
    if created_before and created_ms > created_before:
        return False
    return True


def passes_extension_filter(
    rel_path: str,
    *,
    is_directory: bool,
    sync_filters: FilterCollection,
) -> bool:
    if is_directory:
        return True
    ext_filter = sync_filters.get(SyncFilterKey.FILE_EXTENSIONS)
    if ext_filter is None or ext_filter.is_empty():
        return True
    extension = file_extension(rel_path)
    operator = ext_filter.get_operator()
    operator_str = operator.value if hasattr(operator, "value") else str(operator)
    if extension is None:
        return operator_str == FilterOperator.NOT_IN
    allowed = ext_filter.value
    if not isinstance(allowed, list):
        return True
    normalized = [str(ext).lower().lstrip(".") for ext in allowed]
    if operator_str == FilterOperator.IN:
        return extension in normalized
    if operator_str == FilterOperator.NOT_IN:
        return extension not in normalized
    return True
