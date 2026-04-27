"""Google Gemini provider registration."""

from app.config.ai_models.registry import AIModelProviderBuilder
from app.config.ai_models.types import AIModelField, ModelCapability

from .common_fields import (
    API_KEY,
    EMBEDDING_COMMON_TAIL,
    FRIENDLY_NAME,
    LLM_COMMON_TAIL,
    model_field,
)

# Gemini TTS ships 30 prebuilt voices named after astronomical objects.
# See: https://ai.google.dev/gemini-api/docs/speech-generation#voice-options
_GEMINI_TTS_VOICE_NAMES = [
    "Zephyr", "Puck", "Charon", "Kore", "Fenrir", "Leda", "Orus", "Aoede",
    "Callirrhoe", "Autonoe", "Enceladus", "Iapetus", "Umbriel", "Algieba",
    "Despina", "Erinome", "Algenib", "Rasalgethi", "Laomedeia", "Achernar",
    "Alnilam", "Schedar", "Gacrux", "Pulcherrima", "Achird", "Zubenelgenubi",
    "Vindemiatrix", "Sadachbia", "Sadaltager", "Sulafat",
]

GEMINI_TTS_VOICE = AIModelField(
    name="voice",
    display_name="Voice",
    field_type="SELECT",
    required=False,
    default_value="Kore",
    options=[{"value": v, "label": v} for v in _GEMINI_TTS_VOICE_NAMES],
    description="Default prebuilt voice name for text-to-speech output.",
)

# Gemini's generateContent TTS endpoint emits raw PCM (24 kHz, 16-bit, mono).
# We wrap it in a WAV container (default) or re-encode via ffmpeg for the
# other common formats. Keep this list in sync with _GEMINI_TTS_VALID_FORMATS
# in app/utils/aimodels.py.
GEMINI_TTS_FORMAT = AIModelField(
    name="responseFormat",
    display_name="Audio Format",
    field_type="SELECT",
    required=False,
    default_value="wav",
    options=[
        {"value": "wav", "label": "WAV (24 kHz PCM, no transcode)"},
        {"value": "pcm", "label": "PCM (raw 24 kHz signed 16-bit mono)"},
        {"value": "mp3", "label": "MP3 (requires ffmpeg)"},
        {"value": "opus", "label": "Opus (requires ffmpeg)"},
        {"value": "aac", "label": "AAC (requires ffmpeg)"},
        {"value": "flac", "label": "FLAC (requires ffmpeg)"},
    ],
    description=(
        "WAV/PCM are served directly from Gemini's output. The compressed "
        "formats require ffmpeg on the backend host."
    ),
)


@AIModelProviderBuilder("Gemini", "gemini") \
    .with_description("Gemini models with multimodal capabilities, Imagen image generation, and native speech (STT + TTS).") \
    .with_capabilities([
        ModelCapability.TEXT_GENERATION,
        ModelCapability.EMBEDDING,
        ModelCapability.IMAGE_GENERATION,
        ModelCapability.TTS,
        ModelCapability.STT,
    ]) \
    .with_icon("/icons/ai-models/gemini-color.svg") \
    .with_color("#4285F4") \
    .popular() \
    .add_field(API_KEY, ModelCapability.TEXT_GENERATION) \
    .add_field(model_field("e.g., gemini-3-flash-preview, gemini-3.1-pro-preview"), ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[0], ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[1], ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[2], ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[3], ModelCapability.TEXT_GENERATION) \
    .add_field(API_KEY, ModelCapability.EMBEDDING) \
    .add_field(model_field("e.g., gemini-embedding-001"), ModelCapability.EMBEDDING) \
    .add_field(EMBEDDING_COMMON_TAIL[0], ModelCapability.EMBEDDING) \
    .add_field(EMBEDDING_COMMON_TAIL[1], ModelCapability.EMBEDDING) \
    .add_field(EMBEDDING_COMMON_TAIL[2], ModelCapability.EMBEDDING) \
    .add_field(API_KEY, ModelCapability.IMAGE_GENERATION) \
    .add_field(model_field("e.g., gemini-2.5-flash-image, imagen-4.0-generate-001"), ModelCapability.IMAGE_GENERATION) \
    .add_field(FRIENDLY_NAME, ModelCapability.IMAGE_GENERATION) \
    .add_field(API_KEY, ModelCapability.TTS) \
    .add_field(model_field("e.g., gemini-3.1-flash-tts-preview, gemini-2.5-flash-preview-tts, gemini-2.5-pro-preview-tts"), ModelCapability.TTS) \
    .add_field(GEMINI_TTS_VOICE, ModelCapability.TTS) \
    .add_field(GEMINI_TTS_FORMAT, ModelCapability.TTS) \
    .add_field(FRIENDLY_NAME, ModelCapability.TTS) \
    .add_field(API_KEY, ModelCapability.STT) \
    .add_field(model_field("e.g., gemini-2.5-flash, gemini-2.5-pro, gemini-3-flash-preview"), ModelCapability.STT) \
    .add_field(FRIENDLY_NAME, ModelCapability.STT) \
    .build_decorator()
class GeminiProvider:
    pass
