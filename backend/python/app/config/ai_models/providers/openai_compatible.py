"""OpenAI-compatible provider registration."""

from app.config.ai_models.registry import AIModelProviderBuilder
from app.config.ai_models.types import AIModelField, ModelCapability

from .common_fields import (
    API_KEY,
    API_KEY_OPTIONAL,
    EMBEDDING_COMMON_TAIL,
    FRIENDLY_NAME,
    LLM_COMMON_TAIL,
    model_field,
)

_COMPAT_ENDPOINT = AIModelField(
    name="endpoint",
    display_name="Endpoint URL",
    field_type="URL",
    required=True,
    placeholder="e.g., https://api.together.xyz/v1/",
)

_COMPAT_ENDPOINT_EMB = AIModelField(
    name="endpoint",
    display_name="Endpoint URL",
    field_type="URL",
    required=True,
    placeholder="e.g., https://api.openai.com/v1",
)


_COMPAT_VOICE = AIModelField(
    name="voice",
    display_name="Voice",
    field_type="TEXT",
    required=False,
    placeholder="e.g. alloy",
    description="Voice id the endpoint expects. Leave blank to use the server default.",
)

_COMPAT_FORMAT = AIModelField(
    name="responseFormat",
    display_name="Audio Format",
    field_type="SELECT",
    required=False,
    default_value="mp3",
    options=[
        {"value": "mp3", "label": "MP3"},
        {"value": "opus", "label": "Opus"},
        {"value": "aac", "label": "AAC"},
        {"value": "flac", "label": "FLAC"},
        {"value": "wav", "label": "WAV"},
        {"value": "pcm", "label": "PCM"},
    ],
)


@AIModelProviderBuilder("OpenAI Compatible", "openAICompatible") \
    .with_description("OpenAI-compatible models") \
    .with_capabilities([
        ModelCapability.TEXT_GENERATION,
        ModelCapability.EMBEDDING,
        ModelCapability.IMAGE_GENERATION,
        ModelCapability.TTS,
        ModelCapability.STT,
    ]) \
    .with_icon("/icons/ai-models/openai.svg") \
    .with_color("#0078D4") \
    .add_field(_COMPAT_ENDPOINT, ModelCapability.TEXT_GENERATION) \
    .add_field(API_KEY, ModelCapability.TEXT_GENERATION) \
    .add_field(model_field("e.g. deepseek-ai/DeepSeek-V3"), ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[0], ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[1], ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[2], ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[3], ModelCapability.TEXT_GENERATION) \
    .add_field(_COMPAT_ENDPOINT_EMB, ModelCapability.EMBEDDING) \
    .add_field(API_KEY, ModelCapability.EMBEDDING) \
    .add_field(model_field("e.g., text-embedding-3-small"), ModelCapability.EMBEDDING) \
    .add_field(EMBEDDING_COMMON_TAIL[0], ModelCapability.EMBEDDING) \
    .add_field(EMBEDDING_COMMON_TAIL[1], ModelCapability.EMBEDDING) \
    .add_field(EMBEDDING_COMMON_TAIL[2], ModelCapability.EMBEDDING) \
    .add_field(_COMPAT_ENDPOINT, ModelCapability.IMAGE_GENERATION) \
    .add_field(API_KEY_OPTIONAL, ModelCapability.IMAGE_GENERATION) \
    .add_field(model_field("e.g. gpt-image-1"), ModelCapability.IMAGE_GENERATION) \
    .add_field(FRIENDLY_NAME, ModelCapability.IMAGE_GENERATION) \
    .add_field(_COMPAT_ENDPOINT, ModelCapability.TTS) \
    .add_field(API_KEY_OPTIONAL, ModelCapability.TTS) \
    .add_field(model_field("e.g. tts-1"), ModelCapability.TTS) \
    .add_field(_COMPAT_VOICE, ModelCapability.TTS) \
    .add_field(_COMPAT_FORMAT, ModelCapability.TTS) \
    .add_field(FRIENDLY_NAME, ModelCapability.TTS) \
    .add_field(_COMPAT_ENDPOINT, ModelCapability.STT) \
    .add_field(API_KEY_OPTIONAL, ModelCapability.STT) \
    .add_field(model_field("e.g. whisper-1"), ModelCapability.STT) \
    .add_field(FRIENDLY_NAME, ModelCapability.STT) \
    .build_decorator()
class OpenAICompatibleProvider:
    pass
