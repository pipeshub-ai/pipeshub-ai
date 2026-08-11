"""Env-driven resolution into `CacheConfig` — the global kill switch
layered UNDER the per-call-site `CacheReuseClass` decision (Phase 2).

Phase 1 ships env-only resolution (`ENABLE_PROMPT_CACHING`, default
`true`, per the requirement that prompt caching ships on by default
with an explicit opt-out). Phase 7 adds the platform-wide Labs toggle
via `FeatureFlagService` on top of this same env floor, without
changing this module's public shape — `resolve_cache_config()` stays
the one function callers use.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

ENABLE_PROMPT_CACHING_ENV_VAR = "ENABLE_PROMPT_CACHING"


def _env_bool(name: str, default: bool) -> bool:  # noqa: FBT001
    """Local copy of the codebase's dominant env-bool idiom (see
    `app.agents.agent_loop.env_utils.env_bool`), duplicated rather than
    imported: this package is framework-neutral and importable from
    indexing/query call sites that have no dependency on the agent-loop
    adapter package at all, and this idiom is a stable three-line
    function, not something worth adding a layering edge for.
    """
    return os.getenv(name, str(default)).strip().lower() == "true"


@dataclass(frozen=True)
class CacheConfig:
    """Resolved caching posture for the process. `enabled=False` is a
    global kill switch over every `CacheReuseClass`; per-site
    eligibility is a separate, narrower decision made even when
    `enabled` is True (see `decision.py`, Phase 2).
    """

    enabled: bool
    source: str
    """Where `enabled` came from: "env" (the env var floor, or the
    platform flag service was unavailable/not yet initialized) or
    "feature_flag" (the platform Labs toggle was actually consulted)."""


def _consult_platform_flag(default: bool) -> tuple[bool, bool]:
    """Returns `(enabled, consulted)`. `consulted=False` means the
    `FeatureFlagService` singleton could not be reached (not wired into
    this process's container, e.g. a script or test run) — the caller
    should then treat this as "no opinion" rather than as an explicit
    `False`, matching the same fallback shape
    `app.modules.agents.qna.tool_system.code_execution_enabled` already
    uses for `ENABLE_CODE_EXECUTION`.

    Deliberately synchronous and non-blocking: this is consulted on every
    LLM call's cache-kwargs resolution
    (`LangChainTransport._resolve_cache_kwargs`), so it only ever reads
    `FeatureFlagService`'s already-in-memory value — never awaits a fresh
    etcd round-trip here. Staleness is bounded instead by
    `FeatureFlagService.start_periodic_refresh`, started once at process
    startup for the query/indexing services (see `app.query_main`,
    `app.containers.indexing`).
    """
    try:
        from app.services.featureflag.config.config import CONFIG
        from app.services.featureflag.featureflag import FeatureFlagService

        return (
            bool(FeatureFlagService.get_service().is_feature_enabled(CONFIG.ENABLE_PROMPT_CACHING, default=default)),
            True,
        )
    except Exception:
        return default, False


def resolve_cache_config() -> CacheConfig:
    """Enabled by default. `ENABLE_PROMPT_CACHING=false` (env) is a hard
    floor no platform flag can override — set it and prompt caching is off
    for this process regardless of Labs. Above that floor, the
    `ENABLE_PROMPT_CACHING` Labs toggle (default enabled) can still turn it
    off; `enabled` is the AND of both.
    """
    env_enabled = _env_bool(ENABLE_PROMPT_CACHING_ENV_VAR, True)
    if not env_enabled:
        return CacheConfig(enabled=False, source="env")

    flag_enabled, consulted = _consult_platform_flag(default=True)
    if consulted:
        return CacheConfig(enabled=flag_enabled, source="feature_flag")
    return CacheConfig(enabled=True, source="env")


__all__ = ["CacheConfig", "ENABLE_PROMPT_CACHING_ENV_VAR", "resolve_cache_config"]
