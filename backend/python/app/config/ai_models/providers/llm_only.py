"""LLM-only providers: xAI, Groq, MiniMax, Fireworks, Mistral (LLM side)."""

from app.config.ai_models.registry import AIModelProviderBuilder
from app.config.ai_models.types import ModelCapability

from .common_fields import API_KEY, LLM_COMMON_TAIL, model_field


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
    .with_description("MiniMax M2.7 models with 1M context") \
    .with_capabilities([ModelCapability.TEXT_GENERATION]) \
    .with_icon("/icons/ai-models/minimax.svg") \
    .with_color("#1A1A2E") \
    .add_field(API_KEY) \
    .add_field(model_field("e.g., MiniMax-M2.7, MiniMax-M2.7-highspeed")) \
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

@AIModelProviderBuilder("Fireworks", "fireworks") \
    .with_description("Fast inference for generative AI") \
    .with_capabilities([ModelCapability.TEXT_GENERATION]) \
    .with_icon("/icons/ai-models/fireworks-color.svg") \
    .with_color("#FF6B35") \
    .add_field(API_KEY) \
    .add_field(model_field("e.g. accounts/fireworks/models/kimi-k2-instruct")) \
    .add_field(LLM_COMMON_TAIL[0]) \
    .add_field(LLM_COMMON_TAIL[1]) \
    .add_field(LLM_COMMON_TAIL[2]) \
    .add_field(LLM_COMMON_TAIL[3]) \
    .build_decorator()
class FireworksProvider:
    pass
