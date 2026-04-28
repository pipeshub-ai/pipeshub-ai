"""
Constants and etcd path helpers for MCP (Model Context Protocol) server management.

Mirrors the toolset_constants.py pattern but for external MCP servers.
"""

from enum import Enum


class MCPServerCategory(str, Enum):
    """Categories for MCP server templates in the catalog"""
    DEVELOPMENT = "development"
    COMMUNICATION = "communication"
    DATABASE = "database"
    FILE_SYSTEM = "file_system"
    PRODUCTIVITY = "productivity"
    CUSTOM = "custom"


# ============================================================================
# etcd path helpers
# ============================================================================

DEFAULT_MCP_SERVER_INSTANCES_PATH = "/services/mcp-server-instances"


def get_mcp_server_config_path(instance_id: str, user_id: str) -> str:
    """
    Get etcd path for a user's authentication/credentials for a specific
    MCP server instance (admin-created).

    Path is keyed by instanceId first so we can list all users for an instance
    with a single prefix scan: /services/mcp-servers/{instanceId}/

    Args:
        instance_id: MCP server instance UUID
        user_id: User identifier (userId or agentKey for service accounts)

    Returns:
        etcd path: /services/mcp-servers/{instanceId}/{userId}
    """
    return f"/services/mcp-servers/{instance_id}/{user_id}"


def get_mcp_server_instance_users_prefix(instance_id: str) -> str:
    """
    Get etcd prefix to list all user auth entries for a specific MCP server instance.

    Args:
        instance_id: MCP server instance UUID

    Returns:
        etcd prefix: /services/mcp-servers/{instanceId}/
    """
    return f"/services/mcp-servers/{instance_id}/"


def get_mcp_server_instances_path() -> str:
    """
    Get etcd path for the list of admin-created MCP server instances.

    Returns:
        etcd path: /services/mcp-server-instances
    """
    return DEFAULT_MCP_SERVER_INSTANCES_PATH


def get_mcp_server_oauth_client_path(instance_id: str) -> str:
    """
    Get etcd path for the OAuth client credentials (clientId, clientSecret)
    for a specific MCP server instance. Stored separately from instance
    metadata so secrets are never exposed when listing instances.

    Admin provides these when creating an OAuth MCP server instance.
    All users of this instance share the same OAuth app credentials.

    Args:
        instance_id: MCP server instance UUID

    Returns:
        etcd path: /services/mcp-servers/{instanceId}/oauth-client
    """
    return f"/services/mcp-servers/{instance_id}/oauth-client"


def get_mcp_server_oauth_tokens_path(instance_id: str, user_id: str) -> str:
    """
    Get etcd path for a user's OAuth tokens for a specific MCP server instance.
    Separate from the main auth record to allow atomic token updates on refresh.

    Args:
        instance_id: MCP server instance UUID
        user_id: User identifier

    Returns:
        etcd path: /services/mcp-servers/{instanceId}/{userId}/oauth-tokens
    """
    return f"/services/mcp-servers/{instance_id}/{user_id}/oauth-tokens"


def get_mcp_server_dcr_client_path(instance_id: str, user_id: str) -> str:
    """
    Get etcd path for storing a DCR (Dynamic Client Registration) client record
    for a specific user + MCP server instance.

    DCR-capable servers (e.g. Atlassian) issue a dynamically-generated
    client_id/client_secret per registration. We persist those credentials here
    so they can be reused for token exchange, refresh, and re-authorization
    without re-registering on every OAuth attempt.

    Args:
        instance_id: MCP server instance UUID
        user_id: User identifier

    Returns:
        etcd path: /services/mcp-servers/{instanceId}/{userId}/dcr-client
    """
    return f"/services/mcp-servers/{instance_id}/{user_id}/dcr-client"


def normalize_mcp_server_type(server_type: str) -> str:
    """
    Normalize MCP server type for storage keys.

    Args:
        server_type: Raw server type string

    Returns:
        Normalized server type (lowercase, stripped)
    """
    return server_type.lower().strip()


def normalize_mcp_server_name(name: str) -> str:
    """
    Normalize MCP server name for namespacing (tool names, keys).

    Converts to lowercase and replaces spaces/special chars with underscores.
    Example: "GitHub Server" -> "github_server"

    Args:
        name: Original server name

    Returns:
        Normalized server name safe for tool namespacing
    """
    return name.lower().replace(" ", "_").replace("-", "_")
