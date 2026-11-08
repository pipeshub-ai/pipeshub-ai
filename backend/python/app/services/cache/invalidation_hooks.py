"""Process-wide entry points for dropping stale accessible-record cache entries.

The writes that make records appear or disappear are spread across the
connectors and indexing services — sync completion, permission changes, KB
uploads and deletes, the indexing status flip. Threading an invalidator through
every one of those call chains would mean touching dozens of constructors
(``DataSourceEntitiesProcessor`` alone is built in ~50 places), so services
register one invalidator at startup and the hook sites call these module
functions.

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
    from app.services.permissions.cache import ViewerScopeCache

__all__ = [
    "init_accessible_records_invalidator",
    "get_accessible_records_invalidator",
    "reset_accessible_records_invalidator",
    "get_viewer_scope_cache",
    "init_viewer_scope_version",
    "reset_viewer_scope_version",
    "notify_connector_sync_completed",
    "notify_connector_permissions_changed",
    "notify_principals_changed",
    "notify_kb_records_changed",
    "notify_record_indexed",
]

# A holder rather than a bare module global so registration does not need the
# `global` statement in three places.
_state: dict[str, "AccessibleRecordsInvalidator | None"] = {"invalidator": None}
# The viewer-scope cache whose ACL version moves with every permission event; None until registered.
_scope_cache: dict[str, "ViewerScopeCache | None"] = {"cache": None}
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


def init_viewer_scope_version(cache: "ViewerScopeCache") -> None:
    """Register the cache whose ACL version the permission events below bump."""
    _scope_cache["cache"] = cache


def get_viewer_scope_cache() -> "ViewerScopeCache | None":
    return _scope_cache["cache"]


def reset_viewer_scope_version() -> None:
    _scope_cache["cache"] = None


async def _bump_acl_version(org_id: str | None) -> None:
    cache = _scope_cache["cache"]
    if cache is None:
        return
    try:
        await cache.bump(org_id)
    except Exception as e:  # pragma: no cover - bump already swallows
        _logger.warning("viewer-scope version bump failed: %s", str(e))


async def notify_connector_sync_completed(connector_id: str, org_id: str | None = None) -> None:
    """A connector finished syncing: its record set may have changed."""
    await _bump_acl_version(org_id)
    invalidator = _state["invalidator"]
    if invalidator is None:
        return
    try:
        await invalidator.on_connector_sync_completed(connector_id, org_id)
    except Exception as e:  # pragma: no cover - the invalidator already swallows
        _logger.warning("accessible-records invalidation failed: %s", str(e))


async def notify_connector_permissions_changed(
    connector_id: str, org_id: str | None = None
) -> None:
    """Someone may have lost (or gained) access to this connector's records.

    Call only after the write has committed: a search between the drop and the
    commit would cache the old permissions again."""
    await _bump_acl_version(org_id)
    invalidator = _state["invalidator"]
    if invalidator is None:
        return
    try:
        await invalidator.on_connector_permissions_changed(connector_id, org_id)
    except Exception as e:  # pragma: no cover - the invalidator already swallows
        _logger.warning("accessible-records invalidation failed: %s", str(e))


async def notify_principals_changed(org_id: str | None = None) -> None:
    """Who someone is changed (a team's members, a user added, updated or deleted, an app enabled or disabled):
    what they may read moves with it. Call after the write has committed; it only moves the viewer-scope version."""
    await _bump_acl_version(org_id)


async def notify_kb_records_changed(kb_id: str, org_id: str | None = None, *, scope_org: str | None = None) -> None:
    """Records were added to or removed from a knowledge base. `scope_org` only picks the organisation whose viewer-scope
    version moves; the accessible-records invalidator gets exactly what it always got."""
    await _bump_acl_version(scope_org or org_id)
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
    """A record became searchable. Connector deletes are coalesced — see `on_record_indexed`."""
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
