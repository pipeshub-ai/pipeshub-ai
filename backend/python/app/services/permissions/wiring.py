"""Start-up registration for a service that writes or serves permissions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .cache import ViewerScopeCache
from .mode import AclFilterMode

if TYPE_CHECKING:
    import logging

    from app.config.configuration_service import ConfigurationService
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

    from .scopes import ViewerScopes
    from .serving import ServingPlanner
    from .shadow import ShadowComparator


async def register_viewer_scope_version(
    config_service: ConfigurationService, logger: logging.Logger
) -> ViewerScopeCache | None:
    """When the filter mode is not legacy, create the viewer-scope cache and register it with the invalidation hooks,
    so that permission writes made by this process move the ACL version. Legacy mode does nothing, not even a Redis
    connection. Never raises: without Redis the cache has no client and every lookup bypasses it."""
    if AclFilterMode.from_env() is AclFilterMode.LEGACY:
        return None
    from app.services.cache.invalidation_hooks import init_viewer_scope_version

    try:
        cache = await ViewerScopeCache.create(config_service)
        init_viewer_scope_version(cache)
    except Exception as exc:
        logger.warning("viewer-scope version not registered: %s", type(exc).__name__)
        return None
    return cache


async def register_scope_generation(config_service: ConfigurationService, logger: logging.Logger) -> None:
    """Move the scope generation when the scope fields were switched on after being off (see `generation`). With the fields off and
    never enabled this reads one config key and writes nothing."""
    from .generation import init_scope_generation
    from .mode import scope_fields_enabled
    from .pending import require_filter_terms

    if scope_fields_enabled():
        require_filter_terms()  # raises: a service must not start registering roots that no filter can hide
    await init_scope_generation(config_service, logger)


class _ScopesAtUse:
    """The viewer-scope service of the shadow comparator and the serving planner, on the cache registered *now*: the comparator may be built before
    `register_viewer_scope_version` runs, and a cache captured at construction would stay absent for the life of the process."""

    def __init__(self, resolver: object) -> None:
        self._resolver = resolver
        self._seen: object = None
        self._service: object | None = None

    async def scopes(self, org_id: str, user_key: str) -> ViewerScopes:
        from app.services.cache.invalidation_hooks import get_viewer_scope_cache

        from .service import ViewerScopeService

        cache = get_viewer_scope_cache()
        if self._service is None or cache is not self._seen:       # a new cache is a new service; the same one keeps its background fills
            self._seen, self._service = cache, ViewerScopeService(self._resolver, cache)  # type: ignore[arg-type]
        return await self._service.scopes(org_id, user_key)  # type: ignore[attr-defined]


def _number(name: str, raw: str, kind: type, low: float, high: float | None = None) -> float:
    try:
        value = kind(raw)
    except ValueError:
        raise ValueError(f"{name}={raw!r} is not a number") from None
    if value != value or value < low or (high is not None and value > high):      # NaN fails the comparisons above
        raise ValueError(f"{name}={raw!r} is out of range")
    return value


def shadow_settings_from_env() -> dict[str, float | int]:
    """The shadow comparator's settings from the environment, each validated: ``ACL_SHADOW_SAMPLE`` 0..1 (default 0.1),
    ``ACL_SHADOW_MAX_INFLIGHT`` >= 1 (4), ``ACL_SHADOW_STEP_TIMEOUT_S`` > 0 (5), ``ACL_SHADOW_MAX_APPS`` >= 1 (500).
    A value that is set and invalid raises ValueError naming the setting: a silent default would hide a typo in the one switch
    that bounds the cost."""
    import os

    def get(name: str, default: str) -> str:
        return (os.environ.get(name) or default).strip()

    sample = _number("ACL_SHADOW_SAMPLE", get("ACL_SHADOW_SAMPLE", "0.1"), float, 0.0, 1.0)
    inflight = _number("ACL_SHADOW_MAX_INFLIGHT", get("ACL_SHADOW_MAX_INFLIGHT", "4"), int, 1)
    timeout = _number("ACL_SHADOW_STEP_TIMEOUT_S", get("ACL_SHADOW_STEP_TIMEOUT_S", "5"), float, 0.0)
    if timeout <= 0:
        raise ValueError(f"ACL_SHADOW_STEP_TIMEOUT_S={timeout!r} is out of range")
    apps = _number("ACL_SHADOW_MAX_APPS", get("ACL_SHADOW_MAX_APPS", "500"), int, 1)
    return {"sample_rate": sample, "max_inflight": int(inflight), "step_timeout_seconds": timeout, "max_apps": int(apps)}


def build_shadow_comparator(graph_provider: IGraphDBProvider) -> ShadowComparator | None:
    """The shadow comparator when ``ACL_FILTER_MODE=shadow``, else None (no object, no task, no query in any other mode). Invalid
    settings raise ValueError at start-up (`shadow_settings_from_env`). It reads viewer scopes through the cache registered at the
    time of each comparison (without one the comparison is skipped as ``scopes_unavailable``)."""
    if AclFilterMode.from_env() is not AclFilterMode.SHADOW:
        return None
    from .adapters import GraphPrincipalView
    from .shadow import ShadowComparator
    from .viewer import ViewerScopeResolver

    settings = shadow_settings_from_env()
    resolver = ViewerScopeResolver(graph_provider.permission_hierarchy_view(), GraphPrincipalView(graph_provider))
    return ShadowComparator(_ScopesAtUse(resolver), graph_provider, **settings)  # type: ignore[arg-type]


def build_serving_planner(graph_provider: IGraphDBProvider) -> ServingPlanner | None:
    """The planner that reads a search with the scope filter, when ``ACL_FILTER_MODE=scopes``; None in every other mode (no object, no read). It
    reads viewer scopes through the cache registered at the time of each search, as the shadow comparator does."""
    if AclFilterMode.from_env() is not AclFilterMode.SCOPES:
        return None
    from .adapters import GraphPrincipalView
    from .currency import StampCurrency
    from .serving import ServingPlanner
    from .viewer import ViewerScopeResolver

    resolver = ViewerScopeResolver(graph_provider.permission_hierarchy_view(), GraphPrincipalView(graph_provider))
    return ServingPlanner(_ScopesAtUse(resolver), StampCurrency(graph_provider))  # type: ignore[arg-type]
