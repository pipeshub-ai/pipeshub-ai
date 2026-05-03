"""Azure AI and Azure OpenAI provider registrations."""

from app.config.ai_models.registry import AIModelProviderBuilder
from app.config.ai_models.types import AIModelField, ModelCapability

from .common_fields import (
    API_KEY,
    DEPLOYMENT_NAME,
    EMBEDDING_COMMON_TAIL,
    ENDPOINT,
    LLM_COMMON_TAIL,
    model_field,
)

# ---------------------------------------------------------------------------
# Azure AI
# ---------------------------------------------------------------------------

# The Azure AI Foundry endpoint differs per model family. A single placeholder
# can't express both variants without being truncated inside the input, so we
# keep the placeholder short and surface both URLs as labeled ``examples`` that
# the frontend renders as a copyable note below the field.
_AZURE_AI_ENDPOINT_LLM = AIModelField(
    name="endpoint",
    display_name="Endpoint URL",
    field_type="URL",
    required=True,
    placeholder="https://<your-resource-name>.inference.ai.azure.com/anthropic",
    examples=[
        {
            "label": "For Claude models",
            "value": "https://<your-resource-name>.inference.ai.azure.com/anthropic",
        },
        {
            "label": "For OpenAI / DeepSeek / other models",
            "value": "https://<your-resource-name>.cognitiveservices.azure.com/openai/v1/",
        },
    ],
)

_AZURE_AI_ENDPOINT_EMB = AIModelField(
    name="endpoint",
    display_name="Endpoint URL",
    field_type="URL",
    required=True,
    placeholder="https://<your-resource-name>.services.ai.azure.com/openai/v1/",
    examples=[
        {
            "label": "Azure AI Foundry embeddings endpoint",
            "value": "https://<your-resource-name>.services.ai.azure.com/openai/v1/",
        },
    ],
)


@AIModelProviderBuilder("Azure AI", "azureAI") \
    .with_description("Access Azure AI Foundry models including GPT-4o, DeepSeek R1, Cohere, and more") \
    .with_capabilities([ModelCapability.TEXT_GENERATION, ModelCapability.EMBEDDING]) \
    .with_icon("/icons/ai-models/azure-color.svg") \
    .with_color("#0078D4") \
    .popular() \
    .add_field(_AZURE_AI_ENDPOINT_LLM, ModelCapability.TEXT_GENERATION) \
    .add_field(API_KEY, ModelCapability.TEXT_GENERATION) \
    .add_field(model_field("e.g., gpt-5.1, claude-sonnet-4-5, DeepSeek-V3.1"), ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[0], ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[1], ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[2], ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[3], ModelCapability.TEXT_GENERATION) \
    .add_field(_AZURE_AI_ENDPOINT_EMB, ModelCapability.EMBEDDING) \
    .add_field(API_KEY, ModelCapability.EMBEDDING) \
    .add_field(model_field("e.g., text-embedding-ada-002, embed-v-4-0"), ModelCapability.EMBEDDING) \
    .add_field(EMBEDDING_COMMON_TAIL[0], ModelCapability.EMBEDDING) \
    .add_field(EMBEDDING_COMMON_TAIL[1], ModelCapability.EMBEDDING) \
    .add_field(EMBEDDING_COMMON_TAIL[2], ModelCapability.EMBEDDING) \
    .build_decorator()
class AzureAIProvider:
    pass


# ---------------------------------------------------------------------------
# Azure OpenAI
# ---------------------------------------------------------------------------

_AZURE_OPENAI_ENDPOINT = AIModelField(
    name="endpoint",
    display_name="Endpoint URL",
    field_type="URL",
    required=True,
    placeholder="e.g., https://your-resource.openai.azure.com/",
)


@AIModelProviderBuilder("Azure OpenAI", "azureOpenAI") \
    .with_description("Enterprise-grade OpenAI models") \
    .with_capabilities([ModelCapability.TEXT_GENERATION, ModelCapability.EMBEDDING]) \
    .with_icon("/icons/ai-models/azure-color.svg") \
    .with_color("#0078D4") \
    .add_field(_AZURE_OPENAI_ENDPOINT, ModelCapability.TEXT_GENERATION) \
    .add_field(API_KEY, ModelCapability.TEXT_GENERATION) \
    .add_field(DEPLOYMENT_NAME, ModelCapability.TEXT_GENERATION) \
    .add_field(model_field("e.g., gpt-5, gpt-5-mini, gpt-5-nano"), ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[0], ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[1], ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[2], ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[3], ModelCapability.TEXT_GENERATION) \
    .add_field(_AZURE_OPENAI_ENDPOINT, ModelCapability.EMBEDDING) \
    .add_field(API_KEY, ModelCapability.EMBEDDING) \
    .add_field(DEPLOYMENT_NAME, ModelCapability.EMBEDDING) \
    .add_field(model_field("e.g., text-embedding-3-small"), ModelCapability.EMBEDDING) \
    .add_field(EMBEDDING_COMMON_TAIL[0], ModelCapability.EMBEDDING) \
    .add_field(EMBEDDING_COMMON_TAIL[1], ModelCapability.EMBEDDING) \
    .add_field(EMBEDDING_COMMON_TAIL[2], ModelCapability.EMBEDDING) \
    .build_decorator()
class AzureOpenAIProvider:
    pass
