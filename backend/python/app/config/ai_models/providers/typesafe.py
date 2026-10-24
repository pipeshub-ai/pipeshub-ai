"""TypeSafe provider registration: System One decision models (Jev)."""

from app.config.ai_models.registry import AIModelProviderBuilder
from app.config.ai_models.types import AIModelField, ModelCapability

from .common_fields import API_KEY, FRIENDLY_NAME

TYPESAFE_MODEL = AIModelField(
    name="model",
    display_name="Model",
    field_type="TEXT",
    required=True,
    default_value="jev-latest",
    placeholder="e.g., jev-latest, jev-1.14.0",
    description="An alias follows new releases; a version id such as jev-1.13.0 keeps answers stable.",
)

TYPESAFE_BASE_URL = AIModelField(
    name="endpoint",
    display_name="Base URL (optional)",
    field_type="URL",
    required=False,
    placeholder="https://api.typesafe.ai",
    description="Leave blank for TypeSafe's API. Set it for a server that speaks the same API.",
)


@AIModelProviderBuilder("TypeSafe", "typesafe") \
    .with_description("Jev decision models: calibrated yes/no, choice and score answers") \
    .with_capabilities([ModelCapability.DECISION]) \
    .with_icon("/icons/ai-models/typesafe.svg") \
    .with_color("#4F46E5") \
    .add_field(API_KEY) \
    .add_field(TYPESAFE_BASE_URL) \
    .add_field(TYPESAFE_MODEL) \
    .add_field(FRIENDLY_NAME) \
    .build_decorator()
class TypeSafeProvider:
    pass
