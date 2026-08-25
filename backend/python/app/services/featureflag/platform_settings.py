"""Live reads of Labs-toggled platform feature flags.

The query service does not wire ``FeatureFlagService`` with an ``EtcdProvider``
(only the connectors container does). Agent-runtime gates must therefore read
``/services/platform/settings`` directly through the request's
``ConfigurationService`` — the same store the Labs UI writes to.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

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
