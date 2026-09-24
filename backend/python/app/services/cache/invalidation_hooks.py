"""Process-wide entry points for dropping stale accessible-record cache entries.

The writes that make records appear or disappear are spread across the
connectors and indexing services — sync completion, KB uploads and deletes, the
indexing status flip. Threading an invalidator through every one of those call
chains would mean touching dozens of constructors (``DataSourceEntitiesProcessor``
alone is built in ~50 places), so services register one invalidator at startup
and the hook sites call these module functions.

Every function is a no-op when nothing is registered — which is the case for any
service that never enabled the cache, and during tests — and none of them can
raise: invalidation is best-effort, and the cache TTL is the backstop.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.config.constants.arangodb import Connectors
    from app.services.cache.accessible_records_cache import (
        AccessibleRecordsCache,
        AccessibleRecordsInvalidator,
    )
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

__all__ = [
    "init_accessible_records_invalidator",
    "get_accessible_records_invalidator",
    "reset_accessible_records_invalidator",
    "notify_connector_sync_completed",
    "notify_kb_records_changed",
    "notify_record_indexed",
]

# A holder rather than a bare module global so registration does not need the
# `global` statement in three places.
_state: dict[str, "AccessibleRecordsInvalidator | None"] = {"invalidator": None}
_logger = logging.getLogger(__name__)


def init_accessible_records_invalidator(
    logger: logging.Logger,
    cache: "AccessibleRecordsCache",
    graph_provider: "IGraphDBProvider",
) -> None:
    """Register the process's invalidator. Safe to call more than once."""
    from app.services.cache.accessible_records_cache import AccessibleRecordsInvalidator

    _state["invalidator"] = AccessibleRecordsInvalidator(logger, cache, graph_provider)


def get_accessible_records_invalidator() -> "AccessibleRecordsInvalidator | None":
    return _state["invalidator"]


def reset_accessible_records_invalidator() -> None:
    _state["invalidator"] = None


async def notify_connector_sync_completed(connector_id: str, org_id: str | None = None) -> None:
    """A connector finished syncing: its record set may have changed."""
    invalidator = _state["invalidator"]
    if invalidator is None:
        return
    try:
        await invalidator.on_connector_sync_completed(connector_id, org_id)
    except Exception as e:  # pragma: no cover - the invalidator already swallows
        _logger.warning("accessible-records invalidation failed: %s", str(e))


async def notify_kb_records_changed(kb_id: str, org_id: str | None = None) -> None:
    """Records were added to or removed from a knowledge base."""
    invalidator = _state["invalidator"]
    if invalidator is None:
        return
    try:
        await invalidator.on_kb_records_changed(kb_id, org_id)
    except Exception as e:  # pragma: no cover - the invalidator already swallows
        _logger.warning("accessible-records invalidation failed: %s", str(e))


async def notify_record_indexed(
    connector_name: "Connectors | str | None" = None,
    connector_id: str | None = None,
    external_record_group_id: str | None = None,
    org_id: str | None = None,
) -> None:
    """A record became searchable. KB-only — see `on_record_indexed`."""
    invalidator = _state["invalidator"]
    if invalidator is None:
        return
    try:
        await invalidator.on_record_indexed(
            connector_name=connector_name,
            connector_id=connector_id,
            external_record_group_id=external_record_group_id,
            org_id=org_id,
        )
    except Exception as e:  # pragma: no cover - the invalidator already swallows
        _logger.warning("accessible-records invalidation failed: %s", str(e))
