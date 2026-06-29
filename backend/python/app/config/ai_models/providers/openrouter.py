"""OpenRouter provider registration.

OpenRouter is an OpenAI-compatible AI gateway giving access to 400+ models
from a single API key. Base URL is https://openrouter.ai/api/v1.
Model slugs use the provider/name format, e.g. ``anthropic/claude-sonnet-4``.
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

_OPENROUTER_TTS_VOICE = AIModelField(
    name="voice",
    display_name="Voice",
    field_type="SELECT",
    required=False,
    default_value="alloy",
    options=[
        {"value": "alloy", "label": "Alloy"},
        {"value": "echo", "label": "Echo"},
        {"value": "fable", "label": "Fable"},
        {"value": "onyx", "label": "Onyx"},
        {"value": "nova", "label": "Nova"},
        {"value": "shimmer", "label": "Shimmer"},
    ],
    description="Default voice for text-to-speech output",
)

_OPENROUTER_TTS_FORMAT = AIModelField(
    name="responseFormat",
    display_name="Audio Format",
    field_type="SELECT",
    required=False,
    default_value="mp3",
    options=[
        {"value": "mp3", "label": "MP3"},
        {"value": "pcm", "label": "PCM"},
    ],
)


@AIModelProviderBuilder("OpenRouter", "openRouter") \
    .with_description("400+ models via a single OpenAI-compatible API gateway") \
    .with_capabilities([
        ModelCapability.TEXT_GENERATION,
        ModelCapability.EMBEDDING,
        ModelCapability.IMAGE_GENERATION,
        ModelCapability.TTS,
        ModelCapability.STT,
    ]) \
    .with_icon("/icons/ai-models/openrouter.svg") \
    .with_color("#6366F1") \
    .popular() \
    .add_field(API_KEY, ModelCapability.TEXT_GENERATION) \
    .add_field(model_field("e.g., anthropic/claude-sonnet-4, openai/gpt-4o"), ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[0], ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[1], ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[2], ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[3], ModelCapability.TEXT_GENERATION) \
    .add_field(API_KEY, ModelCapability.EMBEDDING) \
    .add_field(model_field("e.g., openai/text-embedding-3-small"), ModelCapability.EMBEDDING) \
    .add_field(EMBEDDING_COMMON_TAIL[0], ModelCapability.EMBEDDING) \
    .add_field(EMBEDDING_COMMON_TAIL[1], ModelCapability.EMBEDDING) \
    .add_field(EMBEDDING_COMMON_TAIL[2], ModelCapability.EMBEDDING) \
    .add_field(API_KEY, ModelCapability.IMAGE_GENERATION) \
    .add_field(model_field("e.g., bytedance-seed/seedream-4.5, black-forest-labs/flux.2-pro"), ModelCapability.IMAGE_GENERATION) \
    .add_field(FRIENDLY_NAME, ModelCapability.IMAGE_GENERATION) \
    .add_field(API_KEY, ModelCapability.TTS) \
    .add_field(model_field("e.g., openai/gpt-4o-mini-tts-2025-12-15"), ModelCapability.TTS) \
    .add_field(_OPENROUTER_TTS_VOICE, ModelCapability.TTS) \
    .add_field(_OPENROUTER_TTS_FORMAT, ModelCapability.TTS) \
    .add_field(FRIENDLY_NAME, ModelCapability.TTS) \
    .add_field(API_KEY, ModelCapability.STT) \
    .add_field(model_field("e.g., openai/whisper-1"), ModelCapability.STT) \
    .add_field(FRIENDLY_NAME, ModelCapability.STT) \
    .build_decorator()
class OpenRouterProvider:
    pass
