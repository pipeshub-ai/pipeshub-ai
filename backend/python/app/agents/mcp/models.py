"""Pydantic models for the MCP (Model Context Protocol) client registry, instances,
and auth/token management.

All models use camelCase aliases on the wire (matching the frontend/Node conventions)
while keeping snake_case attribute names in Python.
"""
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class MCPCamelModel(BaseModel):
    """Base model: camelCase on the wire, snake_case in Python."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="ignore",
    )


class MCPTransport(str, Enum):
    """Transport used to connect to an MCP server."""

    STDIO = "stdio"
    SSE = "sse"
    STREAMABLE_HTTP = "streamable_http"


class MCPAuthMode(str, Enum):
    """How callers authenticate against an MCP server instance."""

    NONE = "none"
    API_TOKEN = "api_token"
    OAUTH = "oauth"
    HEADERS = "headers"


class AuthHint(MCPCamelModel):
    """UI hint describing what a user must provide for a given auth mode."""

    label: str
    placeholder: Optional[str] = None
    help_text: Optional[str] = None


class MCPServerTemplate(MCPCamelModel):
    """A catalog entry: a well-known MCP server the admin can instantiate quickly.

    Templates are pure metadata (no secrets); they never carry credentials.
    """

    type_id: str
    display_name: str
    description: str
    icon: Optional[str] = None
    transport: MCPTransport
    default_auth_mode: MCPAuthMode
    supported_auth_modes: list[MCPAuthMode] = Field(default_factory=list)

    # STDIO
    command: Optional[str] = None
    args: list[str] = Field(default_factory=list)
    required_env: list[str] = Field(default_factory=list)
    optional_env: list[str] = Field(default_factory=list)

    # SSE / Streamable HTTP
    default_url: Optional[str] = None

    # OAuth
    authorization_url: Optional[str] = None
    token_url: Optional[str] = None
    default_scopes: list[str] = Field(default_factory=list)
    # UI hint only (shown before a live probe resolves) — the authorize flow always
    # re-discovers via `app.agents.mcp.dcr.discover_oauth_metadata` (RFC 9728/8414) rather
    # than trusting this flag, since real DCR support can differ from what a template
    # author assumed (e.g. Notion and Atlassian both support it despite older templates
    # having this set to `False`).
    supports_dcr: bool = False

    documentation_url: Optional[str] = None
    auth_hint: Optional[AuthHint] = None
    tags: list[str] = Field(default_factory=list)


class MCPServerInstanceConfig(MCPCamelModel):
    """Admin-facing request body for creating/updating an instance."""

    name: str
    type_id: Optional[str] = None
    transport: MCPTransport
    auth_mode: MCPAuthMode
    use_admin_auth: bool = False
    description: Optional[str] = None

    # STDIO — env is an explicit allowlist mapping (never arbitrary passthrough)
    command: Optional[str] = None
    args: list[str] = Field(default_factory=list)
    # Custom STDIO only — catalog templates supply required_env from the template.
    required_env: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)

    # SSE / Streamable HTTP
    url: Optional[str] = None
    header_name: Optional[str] = None

    # OAuth (custom servers only — catalog templates already have these)
    authorization_url: Optional[str] = None
    token_url: Optional[str] = None
    scopes: list[str] = Field(default_factory=list)


class MCPServerConfig(MCPCamelModel):
    """Persisted instance metadata — no secrets.

    Stored at /services/mcp/instances/{orgId}/{instanceId}.
    """

    id: str = Field(alias="_id")
    org_id: str
    created_by: str
    name: str
    type_id: Optional[str] = None
    transport: MCPTransport
    auth_mode: MCPAuthMode
    use_admin_auth: bool = False
    description: Optional[str] = None

    command: Optional[str] = None
    args: list[str] = Field(default_factory=list)
    required_env: list[str] = Field(default_factory=list)
    optional_env: list[str] = Field(default_factory=list)

    url: Optional[str] = None
    header_name: Optional[str] = None

    authorization_url: Optional[str] = None
    token_url: Optional[str] = None
    scopes: list[str] = Field(default_factory=list)

    is_custom: bool = False
    created_at: int
    updated_at: int


class OAuthTokens(MCPCamelModel):
    """OAuth token set for a user/admin against a specific instance.

    `token_url` is persisted alongside the tokens so background refresh works for
    custom servers without depending on a catalog template lookup.
    """

    access_token: str
    token_type: str = "Bearer"
    refresh_token: Optional[str] = None
    expires_in: Optional[int] = None
    scope: Optional[str] = None
    token_url: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def is_expired(self) -> bool:
        if not self.expires_in:
            return False
        expiry = self.created_at + timedelta(seconds=self.expires_in)
        now = datetime.now(timezone.utc)
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        return now >= expiry

    @property
    def expires_at_epoch(self) -> Optional[int]:
        if not self.expires_in:
            return None
        created = self.created_at if self.created_at.tzinfo else self.created_at.replace(tzinfo=timezone.utc)
        return int((created + timedelta(seconds=self.expires_in)).timestamp())


class DiscoveredOAuthMetadata(MCPCamelModel):
    """RFC 8414/9728 OAuth metadata discovered for an MCP server's authorization server —
    the result of `app.agents.mcp.dcr.discover_oauth_metadata`.

    Told apart from an instance's stored `authorizationUrl`/`tokenUrl` on purpose: those can
    be wrong (a catalog template's endpoints for a provider's *direct* OAuth flow, not the
    ones that actually front its MCP server) until a live discovery confirms or corrects
    them — see `mcp_servers.py::_build_oauth_authorization_url`.
    """

    authorization_endpoint: Optional[str] = None
    token_endpoint: Optional[str] = None
    registration_endpoint: Optional[str] = None
    scopes_supported: list[str] = Field(default_factory=list)
    issuer: Optional[str] = None

    @property
    def supports_dcr(self) -> bool:
        return bool(self.registration_endpoint)

    @property
    def is_usable(self) -> bool:
        return bool(self.authorization_endpoint and self.token_endpoint)


class DCRClient(MCPCamelModel):
    """RFC 7591 dynamically-registered OAuth client for a custom MCP server."""

    client_id: str
    client_secret: Optional[str] = None
    registration_access_token: Optional[str] = None
    registration_client_uri: Optional[str] = None
    authorization_url: str
    token_url: str
    registered_at: int


class MCPToolInfo(MCPCamelModel):
    """A single tool discovered from a connected MCP server, namespaced for the agent loop."""

    name: str
    namespaced_name: str
    description: Optional[str] = None
    input_schema: dict[str, Any] = Field(default_factory=dict)


def utcnow() -> datetime:
    """Timezone-aware UTC now — use everywhere instead of naive `datetime.now()`."""
    return datetime.now(timezone.utc)
