"""Discovery strategies, classification, and the admin route."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import app.config.ai_models.providers  # noqa: F401
import app.services.ai_models.discovery.catalog as catalog_module
from app.config.ai_models.registry import ai_model_registry
from app.services.ai_models.discovery.cache import DiscoveryCache
from app.services.ai_models.discovery.catalog import load_catalog, lookup_catalog
from app.services.ai_models.discovery.classifier import classify
from app.services.ai_models.discovery.http import DiscoveryHttp
from app.services.ai_models.discovery.registry import discovery_registry
from app.services.ai_models.discovery.service import ModelDiscoveryService
from app.services.ai_models.discovery.types import DiscoveryRequest, RawModel

_FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "ai_models" / "discovery"


def _load(name: str):
    return json.loads((_FIXTURES / name).read_text())


def _service(handler) -> ModelDiscoveryService:
    return ModelDiscoveryService(http=DiscoveryHttp(transport=httpx.MockTransport(handler)))


@pytest.fixture(autouse=True)
def _fixture_catalog(monkeypatch):
    monkeypatch.setattr(catalog_module, "_CATALOG_PATH", _FIXTURES / "model_catalog.json")
    load_catalog.cache_clear()
    yield
    load_catalog.cache_clear()


def test_catalog_prefers_provider_prefix_over_bare_id():
    prefixed = lookup_catalog("openAI", "gpt-5.6-luna")
    bare = lookup_catalog("unknown-provider", "gpt-5.6-luna")
    assert prefixed["max_input_tokens"] == 922000
    assert bare["max_input_tokens"] == 1


def test_every_registered_provider_has_a_discovery_strategy():
    missing = [
        provider["providerId"]
        for provider in ai_model_registry.list_providers()
        if discovery_registry.get(provider["providerId"]) is None
    ]
    assert missing == []


@pytest.mark.asyncio
async def test_openai_classifies_ids_and_drops_moderation_and_realtime():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/models"
        return httpx.Response(200, json=_load("openai_models.json"))

    result = await _service(handler).discover(DiscoveryRequest(
        provider="openAI", configuration={"apiKey": "sk-test"}
    ))
    by_id = {model.id: model for model in result.models}
    assert by_id["gpt-5.6-luna"].capabilities == ["llm"]
    assert by_id["gpt-5.6-luna"].context_length == 922000
    assert by_id["gpt-5.6-luna"].is_multimodal is True
    assert by_id["gpt-5.6-luna"].source == "catalog"
    assert by_id["text-embedding-3-small"].capabilities == ["embedding"]
    assert by_id["whisper-1"].capabilities == ["stt"]
    assert by_id["whisper-1"].source == "heuristic"
    assert by_id["tts-1"].capabilities == ["tts"]
    assert by_id["dall-e-3"].capabilities == ["imageGeneration"]
    assert by_id["custom-ft"].deprecated is True
    assert "omni-moderation-latest" not in by_id
    assert "gpt-4o-realtime-preview" not in by_id
    assert result.error_code is None


@pytest.mark.asyncio
async def test_openai_auth_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "nope"})

    result = await _service(handler).discover(DiscoveryRequest(
        provider="openAI", configuration={"apiKey": "bad"}
    ))
    assert result.error_code.value == "auth_error"
    assert "bad" not in (result.message or "")


@pytest.mark.asyncio
async def test_anthropic_paginates_with_after_id():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        assert request.headers["x-api-key"] == "ant-key"
        assert request.headers["anthropic-version"] == "2023-06-01"
        if "after_id" not in str(request.url):
            return httpx.Response(200, json=_load("anthropic_page1.json"))
        return httpx.Response(200, json=_load("anthropic_page2.json"))

    result = await _service(handler).discover(DiscoveryRequest(
        provider="anthropic", configuration={"apiKey": "ant-key"}
    ))
    assert [model.id for model in result.models] == ["claude-sonnet", "claude-haiku"]
    assert result.models[0].is_multimodal is True
    assert result.models[0].is_reasoning is True
    assert result.models[0].supports_tools is True
    assert result.models[0].source == "provider_api"
    assert any("after_id=claude-sonnet" in url for url in seen)


@pytest.mark.asyncio
async def test_gemini_name_rules_exclude_veo_and_live():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-goog-api-key"] == "gem-key"
        return httpx.Response(200, json=_load("gemini_models.json"))

    result = await _service(handler).discover(DiscoveryRequest(
        provider="gemini", configuration={"apiKey": "gem-key"}
    ))
    by_id = {model.id: model.capabilities for model in result.models}
    assert by_id["gemini-2.5-flash"] == ["llm"]
    assert by_id["text-embedding-004"] == ["embedding"]
    assert by_id["gemini-2.5-flash-tts"] == ["tts"]
    assert by_id["gemini-2.5-flash-image"] == ["imageGeneration"]
    assert "veo-2" not in by_id
    assert "gemini-live" not in by_id


@pytest.mark.asyncio
async def test_openrouter_requests_all_output_modalities():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("output_modalities") == "all"
        return httpx.Response(200, json=_load("openrouter_models.json"))

    result = await _service(handler).discover(DiscoveryRequest(
        provider="openRouter", configuration={"apiKey": "or-key"}
    ))
    by_id = {model.id: model for model in result.models}
    assert by_id["org/embed"].capabilities == ["embedding"]
    assert by_id["org/chat"].is_multimodal is True
    assert by_id["org/chat"].supports_tools is True
    assert by_id["org/chat"].is_reasoning is True


@pytest.mark.asyncio
async def test_jina_strips_prefix_and_drops_rerankers():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_load("jina_models.json"))

    result = await _service(handler).discover(DiscoveryRequest(
        provider="jinaAI", configuration={"apiKey": "jina"}
    ))
    assert [model.id for model in result.models] == ["jina-embeddings-v3"]
    assert result.models[0].capabilities == ["embedding"]


@pytest.mark.asyncio
async def test_litellm_403_falls_back_to_model_ids():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/model/info"):
            return httpx.Response(403, json={"error": "forbidden"})
        return httpx.Response(200, json={"data": [{"id": "fallback-chat"}]})

    result = await _service(handler).discover(DiscoveryRequest(
        provider="litellmProxy",
        configuration={"endpoint": "https://proxy.example/v1", "apiKey": "k"},
    ))
    assert result.models[0].id == "fallback-chat"
    assert any("model info" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_litellm_info_uses_provider_metadata():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_load("litellm_info.json"))

    result = await _service(handler).discover(DiscoveryRequest(
        provider="litellmProxy",
        configuration={"endpoint": "https://proxy.example/v1"},
    ))
    assert result.models[0].capabilities == ["embedding"]
    assert result.models[0].source == "provider_api"
    assert result.models[0].context_length == 8192


@pytest.mark.asyncio
async def test_ollama_show_reads_arch_context_and_is_cached():
    calls = {"show": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/tags"):
            return httpx.Response(200, json=_load("ollama_tags.json"))
        calls["show"] += 1
        return httpx.Response(200, json=_load("ollama_show.json"))

    service = _service(handler)
    config = {"endpoint": "http://ollama.example:11434"}
    first = await service.discover(DiscoveryRequest(provider="ollama", configuration=config))
    assert first.models[0].context_length == 8192
    assert first.models[0].is_multimodal is True
    assert first.models[0].supports_tools is True
    await service.discover(DiscoveryRequest(provider="ollama", configuration=config))
    assert calls["show"] == 1


@pytest.mark.asyncio
async def test_bedrock_lists_inference_profile_ids():
    payload = _load("bedrock.json")
    with patch(
        "app.services.ai_models.discovery.strategies.list_bedrock_pages",
        return_value=(payload["summaries"], payload["profiles"]),
    ):
        result = await ModelDiscoveryService(http=DiscoveryHttp()).discover(DiscoveryRequest(
            provider="bedrock", configuration={"region": "us-east-1"}
        ))
    by_id = {model.id: model for model in result.models}
    assert by_id["amazon.titan-embed-text-v2:0"].capabilities == ["embedding"]
    assert "us.anthropic.claude-sonnet" in by_id
    assert "anthropic.claude-sonnet" not in by_id
    assert by_id["us.anthropic.claude-sonnet"].deprecated is True
    assert by_id["us.anthropic.claude-sonnet"].is_multimodal is True


@pytest.mark.asyncio
async def test_manual_provider_is_not_supported():
    result = await ModelDiscoveryService().discover(DiscoveryRequest(
        provider="azureAI", configuration={}
    ))
    assert result.supported is False
    assert result.error_code.value == "not_supported"


@pytest.mark.asyncio
async def test_static_voyage_needs_no_credentials():
    result = await ModelDiscoveryService().discover(DiscoveryRequest(
        provider="voyage", configuration={}
    ))
    assert result.supported is True
    assert any(model.id == "voyage-3-large" for model in result.models)


def test_missing_catalog_flag_is_not_false():
    model = classify("openAI", RawModel(id="not-in-catalog"))
    assert model is not None
    assert model.capabilities == ["other"]
    assert model.is_multimodal is None
    assert model.supports_tools is None


@pytest.mark.asyncio
async def test_response_too_large_is_invalid_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'{"data":[]}' + b" " * (5 * 1024 * 1024))

    result = await _service(handler).discover(DiscoveryRequest(
        provider="openAI", configuration={"apiKey": "sk"}
    ))
    assert result.error_code.value == "invalid_response"


@pytest.mark.asyncio
async def test_discover_route_is_admin_only():
    from fastapi import HTTPException

    from app.api.middlewares.admin_gate import require_admin_caller
    from app.api.middlewares.auth import deny_service_tokens
    from app.api.routes.ai_models_registry import router

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[deny_service_tokens] = lambda: None

    async def _forbid() -> None:
        raise HTTPException(status_code=403, detail="Only administrators can do this.")

    app.dependency_overrides[require_admin_caller] = _forbid
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        denied = await client.post("/api/v1/ai-models/discover", json={"provider": "voyage"})
    assert denied.status_code == 403

    app.dependency_overrides[require_admin_caller] = lambda: None
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        allowed = await client.post("/api/v1/ai-models/discover", json={"provider": "voyage"})
    assert allowed.status_code == 200
    body = allowed.json()
    assert body["supported"] is True
    assert body["models"]
    assert body["models"][0]["displayName"]


@pytest.mark.asyncio
async def test_registry_response_includes_discovery_block():
    from app.api.routes.ai_models_registry import get_registry

    response = await get_registry(search=None, capability=None)
    providers = json.loads(response.body)["providers"]
    openai = next(item for item in providers if item["providerId"] == "openAI")
    assert openai["discovery"]["mode"] == "list"
    assert "apiKey" in openai["discovery"]["requiredFields"]
    manual = next(item for item in providers if item["providerId"] == "azureAI")
    assert manual["discovery"]["mode"] == "manual"


@pytest.mark.asyncio
async def test_openai_compatible_models_endpoint_adds_v1_and_classifies_media():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path in {"/v1/models/capabilities", "/props"}:
            return httpx.Response(404)
        assert request.url.path == "/v1/models"
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {"id": "llama-3", "object": "model", "owned_by": "local"},
                    {"id": "text-embedding-3-small", "object": "model"},
                    {"id": "gpt-image-1", "object": "model"},
                    {"id": "tts-1", "object": "model"},
                    {"id": "whisper-1", "object": "model"},
                ],
            },
        )

    result = await _service(handler).discover(DiscoveryRequest(
        provider="openAICompatible",
        configuration={"endpoint": "https://models.example"},
    ))
    assert seen[0] == "/v1/models"
    by_id = {model.id: model for model in result.models}
    assert by_id["gpt-image-1"].capabilities == ["imageGeneration"]
    assert by_id["tts-1"].capabilities == ["tts"]
    assert by_id["whisper-1"].capabilities == ["stt"]
    assert by_id["text-embedding-3-small"].capabilities == ["embedding"]
    assert result.error_code is None


def test_cache_key_separates_bedrock_region_and_access_key():
    shared = ("bedrock", "", "secret", "")
    east = DiscoveryCache.key(*shared, region="us-east-1", access_key_id="AKIA")
    west = DiscoveryCache.key(*shared, region="eu-west-1", access_key_id="AKIA")
    other_key = DiscoveryCache.key(*shared, region="us-east-1", access_key_id="AKIOTHER")
    assert len({east, west, other_key}) == 3


@pytest.mark.asyncio
async def test_capability_filter_is_applied_after_the_cache_read():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_load("openai_models.json"))

    service = _service(handler)
    embedding = await service.discover(DiscoveryRequest(
        provider="openAI", capability="embedding", configuration={"apiKey": "sk-test"},
    ))
    chat = await service.discover(DiscoveryRequest(
        provider="openAI", capability="llm", configuration={"apiKey": "sk-test"},
    ))
    assert calls == 1
    embedding_ids = {model.id for model in embedding.models}
    chat_ids = {model.id for model in chat.models}
    assert "text-embedding-3-small" in embedding_ids
    assert "gpt-5.6-luna" not in embedding_ids
    assert "gpt-5.6-luna" in chat_ids
    assert "text-embedding-3-small" not in chat_ids


@pytest.mark.asyncio
async def test_private_endpoint_is_unreachable_not_a_server_error():
    result = await _service(lambda request: httpx.Response(200, json={"data": []})).discover(
        DiscoveryRequest(
            provider="openAICompatible",
            configuration={"endpoint": "http://169.254.169.254"},
        )
    )
    assert result.error_code is not None
    assert result.error_code.value == "unreachable"
    assert result.message


@pytest.mark.asyncio
async def test_bedrock_credential_errors_are_auth_errors():
    from botocore.exceptions import ClientError, NoCredentialsError

    service = ModelDiscoveryService()
    with patch(
        "app.services.ai_models.discovery.strategies.list_bedrock_pages",
        side_effect=NoCredentialsError(),
    ):
        missing = await service.discover(DiscoveryRequest(
            provider="bedrock", configuration={"region": "us-east-1"},
        ))
    assert missing.error_code is not None
    assert missing.error_code.value == "auth_error"

    def rejected(_configuration):
        raise ClientError(
            {"Error": {"Code": "UnrecognizedClientException", "Message": "bad"}},
            "ListFoundationModels",
        )

    with patch(
        "app.services.ai_models.discovery.strategies.list_bedrock_pages",
        side_effect=rejected,
    ):
        denied = await service.discover(DiscoveryRequest(
            provider="bedrock",
            configuration={"region": "us-east-1", "awsAccessKeyId": "AKIA", "awsAccessSecretKey": "secret"},
        ))
    assert denied.error_code is not None
    assert denied.error_code.value == "auth_error"
    assert "UnrecognizedClientException" in (denied.message or "")


@pytest.mark.asyncio
async def test_bedrock_regions_do_not_share_a_cache_entry():
    calls: list[str] = []

    def pages(configuration):
        calls.append(str(configuration.get("region")))
        return (
            [{
                "modelId": "amazon.titan-embed-text-v2:0",
                "outputModalities": ["EMBEDDING"],
                "inputModalities": ["TEXT"],
                "inferenceTypesSupported": ["ON_DEMAND"],
            }],
            [],
        )

    service = ModelDiscoveryService(cache=DiscoveryCache())
    with patch("app.services.ai_models.discovery.strategies.list_bedrock_pages", side_effect=pages):
        config = {"awsAccessKeyId": "AKIA", "awsAccessSecretKey": "secret"}
        await service.discover(DiscoveryRequest(provider="bedrock", configuration={**config, "region": "us-east-1"}))
        await service.discover(DiscoveryRequest(provider="bedrock", configuration={**config, "region": "eu-west-1"}))
        await service.discover(DiscoveryRequest(provider="bedrock", configuration={**config, "region": "us-east-1"}))
    assert calls == ["us-east-1", "eu-west-1"]
