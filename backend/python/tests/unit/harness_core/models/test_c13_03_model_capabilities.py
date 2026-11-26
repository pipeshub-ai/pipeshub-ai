"""C13.03: known model-endpoint incompatibilities are data, and the callers read them.

DeepSeek's Anthropic-compatible endpoint takes no ``cache_control`` and no image input. The row
says so once; the prompt-cache policy sends it no cache parameters and the LLM health check
refuses Multimodal for it without calling the provider.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from app.agents.agent_loop.prompt_cache.registry import cache_strategy_for
from app.agents.agent_loop.prompt_cache.strategies import (
    AnthropicCacheStrategy,
    NoCacheStrategy,
)
from harness_core.models.capabilities import (
    MODEL_CAPABILITY_ROWS,
    UNKNOWN,
    CapabilityRow,
    ModelCapabilities,
    model_capabilities,
)
from harness_core.models.transports.prompt_cache import (
    REJECTED_CACHE_PARAMS,
    CacheScope,
    PromptCacheSettings,
    cache_params_allowed,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

DEEPSEEK = "https://api.deepseek.com/anthropic"


class ChatAnthropic:
    """Stands in for LangChain's class: the registry matches on the class name."""

    def __init__(self, model: str, anthropic_api_url: str | None) -> None:
        self.model = model
        self.anthropic_api_url = anthropic_api_url


@pytest.fixture(autouse=True)
def _fresh_rejections() -> Iterator[None]:
    REJECTED_CACHE_PARAMS.clear()
    yield
    REJECTED_CACHE_PARAMS.clear()


class TestRows:
    def test_c13_03_every_row_cites_its_vendor_page_and_date(self) -> None:
        assert MODEL_CAPABILITY_ROWS
        for row in MODEL_CAPABILITY_ROWS:
            assert row.source.startswith("https://"), row
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", row.read_on), row
            # A row records only what an endpoint does not accept, never a switch-on.
            assert True not in (row.prompt_cache, row.image_input), row

    def test_c13_03_deepseek_anthropic_endpoint_has_no_cache_control_and_no_image_input(self) -> None:
        assert model_capabilities("deepseek-chat", DEEPSEEK) == ModelCapabilities(
            prompt_cache=False, image_input=False,
        )

    @pytest.mark.parametrize("base_url", [None, "", "https://api.anthropic.com", "https://example.com/v1"])
    def test_c13_03_an_endpoint_no_row_names_is_unknown(self, base_url: str | None) -> None:
        assert model_capabilities("deepseek-chat", base_url) is UNKNOWN

    def test_c13_03_a_row_can_narrow_to_model_ids_and_a_host_suffix(self) -> None:
        rows = (CapabilityRow(host=".example.net", model=re.compile(r"^vision-"), prompt_cache=None,
                              image_input=False, source="https://example.net/docs", read_on="2026-11-02"),)
        assert model_capabilities("vision-1", "https://eu.example.net", rows).image_input is False
        assert model_capabilities("text-1", "https://eu.example.net", rows) is UNKNOWN
        assert model_capabilities("vision-1", "https://example.org", rows) is UNKNOWN


class TestPromptCache:
    def test_c13_03_no_cache_parameters_go_to_an_endpoint_whose_row_says_none(self) -> None:
        assert cache_params_allowed("deepseek-chat", DEEPSEEK) is False
        strategy = cache_strategy_for(
            ChatAnthropic("deepseek-chat", DEEPSEEK), settings=PromptCacheSettings(), scope=CacheScope("org-1"),
        )
        assert isinstance(strategy, NoCacheStrategy)

    def test_c13_03_anthropics_own_api_still_gets_its_markers(self) -> None:
        assert cache_params_allowed("claude-sonnet-5", None) is True
        strategy = cache_strategy_for(
            ChatAnthropic("claude-sonnet-5", None), settings=PromptCacheSettings(), scope=CacheScope("org-1"),
        )
        assert isinstance(strategy, AnthropicCacheStrategy)

    def test_c13_03_a_learned_rejection_still_counts(self) -> None:
        REJECTED_CACHE_PARAMS.record("claude-sonnet-5", "https://proxy.example.com")
        assert cache_params_allowed("claude-sonnet-5", "https://proxy.example.com") is False


class TestHealthCheck:
    async def test_c13_03_multimodal_on_an_endpoint_without_image_input_is_refused_without_a_call(self) -> None:
        from app.api.routes.health import perform_llm_health_check

        config = {
            "provider": "anthropic",
            "isMultimodal": True,
            "configuration": {"model": "deepseek-chat", "endpoint": DEEPSEEK},
        }
        with patch("app.api.routes.health.get_generator_model") as build:
            response = await perform_llm_health_check(config, MagicMock())
        build.assert_not_called()
        assert response.status_code == 400
        body = json.loads(response.body)
        assert body["message"] == "This model's endpoint does not accept image input."
        assert body["details"]["hint"] == "Uncheck Multimodal for this model, or choose a vision model."
