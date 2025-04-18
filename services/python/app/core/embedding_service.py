from langchain_openai.embeddings import OpenAIEmbeddings, AzureOpenAIEmbeddings
from pydantic import BaseModel
from typing import Optional

class BaseEmbeddingConfig(BaseModel):
    """Base config for all embedding providers"""
    model: str
    api_key: str

class AzureEmbeddingConfig(BaseEmbeddingConfig):
    azure_endpoint: str
    azure_api_version: str

class OpenAIEmbeddingConfig(BaseEmbeddingConfig):
    organization_id: Optional[str] = None

class EmbeddingFactory:
    """Factory for creating LangChain-compatible embedding models"""

    @staticmethod
    def create_embedding_model(config: BaseEmbeddingConfig):
        if isinstance(config, AzureEmbeddingConfig):
            return AzureOpenAIEmbeddings(
                model=config.model,
                api_key=config.api_key,
                api_version=config.azure_api_version,
                azure_endpoint=config.azure_endpoint
            )
        
        elif isinstance(config, OpenAIEmbeddingConfig):
            return OpenAIEmbeddings(
                model=config.model,
                api_key=config.api_key,
                organization=config.organization_id
            )

        raise ValueError(f"Unsupported embedding config type: {type(config)}")
