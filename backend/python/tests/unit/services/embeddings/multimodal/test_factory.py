"""Tests for MultimodalEmbeddingFactory provider dispatch."""

from unittest.mock import MagicMock

from app.services.embeddings.multimodal.bedrock_provider import BedrockMultimodalProvider
from app.services.embeddings.multimodal.cohere_provider import CohereMultimodalProvider
from app.services.embeddings.multimodal.config import MultimodalProviderConfig
from app.services.embeddings.multimodal.factory import MultimodalEmbeddingFactory
from app.services.embeddings.multimodal.gemini_provider import GeminiMultimodalProvider
from app.services.embeddings.multimodal.jina_provider import JinaMultimodalProvider
from app.services.embeddings.multimodal.ollama_provider import OllamaMultimodalProvider
from app.services.embeddings.multimodal.openai_compat_provider import (
    OpenAICompatMultimodalProvider,
)
from app.services.embeddings.multimodal.voyage_provider import VoyageMultimodalProvider
from app.utils.aimodels import EmbeddingProvider


class TestMultimodalEmbeddingFactory:
    def test_cohere_provider(self) -> None:
        config = MultimodalProviderConfig(provider=EmbeddingProvider.COHERE.value, api_key="k")
        provider = MultimodalEmbeddingFactory.create(config)
        assert isinstance(provider, CohereMultimodalProvider)

    def test_voyage_provider(self) -> None:
        config = MultimodalProviderConfig(
            provider=EmbeddingProvider.VOYAGE.value, dense_embeddings=MagicMock()
        )
        provider = MultimodalEmbeddingFactory.create(config)
        assert isinstance(provider, VoyageMultimodalProvider)

    def test_bedrock_provider(self) -> None:
        config = MultimodalProviderConfig(provider=EmbeddingProvider.AWS_BEDROCK.value)
        provider = MultimodalEmbeddingFactory.create(config)
        assert isinstance(provider, BedrockMultimodalProvider)

    def test_jina_provider(self) -> None:
        config = MultimodalProviderConfig(provider=EmbeddingProvider.JINA_AI.value, api_key="k")
        provider = MultimodalEmbeddingFactory.create(config)
        assert isinstance(provider, JinaMultimodalProvider)

    def test_gemini_provider(self) -> None:
        config = MultimodalProviderConfig(provider=EmbeddingProvider.GEMINI.value, api_key="k")
        provider = MultimodalEmbeddingFactory.create(config)
        assert isinstance(provider, GeminiMultimodalProvider)

    def test_ollama_provider(self) -> None:
        config = MultimodalProviderConfig(provider=EmbeddingProvider.OLLAMA.value)
        provider = MultimodalEmbeddingFactory.create(config)
        assert isinstance(provider, OllamaMultimodalProvider)

    def test_openai_compatible_provider(self) -> None:
        config = MultimodalProviderConfig(
            provider=EmbeddingProvider.OPENAI_COMPATIBLE.value, base_url="http://x"
        )
        provider = MultimodalEmbeddingFactory.create(config)
        assert isinstance(provider, OpenAICompatMultimodalProvider)
        assert provider.provider_name == EmbeddingProvider.OPENAI_COMPATIBLE.value

    def test_lm_studio_routes_through_openai_compat_provider(self) -> None:
        config = MultimodalProviderConfig(
            provider=EmbeddingProvider.LM_STUDIO.value, base_url="http://localhost:1234"
        )
        provider = MultimodalEmbeddingFactory.create(config)
        assert isinstance(provider, OpenAICompatMultimodalProvider)
        assert provider.provider_name == EmbeddingProvider.LM_STUDIO.value

    def test_unsupported_provider_returns_none(self) -> None:
        config = MultimodalProviderConfig(provider="some-unknown-provider")
        assert MultimodalEmbeddingFactory.create(config) is None

    def test_none_provider_returns_none(self) -> None:
        config = MultimodalProviderConfig(provider=None)
        assert MultimodalEmbeddingFactory.create(config) is None

    def test_normalize_fn_propagated_to_bedrock(self) -> None:
        normalize_fn = MagicMock()
        config = MultimodalProviderConfig(
            provider=EmbeddingProvider.AWS_BEDROCK.value, normalize_fn=normalize_fn
        )
        provider = MultimodalEmbeddingFactory.create(config)
        assert provider._normalize_fn is normalize_fn

    def test_logger_propagated_to_provider(self) -> None:
        logger = MagicMock()
        config = MultimodalProviderConfig(
            provider=EmbeddingProvider.COHERE.value, api_key="k", logger=logger
        )
        provider = MultimodalEmbeddingFactory.create(config)
        assert provider.logger is logger
