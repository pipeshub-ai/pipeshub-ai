from enum import Enum

AZURE_EMBEDDING_API_VERSION = "2024-02-01"
DEFAULT_EMBEDDING_MODEL = "BAAI/bge-large-en-v1.5"
EMBEDDING_SERVER_PORT = 8002
DEFAULT_EMBEDDING_SERVER_URL = f"http://localhost:{EMBEDDING_SERVER_PORT}"
EMBEDDING_SERVER_MAX_RETRIES = 5
EMBEDDING_SERVER_REQUEST_TIMEOUT_SECONDS = 600.0

class OCRProvider(Enum):
    AZURE_DI = "azureDI"
    VLM_OCR = "vlmOCR"

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

class AzureOpenAILLM(Enum):
    AZURE_OPENAI_VERSION = "2025-04-01-preview"

class AzureDocIntelligenceModel(Enum):
    PREBUILT_DOCUMENT = "prebuilt-document"
