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
    from collections.abc import Callable, Iterable
    from types import ModuleType

    from app.config.constants.arangodb import Connectors
    from app.services.cache.accessible_records_cache import (
        AccessibleRecordsCache,
        AccessibleRecordsInvalidator,
    )
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
    from app.services.permissions.cache import ViewerScopeCache
    from app.services.permissions.epochs import EpochCounters

__all__ = [
    "init_accessible_records_invalidator",
    "get_accessible_records_invalidator",
    "reset_accessible_records_invalidator",
    "get_viewer_scope_cache",
    "init_viewer_scope_version",
    "reset_viewer_scope_version",
    "init_permission_epochs",
    "get_permission_epochs",
    "reset_permission_epochs",
    "epoch_bump_failures",
    "notify_connector_sync_completed",
    "notify_connector_permissions_changed",
    "notify_connector_slice_committed",
    "notify_principals_changed",
    "notify_kb_records_changed",
    "notify_record_indexed",
]

# A holder rather than a bare module global so registration does not need the
# `global` statement in three places.
_state: dict[str, "AccessibleRecordsInvalidator | None"] = {"invalidator": None}
# The viewer-scope cache whose ACL version moves with every permission event; None until registered.
_scope_cache: dict[str, "ViewerScopeCache | None"] = {"cache": None}
# The permission epochs every permission event moves; None until registered.
_epochs: dict[str, "EpochCounters | None"] = {"counters": None}
# Moves that were due and did not land: until a verdict saved before one expires, it can outlive the change it missed.
_epoch_failures: dict[str, int] = {"count": 0}
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


def init_permission_epochs(counters: "EpochCounters") -> None:
    """Register the store the permission events below move the epochs in."""
    _epochs["counters"] = counters


def get_permission_epochs() -> "EpochCounters | None":
    return _epochs["counters"]


def reset_permission_epochs() -> None:
    _epochs["counters"] = None


def epoch_bump_failures() -> int:
    """How many epoch moves this process could not make since it started."""
    return _epoch_failures["count"]


async def _bump_epochs(keys: Callable[[ModuleType], list[str]]) -> None:
    """Move the epochs `keys` names, given the key builders (`app.services.permissions.epochs`). Never raises: a move
    that cannot be made is logged and counted, and a verdict saved before it lasts at most until it expires."""
    counters = _epochs["counters"]
    if counters is None:
        return
    from app.services.permissions import epochs

    try:
        names = keys(epochs)
    except ValueError:
        # No organisation, or an id that cannot name a key: there is nothing to move, and the miss is counted.
        _epoch_failures["count"] += 1
        _logger.warning("permission epoch not moved: the event names no usable organisation or id")
        return
    if not names:
        return
    try:
        await counters.bump(names)
    except Exception as e:
        _epoch_failures["count"] += 1
        _logger.warning("permission epoch bump failed: %s", type(e).__name__)


async def notify_connector_sync_completed(connector_id: str, org_id: str | None = None) -> None:
    """A connector finished syncing: its record set may have changed."""
    await _bump_acl_version(org_id)
    await _bump_epochs(lambda e: [e.connector_epoch_key(org_id, connector_id)])
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
    await _bump_epochs(lambda e: [e.connector_epoch_key(org_id, connector_id)])
    invalidator = _state["invalidator"]
    if invalidator is None:
        return
    try:
        await invalidator.on_connector_permissions_changed(connector_id, org_id)
    except Exception as e:  # pragma: no cover - the invalidator already swallows
        _logger.warning("accessible-records invalidation failed: %s", str(e))


async def notify_connector_slice_committed(connector_ids: Iterable[str | None], org_id: str | None) -> None:
    """One slice of a sync committed, and may have rewritten the grants of its records. Only the connectors' epochs
    move: the search cache and the viewer-scope version still wait for the end of the sync, as before."""
    ids = sorted({str(c) for c in connector_ids if c})
    if ids:
        await _bump_epochs(lambda e: [e.connector_epoch_key(org_id, c) for c in ids])


async def notify_principals_changed(org_id: str | None = None, *, user_ids: Iterable[str] = ()) -> None:
    """Who someone is changed (a team's members, a user added, updated or deleted, an app enabled or disabled):
    what they may read moves with it. Call after the write has committed. It moves the viewer-scope version, and
    the epoch of each person `user_ids` names (their PipesHub user ids)."""
    await _bump_acl_version(org_id)
    people = list(dict.fromkeys(u for u in user_ids if u))
    if people:
        await _bump_epochs(lambda e: [e.principal_epoch_key(org_id, u) for u in people])


async def notify_kb_records_changed(kb_id: str, org_id: str | None = None, *, scope_org: str | None = None) -> None:
    """Records were added to or removed from a knowledge base. `scope_org` only picks the organisation whose viewer-scope
    version moves; the accessible-records invalidator gets exactly what it always got."""
    await _bump_acl_version(scope_org or org_id)
    # The records' own app moves too: record deletes, trash and restores of any connector come through here.
    await _bump_epochs(lambda e: [e.kb_epoch_key(scope_org or org_id), e.connector_epoch_key(scope_org or org_id, kb_id)])
    invalidator = _state["invalidator"]
    if invalidator is None:
        return
    try:
        await invalidator.on_kb_records_changed(kb_id, org_id)
    except Exception as e:  # pragma: no cover - the invalidator already swallows
        _logger.warning("accessible-records invalidation failed: %s", str(e))


async def notify_record_vrid_moved(
    connector_name: "Connectors | str | None" = None,
    connector_id: str | None = None,
    external_record_group_id: str | None = None,
    org_id: str | None = None,
) -> None:
    """A record now names another virtual record. Drops its cache entry at once (not coalesced like `notify_record_indexed`)."""
    invalidator = _state["invalidator"]
    if invalidator is None:
        return
    try:
        await invalidator.on_record_vrid_moved(
            connector_name=connector_name,
            connector_id=connector_id,
            external_record_group_id=external_record_group_id,
            org_id=org_id,
        )
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
