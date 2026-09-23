"""Which reranker each stored model config builds."""

from __future__ import annotations

import pytest

from app.config.ai_models.providers import ai_model_registry
from app.config.constants.ai_models import (
    COHERE_RERANK_URL,
    DEFAULT_RERANKER_MODEL,
    JINA_RERANK_URL,
    VOYAGE_RERANK_URL,
)
from app.modules.reranker.factory import create_reranker
from app.modules.reranker.wire_formats import CohereWireFormat, VoyageWireFormat
from app.utils.aimodels import RerankerProvider


def _config(provider: str, **configuration) -> dict:
    return {"provider": provider, "configuration": configuration}


class TestLocalModelServer:
    def test_default_uses_the_bundled_model_whatever_is_configured(self, monkeypatch) -> None:
        monkeypatch.setenv("EMBEDDING_SERVER_URL", "http://models:8002")
        reranker = create_reranker(_config("defaultReranker", model="something-else"))
        assert reranker.model_name == DEFAULT_RERANKER_MODEL
        assert reranker._url == "http://models:8002/v1/rerank"

    @pytest.mark.parametrize("provider", ["sentenceTransformers", "huggingFace"])
    def test_named_local_models_run_on_the_model_server(self, provider, monkeypatch) -> None:
        monkeypatch.setenv("EMBEDDING_SERVER_URL", "http://models:8002/v1")
        reranker = create_reranker(_config(provider, model="BAAI/bge-reranker-base, other"))
        assert reranker.model_name == "BAAI/bge-reranker-base"
        assert reranker._url == "http://models:8002/v1/rerank"

    def test_trust_remote_code_is_forwarded(self) -> None:
        reranker = create_reranker(_config("huggingFace", model="m", trustRemoteCode=True))
        body = reranker._wire_format.request_body("m", "q", ["d"], None)
        assert body["trust_remote_code"] is True


class TestHostedApis:
    @pytest.mark.parametrize(("provider", "url", "wire"), [
        ("cohere", COHERE_RERANK_URL, CohereWireFormat),
        ("jinaAI", JINA_RERANK_URL, CohereWireFormat),
        ("voyage", VOYAGE_RERANK_URL, VoyageWireFormat),
    ])
    def test_vendor_endpoints(self, provider, url, wire) -> None:
        reranker = create_reranker(_config(provider, model="m", apiKey="k"))
        assert reranker._url == url
        assert isinstance(reranker._wire_format, wire)
        assert reranker._headers == {"Authorization": "Bearer k"}

    @pytest.mark.parametrize("provider", ["openAICompatible", "litellmProxy"])
    def test_self_hosted_gateways_use_their_endpoint(self, provider) -> None:
        reranker = create_reranker(_config(provider, model="m", endpoint="http://gw:4000/v1/"))
        assert reranker._url == "http://gw:4000/v1/rerank"
        assert reranker._headers == {}


class TestRejectedConfigs:
    def test_unknown_provider(self) -> None:
        with pytest.raises(ValueError, match="Unsupported reranker provider"):
            create_reranker(_config("anthropic", model="m"))

    def test_missing_model(self) -> None:
        with pytest.raises(ValueError, match="no model name"):
            create_reranker(_config("cohere", apiKey="k"))

    def test_missing_endpoint(self) -> None:
        with pytest.raises(ValueError, match="no endpoint"):
            create_reranker(_config("openAICompatible", model="m"))


def test_every_registry_reranking_provider_can_be_built() -> None:
    """The registry and the factory must agree, or the UI offers a provider that fails on save."""
    registry_ids = {p["providerId"] for p in ai_model_registry.filter_by_capability("reranking")}
    assert registry_ids == {p.value for p in RerankerProvider}
