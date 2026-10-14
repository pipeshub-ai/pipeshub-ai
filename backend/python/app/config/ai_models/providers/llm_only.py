"""LLM-only providers: xAI, Groq, MiniMax, Fireworks, Mistral (LLM side)."""

from app.config.ai_models.registry import AIModelProviderBuilder
from app.config.ai_models.types import AIModelField, ModelCapability

from .common_fields import API_KEY, EMBEDDING_COMMON_TAIL, LLM_COMMON_TAIL, model_field


# ---------------------------------------------------------------------------
# xAI (Grok)
# ---------------------------------------------------------------------------

@AIModelProviderBuilder("xAI", "xai") \
    .with_description("Grok models with real-time capabilities") \
    .with_capabilities([ModelCapability.TEXT_GENERATION]) \
    .with_icon("/icons/ai-models/xai.svg") \
    .with_color("#1DA1F2") \
    .add_field(API_KEY) \
    .add_field(model_field("e.g. grok-3-latest")) \
    .add_field(LLM_COMMON_TAIL[0]) \
    .add_field(LLM_COMMON_TAIL[1]) \
    .add_field(LLM_COMMON_TAIL[2]) \
    .add_field(LLM_COMMON_TAIL[3]) \
    .build_decorator()
class XAIProvider:
    pass


# ---------------------------------------------------------------------------
# Groq
# ---------------------------------------------------------------------------

@AIModelProviderBuilder("Groq", "groq") \
    .with_description("High-speed inference for LLM models") \
    .with_capabilities([ModelCapability.TEXT_GENERATION]) \
    .with_icon("/icons/ai-models/groq.svg") \
    .with_color("#F55036") \
    .add_field(API_KEY) \
    .add_field(model_field("e.g. meta-llama/llama-4-scout-17b-16e-instruct")) \
    .add_field(LLM_COMMON_TAIL[0]) \
    .add_field(LLM_COMMON_TAIL[1]) \
    .add_field(LLM_COMMON_TAIL[2]) \
    .add_field(LLM_COMMON_TAIL[3]) \
    .build_decorator()
class GroqProvider:
    pass


# ---------------------------------------------------------------------------
# MiniMax
# ---------------------------------------------------------------------------

@AIModelProviderBuilder("MiniMax", "minimax") \
    .with_description("MiniMax M3 and M2.7 models with 1M context") \
    .with_capabilities([ModelCapability.TEXT_GENERATION]) \
    .with_icon("/icons/ai-models/minimax.svg") \
    .with_color("#1A1A2E") \
    .add_field(API_KEY) \
    .add_field(model_field("e.g., MiniMax-M3, MiniMax-M2.7, MiniMax-M2.7-highspeed")) \
    .add_field(LLM_COMMON_TAIL[0]) \
    .add_field(LLM_COMMON_TAIL[1]) \
    .add_field(LLM_COMMON_TAIL[2]) \
    .add_field(LLM_COMMON_TAIL[3]) \
    .build_decorator()
class MiniMaxProvider:
    pass


# ---------------------------------------------------------------------------
# Fireworks
# ---------------------------------------------------------------------------

_FIREWORKS_ENDPOINT = AIModelField(
    name="endpoint",
    display_name="Endpoint URL",
    field_type="URL",
    required=True,
    placeholder="https://api.fireworks.ai/inference/v1",
)


@AIModelProviderBuilder("Fireworks", "fireworks") \
    .with_description("Fast inference for generative AI") \
    .with_capabilities([ModelCapability.TEXT_GENERATION, ModelCapability.EMBEDDING]) \
    .with_icon("/icons/ai-models/fireworks-color.svg") \
    .with_color("#FF6B35") \
    .add_field(API_KEY, ModelCapability.TEXT_GENERATION) \
    .add_field(model_field("e.g. accounts/fireworks/models/kimi-k2-instruct"), ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[0], ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[1], ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[2], ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[3], ModelCapability.TEXT_GENERATION) \
    .add_field(API_KEY, ModelCapability.EMBEDDING) \
    .add_field(model_field("e.g. nomic-ai/nomic-embed-text-v1.5"), ModelCapability.EMBEDDING) \
    .add_field(EMBEDDING_COMMON_TAIL[0], ModelCapability.EMBEDDING) \
    .add_field(_FIREWORKS_ENDPOINT, ModelCapability.EMBEDDING) \
    .add_field(EMBEDDING_COMMON_TAIL[1], ModelCapability.EMBEDDING) \
    .add_field(EMBEDDING_COMMON_TAIL[2], ModelCapability.EMBEDDING) \
    .build_decorator()
class FireworksProvider:
    pass
