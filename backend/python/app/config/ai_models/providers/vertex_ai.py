"""Google Cloud Vertex AI provider registration (LLM + embeddings)."""

from app.config.ai_models.registry import AIModelProviderBuilder
from app.config.ai_models.types import AIModelField, ModelCapability

from .common_fields import (
    EMBEDDING_COMMON_TAIL,
    LLM_COMMON_TAIL,
    model_field,
)

VERTEX_AI_PROJECT = AIModelField(
    name="project",
    display_name="GCP Project ID",
    field_type="TEXT",
    required=True,
    placeholder="your-gcp-project-id",
    description="Google Cloud project that hosts Vertex AI (same as in the service account JSON).",
)

VERTEX_AI_LOCATION = AIModelField(
    name="location",
    display_name="Region",
    field_type="TEXT",
    required=False,
    default_value="us-central1",
    placeholder="us-central1",
    description="Vertex AI region (e.g. us-central1, europe-west4).",
)

SERVICE_ACCOUNT_JSON = AIModelField(
    name="serviceAccountJson",
    display_name="Service Account JSON",
    field_type="FILE",
    required=True,
    placeholder="Click to upload your Vertex AI service account JSON key",
    description=(
        "Upload a JSON key for a service account with Vertex AI User (or broader) "
        "permissions. Create keys in Google Cloud Console: IAM & Admin > "
        "Service Accounts > Keys."
    ),
    is_secret=True,
    validation={
        "acceptedFileTypes": [".json", "application/json"],
        "validationRules": [
            {
                "type": "json_valid",
                "errorMessage": "File must be valid JSON.",
            },
            {
                "type": "json_has_fields",
                "requiredFields": ["type", "project_id", "private_key"],
                "errorMessage": "Missing required fields: {missing}",
            },
            {
                "type": "json_field_equals",
                "field": "type",
                "value": "service_account",
                "errorMessage": (
                    "This is not a Google Cloud service account JSON file. "
                    "The 'type' field must be 'service_account'."
                ),
            },
        ],
    },
)


@AIModelProviderBuilder("Vertex AI", "vertexAI") \
    .with_description(
        "Google Cloud Vertex AI for Gemini and embedding models using a "
        "service account JSON key (configured here, not via environment variables)."
    ) \
    .with_capabilities([
        ModelCapability.TEXT_GENERATION,
        ModelCapability.EMBEDDING,
    ]) \
    .with_icon("/icons/ai-models/Vertex-AI.svg") \
    .with_color("#4285F4") \
    .popular() \
    .add_field(VERTEX_AI_PROJECT, ModelCapability.TEXT_GENERATION) \
    .add_field(VERTEX_AI_LOCATION, ModelCapability.TEXT_GENERATION) \
    .add_field(SERVICE_ACCOUNT_JSON, ModelCapability.TEXT_GENERATION) \
    .add_field(model_field("e.g., gemini-2.5-flash, gemini-2.5-pro"), ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[0], ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[1], ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[2], ModelCapability.TEXT_GENERATION) \
    .add_field(LLM_COMMON_TAIL[3], ModelCapability.TEXT_GENERATION) \
    .add_field(VERTEX_AI_PROJECT, ModelCapability.EMBEDDING) \
    .add_field(VERTEX_AI_LOCATION, ModelCapability.EMBEDDING) \
    .add_field(SERVICE_ACCOUNT_JSON, ModelCapability.EMBEDDING) \
    .add_field(model_field("e.g., text-embedding-004, text-multilingual-embedding-002"), ModelCapability.EMBEDDING) \
    .add_field(EMBEDDING_COMMON_TAIL[0], ModelCapability.EMBEDDING) \
    .add_field(EMBEDDING_COMMON_TAIL[1], ModelCapability.EMBEDDING) \
    .add_field(EMBEDDING_COMMON_TAIL[2], ModelCapability.EMBEDDING) \
    .build_decorator()
class VertexAIProvider:
    pass
