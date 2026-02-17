from enum import Enum


class ToolsetType(str, Enum):
    """Types of toolsets available in the system"""
    APP = "app"
    FILE = "file"
    WEB_SEARCH = "web_search"
    DATABASE = "database"
    UTILITY = "utility"


class AuthType(str, Enum):
    """Authentication types for toolsets"""
    OAUTH = "OAUTH"
    API_TOKEN = "API_TOKEN"
    BEARER_TOKEN = "BEARER_TOKEN"
    NONE = "NONE"


class ToolCategory(str, Enum):
    """Categories for tools"""
    KNOWLEDGE = "knowledge"
    ACTION = "action"
    UTILITY = "utility"


# etcd path helpers
def get_toolset_config_path(user_id: str, app_name: str) -> str:
    """
    Get etcd path for toolset configuration.

    Args:
        user_id: User identifier
        app_name: Application name (normalized)

    Returns:
        etcd path: /services/toolsets/{userId}/{appName}
    """
    return f"/services/toolsets/{user_id}/{normalize_app_name(app_name)}"


def get_toolset_auth_path(user_id: str, app_name: str) -> str:
    """
    Get etcd auth path for toolset.

    Args:
        user_id: User identifier
        app_name: Application name (normalized)

    Returns:
        etcd auth path
    """
    return f"{get_toolset_config_path(user_id, app_name)}/auth"


def normalize_app_name(name: str) -> str:
    """
    Normalize app name for etcd storage.

    Converts to lowercase and removes spaces and underscores.
    Example: "Slack Workspace" -> "slackworkspace"

    Args:
        name: Original app name

    Returns:
        Normalized app name
    """
    return name.lower().replace(" ", "").replace("_", "")
