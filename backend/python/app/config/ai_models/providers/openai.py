"""OpenAI provider registration."""

from app.config.ai_models.registry import AIModelProviderBuilder
from app.config.ai_models.types import ModelCapability

from .common_fields import (
    API_KEY,
    EMBEDDING_COMMON_TAIL,
    FRIENDLY_NAME,
    LLM_COMMON_TAIL,
    model_field,
)


from app.config.ai_models.types import AIModelField

OPENAI_TTS_VOICE = AIModelField(
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

OPENAI_TTS_FORMAT = AIModelField(
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
    ],
)


@AIModelProviderBuilder("OpenAI", "openAI") \
    .with_description("GPT models for text generation, embeddings, image generation, and speech") \
    .with_capabilities([
        ModelCapability.TEXT_GENERATION,
        ModelCapability.EMBEDDING,
        ModelCapability.IMAGE_GENERATION,
        ModelCapability.TTS,
        ModelCapability.STT,
    ]) \
    .with_icon("/assets/icons/ai-models/openai.svg") \
    .with_color("#10A37F") \
    .popular() \
    .add_field(API_KEY, ModelCapability.TEXT_GENERATION) \
    .add_field(model_field("e.g., gpt-5, gpt-5-mini, gpt-5-nano"), ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[0], ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[1], ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[2], ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[3], ModelCapability.TEXT_GENERATION) \
    .add_field(API_KEY, ModelCapability.EMBEDDING) \
    .add_field(model_field("e.g., text-embedding-3-small, text-embedding-3-large"), ModelCapability.EMBEDDING) \
    .add_field(EMBEDDING_COMMON_TAIL[0], ModelCapability.EMBEDDING) \
    .add_field(EMBEDDING_COMMON_TAIL[1], ModelCapability.EMBEDDING) \
    .add_field(EMBEDDING_COMMON_TAIL[2], ModelCapability.EMBEDDING) \
    .add_field(API_KEY, ModelCapability.IMAGE_GENERATION) \
    .add_field(model_field("e.g., gpt-image-1, dall-e-3"), ModelCapability.IMAGE_GENERATION) \
    .add_field(FRIENDLY_NAME, ModelCapability.IMAGE_GENERATION) \
    .add_field(API_KEY, ModelCapability.TTS) \
    .add_field(model_field("e.g., tts-1, tts-1-hd, gpt-4o-mini-tts"), ModelCapability.TTS) \
    .add_field(OPENAI_TTS_VOICE, ModelCapability.TTS) \
    .add_field(OPENAI_TTS_FORMAT, ModelCapability.TTS) \
    .add_field(FRIENDLY_NAME, ModelCapability.TTS) \
    .add_field(API_KEY, ModelCapability.STT) \
    .add_field(model_field("e.g., whisper-1, gpt-4o-transcribe, gpt-4o-mini-transcribe"), ModelCapability.STT) \
    .add_field(FRIENDLY_NAME, ModelCapability.STT) \
    .build_decorator()
class OpenAIProvider:
    pass
