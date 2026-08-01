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


class ReasoningEffort(str, Enum):
    """Platform-normalized reasoning effort levels, forwarded to LangChain's
    standard ``reasoning_effort`` constructor parameter (langchain-core>=1.5.2)
    for reasoning-capable models. ``NONE`` actively disables reasoning on
    models that support it; absence of this field (``None``) means "no
    explicit choice" — the LLM factory applies ``DEFAULT_REASONING_EFFORT``
    for reasoning-capable models — and must NOT be translated into the string
    ``"none"``.
    """

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    MAX = "max"


REASONING_EFFORT_VALUES = frozenset(item.value for item in ReasoningEffort)

# Applied by the LLM factory (`_reasoning_effort_kwargs`) whenever a
# reasoning-capable model has no explicit effort — from the user's selection,
# an agent's `defaultReasoningEffort`, or any other caller. Reasoning-capable
# models should reason thoroughly by default rather than falling back to
# whatever a given provider's own default happens to be (which varies and is
# often a lower/cheaper tier than users expect).
DEFAULT_REASONING_EFFORT = ReasoningEffort.HIGH.value


def validate_reasoning_effort(value: str | None) -> str | None:
    """Shared Pydantic/manual validator for the platform ``reasoningEffort`` field.

    ``None`` (absent) is always valid — "let the provider/model decide". Any
    non-``None`` value must be one of ``REASONING_EFFORT_VALUES``.
    """
    if value is not None and value not in REASONING_EFFORT_VALUES:
        raise ValueError(
            f"Invalid reasoningEffort '{value}'. Must be one of: "
            f"{', '.join(sorted(REASONING_EFFORT_VALUES))}."
        )
    return value
