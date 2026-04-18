"""Reusable field definitions shared across many AI model providers."""

from app.config.ai_models.types import AIModelField

# ---------------------------------------------------------------------------
# Authentication / credential fields
# ---------------------------------------------------------------------------

API_KEY = AIModelField(
    name="apiKey",
    display_name="API Key",
    field_type="PASSWORD",
    required=True,
    placeholder="Your API Key",
    is_secret=True,
)

API_KEY_OPTIONAL = AIModelField(
    name="apiKey",
    display_name="API Key",
    field_type="PASSWORD",
    required=False,
    placeholder="Your API Key (optional)",
    is_secret=True,
)

ENDPOINT = AIModelField(
    name="endpoint",
    display_name="Endpoint URL",
    field_type="URL",
    required=True,
    placeholder="https://api.example.com/v1",
)

ENDPOINT_OPTIONAL = AIModelField(
    name="endpoint",
    display_name="Endpoint URL",
    field_type="URL",
    required=False,
    placeholder="https://api.example.com/v1",
)

DEPLOYMENT_NAME = AIModelField(
    name="deploymentName",
    display_name="Deployment Name",
    field_type="TEXT",
    required=True,
    placeholder="Your deployment name",
)

# ---------------------------------------------------------------------------
# Model identification fields
# ---------------------------------------------------------------------------

def model_field(placeholder: str = "Model name", required: bool = True) -> AIModelField:
    return AIModelField(
        name="model",
        display_name="Model Name",
        field_type="TEXT",
        required=required,
        placeholder=placeholder,
    )

FRIENDLY_NAME = AIModelField(
    name="modelFriendlyName",
    display_name="Model Friendly Name (Optional)",
    field_type="TEXT",
    required=False,
    placeholder="e.g., My Custom Model",
)

# ---------------------------------------------------------------------------
# LLM-specific flags
# ---------------------------------------------------------------------------

CONTEXT_LENGTH = AIModelField(
    name="contextLength",
    display_name="Context Length",
    field_type="NUMBER",
    required=False,
    placeholder="e.g. 128000",
    validation={"minLength": 1, "maxLength": 1000000},
)

IS_MULTIMODAL_LLM = AIModelField(
    name="isMultimodal",
    display_name="Multimodal",
    field_type="BOOLEAN",
    required=False,
    default_value=True,
    description="Supports text + image input",
)

IS_REASONING = AIModelField(
    name="isReasoning",
    display_name="Reasoning",
    field_type="BOOLEAN",
    required=False,
    default_value=False,
    description="Supports chain-of-thought reasoning",
)

IS_MULTIMODAL_EMBEDDING = AIModelField(
    name="isMultimodal",
    display_name="Multimodal",
    field_type="BOOLEAN",
    required=False,
    default_value=False,
    description="Supports multimodal embeddings",
)

DIMENSIONS = AIModelField(
    name="dimensions",
    display_name="Output Dimensions",
    field_type="NUMBER",
    required=False,
    placeholder="Leave empty for model default",
    description="Override the embedding output dimensions (only supported by some models)",
    validation={"minLength": 1, "maxLength": 65536},
)

# ---------------------------------------------------------------------------
# AWS Bedrock fields
# ---------------------------------------------------------------------------

AWS_ACCESS_KEY_ID = AIModelField(
    name="awsAccessKeyId",
    display_name="AWS Access Key ID",
    field_type="PASSWORD",
    required=False,
    placeholder="Leave empty to use EC2 IAM role",
    is_secret=True,
)

AWS_SECRET_KEY = AIModelField(
    name="awsAccessSecretKey",
    display_name="AWS Access Secret Key",
    field_type="PASSWORD",
    required=False,
    placeholder="Leave empty to use EC2 IAM role",
    is_secret=True,
)

REGION = AIModelField(
    name="region",
    display_name="Region",
    field_type="TEXT",
    required=True,
    placeholder="us-east-1",
)

BEDROCK_PROVIDER = AIModelField(
    name="provider",
    display_name="Provider",
    field_type="SELECT",
    required=True,
    default_value="anthropic",
    options=[
        {"value": "anthropic", "label": "Anthropic (Claude)"},
        {"value": "mistral", "label": "Mistral"},
        {"value": "qwen", "label": "Qwen"},
        {"value": "deepseek", "label": "DeepSeek"},
        {"value": "cohere", "label": "Cohere"},
        {"value": "amazon", "label": "Amazon (Titan)"},
        {"value": "ai21", "label": "AI21 Labs"},
        {"value": "other", "label": "Other (Custom)"},
    ],
)

BEDROCK_PROVIDER_EMBEDDING = AIModelField(
    name="provider",
    display_name="Provider",
    field_type="SELECT",
    required=True,
    default_value="cohere",
    options=[
        {"value": "cohere", "label": "Cohere"},
        {"value": "amazon", "label": "Amazon (Titan)"},
        {"value": "other", "label": "Other (Custom)"},
    ],
)

CUSTOM_PROVIDER = AIModelField(
    name="customProvider",
    display_name="Custom Provider Name",
    field_type="TEXT",
    required=False,
    placeholder="Only needed if you selected 'Other' as provider",
)


# ---------------------------------------------------------------------------
# Convenience bundles
# ---------------------------------------------------------------------------

LLM_COMMON_TAIL = [FRIENDLY_NAME, CONTEXT_LENGTH, IS_MULTIMODAL_LLM, IS_REASONING]
EMBEDDING_COMMON_TAIL = [FRIENDLY_NAME, DIMENSIONS, IS_MULTIMODAL_EMBEDDING]
