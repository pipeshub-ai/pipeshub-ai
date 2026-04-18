"""Mistral provider registration (LLM + embedding)."""

from app.config.ai_models.registry import AIModelProviderBuilder
from app.config.ai_models.types import ModelCapability

from .common_fields import API_KEY, EMBEDDING_COMMON_TAIL, LLM_COMMON_TAIL, model_field


@AIModelProviderBuilder("Mistral", "mistral") \
    .with_description("High-performance language models") \
    .with_capabilities([ModelCapability.TEXT_GENERATION, ModelCapability.EMBEDDING]) \
    .with_icon("/assets/icons/ai-models/mistral-color.svg") \
    .with_color("#FF7000") \
    .add_field(API_KEY, ModelCapability.TEXT_GENERATION) \
    .add_field(model_field("e.g. mistral-large-latest"), ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[0], ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[1], ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[2], ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[3], ModelCapability.TEXT_GENERATION) \
    .add_field(API_KEY, ModelCapability.EMBEDDING) \
    .add_field(model_field("e.g., mistral-embed"), ModelCapability.EMBEDDING) \
    .add_field(EMBEDDING_COMMON_TAIL[0], ModelCapability.EMBEDDING) \
    .add_field(EMBEDDING_COMMON_TAIL[1], ModelCapability.EMBEDDING) \
    .add_field(EMBEDDING_COMMON_TAIL[2], ModelCapability.EMBEDDING) \
    .build_decorator()
class MistralProvider:
    pass
