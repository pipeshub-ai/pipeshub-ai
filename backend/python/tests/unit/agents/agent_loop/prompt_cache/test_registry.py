"""Which strategy each configured chat model gets."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock
from urllib.parse import urlparse

import pytest
from langchain_core.messages import HumanMessage

from app.agent_loop_lib.transport.prompt_cache import (
    REJECTED_CACHE_PARAMS,
    CacheScope,
    PromptCacheSettings,
)
from app.agents.agent_loop.prompt_cache import (
    AnthropicCacheStrategy,
    BedrockCacheStrategy,
    NoCacheStrategy,
    OpenAICacheStrategy,
    cache_strategy_for,
    cache_target,
    forget_cache_params,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from langchain_core.language_models import BaseChatModel

    from app.agents.agent_loop.prompt_cache import CacheStrategy

ON = PromptCacheSettings()
SCOPE = CacheScope("org-1", "conv-1")
KEY = SCOPE.key(ON.routing_shards)


@pytest.fixture(autouse=True)
def _clean_rejections() -> Iterator[None]:
    REJECTED_CACHE_PARAMS.clear()
    yield
    REJECTED_CACHE_PARAMS.clear()


def _strategy(llm: object, model_name: str = "", settings: PromptCacheSettings = ON) -> CacheStrategy:
    return cache_strategy_for(llm, model_name=model_name, settings=settings, scope=SCOPE)


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

    @pytest.mark.parametrize("base_url", ["http://localhost:4000", "https://api.together.xyz/v1"])
    def test_compatible_hosts_get_nothing(self, base_url: str) -> None:
        assert isinstance(_strategy(_openai(base_url)), NoCacheStrategy)

    def test_openrouter_marks_claude_and_routes_by_key(self) -> None:
        strategy = _strategy(_openai("https://openrouter.ai/api/v1", "anthropic/claude-sonnet-5.5"))
        assert isinstance(strategy, OpenAICacheStrategy)
        messages, kwargs = strategy.finalize([HumanMessage(content="hi")])
        assert kwargs == {"prompt_cache_key": KEY}
        assert messages[-1].content == [{"type": "text", "text": "hi", "prompt_cache_breakpoint": {"mode": "explicit"}}]

    def test_foundry_only_for_openai_models(self) -> None:
        foundry = "https://acme.services.ai.azure.com/models"
        assert isinstance(_strategy(_openai(foundry, "gpt-5.4")), OpenAICacheStrategy)
        assert isinstance(_strategy(_openai(foundry, "Llama-4-Maverick")), NoCacheStrategy)

    def test_newer_family_gets_a_breakpoint_and_no_retention(self) -> None:
        strategy = _strategy(_openai(model="gpt-5.6"))
        # OpenAI routes GPT-5.6 and later by prefix: one key per org.
        assert strategy.finalize([])[1] == {"prompt_cache_key": SCOPE.key(1)}
        assert strategy.sent_params == {"prompt_cache_key", "prompt_cache_breakpoint"}
        assert len(strategy.system("stable", "volatile")) == 2

    def test_azure_gpt_6_keeps_the_sharded_key(self) -> None:
        strategy = _strategy(_azure(deployment="gpt-6.1-sol"), model_name="gpt-6.1-sol")
        assert strategy.finalize([])[1] == {"prompt_cache_key": KEY}
        assert "prompt_cache_breakpoint" in strategy.sent_params

    def test_a_refused_parameter_is_left_out_of_the_next_strategy(self) -> None:
        forget_cache_params(_openai(), "gpt-5.4", params={"prompt_cache_retention"})
        assert _strategy(_openai()).finalize([])[1] == {"prompt_cache_key": KEY}
        REJECTED_CACHE_PARAMS.record("gpt-6.1-sol", "https://acme.openai.azure.com", {"prompt_cache_breakpoint"})
        strategy = _strategy(_azure(deployment="gpt-6.1-sol"), model_name="gpt-6.1-sol")
        assert strategy.sent_params == {"prompt_cache_key"}

    def test_everything_refused_means_no_caching(self) -> None:
        forget_cache_params(_openai(), "gpt-5.4", params={"prompt_cache_key", "prompt_cache_retention"})
        assert isinstance(_strategy(_openai()), NoCacheStrategy)

    def test_xai_is_named_by_its_own_endpoint_and_gets_the_key_only(self) -> None:
        from langchain_xai import ChatXAI

        llm = ChatXAI(model="grok-4.7", api_key="test")
        assert urlparse(cache_target(llm, "grok-4.7").base_url).hostname == "api.x.ai"
        strategy = _strategy(llm, model_name="grok-4.7")
        assert strategy.finalize([])[1] == {"prompt_cache_key": KEY}
        assert strategy.sent_params == {"prompt_cache_key"}


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


class TestPromptFamily:
    def test_records_of_one_family_share_its_keys(self) -> None:
        from app.agents.agent_loop.prompt_cache import family_cache_strategy

        keys = {
            family_cache_strategy(_azure(), family="document-extraction", org_id="org-1", shard_key=f"r{i}")
            .finalize([])[1]["prompt_cache_key"]
            for i in range(50)
        }
        assert all(":document-extraction:" in key for key in keys)
        assert 1 < len(keys) <= PromptCacheSettings().routing_shards

    def test_a_refused_model_is_not_asked_again(self) -> None:
        from app.agents.agent_loop.prompt_cache import (
            family_cache_strategy,
            forget_cache_params,
        )

        llm = _openai()
        forget_cache_params(llm, params={"prompt_cache_key", "prompt_cache_retention"})
        assert isinstance(
            family_cache_strategy(llm, family="named-entities", org_id="org-1", shard_key="r1"), NoCacheStrategy,
        )
