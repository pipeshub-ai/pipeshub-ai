"""Self-hosted Whisper STT provider registration (runs locally via faster-whisper)."""

from app.config.ai_models.registry import AIModelProviderBuilder
from app.config.ai_models.types import AIModelField, ModelCapability

from .common_fields import FRIENDLY_NAME

WHISPER_MODEL = AIModelField(
    name="model",
    display_name="Model",
    field_type="SELECT",
    required=True,
    default_value="base",
    options=[
        {"value": "tiny", "label": "tiny (39M params, fastest)"},
        {"value": "base", "label": "base (74M params)"},
        {"value": "small", "label": "small (244M params)"},
        {"value": "medium", "label": "medium (769M params)"},
        {"value": "large-v2", "label": "large-v2 (1.55B params)"},
        {"value": "large-v3", "label": "large-v3 (1.55B params, newest)"},
        {"value": "distil-large-v3", "label": "distil-large-v3 (756M, ~6x faster)"},
    ],
    description="Whisper model size. Larger is more accurate but slower and uses more memory.",
)

WHISPER_DEVICE = AIModelField(
    name="device",
    display_name="Device",
    field_type="SELECT",
    required=False,
    default_value="auto",
    options=[
        {"value": "auto", "label": "Auto (prefer GPU if available)"},
        {"value": "cpu", "label": "CPU"},
        {"value": "cuda", "label": "CUDA (NVIDIA GPU)"},
    ],
)

WHISPER_COMPUTE_TYPE = AIModelField(
    name="computeType",
    display_name="Compute Type",
    field_type="SELECT",
    required=False,
    default_value="int8",
    options=[
        {"value": "int8", "label": "int8 (CPU, lowest memory)"},
        {"value": "int8_float16", "label": "int8_float16 (GPU, low memory)"},
        {"value": "float16", "label": "float16 (GPU)"},
        {"value": "float32", "label": "float32 (highest precision)"},
    ],
    description="Numeric precision used by the runtime. int8 is recommended for CPU.",
)

WHISPER_MODEL_DIR = AIModelField(
    name="modelDir",
    display_name="Model Cache Directory",
    field_type="TEXT",
    required=False,
    placeholder="Leave empty for default cache location",
    description="Optional filesystem path where Whisper weights are downloaded and cached.",
)


@AIModelProviderBuilder("Whisper (local)", "whisper") \
    .with_description("Self-hosted OpenAI Whisper STT running locally via faster-whisper.") \
    .with_notice("Models are downloaded on first use.") \
    .with_capabilities([ModelCapability.STT]) \
    .with_icon("/icons/ai-models/whisper.svg") \
    .with_color("#7C3AED") \
    .add_field(WHISPER_MODEL, ModelCapability.STT) \
    .add_field(WHISPER_DEVICE, ModelCapability.STT) \
    .add_field(WHISPER_COMPUTE_TYPE, ModelCapability.STT) \
    .add_field(WHISPER_MODEL_DIR, ModelCapability.STT) \
    .add_field(FRIENDLY_NAME, ModelCapability.STT) \
    .build_decorator()
class WhisperProvider:
    pass
