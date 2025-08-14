from enum import Enum

AZURE_EMBEDDING_API_VERSION = "2024-02-01"
DEFAULT_EMBEDDING_MODEL = "BAAI/bge-large-en-v1.5"

class OCRProvider(Enum):
    AZURE_DI = "azureDI"
    OCRMYPDF = "ocrmypdf"

class AzureOpenAILLM(Enum):
    AZURE_OPENAI_VERSION = "2025-04-01-preview"

class AzureDocIntelligenceModel(Enum):
    PREBUILT_DOCUMENT = "prebuilt-document"
