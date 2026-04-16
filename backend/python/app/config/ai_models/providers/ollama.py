"""Ollama provider registration."""

from app.config.ai_models.registry import AIModelProviderBuilder
from app.config.ai_models.types import AIModelField, ModelCapability

from .common_fields import (
    API_KEY_OPTIONAL,
    EMBEDDING_COMMON_TAIL,
    LLM_COMMON_TAIL,
    model_field,
)

_OLLAMA_ENDPOINT = AIModelField(
    name="endpoint",
    display_name="Endpoint URL",
    field_type="URL",
    required=False,
    default_value="http://host.docker.internal:11434",
    placeholder="e.g. http://localhost:11434",
)


@AIModelProviderBuilder("Ollama", "ollama") \
    .with_description("Local open-source models") \
    .with_capabilities([ModelCapability.TEXT_GENERATION, ModelCapability.EMBEDDING]) \
    .with_icon("/assets/icons/ai-models/ollama.svg") \
    .with_color("#4A90E2") \
    .add_field(model_field("e.g., gemma4:latest, hf.co/unsloth/gpt-oss-20b-GGUF:F16"), ModelCapability.TEXT_GENERATION) \
    .add_field(API_KEY_OPTIONAL, ModelCapability.TEXT_GENERATION) \
    .add_field(_OLLAMA_ENDPOINT, ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[0], ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[1], ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[2], ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[3], ModelCapability.TEXT_GENERATION) \
    .add_field(model_field("e.g., mxbai-embed-large"), ModelCapability.EMBEDDING) \
    .add_field(_OLLAMA_ENDPOINT, ModelCapability.EMBEDDING) \
    .add_field(EMBEDDING_COMMON_TAIL[0], ModelCapability.EMBEDDING) \
    .add_field(EMBEDDING_COMMON_TAIL[1], ModelCapability.EMBEDDING) \
    .build_decorator()
class OllamaProvider:
    pass
