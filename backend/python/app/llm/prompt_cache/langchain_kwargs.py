"""Computes the invoke-time kwargs `LangChainTransport` passes to
`.ainvoke()`/`.astream()` — the ONE path that carries essentially all
PipesHub chat traffic (all 18 configured providers resolve through
`get_generator_model` -> `LangChainTransport`).

Deliberately does NOT reuse the dict-payload `PromptCacheStrategy`
machinery from `strategy/anthropic.py`/`strategy/openai.py`: those
operate on the formatted PROVIDER REQUEST DICTS the native
`AnthropicTransport`/`OpenAITransport` build themselves.
`LangChainTransport` never sees that shape — LangChain's
`BaseChatModel` builds it internally from `BaseMessage` objects. The
mechanics verified against current LangChain/provider docs are simpler
for this path and need no message restructuring at all:

- `ChatAnthropic` has an AUTOMATIC mode: passing `cache_control` as an
  INVOKE kwarg (`model.ainvoke(messages, cache_control={"type":
  "ephemeral"})`) places the breakpoint on the last cacheable block —
  no block-list restructuring needed.
- `ChatOpenAI`-family models accept `prompt_cache_key` as an invoke
  kwarg for automatic-mode routing on EVERY OpenAI-family model (not
  just GPT-5.6+).

`prompt_cache_options`/explicit per-block breakpoints
(`prompt_cache_breakpoint`) are deliberately NOT sent on this path:
they require restructuring LangChain message content into block
lists, and setting `prompt_cache_options.mode="explicit"` with zero
breakpoints placed would disable OpenAI's implicit last-message
caching outright — turning "explicit mode" into strictly worse
caching than doing nothing at all. That restructuring already exists
for callers on the native `OpenAITransport`
(`app.llm.prompt_cache.strategy.openai.OpenAICacheStrategy`);
extending it to LangChain's message model is not promised here.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from app.llm.prompt_cache.config import CacheConfig
from app.llm.prompt_cache.decision import CacheDecision, CacheReuseClass, decide

_OPENAI_LIKE_PROVIDERS = frozenset({"openai", "azure_openai"})
_ANTHROPIC_INVOKE_KWARGS = {"cache_control": {"type": "ephemeral"}}

# Same host allow-list `app.utils.aimodels._targets_openai_responses_api`
# uses to gate the Responses API for the identical underlying reason: a
# plain `ChatOpenAI` instance only IS OpenAI's own backend when nothing
# overrode its default endpoint.
_OPENAI_NATIVE_HOSTS = frozenset({"api.openai.com"})


def resolve_cache_provider(llm: Any, detected_provider: str) -> str:  # noqa: ANN401
    """Downgrades a `detected_provider` of `"openai"` to `"unknown"`
    (which `resolve_capability` maps to a `mode="none"` capability, i.e.
    no cache kwargs at all) whenever the underlying `ChatOpenAI` instance
    was NOT actually pointed at OpenAI's own API.

    `app.llm.prompt_cache.metrics.detect_langchain_provider` maps every
    `ChatOpenAI`-class instance to `"openai"` by LangChain class name
    alone — correct for a measurement label, but not safe for deciding
    whether to send an OpenAI-specific invoke kwarg: `get_generator_model`
    (`app/utils/aimodels.py`) also constructs plain `ChatOpenAI` for
    OpenAI-compatible gateways that share the wire protocol but not
    necessarily OpenAI's own caching semantics server-side — LM Studio
    (local server), a generic `openAICompatible` entry, LiteLLM proxy,
    OpenRouter, MiniMax. Sending `prompt_cache_key` to one of those risks
    a 400 for an unrecognized field rather than the intended harmless
    no-op. `AzureChatOpenAI` is a distinct LangChain class already
    labeled `"azure_openai"` by `detect_langchain_provider` and is never
    affected by this downgrade.
    """
    if detected_provider != "openai":
        return detected_provider
    base_url = getattr(llm, "openai_api_base", None) or getattr(llm, "base_url", None)
    if not base_url:
        return "openai"
    host = urlparse(str(base_url)).hostname or ""
    return "openai" if host.lower() in _OPENAI_NATIVE_HOSTS else "unknown"


def resolve_langchain_cache_kwargs(
    *,
    provider: str,
    model: str,
    reuse_class: CacheReuseClass,
    cache_config: CacheConfig,
    cache_key: str | None = None,
    shared_static_enabled: bool = False,
) -> tuple[dict[str, object], CacheDecision]:
    """Returns `(invoke_kwargs, decision)`. `invoke_kwargs` is `{}`
    whenever `decision.enabled` is `False` (unsupported provider, the
    `ENABLE_PROMPT_CACHING` kill switch, or a reuse class this call
    isn't eligible for) OR when the provider's automatic mode needs no
    kwarg at all (Gemini) — callers can always do
    `llm.ainvoke(messages, **invoke_kwargs)` unconditionally, with an
    empty dict being a correct, inert result rather than a special
    case the caller has to branch on.

    `shared_static_enabled` (Phase 8) is the Phase-2 per-call-site override
    for `CacheReuseClass.SHARED_STATIC` — see `decide()`. IMPORTANT caveat
    for callers considering flipping this to `True` for a call site whose
    variable content is NOT the physically last message/block: on this
    LangChain invoke-kwarg path, `cache_control` (Anthropic) always lands
    on the LAST cacheable block, per this module's docstring. If the
    static, reusable text is NOT what's last (e.g. a single multi-block
    `HumanMessage` shaped `[static_instructions, ..., unique_document]`),
    enabling this for Anthropic would mark the UNIQUE trailing content as
    "cacheable" instead — a guaranteed write-with-zero-reads, strictly
    worse than no caching at all. OpenAI/Gemini's automatic modes are safe
    regardless (they match the longest common PREFIX from byte 0, not a
    block-list position), so this is an Anthropic-specific hazard, not a
    reason to avoid the flag everywhere.
    """
    decision = decide(
        reuse_class=reuse_class, provider=provider, model=model,
        cache_config=cache_config, cache_key=cache_key,
        shared_static_enabled=shared_static_enabled,
    )
    if not decision.enabled:
        return {}, decision

    provider_key = (provider or "").lower()
    if provider_key == "anthropic":
        return dict(_ANTHROPIC_INVOKE_KWARGS), decision
    if provider_key in _OPENAI_LIKE_PROVIDERS:
        return ({"prompt_cache_key": cache_key} if cache_key else {}), decision
    return {}, decision


__all__ = ["resolve_cache_provider", "resolve_langchain_cache_kwargs"]
