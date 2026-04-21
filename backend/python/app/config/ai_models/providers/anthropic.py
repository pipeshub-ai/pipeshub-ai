"""Anthropic provider registration."""

from app.config.ai_models.registry import AIModelProviderBuilder
from app.config.ai_models.types import ModelCapability

from .common_fields import API_KEY, LLM_COMMON_TAIL, model_field


@(
    AIModelProviderBuilder("Anthropic", "anthropic")
    .with_description("Claude models for advanced text processing")
    .with_capabilities([ModelCapability.TEXT_GENERATION])
    .with_icon("/assets/icons/ai-models/claude-color.svg")
    .with_color("#D97706")
    .popular()
    .add_field(API_KEY)
    .add_field(model_field("e.g., claude-opus-4-7, claude-sonnet-4-6, claude-haiku-4-6"))
    .add_field(LLM_COMMON_TAIL[0])
    .add_field(LLM_COMMON_TAIL[1])
    .add_field(LLM_COMMON_TAIL[2])
    .add_field(LLM_COMMON_TAIL[3])
    .build_decorator()
)

class AnthropicProvider:
    pass
