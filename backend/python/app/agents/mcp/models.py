"""
Pydantic models for MCP (Model Context Protocol) server management.

Defines the data structures used across catalog, registry, instances,
and tool discovery for external MCP servers.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
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
# Request payload models (API input validation)
# ---------------------------------------------------------------------------

_TEMPLATE_INTRINSIC_FIELDS = (
    "command",
    "args",
    "url",
    "transport",
    "required_env",
    "supported_auth_types",
    "auth_header_mapping",
)


class CreateInstanceRequest(_CamelModel):
    """Payload for creating a new MCP server instance.

    For catalog-backed instances (server_type != "custom"), the
    template-intrinsic fields must NOT be present — they are resolved
    from the catalog template at read time. For custom instances,
    command (stdio) or url (sse/streamable_http) is required.
    """

    instance_name: str
    server_type: str = "custom"
    display_name: str | None = None
    description: str | None = None
    icon_path: str | None = None
    auth_mode: MCPAuthMode = MCPAuthMode.NONE
    enabled: bool = True

    client_id: str = ""
    client_secret: str = ""

    command: str | None = None
    args: list[str] | None = None
    url: str | None = None
    transport: MCPTransport | None = None
    required_env: list[str] | None = None
    supported_auth_types: list[str] | None = None
    auth_header_mapping: dict[str, str] | None = None

    @model_validator(mode="after")
    def validate_instance_fields(self) -> Self:
        if self.server_type != "custom":
            present = [
                f for f in _TEMPLATE_INTRINSIC_FIELDS if getattr(self, f) is not None
            ]
            if present:
                raise ValueError(
                    f"Cannot override template-intrinsic fields for catalog "
                    f"instance: {present}"
                )
        else:
            t = self.transport or MCPTransport.STDIO
            if t == MCPTransport.STDIO and not self.command:
                raise ValueError("command is required when transport is stdio")
            if t in (MCPTransport.SSE, MCPTransport.STREAMABLE_HTTP) and not self.url:
                raise ValueError(
                    "url is required when transport is sse or streamable_http"
                )
        return self


class UpdateInstanceRequest(_CamelModel):
    """Payload for updating an MCP server instance.

    All fields are optional (partial update). Rejection of template-intrinsic
    fields for catalog-backed instances is NOT done inside a Pydantic
    model_validator because the validator needs the stored instance's
    serverType, which is NOT part of the update request body — it only
    exists in the persisted instance. That business-rule check is handled
    in the service layer where the stored instance is already loaded.
    """

    instance_name: str | None = None
    display_name: str | None = None
    description: str | None = None
    icon_path: str | None = None
    auth_mode: MCPAuthMode | None = None
    enabled: bool | None = None

    command: str | None = None
    args: list[str] | None = None
    url: str | None = None
    transport: MCPTransport | None = None
    required_env: list[str] | None = None
    supported_auth_types: list[str] | None = None
    auth_header_mapping: dict[str, str] | None = None


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
