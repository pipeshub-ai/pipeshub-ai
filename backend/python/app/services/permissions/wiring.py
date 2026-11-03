"""Start-up registration for a service that writes or serves permissions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .cache import ViewerScopeCache
from .mode import AclFilterMode

if TYPE_CHECKING:
    import logging

    from app.config.configuration_service import ConfigurationService


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
