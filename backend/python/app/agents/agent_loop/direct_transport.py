"""Picks the direct-SDK transport matching a configured LangChain model.

The switch stays binary -- `PIPESHUB_AGENT_TRANSPORT=langchain|direct` -- because
a deployment chooses *how* it talks to providers, not which provider a given
request uses. Which provider is a property of the model the request selected, so
that decision belongs here rather than in an env var: with four models
configured, an env value naming one provider is meaningless for a request that
picked another.

Dispatch is on the LangChain model class, which is the discriminator actually
available at the call site and the same object each `from_langchain_model`
consumes. `AzureChatOpenAI` and `ChatOpenAI` are siblings under `BaseChatOpenAI`
rather than parent and child, so isinstance order carries no trap.

Anything without a direct transport falls back to LangChain, so adding a model
for an unsupported provider degrades to the old path instead of failing.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.transport.prompt_cache import (
    REJECTED_CACHE_PARAMS,
    CacheScope,
    openai_cache_plan,
)
from app.agents.agent_loop.prompt_cache import cache_target
from app.agents.agent_loop.prompt_cache.strategies import ANTHROPIC_PARAM
from app.services.model_identity.transport.instrument import identity_async_http_client

if TYPE_CHECKING:
    from app.agent_loop_lib.transport.base import LLMTransport
    from app.agent_loop_lib.transport.prompt_cache import PromptCacheSettings

logger = logging.getLogger(__name__)


def build_direct_transport(
    llm: Any,
    model_name: str = "",
    model_key: str | None = None,
    *,
    cache_settings: PromptCacheSettings | None = None,
    cache_scope: CacheScope | None = None,
) -> "LLMTransport | None":
    """The direct transport for `llm`, or None when there is no direct path.

    Returning None rather than raising is deliberate: the caller falls back to
    the LangChain transport, so an unsupported provider is slower, never broken.

    Prompt caching follows the policy the LangChain arm applies
    (`transport/prompt_cache.py`), passed to each transport as plain values.
    Without `cache_settings` the transports keep their own defaults.
    """
    class_name = type(llm).__name__
    target = cache_target(llm, model_name)
    refused = REJECTED_CACHE_PARAMS.refused(target.model, target.base_url)
    caching = cache_settings is not None and cache_settings.enabled and "*" not in refused

    # The LangChain model's own client when identity forwarding is enabled on its
    # connection, so a direct request carries the same identity (and egress guard)
    # a LangChain request does. Nothing is passed otherwise: the default is unchanged.
    http = {"http_client": client} if (client := identity_async_http_client(llm)) is not None else {}

    def _openai_cache() -> dict[str, Any]:
        if cache_settings is None:
            return {}
        plan = openai_cache_plan(
            model=target.model, base_url=target.base_url, settings=cache_settings,
            scope=cache_scope or CacheScope(""), refused=refused,
        ) if caching else None
        return {
            "cache_plan": plan,
            "on_cache_rejected": lambda params: REJECTED_CACHE_PARAMS.record(target.model, target.base_url, params),
        }

    def _anthropic_cache() -> dict[str, Any]:
        if cache_settings is None:
            return {}
        return {
            "cache_ttl": cache_settings.anthropic_ttl if caching and ANTHROPIC_PARAM not in refused else None,
            "on_cache_rejected": lambda: REJECTED_CACHE_PARAMS.record(target.model, target.base_url),
        }

    # Imported lazily and individually: each transport imports its provider SDK
    # at construction, and a deployment that installs only some of them should
    # not fail to import this module.
    try:
        if class_name == "AzureChatOpenAI":
            from app.agent_loop_lib.transport.azure_openai import AzureOpenAITransport
            return AzureOpenAITransport.from_langchain_model(
                llm, model_name=model_name, model_key=model_key, **_openai_cache(), **http,
            )
        if class_name in ("ChatOpenAI", "BaseChatOpenAI"):
            from app.agent_loop_lib.transport.openai import OpenAITransport
            return OpenAITransport.from_langchain_model(
                llm, model_name=model_name, model_key=model_key, **_openai_cache(), **http,
            )
        if class_name == "ChatAnthropic":
            from app.agent_loop_lib.transport.anthropic import AnthropicTransport
            return AnthropicTransport.from_langchain_model(
                llm, model_name=model_name, model_key=model_key, **_anthropic_cache(), **http,
            )
        if class_name == "ChatGoogleGenerativeAI":
            from app.agent_loop_lib.transport.gemini import GeminiTransport
            return GeminiTransport.from_langchain_model(
                llm, model_name=model_name, model_key=model_key,
            )
    except Exception as exc:
        # A misconfigured model must not take the turn down: the LangChain arm
        # can still serve it, and the log names the model so the gap is visible.
        logger.warning(
            "build_direct_transport: no direct transport for %s (model=%s): %s; "
            "falling back to LangChain",
            class_name, model_name or "?", exc,
        )
        return None

    logger.info(
        "build_direct_transport: %s has no direct transport (model=%s); "
        "using LangChain",
        class_name, model_name or "?",
    )
    return None


__all__ = ["build_direct_transport"]
