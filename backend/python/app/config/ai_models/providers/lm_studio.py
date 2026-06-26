"""LM Studio provider registration.

LM Studio is a local desktop application that runs large language models via an
OpenAI-compatible API server (default http://localhost:1234/v1).  API keys are
not validated by LM Studio; any non-empty string is accepted.
"""

from app.config.ai_models.registry import AIModelProviderBuilder
from app.config.ai_models.types import AIModelField, ModelCapability

from .common_fields import (
    API_KEY_OPTIONAL,
    EMBEDDING_COMMON_TAIL,
    LLM_COMMON_TAIL,
    model_field,
)

_LM_STUDIO_ENDPOINT = AIModelField(
    name="endpoint",
    display_name="Endpoint URL",
    field_type="URL",
    required=True,
    default_value="http://host.docker.internal:1234/v1",
    placeholder="e.g. http://localhost:1234/v1",
)


@AIModelProviderBuilder("LM Studio", "lmStudio") \
    .with_description("Run local models via LM Studio's OpenAI-compatible server") \
    .with_capabilities([ModelCapability.TEXT_GENERATION, ModelCapability.EMBEDDING]) \
    .with_icon("/icons/ai-models/lm-studio.svg") \
    .with_color("#8B5CF6") \
    .add_field(_LM_STUDIO_ENDPOINT, ModelCapability.TEXT_GENERATION) \
    .add_field(API_KEY_OPTIONAL, ModelCapability.TEXT_GENERATION) \
    .add_field(model_field("e.g. lmstudio-community/Meta-Llama-3.1-8B-Instruct-GGUF"), ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[0], ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[1], ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[2], ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[3], ModelCapability.TEXT_GENERATION) \
    .add_field(_LM_STUDIO_ENDPOINT, ModelCapability.EMBEDDING) \
    .add_field(API_KEY_OPTIONAL, ModelCapability.EMBEDDING) \
    .add_field(model_field("e.g. nomic-ai/nomic-embed-text-v1.5-GGUF"), ModelCapability.EMBEDDING) \
    .add_field(EMBEDDING_COMMON_TAIL[0], ModelCapability.EMBEDDING) \
    .add_field(EMBEDDING_COMMON_TAIL[1], ModelCapability.EMBEDDING) \
    .add_field(EMBEDDING_COMMON_TAIL[2], ModelCapability.EMBEDDING) \
    .build_decorator()
class LMStudioProvider:
    pass
