"""Pydantic models for the MCP (Model Context Protocol) client registry, instances,
and auth/token management.

All models use camelCase aliases on the wire (matching the frontend/Node conventions)
while keeping snake_case attribute names in Python.
"""
import re
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Annotated, Any, Optional
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator
from pydantic.alias_generators import to_camel

from app.agents.mcp.tool_kind import KindSource, ToolKind, tool_kind


def is_web_url(value: object) -> bool:
    """An absolute http(s) URL with a host and no user-info. OAuth endpoints are opened in
    the user's browser, so anything else (`javascript:`, `data:`, …) would run as PipesHub."""
    if not isinstance(value, str):
        return False
    try:
        parts = urlsplit(value.strip())
        host = parts.hostname
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(host) and "@" not in parts.netloc


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


class TokenEndpointAuthMethod(str, Enum):
    """How PipesHub proves which OAuth client it is when it asks for tokens (RFC 6749 §2.3.1)."""

    CLIENT_SECRET_POST = "client_secret_post"
    CLIENT_SECRET_BASIC = "client_secret_basic"
    NONE = "none"


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
    # Extra `GET {authorizationUrl}` query parameters this provider needs (e.g. Google's
    # `access_type=offline`). Protocol parameters are not overridable — see
    # `dcr.build_authorization_url`.
    authorization_params: dict[str, str] = Field(default_factory=dict)
    # UI hint only (shown before a live probe resolves) — the authorize flow always
    # re-discovers via `app.agents.mcp.dcr.discover_oauth_metadata` (RFC 9728/8414) rather
    # than trusting this flag, since real DCR support can differ from what a template
    # author assumed (e.g. Notion and Atlassian both support it despite older templates
    # having this set to `False`).
    supports_dcr: bool = False

    documentation_url: Optional[str] = None
    auth_hint: Optional[AuthHint] = None
    tags: list[str] = Field(default_factory=list)
    # The catalog entry that took this one's place. A replaced entry can't be used for a new
    # server; servers already made from it keep running from it.
    replaced_by: Optional[str] = None


# Header names an instance may not use for its credential: they belong to HTTP itself or to
# the MCP transport, and a value there would break or hijack the connection.
RESERVED_HEADER_NAMES = frozenset({
    "host", "content-length", "content-type", "transfer-encoding", "connection", "upgrade", "te",
    "trailer", "keep-alive", "proxy-authorization", "proxy-connection", "accept", "last-event-id",
    "mcp-session-id", "mcp-protocol-version", "mcp-method", "mcp-name",
})
_HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")


def check_header_name(value: Optional[str]) -> Optional[str]:
    """An HTTP token (RFC 9110) that isn't reserved, or None for the default."""
    value = (value or "").strip()
    if not value:
        return None
    if not _HEADER_NAME.fullmatch(value):
        raise ValueError("must be a valid HTTP header name")
    if value.lower() in RESERVED_HEADER_NAMES:
        raise ValueError(f"{value} is used by HTTP or MCP itself and can't carry a credential")
    return value


