"""`CacheReuseClass` + `decide()` — the question that actually controls
cost, per the plan's core idea: "will this prefix be read again before
the TTL expires?" A global on/off switch cannot answer that; it is
answered per call site, before the strategy ever runs.

- `MULTI_TURN` — the agent-loop turn sequence. The same prefix is
  re-read on every subsequent turn, typically within seconds; break-even
  clears on turn 2. **On by default.**
- `SHARED_STATIC` — a prefix that is byte-identical across DIFFERENT
  requests (intent parsing, goal building, query rewrite, ...). Reuse
  comes from request volume, not turns, so payoff is
  deployment-dependent. **Off by default** — enabled per call site only
  once Phase 0 measurement shows the prefix both clears the model's
  floor and is re-read within the TTL at real traffic (Phase 8).
- `ONE_SHOT_UNIQUE` — the cacheable prefix contains content unique to
  this call (a single document body, a health-check probe): a
  structurally guaranteed write-with-zero-reads. **Never cached.**

The admin `CacheConfig.enabled` flag (see `config.py`) is a global kill
switch layered UNDER this decision, not a replacement for it — turning
it off disables every reuse class; turning it on does not, by itself,
enable `SHARED_STATIC` sites that haven't been individually justified.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.llm.prompt_cache.capabilities import CacheCapability, resolve_capability
from app.llm.prompt_cache.config import CacheConfig


class CacheReuseClass(StrEnum):
    MULTI_TURN = "multi_turn"
    SHARED_STATIC = "shared_static"
    ONE_SHOT_UNIQUE = "one_shot_unique"


@dataclass(frozen=True)
class CacheDecision:
    enabled: bool
    reason: str
    """Logged verbatim — what makes a below-minimum or reuse-class
    no-op diagnosable instead of silent."""
    reuse_class: CacheReuseClass
    capability: CacheCapability
    ttl: str
    cache_key: str | None = None


def decide(
    *,
    reuse_class: CacheReuseClass,
    provider: str,
    model: str | None,
    cache_config: CacheConfig,
    shared_static_enabled: bool = False,
    cache_key: str | None = None,
) -> CacheDecision:
    """Resolves whether — and why — a call with this `reuse_class`
    against `(provider, model)` should be cached.

    `shared_static_enabled` is the Phase 8 per-call-site override: it
    has no effect on `MULTI_TURN`/`ONE_SHOT_UNIQUE` and only matters
    when `reuse_class is SHARED_STATIC`, so a caller flips it on for
    ONE justified site without touching every other `SHARED_STATIC`
    call in the codebase.
    """
    capability = resolve_capability(provider, model)

    if reuse_class is CacheReuseClass.ONE_SHOT_UNIQUE:
        return CacheDecision(
            enabled=False, reason="one_shot_unique_never_cached",
            reuse_class=reuse_class, capability=capability, ttl=capability.default_ttl,
        )

    if not cache_config.enabled:
        return CacheDecision(
            enabled=False, reason=f"cache_disabled_by_{cache_config.source}",
            reuse_class=reuse_class, capability=capability, ttl=capability.default_ttl,
        )

    if capability.mode == "none":
        return CacheDecision(
            enabled=False, reason="capability_mode_none",
            reuse_class=reuse_class, capability=capability, ttl=capability.default_ttl,
        )

    if reuse_class is CacheReuseClass.SHARED_STATIC and not shared_static_enabled:
        return CacheDecision(
            enabled=False, reason="shared_static_off_by_default",
            reuse_class=reuse_class, capability=capability, ttl=capability.default_ttl,
        )

    reason = (
        "multi_turn_default_on"
        if reuse_class is CacheReuseClass.MULTI_TURN
        else "shared_static_enabled_for_call_site"
    )
    return CacheDecision(
        enabled=True, reason=reason, reuse_class=reuse_class,
        capability=capability, ttl=capability.default_ttl, cache_key=cache_key,
    )


__all__ = ["CacheReuseClass", "CacheDecision", "decide"]
