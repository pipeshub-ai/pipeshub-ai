"""Which strategy each configured chat model gets."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from app.agent_loop_lib.transport.prompt_cache import (
    REJECTED_CACHE_PARAMS,
    PromptCacheSettings,
)
from app.agents.agent_loop.prompt_cache import (
    AnthropicCacheStrategy,
    BedrockCacheStrategy,
    NoCacheStrategy,
    OpenAICacheStrategy,
    cache_strategy_for,
    cache_target,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from langchain_core.language_models import BaseChatModel

    from app.agents.agent_loop.prompt_cache import CacheStrategy

ON = PromptCacheSettings()
KEY = "pipeshub:abc:1"


@pytest.fixture(autouse=True)
def _clean_rejections() -> Iterator[None]:
    REJECTED_CACHE_PARAMS.clear()
    yield
    REJECTED_CACHE_PARAMS.clear()


def _strategy(llm: object, model_name: str = "", settings: PromptCacheSettings = ON) -> CacheStrategy:
    return cache_strategy_for(llm, model_name=model_name, settings=settings, routing_key=KEY)


def _anthropic(base_url: str | None = None) -> BaseChatModel:
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(model="claude-opus-5-5", api_key="test", **({"base_url": base_url} if base_url else {}))


def _openai(base_url: str | None = None, model: str = "gpt-5.4") -> BaseChatModel:
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(model=model, api_key="test", **({"base_url": base_url} if base_url else {}))


def _azure(endpoint: str = "https://acme.openai.azure.com", deployment: str = "prod-54") -> BaseChatModel:
    from langchain_openai import AzureChatOpenAI

    return AzureChatOpenAI(azure_endpoint=endpoint, azure_deployment=deployment, api_version="2025-04-01-preview",
                           api_key="test")


def _bedrock(model_id: str) -> BaseChatModel:
    from langchain_aws import ChatBedrockConverse

    return ChatBedrockConverse(model=model_id, region_name="us-east-1", client=MagicMock())


class TestAnthropic:
    @pytest.mark.parametrize(("base_url", "top_level"), [
        (None, True),
        ("https://api.anthropic.com", True),
        ("https://acme.services.ai.azure.com/anthropic", True),
        ("https://llm-proxy.acme.internal", False),
    ])
    def test_top_level_only_where_accepted(self, base_url: str | None, top_level: bool) -> None:
        strategy = _strategy(_anthropic(base_url))
        assert isinstance(strategy, AnthropicCacheStrategy)
        assert strategy.finalize([])[1] == ({"cache_control": {"type": "ephemeral"}} if top_level else {})

    def test_ttl_from_settings(self) -> None:
        strategy = _strategy(_anthropic(), settings=PromptCacheSettings(anthropic_ttl="1h"))
        assert strategy.finalize([])[1] == {"cache_control": {"type": "ephemeral", "ttl": "1h"}}


class TestOpenAI:
    def test_openai_gets_key_and_retention(self) -> None:
        strategy = _strategy(_openai())
        assert isinstance(strategy, OpenAICacheStrategy)
        assert strategy.finalize([])[1] == {"prompt_cache_key": KEY, "prompt_cache_retention": "24h"}

    def test_azure_names_the_model_from_the_transport(self) -> None:
        # AzureChatOpenAI.model_name is None; the configured model decides retention.
        assert _strategy(_azure(), model_name="gpt-5.4").finalize([])[1] == {
            "prompt_cache_key": KEY, "prompt_cache_retention": "24h",
        }

    def test_azure_unknown_deployment_gets_the_key_only(self) -> None:
        assert _strategy(_azure()).finalize([])[1] == {"prompt_cache_key": KEY}

    @pytest.mark.parametrize("base_url", [
        "https://openrouter.ai/api/v1", "http://localhost:4000", "https://api.together.xyz/v1",
    ])
    def test_compatible_hosts_get_nothing(self, base_url: str) -> None:
        assert isinstance(_strategy(_openai(base_url)), NoCacheStrategy)

    def test_foundry_only_for_openai_models(self) -> None:
        foundry = "https://acme.services.ai.azure.com/models"
        assert isinstance(_strategy(_openai(foundry, "gpt-5.4")), OpenAICacheStrategy)
        assert isinstance(_strategy(_openai(foundry, "Llama-4-Maverick")), NoCacheStrategy)

    def test_newer_family_gets_no_retention(self) -> None:
        assert _strategy(_openai(model="gpt-5.6")).finalize([])[1] == {"prompt_cache_key": KEY}


class TestBedrock:
    @pytest.mark.parametrize(("model_id", "cache_tools"), [
        ("us.anthropic.claude-sonnet-4-5-20250929-v1:0", True),
        ("anthropic.claude-3-7-sonnet-20250219-v1:0", True),
        ("anthropic.claude-3-5-haiku-20241022-v1:0", True),
        ("global.anthropic.claude-opus-5-5-v1:0", True),
        ("amazon.nova-pro-v1:0", False),
        ("us.amazon.nova-lite-v1:0", False),
    ])
    def test_caching_models(self, model_id: str, cache_tools: bool) -> None:
        strategy = _strategy(_bedrock(model_id))
        assert isinstance(strategy, BedrockCacheStrategy)
        assert (strategy.tools([{"name": "t"}])[-1] == {"cachePoint": {"type": "default"}}) is cache_tools

    @pytest.mark.parametrize("model_id", [
        "anthropic.claude-3-sonnet-20240229-v1:0", "anthropic.claude-v2:1", "meta.llama3-70b-instruct-v1:0",
        "mistral.mistral-large-2402-v1:0",
    ])
    def test_other_models_get_nothing(self, model_id: str) -> None:
        assert isinstance(_strategy(_bedrock(model_id)), NoCacheStrategy)


class TestEverythingElse:
    @pytest.mark.parametrize("class_name", ["ChatGoogleGenerativeAI", "ChatVertexAI", "ChatOllama", "ChatGroq"])
    def test_unmatched_classes(self, class_name: str) -> None:
        assert isinstance(_strategy(type(class_name, (), {})()), NoCacheStrategy)

    def test_subclass_resolves_to_its_family(self) -> None:
        from langchain_anthropic import ChatAnthropic

        class TracedAnthropic(ChatAnthropic):
            pass

        assert isinstance(_strategy(TracedAnthropic(model="claude-opus-5-5", api_key="test")), AnthropicCacheStrategy)

    @pytest.mark.parametrize("build", [_anthropic, _openai, _azure, lambda: _bedrock("amazon.nova-pro-v1:0")])
    def test_disabled(self, build: Callable[[], BaseChatModel]) -> None:
        assert isinstance(_strategy(build(), settings=PromptCacheSettings(enabled=False)), NoCacheStrategy)

    def test_rejected_target_is_not_asked_again(self) -> None:
        llm = _openai()
        target = cache_target(llm)
        REJECTED_CACHE_PARAMS.record(target.model, target.base_url)
        assert isinstance(_strategy(llm), NoCacheStrategy)
        assert isinstance(_strategy(_openai(model="gpt-5.5")), OpenAICacheStrategy)


class TestCacheTarget:
    def test_targets(self) -> None:
        assert cache_target(_anthropic()).family == "anthropic"
        assert cache_target(_azure(), "gpt-5.4").model == "gpt-5.4"
        assert cache_target(_azure()).model == "prod-54"
        assert cache_target(_azure()).base_url == "https://acme.openai.azure.com"
        assert cache_target(_bedrock("amazon.nova-pro-v1:0")).model == "amazon.nova-pro-v1:0"
        assert cache_target(object(), "x") == cache_target(object(), "x")