class MCPServerInstanceConfig(MCPCamelModel):
    """Request body for creating/updating an instance. Every text field is bounded, far above
    any real value, so a request can't store arbitrarily large records."""

    name: str = Field(max_length=200)
    type_id: Optional[str] = Field(default=None, max_length=128)
    # "org" (visible to everyone, admins only) or "personal" (visible to its creator).
    # Non-admins always get "personal"; it can't change after creation.
    scope: Optional[str] = Field(default=None, max_length=32)
    transport: MCPTransport
    auth_mode: MCPAuthMode
    use_admin_auth: bool = False
    description: Optional[str] = Field(default=None, max_length=2000)

    # STDIO — credential values are never part of the instance; users supply them per
    # instance via /authenticate, allowlisted against these names.
    command: Optional[str] = Field(default=None, max_length=512)
    args: list[Annotated[str, Field(max_length=2048)]] = Field(default_factory=list, max_length=64)
    # Custom STDIO only — catalog templates supply required_env from the template.
    required_env: list[Annotated[str, Field(max_length=128)]] = Field(default_factory=list, max_length=64)

    # SSE / Streamable HTTP
    url: Optional[str] = Field(default=None, max_length=2048)
    header_name: Optional[str] = Field(default=None, max_length=128)

    # OAuth (custom servers only — catalog templates already have these)
    authorization_url: Optional[str] = Field(default=None, max_length=2048)
    token_url: Optional[str] = Field(default=None, max_length=2048)
    scopes: list[Annotated[str, Field(max_length=256)]] = Field(default_factory=list, max_length=64)

    @field_validator("header_name")
    @classmethod
    def _header_name_is_usable(cls, value: Optional[str]) -> Optional[str]:
        return check_header_name(value)

    @field_validator("authorization_url", "token_url")
    @classmethod
    def _oauth_endpoint_is_a_web_url(cls, value: Optional[str]) -> Optional[str]:
        if value and not is_web_url(value):
            raise ValueError("must be an http or https URL")
        return value or None

    # None = the deployment default (see `app.agents.mcp.client`). Connect stays well under the
    # gateway's 90 s MCP proxy timeout: a discovery that needs a token refresh connects twice.
    connect_timeout_seconds: Optional[float] = Field(default=None, ge=1, le=45)
    call_timeout_seconds: Optional[float] = Field(default=None, ge=1, le=600)


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
    scope: str = "org"
    connect_timeout_seconds: Optional[float] = None
    call_timeout_seconds: Optional[float] = None
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
    # RFC 8707: the server the tokens were issued for, sent again on every refresh.
    resource: Optional[str] = None
    # The OAuth client that issued them; refresh uses exactly that client, the same way.
    client_id: Optional[str] = None
    # Absent on tokens from before it was recorded, all issued with `client_secret_post`.
    token_endpoint_auth_method: Optional[str] = None
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
    # The authorization server's scopes: every application it serves, so shown, never requested.
    scopes_supported: list[str] = Field(default_factory=list)
    issuer: Optional[str] = None
    # From the MCP server's own RFC 9728 metadata, only when it publishes one: its identifier
    # (sent as RFC 8707 `resource`) and the scopes it asks clients to request.
    resource: Optional[str] = None
    resource_scopes: list[str] = Field(default_factory=list)
    # The scopes the server's own 401 asked for (`WWW-Authenticate: Bearer scope=...`): what the
    # MCP spec has a client request first.
    challenge_scopes: list[str] = Field(default_factory=list)
    # None when the authorization server doesn't say.
    token_endpoint_auth_methods_supported: Optional[list[str]] = None
    code_challenge_methods_supported: Optional[list[str]] = None
    # It takes a URL as client id and reads the client from it (`app.agents.mcp.cimd`).
    client_id_metadata_document_supported: bool = False

    @property
    def supports_dcr(self) -> bool:
        return bool(self.registration_endpoint)

    @property
    def refuses_s256_pkce(self) -> bool:
        """It lists its PKCE methods and S256 isn't one. The MCP spec also refuses a server that
        lists none, but some that do PKCE don't say so (Entra ID), so that case still signs in."""
        methods = self.code_challenge_methods_supported
        return methods is not None and "S256" not in methods

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
    # What it was registered for, so a client that no longer fits is replaced. Records from
    # before these fields have neither and are kept as they are.
    redirect_uri: Optional[str] = None
    # RFC 7591: epoch seconds; 0 or absent = never expires.
    client_secret_expires_at: Optional[int] = None
    # What the provider registered it for; records from before this field: `client_secret_post`.
    token_endpoint_auth_method: Optional[str] = None


class MCPToolInfo(MCPCamelModel):
    """A single tool discovered from a connected MCP server, namespaced for the agent loop."""

    name: str
    namespaced_name: str
    description: Optional[str] = None
    input_schema: dict[str, Any] = Field(default_factory=dict)
    # The server's own hints (`readOnlyHint`, `destructiveHint`, `title`, …) as it sent them.
    # A server can claim anything, so they only ever set a starting point.
    annotations: Optional[dict[str, Any]] = None

    # Sent with every listing so the rules dialogs show the starting rule the server applies.
    @computed_field
    @property
    def kind(self) -> ToolKind:
        return tool_kind(self.name, self.annotations)[0]

    @computed_field
    @property
    def kind_source(self) -> KindSource:
        return tool_kind(self.name, self.annotations)[1]


def utcnow() -> datetime:
    """Timezone-aware UTC now — use everywhere instead of naive `datetime.now()`."""
    return datetime.now(timezone.utc)
