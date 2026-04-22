"""Cohere provider registration."""

from app.config.ai_models.registry import AIModelProviderBuilder
from app.config.ai_models.types import ModelCapability

from .common_fields import API_KEY, EMBEDDING_COMMON_TAIL, LLM_COMMON_TAIL, model_field


@AIModelProviderBuilder("Cohere", "cohere") \
    .with_description("Command models for text generation and embeddings") \
    .with_capabilities([ModelCapability.TEXT_GENERATION, ModelCapability.EMBEDDING]) \
    .with_icon("/assets/icons/ai-models/cohere-color.svg") \
    .with_color("#39C5BB") \
    .add_field(API_KEY, ModelCapability.TEXT_GENERATION) \
    .add_field(model_field("e.g. command-a-03-2025"), ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[0], ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[1], ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[2], ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[3], ModelCapability.TEXT_GENERATION) \
    .add_field(API_KEY, ModelCapability.EMBEDDING) \
    .add_field(model_field("e.g., embed-v4.0"), ModelCapability.EMBEDDING) \
    .add_field(EMBEDDING_COMMON_TAIL[0], ModelCapability.EMBEDDING) \
    .add_field(EMBEDDING_COMMON_TAIL[1], ModelCapability.EMBEDDING) \
    .add_field(EMBEDDING_COMMON_TAIL[2], ModelCapability.EMBEDDING) \
    .build_decorator()
class CohereProvider:
    pass
