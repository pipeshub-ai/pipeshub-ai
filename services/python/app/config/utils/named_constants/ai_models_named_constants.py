from enum import Enum

AZURE_EMBEDDING_API_VERSION = "2024-02-01"
DEFAULT_EMBEDDING_MODEL = "BAAI/bge-large-en-v1.5"

class LLMProvider(Enum):
    AZURE_OPENAI_PROVIDER = "azureOpenAI"
    OPENAI_PROVIDER = "openAI"
    GEMINI_PROVIDER = "gemini"
    VERTEX_AI_PROVIDER = "vertexAI"
    ANTHROPIC_PROVIDER = "anthropic"
    AWS_BEDROCK_PROVIDER = "bedrock"
    OLLAMA_PROVIDER = "ollama"

class OCRProvider(Enum):
    AZURE_PROVIDER = "azureDI"
    OCRMYPDF_PROVIDER = "ocrmypdf"

class EmbeddingProvider(Enum):
    AZURE_OPENAI_PROVIDER = "azureOpenAI"
    OPENAI_PROVIDER = "openAI"
    HUGGING_FACE_PROVIDER = "huggingFace"
    SENTENCE_TRANSFOMERS = "sentenceTransformers"

class AzureOpenAILLM(Enum):
    AZURE_OPENAI_VERSION = "2023-07-01-preview"

class AzureDocIntelligenceModel(Enum):
    PREBUILT_DOCUMENT = "prebuilt-document"
