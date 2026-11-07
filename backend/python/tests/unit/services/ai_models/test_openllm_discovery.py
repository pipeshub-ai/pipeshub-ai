"""OpenLLM model discovery: chat cards only, capability flags mapped onto the discovered model."""

from __future__ import annotations

import httpx
import pytest

import app.config.ai_models.providers  # noqa: F401
from app.config.constants.ai_models import OPENLLM_KEY_REQUIRED_MESSAGE
from app.services.ai_models.discovery.http import DiscoveryHttp
from app.services.ai_models.discovery.registry import discovery_registry
from app.services.ai_models.discovery.service import ModelDiscoveryService
from app.services.ai_models.discovery.types import DiscoveryErrorCode, DiscoveryRequest

_LOCAL = "http://host.docker.internal:8787/v1"

_CARDS = {
    "object": "list",
    "data": [
        {
            "id": "claude_code/claude-sonnet-5",
            "object": "model",
            "display_name": "Claude Sonnet 5",
            "capabilities": ["chat", "vision", "tools", "reasoning", "streaming"],
            "context_window": 200000,
            "max_input_tokens": 180000,
            "max_output_tokens": 64000,
        },
        {
            "id": "lite",
            "object": "model",
            "capabilities": ["chat", "streaming"],
            "max_input_tokens": 128000,
        },
        {
            "id": "openai/text-embedding-3-small",
            "object": "model",
            "capabilities": ["embedding"],
        },
        {
            "id": "openai/gpt-image-2",
            "object": "model",
            "capabilities": ["image_generation"],
        },
        {"id": "legacy/no-capability-list", "object": "model"},
        {"id": "", "object": "model", "capabilities": ["chat"]},
    ],
}


def _service(handler) -> ModelDiscoveryService:
    return ModelDiscoveryService(
        http=DiscoveryHttp(transport=httpx.MockTransport(handler))
    )


def test_openllm_has_a_list_strategy_that_needs_only_an_endpoint() -> None:
    strategy = discovery_registry.get("openLLM")
    assert strategy is not None
    assert discovery_registry.describe("openLLM") == {
        "mode": "list",
        "requiredFields": ["endpoint"],
    }


@pytest.mark.asyncio
async def test_only_chat_cards_are_listed_with_their_capabilities_mapped() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_CARDS)

    result = await _service(handler).discover(
        DiscoveryRequest(
            provider="openLLM",
            capability="text_generation",
            configuration={"endpoint": _LOCAL},
        )
    )
    assert str(seen[0].url) == f"{_LOCAL}/models"
    by_id = {m.id: m for m in result.models}
    assert set(by_id) == {
        "claude_code/claude-sonnet-5",
        "lite",
        "legacy/no-capability-list",
    }

    sonnet = by_id["claude_code/claude-sonnet-5"]
    assert sonnet.display_name == "Claude Sonnet 5"
    assert sonnet.capabilities == ["llm"]
    assert sonnet.context_length == 200000
    assert sonnet.is_multimodal is True
    assert sonnet.is_reasoning is True
    assert sonnet.supports_tools is True

    lite = by_id["lite"]
    assert lite.display_name == "lite"
    assert lite.context_length == 128000
    assert lite.is_multimodal is False
    assert lite.is_reasoning is False
    assert lite.supports_tools is False


@pytest.mark.asyncio
async def test_a_card_without_a_capability_list_is_kept_and_its_flags_stay_unknown() -> (
    None
):
    result = await _service(lambda request: httpx.Response(200, json=_CARDS)).discover(
        DiscoveryRequest(provider="openLLM", configuration={"endpoint": _LOCAL})
    )
    legacy = {m.id: m for m in result.models}["legacy/no-capability-list"]
    assert legacy.capabilities == ["llm"]
    assert legacy.is_multimodal is None
    assert legacy.is_reasoning is None
    assert legacy.supports_tools is None
    assert legacy.context_length is None


@pytest.mark.asyncio
async def test_local_daemon_is_called_with_the_placeholder_bearer_when_no_key_is_set() -> (
    None
):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization"))
        return httpx.Response(200, json=_CARDS)

    await _service(handler).discover(
        DiscoveryRequest(provider="openLLM", configuration={"endpoint": _LOCAL})
    )
    assert seen == ["Bearer openllm"]


@pytest.mark.asyncio
async def test_a_cloud_key_is_sent_as_the_bearer() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), request.headers.get("authorization")))
        return httpx.Response(200, json=_CARDS)

    await _service(handler).discover(
        DiscoveryRequest(
            provider="openLLM",
            configuration={
                "endpoint": "https://www.openllm.sh/v1",
                "apiKey": "sk-llm-abc",
            },
        )
    )
    assert seen == [("https://www.openllm.sh/v1/models", "Bearer sk-llm-abc")]


@pytest.mark.asyncio
async def test_an_endpoint_without_the_v1_suffix_still_lists_models() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=_CARDS)

    await _service(handler).discover(
        DiscoveryRequest(
            provider="openLLM", configuration={"endpoint": "http://127.0.0.1:8787"}
        )
    )
    assert seen == ["http://127.0.0.1:8787/v1/models"]


@pytest.mark.asyncio
async def test_a_rejected_key_is_an_auth_error() -> None:
    result = await _service(lambda request: httpx.Response(401, json={})).discover(
        DiscoveryRequest(
            provider="openLLM",
            configuration={
                "endpoint": "https://www.openllm.sh/v1",
                "apiKey": "sk-llm-bad",
            },
        )
    )
    assert result.error_code == DiscoveryErrorCode.AUTH_ERROR
    assert "sk-llm-bad" not in (result.message or "")


@pytest.mark.asyncio
async def test_a_missing_endpoint_asks_for_one() -> None:
    result = await _service(lambda request: httpx.Response(200, json=_CARDS)).discover(
        DiscoveryRequest(provider="openLLM", configuration={})
    )
    assert result.error_code == DiscoveryErrorCode.INVALID_RESPONSE
    assert "endpoint" in (result.message or "")


@pytest.mark.asyncio
async def test_a_rejected_hosted_endpoint_without_a_key_asks_for_one() -> None:
    result = await _service(lambda request: httpx.Response(401, json={})).discover(
        DiscoveryRequest(
            provider="openLLM", configuration={"endpoint": "https://www.openllm.sh/v1"}
        )
    )
    assert result.error_code == DiscoveryErrorCode.AUTH_ERROR
    assert result.message == OPENLLM_KEY_REQUIRED_MESSAGE


@pytest.mark.asyncio
async def test_context_window_falls_back_when_it_is_not_a_positive_integer() -> None:
    cards = {
        "data": [
            {
                "id": "a",
                "capabilities": ["chat"],
                "context_window": 0,
                "max_input_tokens": 1000,
            },
            {"id": "b", "capabilities": ["chat"], "context_window": True},
            {"id": "c", "capabilities": ["chat"], "context_window": "9"},
        ]
    }
    result = await _service(lambda request: httpx.Response(200, json=cards)).discover(
        DiscoveryRequest(provider="openLLM", configuration={"endpoint": _LOCAL})
    )
    by_id = {m.id: m.context_length for m in result.models}
    assert by_id == {"a": 1000, "b": None, "c": None}
