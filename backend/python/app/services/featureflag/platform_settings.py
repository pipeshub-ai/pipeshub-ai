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
from app.utils.env_utils import high_accuracy_mode

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService

logger = logging.getLogger(__name__)

PLATFORM_SETTINGS_KEY = "/services/platform/settings"


async def read_platform_feature_flag(
    flag_name: str,
    config_service: Any,
    *,
    default: bool,
    use_cache: bool = False,
    fail_closed: bool = False,
) -> bool:
    """Return a platform feature flag's boolean value from encrypted settings.

    Reads with ``use_cache=False`` by default so flipping a flag in Labs takes
    effect on the next request instead of after a service restart. A hot path
    may pass ``use_cache=True``: a save in Labs invalidates the cached key in
    every process. On missing settings or an absent flag key, returns ``default``.

    ``get_config`` answers ``default`` for a store outage or an undecryptable
    value, which here reads as "flag absent". For a flag whose default is on,
    that would silently undo an admin's saved opt-out, so ``fail_closed=True``
    asks ``get_config`` to raise and returns ``False`` when the saved value
    cannot be read. Without it, a read failure also returns ``default``.
    """
    try:
        if fail_closed:
            settings = await config_service.get_config(
                PLATFORM_SETTINGS_KEY, default={}, use_cache=use_cache, raise_on_error=True,
            )
        else:
            settings = await config_service.get_config(
                PLATFORM_SETTINGS_KEY, default={}, use_cache=use_cache,
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
        fallback = False if fail_closed else default
        logger.error(
            "Failed to read %s for feature flag %s; using %s: %s",
            PLATFORM_SETTINGS_KEY,
            flag_name,
            fallback,
            e,
            exc_info=True,
        )
        return fallback


async def _platform_flag(
    config_service: Optional["ConfigurationService"],
    flag: str,
    *,
    default: bool,
) -> bool:
    """Shared resolution path for org-level platform feature flags.

    Resolution order (first hit wins), mirroring ``is_mcp_enabled``:
    1. ``config_service`` — live read of the platform settings the Labs UI writes,
       via the shared ``read_platform_feature_flag`` helper (``use_cache=False``,
       so flipping the flag in Labs takes effect on the next request instead of
       after a service restart)
    2. ``FeatureFlagService`` — only reachable in services that wire an
       ``EtcdProvider`` (the connectors service); the query service does not, which
       is why the ``config_service`` read above is the primary path
    3. ``default``
    """
    if config_service is not None:
        return await read_platform_feature_flag(flag, config_service, default=default)

    try:
        from app.services.featureflag.featureflag import FeatureFlagService

        return bool(
            FeatureFlagService.get_service().is_feature_enabled(flag, default=default)
        )
    except Exception as e:
        logger.warning(
            f"FeatureFlagService unavailable for {flag}, treating as default={default}: {e}"
        )
        return default


async def is_api_toolsets_enabled(config_service: Optional["ConfigurationService"] = None) -> bool:
    """Org-level gate for API Toolsets: their routes, UI and agent tools. Source of truth is
    the ``ENABLE_API_TOOLSETS`` platform feature flag. Defaults to DISABLED: agents call
    org-internal APIs only once an admin opts in from Labs."""
    return await _platform_flag(config_service, CONFIG.ENABLE_API_TOOLSETS, default=False)


async def is_actions_enabled(config_service: Optional["ConfigurationService"] = None) -> bool:
    """Deployment-level gate for Actions: agents may only load/use toolset
    (connector) tools when this is true. Source of truth is the
    ``ENABLE_ACTIONS`` platform feature flag. Defaults to ENABLED; admins
    may opt out from Labs.
    """
    return await _platform_flag(config_service, CONFIG.ENABLE_ACTIONS, default=True)


async def is_skills_enabled(config_service: Optional["ConfigurationService"] = None) -> bool:
    """Org-level gate for the whole Skills subsystem: UI, REST, agent-runtime
    skill tools, and builtin seeding. Source of truth is the ``ENABLE_SKILLS``
    platform feature flag. Defaults to ENABLED (Beta) — mirrors
    ``is_actions_enabled``.

    This is layered *under* the deployment-level ``PIPESHUB_ENABLE_SKILLS`` env
    kill-switch (see ``skills_wiring.skills_enabled()``); both must be true for
    skills to run.
    """
    return await _platform_flag(config_service, CONFIG.ENABLE_SKILLS, default=True)


async def is_named_entity_extraction_enabled(config_service: ConfigurationService | None = None) -> bool:
    """Gate for typed named-entity extraction and the tools that read it: on when
    the deployment sets ``HIGH_ACCURACY_MODE`` or the Labs flag is on. The flag is
    read live and defaults to off; under ``HIGH_ACCURACY_MODE`` it is not read."""
    if high_accuracy_mode():
        return True
    return await _platform_flag(config_service, CONFIG.ENABLE_NAMED_ENTITY_EXTRACTION, default=False)


async def is_soft_delete_enabled(config_service: ConfigurationService | None = None) -> bool:
    """Whether a record delete moves the record to the trash instead of removing it.

    Read live on every delete. Defaults to DISABLED: off means today's hard delete.
    """
    return await _platform_flag(config_service, CONFIG.ENABLE_SOFT_DELETE, default=False)


async def is_user_context_enabled(config_service: Optional["ConfigurationService"] = None) -> bool:
    """Org-level gate for injecting user/org profile into the default
    assistant and Universal Agent Mode system prompts. Source of truth is
    the ``ENABLE_USER_CONTEXT`` platform feature flag. Defaults to ENABLED
    — this is pre-existing prompt behaviour; admins may opt out from Labs.

    Agent Builder agents do not use this flag. Each stored agent has its
    own ``sendUserContext`` field.
    """
    return await _platform_flag(config_service, CONFIG.ENABLE_USER_CONTEXT, default=True)


async def is_kh_scope_listing_enabled(config_service: Optional["ConfigurationService"]) -> bool:
    """Gate for the knowledge hub's precomputed-scope listing. Source of truth
    is the ``ENABLE_KH_SCOPE_LISTING`` platform feature flag. Defaults to
    DISABLED, matching the Labs default. Read on every listing request, so it
    reads through the config cache; a save in Labs invalidates it everywhere.
    """
    if config_service is None:
        return False
    return await read_platform_feature_flag(
        CONFIG.ENABLE_KH_SCOPE_LISTING, config_service, default=False, use_cache=True,
    )
