from typing import Any, Dict

from app.connectors.core.registry.auth_builder import OAuthConfig
from app.connectors.core.registry.types import AuthField


def auth_field_to_dict(field: AuthField) -> Dict[str, Any]:
    """
    Convert AuthField to field config dictionary.

    This is a shared utility function used by both ConnectorConfigBuilder
    and ToolsetConfigBuilder to avoid code duplication.

    Args:
        field: AuthField instance to convert

    Returns:
        Dictionary representation of the auth field
    """
    min_length = 0 if field.field_type == "CHECKBOX" else field.min_length
    return {
        "name": field.name,
        "displayName": field.display_name,
        "placeholder": field.placeholder,
        "description": field.description,
        "fieldType": field.field_type,
        "required": field.required,
        "defaultValue": field.default_value,
        "validation": {
            "minLength": min_length,
            "maxLength": field.max_length,
        },
        "isSecret": field.is_secret
    }


def auto_add_oauth_fields(
    config: Dict[str, Any],
    oauth_config: OAuthConfig,
    auth_type: str
) -> None:
    """
    Automatically add all OAuth fields from OAuth config to the config dictionary.

    This is a shared utility function used by both ConnectorConfigBuilder
    and ToolsetConfigBuilder to avoid code duplication.

    Args:
        config: The configuration dictionary to modify
        oauth_config: OAuthConfig instance with fields to add
        auth_type: The authentication type to add fields to
    """
    # Initialize schema structure if needed
    if "schemas" not in config["auth"]:
        config["auth"]["schemas"] = {}
    if auth_type not in config["auth"]["schemas"]:
        config["auth"]["schemas"][auth_type] = {"fields": []}

    target_schema = config["auth"]["schemas"][auth_type]
    existing_fields = {f.get("name") for f in target_schema.get("fields", []) if isinstance(f, dict)}

    # Add all fields from OAuth config (reuse conversion logic)
    for auth_field in oauth_config.auth_fields:
        if auth_field.name not in existing_fields:
            field_config = auth_field_to_dict(auth_field)
            target_schema["fields"].append(field_config)


