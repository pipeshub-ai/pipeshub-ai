"""Wispr Flow Speech-to-Text provider registration.

Wispr Flow (https://wisprflow.ai) exposes a hosted STT API that returns
high-accuracy transcripts with automatic edits, filler-word removal, and
context-aware formatting. Unlike the self-hosted Whisper provider, this
one requires only an API key — audio is sent to Wispr's servers for
inference.
"""

from app.config.ai_models.registry import AIModelProviderBuilder
from app.config.ai_models.types import AIModelField, ModelCapability

from .common_fields import API_KEY, FRIENDLY_NAME

WISPR_MODEL = AIModelField(
    name="model",
    display_name="Model",
    field_type="SELECT",
    required=True,
    default_value="flow-v1",
    options=[
        {"value": "flow-v1", "label": "Flow (Voice Interface API)"},
    ],
    description="Wispr Flow currently exposes a single hosted model endpoint.",
)

# ISO 639-1 subset that Wispr Flow officially advertises. Leaving this
# empty forces Wispr to auto-detect across its full supported set.
WISPR_LANGUAGE = AIModelField(
    name="language",
    display_name="Default Language",
    field_type="SELECT",
    required=False,
    default_value="",
    options=[
        {"value": "", "label": "Auto-detect"},
        {"value": "en", "label": "English (en)"},
        {"value": "es", "label": "Spanish (es)"},
        {"value": "fr", "label": "French (fr)"},
        {"value": "de", "label": "German (de)"},
        {"value": "it", "label": "Italian (it)"},
        {"value": "pt", "label": "Portuguese (pt)"},
        {"value": "nl", "label": "Dutch (nl)"},
        {"value": "pl", "label": "Polish (pl)"},
        {"value": "ru", "label": "Russian (ru)"},
        {"value": "ja", "label": "Japanese (ja)"},
        {"value": "ko", "label": "Korean (ko)"},
        {"value": "zh", "label": "Chinese (zh)"},
        {"value": "hi", "label": "Hindi (hi)"},
        {"value": "ar", "label": "Arabic (ar)"},
    ],
    description=(
        "Optional hint that biases transcription toward a single language. "
        "Leave on Auto-detect for multilingual users."
    ),
)

WISPR_APP_TYPE = AIModelField(
    name="appType",
    display_name="App Type",
    field_type="SELECT",
    required=False,
    default_value="ai",
    options=[
        {"value": "ai", "label": "AI prompt / chat"},
        {"value": "email", "label": "Email"},
        {"value": "other", "label": "Other"},
    ],
    description=(
        "Controls Flow's output formatting. 'ai' is tuned for prompt-style "
        "chat input; 'email' keeps salutations / punctuation."
    ),
)

WISPR_ENDPOINT = AIModelField(
    name="endpoint",
    display_name="API Endpoint (Optional)",
    field_type="URL",
    required=False,
    placeholder="https://platform-api.wisprflow.ai",
    description=(
        "Override the Wispr Flow API base URL. Leave blank to use the "
        "default production endpoint."
    ),
)


@AIModelProviderBuilder("Wispr Flow", "wispr") \
    .with_description("Hosted STT from Wispr Flow with auto-edits and 100+ languages.") \
    .with_notice("Requires a Wispr Flow API key (enterprise@wisprflow.ai) and ffmpeg installed on the backend host for audio transcoding.") \
    .with_capabilities([ModelCapability.STT]) \
    .with_icon("/icons/ai-models/wispr.svg") \
    .with_color("#6E56CF") \
    .add_field(API_KEY, ModelCapability.STT) \
    .add_field(WISPR_MODEL, ModelCapability.STT) \
    .add_field(WISPR_LANGUAGE, ModelCapability.STT) \
    .add_field(WISPR_APP_TYPE, ModelCapability.STT) \
    .add_field(WISPR_ENDPOINT, ModelCapability.STT) \
    .add_field(FRIENDLY_NAME, ModelCapability.STT) \
    .build_decorator()
class WisprProvider:
    pass
