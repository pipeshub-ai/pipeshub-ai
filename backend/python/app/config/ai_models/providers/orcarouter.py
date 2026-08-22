"""OrcaRouter provider registration.

OrcaRouter is an OpenAI-compatible AI gateway for AI agents. Base URL is
https://api.orcarouter.ai/v1. Model slugs use the router/model format, e.g.
``orcarouter/auto``.
"""

from app.config.ai_models.registry import AIModelProviderBuilder
from app.config.ai_models.types import ModelCapability

from .common_fields import API_KEY, LLM_COMMON_TAIL, model_field


@AIModelProviderBuilder("OrcaRouter", "orcarouter") \
    .with_description("OpenAI-compatible AI gateway for AI agents") \
    .with_capabilities([ModelCapability.TEXT_GENERATION]) \
    .with_icon("/icons/ai-models/orcarouter.svg") \
    .with_color("#0160E6") \
    .add_field(API_KEY, ModelCapability.TEXT_GENERATION) \
    .add_field(model_field("e.g., orcarouter/auto"), ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[0], ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[1], ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[2], ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[3], ModelCapability.TEXT_GENERATION) \
    .build_decorator()
class OrcaRouterProvider:
    pass
