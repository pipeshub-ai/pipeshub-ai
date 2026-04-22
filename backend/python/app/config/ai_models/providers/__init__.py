"""Import all provider modules so their decorators run, then register with the global registry."""

from app.config.ai_models.registry import ai_model_registry

from .anthropic import AnthropicProvider
from .azure import AzureAIProvider, AzureOpenAIProvider
from .bedrock import BedrockProvider
from .cohere import CohereProvider
from .embedding_only import (
    DefaultEmbeddingProvider,
    HuggingFaceProvider,
    JinaAIProvider,
    SentenceTransformersProvider,
    VoyageProvider,
)
from .gemini import GeminiProvider
from .llm_only import FireworksProvider, GroqProvider, MiniMaxProvider, XAIProvider
from .mistral import MistralProvider
from .ollama import OllamaProvider
from .openai import OpenAIProvider
from .openai_compatible import OpenAICompatibleProvider
from .together import TogetherProvider
from .whisper import WhisperProvider

ALL_PROVIDER_CLASSES: list[type] = [
    OpenAIProvider,
    GeminiProvider,
    AnthropicProvider,
    OpenAICompatibleProvider,
    AzureAIProvider,
    AzureOpenAIProvider,
    CohereProvider,
    BedrockProvider,
    OllamaProvider,
    TogetherProvider,
    XAIProvider,
    GroqProvider,
    MiniMaxProvider,
    FireworksProvider,
    MistralProvider,
    DefaultEmbeddingProvider,
    SentenceTransformersProvider,
    JinaAIProvider,
    VoyageProvider,
    HuggingFaceProvider,
    WhisperProvider,
]


def register_all_providers() -> None:
    """Register every provider class with the module-level registry."""
    for cls in ALL_PROVIDER_CLASSES:
        ai_model_registry.register(cls)


register_all_providers()
