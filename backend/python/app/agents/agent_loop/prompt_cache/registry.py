"""Which ``CacheStrategy`` a LangChain chat model gets.

Matched on class names in the model's MRO, as ``_supports_multipart_tool_result``
does, so no provider package has to be installed for this module to import and
a subclass resolves to its family. Anything unmatched gets ``NoCacheStrategy``:
an unknown provider is never sent a parameter it may reject.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.transport.prompt_cache import (
    REJECTED_CACHE_PARAMS,
    anthropic_top_level_allowed,
    openai_cache_kwargs,
)
from app.agents.agent_loop.prompt_cache.strategies import (
    AnthropicCacheStrategy,
    BedrockCacheStrategy,
    NoCacheStrategy,
    OpenAICacheStrategy,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.agent_loop_lib.transport.prompt_cache import PromptCacheSettings
    from app.agents.agent_loop.prompt_cache.strategies import CacheStrategy

# Bedrock models that take cache points: Claude 3.5 Haiku, 3.7 Sonnet and every
# Claude 4+ (tools, system, messages); Nova (system and messages only). Older
# models reject the field, so they are matched out rather than tried.
_BEDROCK_CLAUDE = re.compile(r"anthropic\.claude-(3-7-sonnet|3-5-haiku|(opus|sonnet|haiku)-\d)", re.IGNORECASE)
_BEDROCK_NOVA = re.compile(r"amazon\.nova-(micro|lite|pro|premier)", re.IGNORECASE)


@dataclass(frozen=True)
class CacheTarget:
    """The model and endpoint a strategy is chosen for, and the identity a
    rejected cache parameter is remembered under."""

    family: str
    model: str
    base_url: str | None


def cache_target(chat_model: Any, model_name: str = "") -> CacheTarget:  # noqa: ANN401 - any LangChain chat model
    names = {cls.__name__ for cls in type(chat_model).__mro__}
    if "ChatAnthropic" in names:
        return CacheTarget(
            "anthropic", _first(model_name, getattr(chat_model, "model", None)),
            getattr(chat_model, "anthropic_api_url", None),
        )
    if "ChatBedrockConverse" in names:
        return CacheTarget(
            "bedrock",
            _first(getattr(chat_model, "base_model_id", None), getattr(chat_model, "model_id", None), model_name),
            getattr(chat_model, "endpoint_url", None) or getattr(chat_model, "region_name", None),
        )
    if "AzureChatOpenAI" in names:
        # ``model_name`` is None on AzureChatOpenAI; the deployment is all it knows.
        return CacheTarget(
            "openai",
            _first(model_name, getattr(chat_model, "model_name", None), getattr(chat_model, "deployment_name", None)),
            getattr(chat_model, "azure_endpoint", None),
        )
    if names & {"BaseChatOpenAI", "ChatOpenAI"}:
        return CacheTarget(
            "openai", _first(model_name, getattr(chat_model, "model_name", None)),
            getattr(chat_model, "openai_api_base", None),
        )
    return CacheTarget("none", model_name, None)


def cache_strategy_for(
    chat_model: Any,  # noqa: ANN401 - any LangChain chat model
    *,
    model_name: str = "",
    settings: PromptCacheSettings,
    routing_key: str,
) -> CacheStrategy:
    target = cache_target(chat_model, model_name)
    if not settings.enabled or REJECTED_CACHE_PARAMS.rejected(target.model, target.base_url):
        return NoCacheStrategy()
    build = _BUILDERS.get(target.family)
    return build(target, settings, routing_key) if build else NoCacheStrategy()


def _anthropic(target: CacheTarget, settings: PromptCacheSettings, _key: str) -> CacheStrategy:
    return AnthropicCacheStrategy(settings.breakpoint, top_level=anthropic_top_level_allowed(target.base_url))


def _bedrock(target: CacheTarget, _settings: PromptCacheSettings, _key: str) -> CacheStrategy:
    if _BEDROCK_CLAUDE.search(target.model):
        return BedrockCacheStrategy(cache_tools=True)
    if _BEDROCK_NOVA.search(target.model):
        return BedrockCacheStrategy(cache_tools=False)
    return NoCacheStrategy()


def _openai(target: CacheTarget, settings: PromptCacheSettings, key: str) -> CacheStrategy:
    kwargs = openai_cache_kwargs(model=target.model, base_url=target.base_url, settings=settings, key=key)
    return OpenAICacheStrategy(kwargs) if kwargs else NoCacheStrategy()


_BUILDERS: dict[str, Callable[[CacheTarget, PromptCacheSettings, str], CacheStrategy]] = {
    "anthropic": _anthropic,
    "bedrock": _bedrock,
    "openai": _openai,
}


def _first(*values: str | None) -> str:
    return next((value for value in values if value), "")
