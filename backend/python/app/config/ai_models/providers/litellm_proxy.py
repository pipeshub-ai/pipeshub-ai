"""LiteLLM Proxy provider registration.

LiteLLM Proxy is a self-hosted OpenAI-compatible AI gateway that routes
requests to 100+ upstream LLM providers.  It exposes the full OpenAI API
surface (chat, embeddings, images, TTS, STT) at a user-supplied endpoint.
"""

from app.config.ai_models.registry import AIModelProviderBuilder
from app.config.ai_models.types import AIModelField, ModelCapability

from .common_fields import (
    API_KEY,
    EMBEDDING_COMMON_TAIL,
    FRIENDLY_NAME,
    LLM_COMMON_TAIL,
    model_field,
)
from .openai import OPENAI_TTS_FORMAT, OPENAI_TTS_VOICE

_LITELLM_ENDPOINT = AIModelField(
    name="endpoint",
    display_name="Endpoint URL",
    field_type="URL",
    required=True,
    default_value="http://host.docker.internal:4000",
    placeholder="e.g. http://localhost:4000",
)


@AIModelProviderBuilder("LiteLLM Proxy", "litellmProxy") \
    .with_description("Unified OpenAI-compatible gateway routing to 100+ LLM providers") \
    .with_capabilities([
        ModelCapability.TEXT_GENERATION,
        ModelCapability.EMBEDDING,
        ModelCapability.IMAGE_GENERATION,
        ModelCapability.TTS,
        ModelCapability.STT,
    ]) \
    .with_icon("/icons/ai-models/litellm.svg") \
    .with_color("#0EA5E9") \
    .add_field(_LITELLM_ENDPOINT, ModelCapability.TEXT_GENERATION) \
    .add_field(API_KEY, ModelCapability.TEXT_GENERATION) \
    .add_field(model_field("e.g. gpt-4o, claude-opus-4-5, gemini-2.5-flash"), ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[0], ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[1], ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[2], ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[3], ModelCapability.TEXT_GENERATION) \
    .add_field(_LITELLM_ENDPOINT, ModelCapability.EMBEDDING) \
    .add_field(API_KEY, ModelCapability.EMBEDDING) \
    .add_field(model_field("e.g. text-embedding-3-small, azure-embeddings"), ModelCapability.EMBEDDING) \
    .add_field(EMBEDDING_COMMON_TAIL[0], ModelCapability.EMBEDDING) \
    .add_field(EMBEDDING_COMMON_TAIL[1], ModelCapability.EMBEDDING) \
    .add_field(EMBEDDING_COMMON_TAIL[2], ModelCapability.EMBEDDING) \
    .add_field(_LITELLM_ENDPOINT, ModelCapability.IMAGE_GENERATION) \
    .add_field(API_KEY, ModelCapability.IMAGE_GENERATION) \
    .add_field(model_field("e.g. dall-e-3, gpt-image-1"), ModelCapability.IMAGE_GENERATION) \
    .add_field(FRIENDLY_NAME, ModelCapability.IMAGE_GENERATION) \
    .add_field(_LITELLM_ENDPOINT, ModelCapability.TTS) \
    .add_field(API_KEY, ModelCapability.TTS) \
    .add_field(model_field("e.g. tts-1, tts-1-hd"), ModelCapability.TTS) \
    .add_field(OPENAI_TTS_VOICE, ModelCapability.TTS) \
    .add_field(OPENAI_TTS_FORMAT, ModelCapability.TTS) \
    .add_field(FRIENDLY_NAME, ModelCapability.TTS) \
    .add_field(_LITELLM_ENDPOINT, ModelCapability.STT) \
    .add_field(API_KEY, ModelCapability.STT) \
    .add_field(model_field("e.g. whisper-1"), ModelCapability.STT) \
    .add_field(FRIENDLY_NAME, ModelCapability.STT) \
    .build_decorator()
class LiteLLMProxyProvider:
    pass
