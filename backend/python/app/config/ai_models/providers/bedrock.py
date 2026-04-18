"""AWS Bedrock provider registration."""

from app.config.ai_models.registry import AIModelProviderBuilder
from app.config.ai_models.types import ModelCapability

from .common_fields import (
    AWS_ACCESS_KEY_ID,
    AWS_SECRET_KEY,
    BEDROCK_PROVIDER,
    BEDROCK_PROVIDER_EMBEDDING,
    CUSTOM_PROVIDER,
    EMBEDDING_COMMON_TAIL,
    LLM_COMMON_TAIL,
    REGION,
    model_field,
)


@AIModelProviderBuilder("Bedrock", "bedrock") \
    .with_description("AWS Bedrock models") \
    .with_capabilities([ModelCapability.TEXT_GENERATION, ModelCapability.EMBEDDING]) \
    .with_icon("/assets/icons/ai-models/bedrock-color.svg") \
    .with_color("#0078D4") \
    .add_field(AWS_ACCESS_KEY_ID, ModelCapability.TEXT_GENERATION) \
    .add_field(AWS_SECRET_KEY, ModelCapability.TEXT_GENERATION) \
    .add_field(REGION, ModelCapability.TEXT_GENERATION) \
    .add_field(model_field("e.g. us.anthropic.claude-sonnet-4-20250514-v1:0"), ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[0], ModelCapability.TEXT_GENERATION) \
    .add_field(BEDROCK_PROVIDER, ModelCapability.TEXT_GENERATION) \
    .add_field(CUSTOM_PROVIDER, ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[1], ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[2], ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[3], ModelCapability.TEXT_GENERATION) \
    .add_field(AWS_ACCESS_KEY_ID, ModelCapability.EMBEDDING) \
    .add_field(AWS_SECRET_KEY, ModelCapability.EMBEDDING) \
    .add_field(REGION, ModelCapability.EMBEDDING) \
    .add_field(model_field("e.g. cohere2.embed-multilingual-v3"), ModelCapability.EMBEDDING) \
    .add_field(EMBEDDING_COMMON_TAIL[0], ModelCapability.EMBEDDING) \
    .add_field(BEDROCK_PROVIDER_EMBEDDING, ModelCapability.EMBEDDING) \
    .add_field(CUSTOM_PROVIDER, ModelCapability.EMBEDDING) \
    .add_field(EMBEDDING_COMMON_TAIL[1], ModelCapability.EMBEDDING) \
    .add_field(EMBEDDING_COMMON_TAIL[2], ModelCapability.EMBEDDING) \
    .build_decorator()
class BedrockProvider:
    pass
