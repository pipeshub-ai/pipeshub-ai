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
    USERNAME_PASSWORD = "USERNAME_PASSWORD"
    NONE = "NONE"


class ToolCategory(str, Enum):
    """Categories for tools"""
    KNOWLEDGE = "knowledge"
    ACTION = "action"
    UTILITY = "utility"


# ============================================================================
# etcd path helpers
# ============================================================================

def get_toolset_config_path(instance_id: str, user_id: str) -> str:
    """
    Get etcd path for a user's authentication/credentials for a specific
    toolset instance (admin-created).

    Path is keyed by instanceId first so we can list all users for an instance
    with a single prefix scan: /services/toolsets/{instanceId}/

    Args:
        instance_id: Toolset instance UUID (admin-created instance _id)
        user_id: User identifier

    Returns:
        etcd path: /services/toolsets/{instanceId}/{userId}
    """
    return f"/services/toolsets/{instance_id}/{user_id}"


def get_toolset_instance_users_prefix(instance_id: str) -> str:
    """
    Get etcd prefix to list all user auth entries for a specific toolset instance.

    Args:
        instance_id: Toolset instance UUID

    Returns:
        etcd prefix: /services/toolsets/{instanceId}/
    """
    return f"/services/toolsets/{instance_id}/"


def get_user_toolsets_prefix(user_id: str) -> str:
    """
    DEPRECATED - cannot efficiently scan all instances for a user with new layout.
    Kept for backward-compatibility references only.

    Args:
        user_id: User identifier

    Returns:
        Empty string (path structure changed)
    """
    # With new path structure ({instanceId}/{userId}) we cannot list by userId prefix.
    # Use get_toolset_config_path(instance_id, user_id) with known instance IDs instead.
    return ""


def get_toolset_instances_path(org_id: str) -> str:
    """
    Get etcd path for the list of admin-created toolset instances.
    Simplified for single-org mode.

    Args:
        org_id: Organization identifier (ignored in single-org mode)

    Returns:
        etcd path: /services/toolset-instances
    """
    # Single org mode: ignore org_id and use single path
    return "/services/toolset-instances"


def get_toolset_oauth_config_path(toolset_type: str) -> str:
    """
    Get etcd path for the list of OAuth configurations for a toolset type.
    Mirrors the connector pattern (/services/oauth/{connectorType}).

    Args:
        toolset_type: Toolset type (e.g. "jira", "slack")

    Returns:
        etcd path: /services/oauths/toolsets/{toolsetType}
    """
    normalized = normalize_toolset_type(toolset_type)
    return f"/services/oauths/toolsets/{normalized}"


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


def normalize_toolset_type(toolset_type: str) -> str:
    """
    Normalize toolset type for storage keys.

    Args:
        toolset_type: Raw toolset type string

    Returns:
        Normalized toolset type (lowercase, no spaces)
    """
    return toolset_type.lower().strip()
