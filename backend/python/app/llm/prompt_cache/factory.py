"""`resolve_strategy(provider, model)` — the single place that maps a
resolved `CacheCapability` to a concrete `PromptCacheStrategy`.

Callers should generally go through `decision.decide()` (Phase 2) once
it exists, which additionally applies the `CacheReuseClass` and the
global `CacheConfig` kill switch before deciding whether to even ask
for a strategy. This module stays a pure `(provider, model) ->
strategy` lookup so those two concerns compose instead of merging into
one god-function.
"""

from __future__ import annotations

from app.agent_loop_lib.cache.base import NoopStrategy, PromptCacheStrategy
from app.llm.prompt_cache.capabilities import resolve_capability
from app.llm.prompt_cache.strategy.anthropic import AnthropicCacheStrategy
from app.llm.prompt_cache.strategy.openai import OpenAICacheStrategy

# Stateless and cheap to construct — reused for every unsupported
# capability rather than allocated per call.
_NOOP_STRATEGY = NoopStrategy()

_OPENAI_LIKE_PROVIDERS = ("openai", "azure_openai")


def resolve_strategy(
    provider: str, model_name: str | None = None, cache_key: str | None = None
) -> PromptCacheStrategy:
    """Returns the strategy for `(provider, model_name)`, degrading to
    `NoopStrategy` for any capability that resolves to `mode="none"`
    (unsupported provider, unrecognized model on a known provider, or
    a provider not yet implemented — Google/Bedrock land in Phase 9).

    Every concrete strategy is constructed fresh per call rather than
    shared as a singleton: breakpoint floor and cap come from
    `capability`, which is MODEL-specific (Haiku's 2,048-token floor
    vs Sonnet/Opus's 1,024; GPT-5.6 explicit vs GPT-5.5 automatic) — a
    singleton would silently apply the wrong floor to whichever model
    resolved second. `cache_key` should be built via
    `app.llm.prompt_cache.cache_key.build_prompt_cache_key` — tenant
    (and user, per the plan's settled decision) scoped — and is only
    consumed by strategies that use one (OpenAI today).
    """
    capability = resolve_capability(provider, model_name)
    if capability.mode == "none":
        return _NOOP_STRATEGY
    provider_key = (provider or "").lower()
    if provider_key == "anthropic":
        return AnthropicCacheStrategy(capability)
    if provider_key in _OPENAI_LIKE_PROVIDERS:
        return OpenAICacheStrategy(capability, cache_key=cache_key)
    # Capability says explicit/automatic but no strategy is wired yet
    # for this provider (Google/Bedrock, Phase 9) — inert rather than
    # guessing at a shape the API would reject.
    return _NOOP_STRATEGY


__all__ = ["resolve_strategy"]
