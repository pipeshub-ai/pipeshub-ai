"""Live reads of Labs-toggled platform feature flags.

The query service does not wire ``FeatureFlagService`` with an ``EtcdProvider``
(only the connectors container does). Agent-runtime gates must therefore read
``/services/platform/settings`` directly through the request's
``ConfigurationService`` — the same store the Labs UI writes to.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional

from app.services.featureflag.config.config import CONFIG

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService

logger = logging.getLogger(__name__)

PLATFORM_SETTINGS_KEY = "/services/platform/settings"


async def read_platform_feature_flag(
    flag_name: str,
    config_service: Any,
    *,
    default: bool,
) -> bool:
    """Return a platform feature flag's boolean value from encrypted settings.

    Reads with ``use_cache=False`` so flipping a flag in Labs takes effect on
    the next request instead of after a service restart. On missing settings,
    an absent flag key, or a read failure, returns ``default``.
    """
    try:
        settings = await config_service.get_config(
            PLATFORM_SETTINGS_KEY, default={}, use_cache=False,
        )
        flags = settings.get("featureFlags") if isinstance(settings, dict) else None
        if not isinstance(flags, dict):
            return default
        # Keys are normalized to upper-case, matching `EtcdProvider`.
        normalized = {str(k).upper(): v for k, v in flags.items()}
        if str(flag_name).upper() not in normalized:
            return default
        return bool(normalized[str(flag_name).upper()])
    except Exception as e:
        logger.error(
            "Failed to read %s for feature flag %s; using default=%s: %s",
            PLATFORM_SETTINGS_KEY,
            flag_name,
            default,
            e,
            exc_info=True,
        )
        return default


async def is_actions_enabled(config_service: Optional["ConfigurationService"] = None) -> bool:
    """Deployment-level gate for Actions: agents may only load/use toolset
    (connector) tools when this is true. Source of truth is the
    ``ENABLE_ACTIONS`` platform feature flag. Defaults to ENABLED — unlike
    ``ENABLE_MCP``, toolsets/actions are pre-existing functionality; admins
    may opt out from Labs.

    Resolution order (first hit wins), mirroring ``is_mcp_enabled``:
    1. ``config_service`` — live read of the platform settings the Labs UI writes,
       via the shared ``read_platform_feature_flag`` helper
    2. ``FeatureFlagService`` — only reachable in services that wire an
       ``EtcdProvider`` (the connectors service); the query service does not, which
       is why the ``config_service`` read above is the primary path
    3. Default: ``True``

    Reads with ``use_cache=False`` (via ``read_platform_feature_flag``) so
    flipping the flag in Labs takes effect on the next chat instead of after
    a service restart.
    """
    if config_service is not None:
        return await read_platform_feature_flag(
            CONFIG.ENABLE_ACTIONS, config_service, default=True,
        )

    try:
        from app.services.featureflag.featureflag import FeatureFlagService

        return bool(
            FeatureFlagService.get_service().is_feature_enabled(
                CONFIG.ENABLE_ACTIONS, default=True
            )
        )
    except Exception as e:
        logger.warning(f"FeatureFlagService unavailable for ENABLE_ACTIONS, treating Actions as enabled: {e}")
        return True
