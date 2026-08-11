"""Phase 0: structured observation of prompt-cache usage, zero behavior change.

Every function here only reads a value the provider/LangChain already
computed and logs it — none of them mutate a request, a response, or
control flow. That is deliberate: Phase 0 exists to validate the
break-even assumptions in the plan (~1.4 reads/write at a 5m TTL)
against real traffic before Phase 1 changes any provider behavior.

`log_cache_usage` never raises: a bug in measurement must not take
down the call site it instruments.

Phase 7 extends this with the call's `CacheDecision` (why caching was or
wasn't attempted) alongside the outcome (what actually happened), so the
two don't have to be cross-referenced from two separate, differently-timed
log lines — see `LangChainTransport._resolve_cache_kwargs`, which is the
only call site that has a `CacheDecision` to attach today.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.llm.prompt_cache.decision import CacheDecision

logger = logging.getLogger("app.llm.prompt_cache.metrics")

# Provider SDK class name -> the provider label used in logs and, from
# Phase 1 onward, in `resolve_capability(provider, model)`. Best-effort
# and label-only: never used to decide API shape or dispatch, which stays
# inside each provider's own LangChain integration.
_LANGCHAIN_CLASS_TO_PROVIDER: dict[str, str] = {
    "ChatAnthropic": "anthropic",
    "ChatOpenAI": "openai",
    "AzureChatOpenAI": "azure_openai",
    "ChatBedrock": "bedrock",
    "ChatBedrockConverse": "bedrock",
    "ChatGoogleGenerativeAI": "google",
    "ChatVertexAI": "google",
    "ChatMistralAI": "mistral",
    "ChatOllama": "ollama",
    "ChatFireworks": "fireworks",
    "ChatGroq": "groq",
    "ChatCohere": "cohere",
    "ChatXAI": "xai",
    "ChatTogether": "together",
    "ChatDeepSeek": "deepseek",
    "ChatPerplexity": "perplexity",
}


def detect_langchain_provider(llm: Any) -> str:  # noqa: ANN401
    """Best-effort provider label for a LangChain `BaseChatModel` instance.

    Falls back to the lowercased class name for anything unmapped
    (custom/OpenAI-compatible integrations, e.g. `ChatLiteLLM`) rather
    than raising or returning "unknown" — the label still groups by
    integration even when it isn't in the table above.
    """
    cls_name = type(llm).__name__
    return _LANGCHAIN_CLASS_TO_PROVIDER.get(cls_name, cls_name.lower())


def model_name_of(llm: Any) -> str:  # noqa: ANN401
    """Best-effort model identifier off a LangChain `BaseChatModel`."""
    for attr in ("model", "model_name", "model_id"):
        value = getattr(llm, attr, None)
        if isinstance(value, str) and value:
            return value
    return ""


@dataclass(frozen=True)
class CacheUsageSample:
    """One LLM call's usage, normalized so `input_tokens` EXCLUDES cached
    tokens on every path that produces a sample here.

    This differs from `app.agents.agent_loop.converters.token_usage_from_ai_message`,
    which currently double-counts cache tokens into `input_tokens` on the
    LangChain path (see the plan's "Existing bugs this plan fixes" #1,
    fixed at that call site in Phase 5). This module is new code with no
    existing behavior to preserve, so it is written with the correct
    semantics from the start rather than inheriting the bug.
    """

    provider: str
    model: str
    call_site: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    decision_enabled: bool | None = None
    """Whether cache kwargs were actually sent for this call, per the
    `CacheDecision` resolved before it. `None` means no decision was
    threaded through (e.g. the Phase 0 `Runnable`-chain taps in
    `query_transform.py`/`query_decompose.py`, which log usage without
    ever having made a caching decision of their own)."""
    decision_reason: str | None = None
    """`CacheDecision.reason` verbatim — why caching was/wasn't attempted,
    logged next to what actually happened so the two never need
    cross-referencing across two separately-timed log lines."""


def usage_from_ai_message(
    ai_message: Any,  # noqa: ANN401
    *,
    provider: str,
    model: str,
    call_site: str,
    decision: "CacheDecision | None" = None,
) -> CacheUsageSample | None:
    """Extract a `CacheUsageSample` from a LangChain `AIMessage`-like object.

    Returns `None` when there is no usage metadata at all (a provider
    integration that never populates it), so callers skip logging
    cleanly instead of emitting an all-zero line indistinguishable from
    "caching produced zero reads".
    """
    usage = getattr(ai_message, "usage_metadata", None)
    if not usage:
        return None
    input_details = usage.get("input_token_details") or {}
    cache_read = input_details.get("cache_read", 0) or 0
    cache_write = input_details.get("cache_creation", 0) or 0
    raw_input = usage.get("input_tokens", 0) or 0
    return CacheUsageSample(
        provider=provider,
        model=model,
        call_site=call_site,
        input_tokens=max(raw_input - cache_read, 0),
        output_tokens=usage.get("output_tokens", 0) or 0,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        decision_enabled=decision.enabled if decision is not None else None,
        decision_reason=decision.reason if decision is not None else None,
    )


def _net_savings_tokens(
    *, provider: str, model: str, cache_read_tokens: int, cache_write_tokens: int
) -> float | None:
    """Net effect of this call's cache reads/writes, expressed in
    full-price-input-token EQUIVALENTS — not dollars. This package has no
    per-model $/token pricing table by design (that lives in
    `app.agent_loop_lib.modules.providers.budget.pricing`, a hermetic,
    agent-loop-only concern this framework-neutral package must not depend
    on); `resolve_capability`'s read/write multipliers are enough to answer
    "was this call's cache activity a net win, in token terms" without one.

    Positive => cheaper than sending every token at full price this call.
    Negative => this call paid a write premium its OWN reads didn't recoup
    (expected on a cold/first write — the payoff is a LATER call's reads,
    not this one). `None` if the capability lookup itself fails.
    """
    try:
        from app.llm.prompt_cache.capabilities import resolve_capability

        capability = resolve_capability(provider, model)
    except Exception:
        return None
    read_savings = cache_read_tokens * (1 - capability.read_multiplier)
    write_cost = cache_write_tokens * (capability.write_multiplier - 1)
    return read_savings - write_cost


def log_cache_usage(sample: CacheUsageSample | None) -> None:
    """Log one LLM call's usage for prompt-cache measurement.

    A `None` sample (no usage metadata available) is a silent no-op —
    that case is itself useful to know about via the absence of a log
    line for a given provider, not by emitting a misleading all-zero one.
    """
    if sample is None:
        return
    try:
        total_input = sample.input_tokens + sample.cache_read_tokens
        hit_rate = sample.cache_read_tokens / total_input if total_input else 0.0
        net_savings = _net_savings_tokens(
            provider=sample.provider,
            model=sample.model,
            cache_read_tokens=sample.cache_read_tokens,
            cache_write_tokens=sample.cache_write_tokens,
        )
        decision_suffix = ""
        if sample.decision_enabled is not None:
            decision_suffix = " decision_enabled=%s decision_reason=%s" % (
                sample.decision_enabled,
                sample.decision_reason,
            )
        logger.info(
            "prompt_cache_usage provider=%s model=%s call_site=%s "
            "input_tokens=%d output_tokens=%d cache_read_tokens=%d "
            "cache_write_tokens=%d hit_rate=%.3f net_savings_tokens=%s%s",
            sample.provider,
            sample.model,
            sample.call_site,
            sample.input_tokens,
            sample.output_tokens,
            sample.cache_read_tokens,
            sample.cache_write_tokens,
            hit_rate,
            f"{net_savings:.1f}" if net_savings is not None else "n/a",
            decision_suffix,
        )
    except Exception:
        # Measurement must never take down the call site it instruments.
        logger.debug("prompt_cache_usage: failed to log sample", exc_info=True)


__all__ = [
    "CacheUsageSample",
    "detect_langchain_provider",
    "model_name_of",
    "usage_from_ai_message",
    "log_cache_usage",
]
