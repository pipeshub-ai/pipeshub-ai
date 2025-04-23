from enum import Enum


class LLMProvider(Enum):
    AZURE_OPENAI_PROVIDER = "azureOpenAI"
    OPENAI_PROVIDER = "openAI"
    GEMINI_PROVIDER = "gemini"
    VERTEX_AI_PROVIDER = "vertexAI"
    ANTHROPIC_PROVIDER = "anthropic"
    AWS_BEDROCK_PROVIDER = "bedrock"


class OCRProvider(Enum):
    AZURE_PROVIDER = "azureDI"
    OCRMYPDF_PROVIDER = "ocrmypdf"


class EmbeddingProvider(Enum):
    AZURE_OPENAI_PROVIDER = "azureOpenAI"
    OPENAI_PROVIDER = "openAI"


class EmbeddingModel(Enum):
    DEFAULT_EMBEDDING_MODEL = "BAAI/bge-large-en-v1.5"
    DEFAULT_EMBEDDING_SIZE = 1024
    TE3_SMALL = "text-embedding-3-small"
    TE3_LARGE = "text-embedding-3-large"
    AZURE_EMBEDDING_VERSION = "2024-02-01"


class AzureOpenAILLM(Enum):
    AZURE_OPENAI_VERSION = "2023-07-01-preview"


class AzureDocIntelligenceModel(Enum):
    PREBUILT_DOCUMENT = "prebuilt-document"
