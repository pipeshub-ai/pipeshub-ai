"""Data shapes for API Toolsets. camelCase on the wire and in storage, snake_case in Python."""
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from app.agents.mcp.models import OAuthTokens
from app.agents.mcp.tool_kind import ToolKind


class ApiToolsetModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")


class ParameterLocation(str, Enum):
    PATH = "path"
    QUERY = "query"
    HEADER = "header"
    COOKIE = "cookie"


class OperationParameter(ApiToolsetModel):
    """One OpenAPI parameter, with the serialization rules the request builder needs."""

    name: str
    location: ParameterLocation
    required: bool = False
    style: Optional[str] = None
    explode: Optional[bool] = None


class CompiledOperation(ApiToolsetModel):
    """One spec operation, ready to offer as a tool and to turn into an HTTP request.

    `input_schema` is the JSON Schema the model fills: path, query, header and cookie
    parameters by their own names, and the JSON body under `body_argument`. Shared schemas
    sit under its `$defs`, so it is self-contained.
    """

    tool_name: str
    operation_id: Optional[str] = None
    method: str
    path: str
    summary: str = ""
    description: str = ""
    parameters: list[OperationParameter] = Field(default_factory=list)
    body_argument: Optional[str] = None
    body_required: bool = False
    input_schema: dict[str, Any]
    kind: ToolKind = "write"
    requires_auth: bool = True


class AuthType(str, Enum):
    NONE = "none"
    API_KEY = "api_key"
    HTTP_BEARER = "http_bearer"
    HTTP_BASIC = "http_basic"
    OAUTH2_CLIENT_CREDENTIALS = "oauth2_client_credentials"
    OAUTH2_AUTHORIZATION_CODE = "oauth2_authorization_code"


OAUTH2_TYPES = frozenset({AuthType.OAUTH2_CLIENT_CREDENTIALS, AuthType.OAUTH2_AUTHORIZATION_CODE})


class AuthScheme(ApiToolsetModel):
    """One way the API accepts credentials. An OAuth2 scheme with two flows becomes two of these."""

    name: str
    type: AuthType
    description: str = ""
    # API key only: where the key goes ("header", "query" or "cookie") and under what name.
    location: Optional[str] = None
    param_name: Optional[str] = None
    # OAuth2 only. A relative token or authorization URL resolves against the base URL.
    token_url: Optional[str] = None
    authorization_url: Optional[str] = None
    scopes: dict[str, str] = Field(default_factory=dict)


NO_AUTH = AuthScheme(name="none", type=AuthType.NONE)


class ServerOption(ApiToolsetModel):
    """A base URL the spec offers, with its server variables filled with their defaults."""

    url: str
    description: str = ""


class CompiledSpec(ApiToolsetModel):
    title: str
    version: str = ""
    openapi_version: str
    description: str = ""
    servers: list[ServerOption] = Field(default_factory=list)
    auth_schemes: list[AuthScheme] = Field(default_factory=list)
    operations: list[CompiledOperation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    spec_hash: str


class ApiToolsetInstance(ApiToolsetModel):
    """An org's API Toolset: where to call, how to authenticate, and the operations the admin chose.

    Holds no secrets. The uploaded spec itself isn't kept, only what was compiled from it.
    """

    id: str
    org_id: str
    created_by: str
    name: str
    description: str = ""
    spec_title: str
    spec_version: str = ""
    openapi_version: str
    spec_hash: str
    base_url: str
    auth_scheme: AuthScheme
    # OAuth2 scopes to ask for; empty asks for none.
    scopes: list[str] = Field(default_factory=list)
    # One credential the admin stores for everyone, instead of one per user.
    use_shared_credential: bool = False
    timeout_seconds: float
    operations: list[CompiledOperation]
    created_at: int
    updated_at: int


class OAuthClient(ApiToolsetModel):
    """The OAuth app an admin registered with the API, for the authorization-code flow."""

    client_id: str
    client_secret: Optional[str] = None


class ApiToolsetCredentials(ApiToolsetModel):
    """What a user (or the admin, for a shared credential) stored for one toolset.

    Only the fields the toolset's auth scheme uses are set. `oauth` holds the current
    OAuth2 tokens, replaced whenever they are refreshed.
    """

    api_key: Optional[str] = None
    token: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    oauth: Optional[OAuthTokens] = None
