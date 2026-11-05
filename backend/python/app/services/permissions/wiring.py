"""Start-up registration for a service that writes or serves permissions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .cache import ViewerScopeCache
from .mode import AclFilterMode

if TYPE_CHECKING:
    import logging

    from app.config.configuration_service import ConfigurationService
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

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

    await init_scope_generation(config_service, logger)


def build_shadow_comparator(graph_provider: IGraphDBProvider) -> ShadowComparator | None:
    """The shadow comparator when ``ACL_FILTER_MODE=shadow``, else None (no object, no task, no query in any other mode). It reads viewer
    scopes through the cache that ``register_viewer_scope_version`` created (without one every comparison is skipped as
    ``scopes_unavailable``)."""
    if AclFilterMode.from_env() is not AclFilterMode.SHADOW:
        return None
    from app.services.cache.invalidation_hooks import get_viewer_scope_cache

    from .adapters import GraphPrincipalView
    from .service import ViewerScopeService
    from .shadow import ShadowComparator
    from .viewer import ViewerScopeResolver

    resolver = ViewerScopeResolver(graph_provider.permission_hierarchy_view(), GraphPrincipalView(graph_provider))
    return ShadowComparator(ViewerScopeService(resolver, get_viewer_scope_cache()), graph_provider)
