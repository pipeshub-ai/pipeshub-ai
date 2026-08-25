"""Maps an ``EmbeddingProvider`` value to its ``IMultimodalEmbeddingProvider``.

Centralising provider selection here (rather than an if/elif chain inside
``VectorStore``) means adding a new multimodal-capable provider only touches
this file plus its own ``<provider>_provider.py`` — ``VectorStore`` itself
never needs to change (Open/Closed Principle).
"""

from collections.abc import Callable

from app.services.embeddings.multimodal.bedrock_provider import (
    BedrockMultimodalProvider,
)
from app.services.embeddings.multimodal.cohere_provider import CohereMultimodalProvider
from app.services.embeddings.multimodal.config import MultimodalProviderConfig
from app.services.embeddings.multimodal.gemini_provider import GeminiMultimodalProvider
from app.services.embeddings.multimodal.interface import IMultimodalEmbeddingProvider
from app.services.embeddings.multimodal.jina_provider import JinaMultimodalProvider
from app.services.embeddings.multimodal.ollama_provider import OllamaMultimodalProvider
from app.services.embeddings.multimodal.openai_compat_provider import (
    OpenAICompatMultimodalProvider,
)
from app.services.embeddings.multimodal.voyage_provider import VoyageMultimodalProvider
from app.utils.aimodels import EmbeddingProvider

# Providers that speak the OpenAI-compatible /v1/embeddings shape. Both are
# routed through the same provider class, only the label used for logging
# differs.
_OPENAI_COMPAT_STYLE_PROVIDERS = {
    EmbeddingProvider.OPENAI_COMPATIBLE.value,
    EmbeddingProvider.LM_STUDIO.value,
}


def _build_openai_compat(provider_label: str) -> Callable[
    [MultimodalProviderConfig], IMultimodalEmbeddingProvider
]:
    return lambda config: OpenAICompatMultimodalProvider(
        base_url=config.base_url,
        api_key=config.api_key,
        model_name=config.model_name,
        provider_label=provider_label,
        normalize_fn=config.normalize_fn,
        logger=config.logger,
    )


# Registry of provider builders, keyed by EmbeddingProvider value. Adding a new
# multimodal-capable provider means adding one entry here plus its own
# ``<provider>_provider.py`` — no other branch of this factory needs to change.
_PROVIDER_BUILDERS: dict[str, Callable[[MultimodalProviderConfig], IMultimodalEmbeddingProvider]] = {
    EmbeddingProvider.COHERE.value: lambda config: CohereMultimodalProvider(
        api_key=config.api_key,
        model_name=config.model_name,
        logger=config.logger,
    ),
    EmbeddingProvider.VOYAGE.value: lambda config: VoyageMultimodalProvider(
        dense_embeddings=config.dense_embeddings,
        logger=config.logger,
    ),
    EmbeddingProvider.AWS_BEDROCK.value: lambda config: BedrockMultimodalProvider(
        model_name=config.model_name,
        region_name=config.region_name,
        aws_access_key_id=config.aws_access_key_id,
        aws_secret_access_key=config.aws_secret_access_key,
        embedding_size=config.embedding_size,
        normalize_fn=config.normalize_fn,
        logger=config.logger,
    ),
    EmbeddingProvider.JINA_AI.value: lambda config: JinaMultimodalProvider(
        api_key=config.api_key,
        model_name=config.model_name,
        normalize_fn=config.normalize_fn,
        logger=config.logger,
    ),
    EmbeddingProvider.GEMINI.value: lambda config: GeminiMultimodalProvider(
        api_key=config.api_key,
        model_name=config.model_name,
        normalize_fn=config.normalize_fn,
        logger=config.logger,
    ),
    EmbeddingProvider.OLLAMA.value: lambda config: OllamaMultimodalProvider(
        base_url=config.base_url,
        model_name=config.model_name,
        logger=config.logger,
    ),
    **{
        provider_label: _build_openai_compat(provider_label)
        for provider_label in _OPENAI_COMPAT_STYLE_PROVIDERS
    },
}


class MultimodalEmbeddingFactory:
    """Instantiates the ``IMultimodalEmbeddingProvider`` for a config's provider."""

    @staticmethod
    def create(config: MultimodalProviderConfig) -> IMultimodalEmbeddingProvider | None:
        builder = _PROVIDER_BUILDERS.get(config.provider)
        return builder(config) if builder else None
