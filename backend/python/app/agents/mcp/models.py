"""
Pydantic models for MCP (Model Context Protocol) server management.

Defines the data structures used across catalog, registry, instances,
and tool discovery for external MCP servers.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class MCPTransport(str, Enum):
    """Supported MCP transport types"""
    STDIO = "stdio"
    SSE = "sse"
    STREAMABLE_HTTP = "streamable_http"


class MCPAuthMode(str, Enum):
    """Authentication modes for MCP servers"""
    NONE = "none"
    API_TOKEN = "api_token"
    OAUTH = "oauth"
    HEADERS = "headers"


# ---------------------------------------------------------------------------
# Catalog / Template models
# ---------------------------------------------------------------------------

class _CamelModel(BaseModel):
    """Base model that serializes field names to camelCase."""
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class AuthHint(_CamelModel):
    """Describes the recommended auth mechanism for a catalog template."""

    methods: list[str] = Field(
        default_factory=lambda: ["api_key"],
        description="Supported auth methods: api_key, oauth2, basic, bearer, env_vars",
    )
    default_method: str = "api_key"
    oauth2_authorization_url: str = ""
    oauth2_token_url: str = ""
    oauth2_scopes: list[str] = Field(default_factory=list)
    env_mapping: dict[str, str] = Field(
        default_factory=dict,
        description="Maps credential field names to env vars for the server",
    )


class MCPServerTemplate(_CamelModel):
    """Describes a type of MCP server that can be instantiated from the catalog."""

    type_id: str
    display_name: str
    description: str
    transport: MCPTransport = MCPTransport.STDIO
    command: str = ""
    default_args: list[str] = Field(default_factory=list)
    required_env: list[str] = Field(default_factory=list)
    optional_env: list[str] = Field(default_factory=list)
    url: str = ""
    auth_mode: MCPAuthMode = MCPAuthMode.NONE
    supported_auth_types: list[str] = Field(default_factory=list)
    redirect_uri: str = Field(
        default="",
        description="Relative redirect URI path for OAuth (appended to base_url by the backend)",
    )
    icon_path: str = ""
    documentation_url: str = ""
    tags: list[str] = Field(default_factory=list)
    auth: AuthHint = Field(default_factory=AuthHint)


# ---------------------------------------------------------------------------
# Instance models (stored in etcd)
# ---------------------------------------------------------------------------

class MCPServerInstanceConfig(BaseModel):
    """Configuration for a user-created MCP server instance (persisted in etcd)."""

    instance_id: str = ""
    instance_name: str = ""
    server_type: str | None = None
    display_name: str = ""
    transport: MCPTransport = MCPTransport.STDIO
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    auth_mode: MCPAuthMode = MCPAuthMode.NONE
    supported_auth_types: list[str] = Field(default_factory=list)
    enabled: bool = True
    icon_path: str = ""
    description: str = ""
    org_id: str = ""
    created_by: str = ""
    created_at: str = ""
    updated_at: str = ""


# ---------------------------------------------------------------------------
# Runtime connection config (resolved from instance + credentials)
# ---------------------------------------------------------------------------

class MCPServerConfig(BaseModel):
    """Resolved runtime configuration used to connect to an MCP server."""

    name: str
    server_type: str = ""
    transport: MCPTransport = MCPTransport.STDIO
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    auth_mode: MCPAuthMode = MCPAuthMode.NONE


# ---------------------------------------------------------------------------
# OAuth token storage
# ---------------------------------------------------------------------------

class OAuthTokens(BaseModel):
    """Stored OAuth tokens for a user's MCP server instance."""

    access_token: str = ""
    refresh_token: str = ""
    token_type: str = "Bearer"
    expires_at: int = 0
    scope: str = ""


# ---------------------------------------------------------------------------
# Tool discovery models
# ---------------------------------------------------------------------------

class MCPToolInfo(BaseModel):
    """Metadata for a single tool discovered from an MCP server."""

    name: str
    namespaced_name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    server_name: str = ""
    instance_id: str = ""
    fixed_params: dict[str, str] = Field(
        default_factory=dict,
        description="Parameters auto-injected at call time (e.g. cloudId for Atlassian)",
    )
