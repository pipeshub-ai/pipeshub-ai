"""OpenAI-compatible provider registration."""

from app.config.ai_models.registry import AIModelProviderBuilder
from app.config.ai_models.types import AIModelField, ModelCapability

from .common_fields import API_KEY, EMBEDDING_COMMON_TAIL, LLM_COMMON_TAIL, model_field

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


@AIModelProviderBuilder("OpenAI Compatible", "openAICompatible") \
    .with_description("OpenAI-compatible models") \
    .with_capabilities([ModelCapability.TEXT_GENERATION, ModelCapability.EMBEDDING]) \
    .with_icon("/assets/icons/ai-models/openai.svg") \
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
    .build_decorator()
class OpenAICompatibleProvider:
    pass
