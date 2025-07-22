from enum import Enum

AZURE_EMBEDDING_API_VERSION = "2024-02-01"
DEFAULT_EMBEDDING_MODEL = "BAAI/bge-large-en-v1.5"

class LLMProvider(Enum):
    AZURE_OPENAI = "azureOpenAI"
    OPENAI = "openAI"
    GEMINI = "gemini"
    VERTEX_AI = "vertexAI"
    ANTHROPIC = "anthropic"
    AWS_BEDROCK = "bedrock"
    OLLAMA = "ollama"
    OPENAI_COMPATIBLE = "openAICompatible"
    COHERE = "cohere"
    FIREWORKS = "fireworks"
    GROQ = "groq"
    MISTRAL = "mistral"
    TOGETHER = "together"
    XAI = "xai"


class OCRProvider(Enum):
    AZURE_DI = "azureDI"
    OCRMYPDF = "ocrmypdf"

class EmbeddingProvider(Enum):
    AZURE_OPENAI = "azureOpenAI"
    OPENAI = "openAI"
    OPENAI_COMPATIBLE = "openAICompatible"
    HUGGING_FACE = "huggingFace"
    SENTENCE_TRANSFOMERS = "sentenceTransformers"
    GEMINI = "gemini"
    COHERE = "cohere"
    OLLAMA = "ollama"
    DEFAULT = "default"

class AzureOpenAILLM(Enum):
    AZURE_OPENAI_VERSION = "2023-07-01-preview"

class AzureDocIntelligenceModel(Enum):
    PREBUILT_DOCUMENT = "prebuilt-document"
