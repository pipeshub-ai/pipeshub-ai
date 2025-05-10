from typing import Any, Dict, Optional, Union

from langchain_cohere import CohereEmbeddings
from langchain_community.embeddings import (
    HuggingFaceEmbeddings,
    SentenceTransformerEmbeddings,
)
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_openai.embeddings import AzureOpenAIEmbeddings, OpenAIEmbeddings
from pydantic import BaseModel, Field


class BaseEmbeddingConfig(BaseModel):
    """Base config for all embedding providers"""

    model: str
    api_key: Optional[str] = None
    dimensions: Optional[int] = None

class AzureEmbeddingConfig(BaseEmbeddingConfig):
    azure_endpoint: str
    azure_api_version: str


class OpenAIEmbeddingConfig(BaseEmbeddingConfig):
    organization_id: Optional[str] = None

class HuggingFaceEmbeddingConfig(BaseEmbeddingConfig):
    """Hugging Face embedding models"""
    model_kwargs: Optional[Dict[str, Any]] = Field(default_factory=dict)
    encode_kwargs: Optional[Dict[str, Any]] = Field(default_factory=dict)

class SentenceTransformersEmbeddingConfig(BaseEmbeddingConfig):
    """Sentence Transformers embedding models"""
    cache_folder: Optional[str] = None
    encode_kwargs: Optional[Dict[str, Any]] = Field(default_factory=dict)

class GeminiEmbeddingConfig(BaseEmbeddingConfig):
    """Google Gemini embedding models"""
    task_type: Optional[str] = None
    title: Optional[str] = None
    google_api_endpoint: Optional[str] = None

class CohereEmbeddingConfig(BaseEmbeddingConfig):
    """Cohere embedding models"""

class EmbeddingFactory:
    """Factory for creating LangChain-compatible embedding models"""

    @staticmethod
    def create_embedding_model(config: Union[AzureEmbeddingConfig, OpenAIEmbeddingConfig,
                                            HuggingFaceEmbeddingConfig, SentenceTransformersEmbeddingConfig,
                                            GeminiEmbeddingConfig, CohereEmbeddingConfig]):
        if isinstance(config, AzureEmbeddingConfig):
            return AzureOpenAIEmbeddings(
                model=config.model,
                api_key=config.api_key,
                api_version=config.azure_api_version,
                azure_endpoint=config.azure_endpoint,
            )

        elif isinstance(config, OpenAIEmbeddingConfig):
            return OpenAIEmbeddings(
                model=config.model,
                api_key=config.api_key,
                organization=config.organization_id
            )

        elif isinstance(config, HuggingFaceEmbeddingConfig):
            model_kwargs = config.model_kwargs.copy()
            # Hugging Face embedding models typically don't use API keys in the same way
            # but we include it in case it's needed for private models
            if config.api_key:
                model_kwargs["api_key"] = config.api_key

            # Set default encoding parameters
            encode_kwargs = config.encode_kwargs.copy()
            if "normalize_embeddings" not in encode_kwargs:
                encode_kwargs["normalize_embeddings"] = True

            return HuggingFaceEmbeddings(
                model_name=config.model,
                model_kwargs=model_kwargs,
                encode_kwargs=encode_kwargs
            )

        elif isinstance(config, SentenceTransformersEmbeddingConfig):
            encode_kwargs = config.encode_kwargs.copy()

            return SentenceTransformerEmbeddings(
                model_name=config.model,
                cache_folder=config.cache_folder,
                encode_kwargs=encode_kwargs
            )

        elif isinstance(config, CohereEmbeddingConfig):
            return CohereEmbeddings(
                model=config.model,
                cohere_api_key=config.api_key,
            )

        elif isinstance(config, GeminiEmbeddingConfig):

            # Add "models/" prefix if it's missing
            model_name = config.model
            if not model_name.startswith("models/"):
                model_name = f"models/{model_name}"
            return GoogleGenerativeAIEmbeddings(
                model=model_name,  # Now properly formatted as models/text-embedding-004
                google_api_key=config.api_key,
            )

        raise ValueError(f"Unsupported embedding config type: {type(config)}")
