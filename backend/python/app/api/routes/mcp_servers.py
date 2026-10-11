"""MCP Servers API Routes — registry, org-scoped instances, auth, OAuth+DCR, and
live tool discovery for Model Context Protocol servers.

Architecture:
  - Catalog: in-memory templates (`app.agents.mcp.registry`), no secrets.
  - Admin creates "instances" (org-wide, no secrets): /services/mcp/instances/{instanceId}
  - Users authenticate against instances: POST /instances/{id}/authenticate (or OAuth)
  - Per-user/admin credentials: /services/mcp/credentials/{instanceId}/{userId}
  - GET /my-mcp-servers returns a merged view (instances + current user's auth status + tools)

Secrets are never written to plain dicts returned to the caller (OAuth client secrets and
admin-shared credentials are masked); the underlying `ConfigurationService` store encrypts
values at rest exactly like the toolsets/connectors credential paths.
"""
import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal, NamedTuple, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.agents.agent_loop import tool_approvals
from app.agents.constants.mcp_server_constants import (
    OAUTH_STATE_TTL_SECONDS,
    get_mcp_credentials_path,
    get_mcp_dcr_client_path,
    get_mcp_oauth_client_config_path,
    get_mcp_oauth_state_claim_path,
    get_mcp_oauth_state_path,
    get_mcp_shared_dcr_client_path,
    get_mcp_unhashed_oauth_state_claim_path,
    get_mcp_unhashed_oauth_state_path,
)
from app.agents.mcp import cimd, stdio_policy, step_up
from app.agents.mcp import dcr as dcr_module
from app.agents.mcp import lifecycle as mcp_lifecycle
from app.agents.mcp import oauth_client as oauth_client_module
from app.agents.mcp import service as mcp_service
from app.agents.mcp import token_refresh as mcp_token_refresh
from app.agents.mcp.client import MCPConnectionError
from app.agents.mcp.discovery import cached_tools_for_owner, discover_tools_for_owner
from app.agents.mcp.errors import MCPUrlBlockedError
from app.agents.mcp.failure import classify_mcp_failure
from app.agents.mcp.models import (
    DiscoveredOAuthMetadata,
    MCPAuthMode,
    MCPServerInstanceConfig,
    MCPTransport,
    TokenEndpointAuthMethod,
    is_web_url,
)
from app.agents.mcp.naming import assign_namespaces
from app.agents.mcp.registry import MCPRegistry
from app.agents.mcp.service import template_connection_fields
from app.agents.mcp.url_guard import (
    MCPUrlPolicy,
    check_mcp_url_at_save,
    private_network_allowed,
)
from app.agents.mcp.www_authenticate import challenge_scopes
from app.api.middlewares.auth import require_scopes
from app.api.middlewares.caller_role import CallerRoleStatus, fetch_caller_role
from app.config.configuration_service import ConfigurationService
from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import DefaultEndpoints, OAuthScopes
from app.edition_config import (
    build_schedule_refresh_kwargs,
    can_reveal_secrets,
    forbid_inherited_mcp_mutation,
    get_mcp_instance_resolved,
    load_mcp_instances,
    mask_mcp_instance_for_response,
    resolve_instance_owner_config_service,
    resolve_mcp_instances_with_inheritance,
)
from app.utils.env_utils import env_int
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from app.utils.user_messages import action_failed

logger = logging.getLogger(__name__)
DEFAULT_ENDPOINTS_PATH = "/services/endpoints"
# How long a listing (`/my-mcp-servers`, `/agents/{key}` with tools) waits for any one server's
# tools. Pages — the builder, chat, projects — wait on the whole listing, so one slow server must
# not hold them; chat itself still gives each server its full discovery budget.
LISTING_DISCOVERY_BUDGET_SECONDS = 10.0
# A listing discovers at most this many servers at once: each one can be a new connection, a
# token refresh or a local process started.
LISTING_DISCOVERY_CONCURRENCY = 8
# And waits this long for all of them; a server whose turn comes too late is reported as timed
# out rather than holding the page.
LISTING_DISCOVERY_DEADLINE_SECONDS = 15.0
# With less time than this left before the deadline, a server isn't contacted at all: no
# connection or local process is started only to be cut off.
LISTING_DISCOVERY_MIN_SECONDS = 1.0


# ============================================================================
# Request bodies
# ============================================================================


_MAX_SECRET_CHARS = 16_384


def _single_line(value: Optional[str]) -> Optional[str]:
    if value is not None and ("\r" in value or "\n" in value):
        raise ValueError("must be a single line")
    return value


class AuthenticateRequest(BaseModel):
    """Non-OAuth credential payload — fields depend on the instance's authMode. Bounded, and a
    header value is one line, so it can't smuggle a second header."""

    model_config = ConfigDict(populate_by_name=True)

    api_token: Optional[str] = Field(default=None, alias="apiToken", max_length=_MAX_SECRET_CHARS)
    # Accepted for older clients but ignored: the instance decides which header carries the
    # credential, never the person entering it.
    header_name: Optional[str] = Field(default=None, alias="headerName", max_length=128)
    header_value: Optional[str] = Field(default=None, alias="headerValue", max_length=_MAX_SECRET_CHARS)
    # STDIO multi-env (allowlisted against instance requiredEnv/optionalEnv), e.g. Slack.
    env: Optional[dict[Annotated[str, Field(max_length=128)], Annotated[str, Field(max_length=_MAX_SECRET_CHARS)]]] = Field(
        default=None, max_length=64,
    )

    @field_validator("header_value")
    @classmethod
    def _header_value_is_one_line(cls, value: Optional[str]) -> Optional[str]:
        return _single_line(value)


class OAuthClientConfigRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    client_id: str = Field(alias="clientId")
    client_secret: str = Field(alias="clientSecret")


class OAuthDiscoveryRequest(BaseModel):
    """Body for `POST /oauth/discover` — probe a server URL for live OAuth metadata before
    an instance exists (needed by the config panel in create mode)."""

    url: str


# ============================================================================
# Helpers — user context, admin gate, registry, storage
# ============================================================================


def _get_user_context(request: Request) -> dict[str, Any]:
    """Extract and validate user context from request (same convention as toolsets)."""
    user = getattr(request.state, "user", {}) or {}
    user_id = user.get("userId")
    org_id = user.get("orgId")

    if not user_id or not org_id:
        raise HTTPException(
            status_code=HttpStatusCode.UNAUTHORIZED.value,
            detail="Authentication required. Please provide valid user credentials.",
        )

    return {"user_id": user_id, "org_id": org_id}


async def _check_user_is_admin(
    user_id: str,
    request: Request,
    config_service: ConfigurationService,
    *,
    require_known: bool = False,
) -> bool:
    """Admin gate for MCP routes: the live role Node reports for the caller's own token,
    never a client- or proxy-supplied flag. ``user_id`` is kept for existing callers.

    An unconfirmed role counts as "not an administrator", which is the safe answer for a gate.
    With ``require_known`` it is an error instead, for callers where "not an administrator"
    would quietly do something else."""
    del user_id
    role = await fetch_caller_role(request, config_service)
    if require_known and role.status is not CallerRoleStatus.VALID:
        raise HTTPException(
            status_code=HttpStatusCode.SERVICE_UNAVAILABLE.value,
            detail="We couldn't confirm your role right now, so nothing was created. Please try again.",
        )
    return role.is_admin


def _get_config_service(request: Request) -> ConfigurationService:
    """Configuration service is set on `app.state` at connectors-service startup —
    no DI wiring needed for this module (same pattern as `_get_registry`/`_get_graph_provider`
    for the toolset registry / graph provider).
    """
    config_service = getattr(request.app.state, "config_service", None)
    if not config_service:
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail="Configuration service not initialized. Please contact system administrator.",
        )
    return config_service


async def _require_mcp_enabled(request: Request) -> None:
    """FastAPI dependency — rejects the request with 403 when the ``ENABLE_MCP``
    platform feature flag is disabled for the calling org.  Applied to every MCP
    endpoint so that visibility, auth, and runtime are gated consistently."""
    from app.agents.mcp.service import is_mcp_enabled

    if not await is_mcp_enabled(_get_config_service(request)):
        raise HTTPException(
            status_code=HttpStatusCode.FORBIDDEN.value,
            detail="MCP servers are disabled for this organization.",
        )


router = APIRouter(
    prefix="/api/v1/mcp-servers",
    tags=["mcp-servers"],
    dependencies=[Depends(_require_mcp_enabled)],
)


def _get_mcp_registry(request: Request) -> MCPRegistry:
    registry = getattr(request.app.state, "mcp_registry", None)
    if not registry:
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail="MCP registry not initialized. Please contact system administrator.",
        )
    return registry


async def _get_configured_frontend_base_url(config_service: ConfigurationService) -> str:
    """The configured public frontend address, or the default when none is configured. A store
    that can't be read is an error, not "none configured": the default address would make every
    registered OAuth client look registered for the wrong address and get replaced."""
    try:
        endpoints = await config_service.get_config(DEFAULT_ENDPOINTS_PATH, use_cache=False, raise_on_error=True)
    except Exception as e:
        logger.error(f"Could not read the configured frontend endpoint: {e}")
        raise HTTPException(
            status_code=HttpStatusCode.SERVICE_UNAVAILABLE.value,
            detail="The server's public address couldn't be read. Please try again in a moment.",
        ) from e
    frontend = endpoints.get("frontend") if isinstance(endpoints, dict) else None
    frontend_url = frontend.get("publicEndpoint") if isinstance(frontend, dict) else None
    if isinstance(frontend_url, str) and frontend_url.strip():
        return frontend_url.strip().rstrip("/")
    return DefaultEndpoints.FRONTEND_ENDPOINT.value.rstrip("/")


MCP_OAUTH_CALLBACK_PATH = "/mcp-servers/oauth/callback/"


async def _mcp_oauth_redirect_uri(config_service: ConfigurationService) -> str:
    """Where the provider sends the browser back: the configured public frontend address, its
    sub-path included. Never taken from the browser: registered as a shared client's redirect
    URI, a path chosen by whoever signed in first would hold for everyone."""
    return f"{await _get_configured_frontend_base_url(config_service)}{MCP_OAUTH_CALLBACK_PATH}"


async def _displayed_redirect_uri(config_service: ConfigurationService) -> Optional[str]:
    """The redirect URI for the admin form, or None when it can't be read right now (the form
    then shows its own stand-in); only sign-in has to fail on that."""
    try:
        return await _mcp_oauth_redirect_uri(config_service)
    except HTTPException:
        return None


# Treat a client secret as expired a little early, so a sign-in isn't started with one that
# lapses before the code is exchanged.
_DCR_SECRET_EXPIRY_MARGIN_SECONDS = 300


def _dcr_secret_expired(record: dict[str, Any]) -> bool:
    expires_at = record.get("clientSecretExpiresAt")
    if not isinstance(expires_at, int) or expires_at <= 0:
        return False
    return expires_at <= get_epoch_timestamp_in_ms() // 1000 + _DCR_SECRET_EXPIRY_MARGIN_SECONDS


def _dcr_client_fits(record: dict[str, Any], redirect_uri: str) -> bool:
    """A stored registration is still usable for `redirect_uri`. Records from before the
    redirect URI and expiry were kept can't be checked, and are trusted as before."""
    if record.get(mcp_token_refresh.REJECTED_AT_FIELD):
        return False
    registered_for = record.get("redirectUri")
    if registered_for and registered_for != redirect_uri:
        return False
    return not _dcr_secret_expired(record)


# ---------------------------------------------------------------------------
# Instance storage
# ---------------------------------------------------------------------------



_load_org_instances = load_mcp_instances
_get_org_instance = get_mcp_instance_resolved

MAX_PERSONAL_INSTANCES_ENV = "MCP_MAX_PERSONAL_INSTANCES"
DEFAULT_MAX_PERSONAL_INSTANCES = 25


def _max_personal_instances() -> int:
    return env_int(MAX_PERSONAL_INSTANCES_ENV, DEFAULT_MAX_PERSONAL_INSTANCES, lo=0)


async def _require_manage_access(
    instance: dict[str, Any],
    user_context: dict[str, Any],
    request: Request,
    config_service: ConfigurationService,
    action: str,
    *,
    admin_may_manage_personal: bool = False,
) -> None:
    """Org instances are managed by administrators, a personal instance by its owner.
    Administrators may additionally delete a user's personal instance, never use it."""
    if mcp_service.is_personal(instance) and instance.get("createdBy") == user_context["user_id"]:
        return
    if (not mcp_service.is_personal(instance) or admin_may_manage_personal) and await _check_user_is_admin(
        user_context["user_id"], request, config_service,
    ):
        return
    raise HTTPException(status_code=HttpStatusCode.FORBIDDEN.value, detail=f"Only administrators can {action} this MCP server.")


def _validate_personal_instance(payload: MCPServerInstanceConfig) -> None:
    """Any user can create a personal instance, so it gets none of the server-side reach an
    administrator's may have: no local process, no shared credential."""
    if payload.transport == MCPTransport.STDIO:
        raise _bad_request("Personal MCP servers can't run a local command (STDIO). Use a remote server URL instead.")
    if payload.use_admin_auth:
        raise _bad_request("Personal MCP servers can't share an administrator credential.")


def _bad_request(detail: str) -> HTTPException:
    return HTTPException(status_code=HttpStatusCode.BAD_REQUEST.value, detail=detail)


def _refuse_replaced_template(payload: MCPServerInstanceConfig, registry: MCPRegistry) -> None:
    """A replaced catalog entry makes no new servers; the ones already made from it keep running."""
    template = registry.get_template(payload.type_id) if payload.type_id else None
    if template is None or not template.replaced_by:
        return
    replacement = registry.get_template(template.replaced_by)
    instead = f" Add {replacement.display_name} instead." if replacement else ""
    raise _bad_request(f"{template.display_name} is no longer offered for new servers.{instead}")


def _bad_request(detail: str) -> HTTPException:
    return HTTPException(status_code=HttpStatusCode.BAD_REQUEST.value, detail=detail)


def _validate_instance_config(payload: MCPServerInstanceConfig, registry: MCPRegistry) -> None:
    """Cross-field validation the Pydantic model alone can't express, plus the STDIO
    launch policy (`app.agents.mcp.stdio_policy`). Shared by create and update."""
    if payload.use_admin_auth and payload.auth_mode not in (MCPAuthMode.API_TOKEN, MCPAuthMode.HEADERS):
        raise _bad_request("A shared admin credential applies only to API token or header sign-in.")
    if payload.type_id:
        template = registry.get_template(payload.type_id)
        if not template:
            raise _bad_request(f"Unknown catalog type_id: {payload.type_id}")
        # Everything else about a catalog server comes from its template (see
        # `_build_instance_record`); a different transport would pair the catalog's name
        # with a command or URL the request chose.
        if payload.transport != template.transport:
            raise _bad_request(
                f"Catalog server '{template.type_id}' uses the {template.transport.value} transport; "
                "it cannot be changed."
            )
        if (payload.command and payload.command != template.command) or (
            payload.args and list(payload.args) != list(template.args)
        ):
            raise _bad_request(
                f"Catalog server '{template.type_id}' always runs its catalog command; "
                "command and args cannot be overridden. Add a custom server instead."
            )
        if template.supported_auth_modes and payload.auth_mode not in template.supported_auth_modes:
            raise _bad_request(f"{template.display_name} does not support the {payload.auth_mode.value} auth mode.")
        return

    # Custom server — validate transport-specific required fields.
    if payload.transport == MCPTransport.STDIO:
        try:
            stdio_policy.check_env_names(payload.required_env)
        except stdio_policy.StdioPolicyError as e:
            raise _bad_request(str(e)) from e
        if not stdio_policy.custom_stdio_allowed():
            raise HTTPException(
                status_code=HttpStatusCode.FORBIDDEN.value,
                detail=stdio_policy.CUSTOM_STDIO_DISABLED_MESSAGE,
            )
        try:
            stdio_policy.check_stdio_command(payload.command, payload.args)
        except stdio_policy.StdioPolicyError as e:
            raise _bad_request(str(e)) from e
    elif not payload.url:
        raise _bad_request("A custom SSE/streamable_http MCP server requires a url.")


def _assert_instance_urls_allowed(record: dict[str, Any]) -> None:
    """The server URL and token URL are fetched server-side; `authorizationUrl` only ever
    opens in the user's browser, so it is not checked here."""
    allow_private = private_network_allowed(record)
    urls = [record.get("tokenUrl")]
    if record.get("transport") != MCPTransport.STDIO.value:
        urls.append(record.get("url"))
    for url in filter(None, urls):
        try:
            check_mcp_url_at_save(url, allow_private=allow_private)
        except MCPUrlBlockedError as e:
            raise HTTPException(status_code=HttpStatusCode.BAD_REQUEST.value, detail=str(e)) from e


def _build_instance_record(
    payload: MCPServerInstanceConfig,
    instance_id: str,
    org_id: str,
    user_id: str,
    registry: MCPRegistry,
    existing: Optional[dict[str, Any]] = None,
    *,
    scope: str = mcp_service.SCOPE_ORG,
) -> dict[str, Any]:
    """Merge a validated request body into the stored instance record (camelCase, no secrets)."""
    template = registry.get_template(payload.type_id) if payload.type_id else None
    now_ms = get_epoch_timestamp_in_ms()

    record: dict[str, Any] = {
        "_id": instance_id,
        "orgId": org_id,
        "createdBy": existing.get("createdBy") if existing else user_id,
        "name": payload.name,
        "typeId": payload.type_id,
        "transport": (template.transport if template else payload.transport).value,
        "authMode": payload.auth_mode.value,
        "useAdminAuth": payload.use_admin_auth,
        "description": payload.description,
        "headerName": payload.header_name,
        "isCustom": payload.type_id is None,
        "scope": scope,
        "connectTimeoutSeconds": payload.connect_timeout_seconds,
        "callTimeoutSeconds": payload.call_timeout_seconds,
        mcp_service.SHARED_CREDENTIAL_SLOT_MARKER: True,
        "createdAt": existing.get("createdAt") if existing else now_ms,
        "updatedAt": now_ms,
    }
    if template:
        # A catalog server's launch and OAuth settings are fixed by its template: taking
        # them from the request would let a "GitHub" instance run any command or URL.
        record.update(template_connection_fields(template))
    else:
        record.update({
            "command": payload.command,
            "args": list(payload.args or []),
            "requiredEnv": list(payload.required_env or []),
            "optionalEnv": [],
            "url": payload.url,
            "authorizationUrl": payload.authorization_url,
            "tokenUrl": payload.token_url,
            "scopes": list(payload.scopes or []),
        })
    return record


# ---------------------------------------------------------------------------
# Effective auth resolution (shared by discovery + the agent-loop runtime) —
# implementations live in `app.agents.mcp.service` (see that module's docstring);
# aliased here under the original private names for route-internal call sites and
# existing tests.
# ---------------------------------------------------------------------------

_resolve_effective_user_auth = mcp_service.resolve_effective_user_auth
_instance_config_model_from_dict = mcp_service.instance_config_from_dict
_credentials_to_discovery_dict = mcp_service.credentials_to_discovery_dict




# ============================================================================
# Catalog
# ============================================================================


@router.get("/catalog", dependencies=[Depends(require_scopes(OAuthScopes.MCP_READ))])
async def list_catalog(
    request: Request,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    search: Optional[str] = Query(default=None),
) -> dict[str, Any]:
    _get_user_context(request)
    registry = _get_mcp_registry(request)

    from app.agents.mcp.catalog import paginate, search_templates

    templates = search_templates(registry.list_templates(), search)
    page_items, total = paginate(templates, page, limit)

    return {
        "templates": [t.model_dump(by_alias=True) for t in page_items],
        "total": total,
        "page": page,
        "limit": limit,
        "customStdioAllowed": stdio_policy.custom_stdio_allowed(),
    }


@router.get("/catalog/{type_id}", dependencies=[Depends(require_scopes(OAuthScopes.MCP_READ))])
async def get_catalog_template(request: Request, type_id: str) -> dict[str, Any]:
    _get_user_context(request)
    registry = _get_mcp_registry(request)
    template = registry.get_template(type_id)
    if not template:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail=f"Unknown MCP server type: {type_id}")
    return template.model_dump(by_alias=True)


# ============================================================================
# Instances (admin-managed)
# ============================================================================


@router.get("/instances", dependencies=[Depends(require_scopes(OAuthScopes.MCP_READ))])
async def list_instances(
    request: Request,
    include_personal: bool = Query(default=False, alias="includePersonal"),
) -> dict[str, Any]:
    """The org's instances; with `includePersonal`, a summary of every user's personal instance
    too, so an administrator can review and delete them (see `_personal_instance_summary`)."""
    config_service = _get_config_service(request)
    user_context = _get_user_context(request)
    is_admin = await _check_user_is_admin(user_context["user_id"], request, config_service)
    if not is_admin:
        raise HTTPException(status_code=HttpStatusCode.FORBIDDEN.value, detail="Only administrators can list MCP server instances.")

    instances = await resolve_mcp_instances_with_inheritance(config_service, user_context["org_id"])

    async def _mark_oauth_client_config(instance: dict[str, Any]) -> None:
        instance["scope"] = mcp_service.instance_scope(instance)
        owner_svc = await resolve_instance_owner_config_service(instance["_id"], config_service) or config_service
        instance["hasOAuthClientConfig"] = bool(
            await owner_svc.get_config(get_mcp_oauth_client_config_path(instance["_id"]), default=None)
        )
        instance["disabledReason"] = stdio_policy.instance_disabled_reason(instance)

    await asyncio.gather(*[_mark_oauth_client_config(i) for i in instances])
    reveal = can_reveal_secrets(request)
    instances = [mask_mcp_instance_for_response(i, reveal=reveal) for i in instances]
    if include_personal:
        # A summary only, never how to reach the server, so there is nothing to reveal.
        personal = await mcp_service.load_personal_instances_for_admin(config_service, user_context["org_id"])
        instances.extend(_personal_instance_summary(i) for i in personal)
    return {"instances": instances}


# What an administrator may see of a user's own server: enough to recognise and delete it.
# Never how to reach it — a URL or argument can carry the user's credential.
_PERSONAL_SUMMARY_FIELDS = (
    "_id", "orgId", "createdBy", "name", "typeId", "transport", "authMode", "isCustom", "createdAt", "updatedAt",
)


def _personal_instance_summary(instance: dict[str, Any]) -> dict[str, Any]:
    return {**{k: instance.get(k) for k in _PERSONAL_SUMMARY_FIELDS}, "scope": mcp_service.SCOPE_PERSONAL}


# What a member sees of an org server is enough to connect to it and use it; how to reach it
# stays with administrators, because a URL or argument can carry a credential.
_ADMIN_ONLY_CONNECTION_FIELDS = frozenset({"url", "command", "args", "authorizationUrl", "tokenUrl", "scopes"})


def _without_connection_details(instance: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in instance.items() if k not in _ADMIN_ONLY_CONNECTION_FIELDS}


@router.post(
    "/instances",
    status_code=HttpStatusCode.CREATED.value,
    dependencies=[Depends(require_scopes(OAuthScopes.MCP_WRITE))],
)
async def create_instance(
    request: Request,
    payload: MCPServerInstanceConfig,
) -> dict[str, Any]:
    config_service = _get_config_service(request)
    user_context = _get_user_context(request)
    if payload.scope not in (None, mcp_service.SCOPE_ORG, mcp_service.SCOPE_PERSONAL):
        raise _bad_request("scope must be 'org' or 'personal'.")
    # Only administrators create org-wide instances; everyone else's are their own. A request
    # for a personal server needs no role. Otherwise an unconfirmed role is an error: treating it
    # as "member" made an administrator's new server personal, and scope can't change later.
    if payload.scope == mcp_service.SCOPE_PERSONAL:
        scope = mcp_service.SCOPE_PERSONAL
    else:
        is_admin = await _check_user_is_admin(user_context["user_id"], request, config_service, require_known=True)
        scope = mcp_service.SCOPE_ORG if is_admin else mcp_service.SCOPE_PERSONAL

    registry = _get_mcp_registry(request)
    _validate_instance_config(payload, registry)
    _refuse_replaced_template(payload, registry)
    if scope == mcp_service.SCOPE_PERSONAL:
        _validate_personal_instance(payload)
        existing = await mcp_service.load_user_instances(config_service, user_context["org_id"], user_context["user_id"])
        if len(existing) >= _max_personal_instances():
            raise HTTPException(
                status_code=HttpStatusCode.CONFLICT.value,
                detail=f"You can have at most {_max_personal_instances()} personal MCP servers. Remove one first.",
            )

    instance_id = str(uuid.uuid4())
    record = _build_instance_record(
        payload, instance_id, user_context["org_id"], user_context["user_id"], registry, scope=scope,
    )
    _assert_instance_urls_allowed(record)

    success = await config_service.set_config(mcp_service.instance_record_path(record), record)
    if not success:
        raise HTTPException(status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail="Failed to create MCP server instance.")

    return record


@router.get("/instances/{instance_id}", dependencies=[Depends(require_scopes(OAuthScopes.MCP_READ))])
async def get_instance(request: Request, instance_id: str) -> dict[str, Any]:
    config_service = _get_config_service(request)
    user_context = _get_user_context(request)
    instance = await _get_org_instance(instance_id, config_service, user_context["org_id"], user_context["user_id"])
    if not instance:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="MCP server instance not found.")
    await _require_manage_access(instance, user_context, request, config_service, "view")

    instance["scope"] = mcp_service.instance_scope(instance)
    owner_svc = await resolve_instance_owner_config_service(instance_id, config_service) or config_service
    instance["hasOAuthClientConfig"] = bool(
        await owner_svc.get_config(get_mcp_oauth_client_config_path(instance_id), default=None)
    )
    instance["disabledReason"] = stdio_policy.instance_disabled_reason(instance)
    return instance


@router.put("/instances/{instance_id}", dependencies=[Depends(require_scopes(OAuthScopes.MCP_WRITE))])
async def update_instance(
    request: Request,
    instance_id: str,
    payload: MCPServerInstanceConfig,
) -> dict[str, Any]:
    config_service = _get_config_service(request)
    user_context = _get_user_context(request)
    existing = await _get_org_instance(instance_id, config_service, user_context["org_id"], user_context["user_id"])
    if not existing:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="MCP server instance not found.")
    await _require_manage_access(existing, user_context, request, config_service, "update")
    forbid_inherited_mcp_mutation(existing)

    registry = _get_mcp_registry(request)
    _validate_instance_config(payload, registry)
    if payload.type_id != existing.get("typeId"):
        _refuse_replaced_template(payload, registry)
    # Scope is fixed at creation: moving an instance between a user and the org would change
    # who can reach the credentials stored against it.
    scope = mcp_service.instance_scope(existing)
    if scope == mcp_service.SCOPE_PERSONAL:
        _validate_personal_instance(payload)
    record = _build_instance_record(
        payload, instance_id, user_context["org_id"], user_context["user_id"], registry, existing=existing, scope=scope,
    )
    _assert_instance_urls_allowed(record)

    # Stored tokens, env values and refresh tokens were given for the old target; sending
    # them to a new URL, token endpoint or program would hand them to someone else. Purged
    # before the new target is saved, so no request can pair the two.
    credentials_reset = _connection_target_changed(existing, record)
    if credentials_reset:
        await _purge_instance_credentials(config_service, instance_id)
    else:
        credentials_reset = await _settle_shared_credential(config_service, existing, record)

    success = await config_service.set_config(mcp_service.instance_record_path(record), record)
    if not success:
        raise HTTPException(status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail="Failed to update MCP server instance.")
    return {**record, "credentialsReset": credentials_reset}


async def _settle_shared_credential(
    config_service: ConfigurationService, existing: dict[str, Any], record: dict[str, Any],
) -> bool:
    """Keep the shared admin credential in step with `useAdminAuth`. Returns whether it was removed.

    Switching it on or off empties the shared slot: on, so an administrator enters a credential
    meant for everyone (it never becomes the creator's personal one); off, because nobody should
    keep acting through it. Personal credentials are never touched.
    """
    shared_before = mcp_service.uses_shared_credential(existing)
    shared_after = mcp_service.uses_shared_credential(record)
    if shared_before and shared_after:
        # This edit marks the record as using the shared slot, so a credential still at the
        # creator's legacy path has to move first or nobody would read it again.
        await mcp_service.adopt_legacy_shared_credential(existing, config_service)
        return False
    if shared_before == shared_after:
        return False
    shared_path = get_mcp_credentials_path(record["_id"], mcp_service.SHARED_CREDENTIAL_OWNER)
    removed = isinstance(await config_service.get_config(shared_path, default=None, use_cache=False), dict)
    await config_service.delete_config(shared_path)
    return removed and shared_before


_CONNECTION_TARGET_FIELDS = ("typeId", "transport", "url", "command", "args", "authMode", "tokenUrl")


def _connection_target_changed(existing: dict[str, Any], record: dict[str, Any]) -> bool:
    fields = _CONNECTION_TARGET_FIELDS
    if not record.get("isCustom") and existing.get("typeId") == record.get("typeId"):
        # Same catalog type: runtime connects to the template's target whatever the stored copy
        # says, so a copy that trails a release moves no credential. A new auth mode does.
        fields = ("authMode",)
    # `or None` so an older record's missing key and a new empty list compare equal.
    return any((existing.get(field) or None) != (record.get(field) or None) for field in fields)


async def _purge_instance_credentials(config_service: ConfigurationService, instance_id: str) -> None:
    await mcp_lifecycle.purge_instance_credentials(config_service, instance_id)


async def _assert_instance_not_in_use(request: Request, instance: dict[str, Any]) -> None:
    """Deleting an attached instance would make every chat with those agents fail. Fails
    closed, like toolsets: an unverifiable check blocks the delete."""
    from app.api.routes.toolsets import MAX_AGENT_NAMES_DISPLAY, _get_graph_provider

    try:
        agent_names = await _get_graph_provider(request).check_mcp_instance_in_use(instance["_id"])
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to check agent usage for MCP instance {instance['_id']}: {e}", exc_info=True)
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail="Cannot delete the MCP server: unable to verify whether agents use it. Please try again.",
        ) from e
    if not agent_names:
        return
    shown = ", ".join(f"'{name}'" for name in agent_names[:MAX_AGENT_NAMES_DISPLAY])
    if len(agent_names) > MAX_AGENT_NAMES_DISPLAY:
        shown += f" and {len(agent_names) - MAX_AGENT_NAMES_DISPLAY} more"
    raise HTTPException(
        status_code=HttpStatusCode.CONFLICT.value,
        detail=f"Cannot delete MCP server '{instance.get('name')}': it is used by {shown}. Remove it from those agents first.",
    )


@router.delete("/instances/{instance_id}", dependencies=[Depends(require_scopes(OAuthScopes.MCP_DELETE))])
async def delete_instance(request: Request, instance_id: str) -> dict[str, Any]:
    config_service = _get_config_service(request)
    user_context = _get_user_context(request)
    instance = await _get_org_instance(instance_id, config_service, user_context["org_id"], user_context["user_id"])
    if not instance and await _check_user_is_admin(user_context["user_id"], request, config_service):
        instance = await mcp_service.find_personal_instance_for_admin(instance_id, config_service, user_context["org_id"])
    if not instance:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="MCP server instance not found.")
    await _require_manage_access(instance, user_context, request, config_service, "delete", admin_may_manage_personal=True)
    forbid_inherited_mcp_mutation(instance)
    await _assert_instance_not_in_use(request, instance)

    # The record goes first and must go: if it stays, the server is still listed and usable.
    # What hangs off it is removed best-effort.
    if not await config_service.delete_config(mcp_service.instance_record_path(instance)):
        raise HTTPException(status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail="Failed to delete MCP server instance.")
    await mcp_lifecycle.delete_instance_data(config_service, instance)

    return {"success": True, "_id": instance_id}


# ============================================================================
# Auth — API token / headers
# ============================================================================


def _assert_non_oauth_auth_mode(instance: dict[str, Any]) -> None:
    if instance.get("authMode") == MCPAuthMode.OAUTH.value:
        raise HTTPException(
            status_code=HttpStatusCode.BAD_REQUEST.value,
            detail="This instance uses OAuth — use /oauth/authorize instead.",
        )
    if instance.get("authMode") == MCPAuthMode.NONE.value:
        raise HTTPException(status_code=HttpStatusCode.BAD_REQUEST.value, detail="This instance does not require authentication.")


async def _assert_admin_owns_shared_credential(
    instance: dict[str, Any], user_context: dict[str, Any], request: Request, config_service: ConfigurationService
) -> None:
    """Shared `useAdminAuth` guard — only administrators may manage the one shared credential
    every other user/agent on this instance relies on.
    """
    if mcp_service.uses_shared_credential(instance):
        is_admin = await _check_user_is_admin(user_context["user_id"], request, config_service)
        if not is_admin:
            raise HTTPException(
                status_code=HttpStatusCode.FORBIDDEN.value,
                detail="This instance uses a shared admin credential; only administrators can manage it.",
            )


async def _assert_can_write_credentials(
    instance: dict[str, Any], user_context: dict[str, Any], request: Request, config_service: ConfigurationService
) -> None:
    """Shared guard for authenticate/credentials writes — `PUT /credentials` must enforce the
    same useAdminAuth + oauth-mode checks as `POST /authenticate` (the reference PR let
    `PUT /credentials` bypass both). Only applies to writing raw apiToken/header credentials;
    clearing credentials (disconnect) is valid for any auth mode, see
    `_assert_admin_owns_shared_credential`.
    """
    _assert_non_oauth_auth_mode(instance)
    await _assert_admin_owns_shared_credential(instance, user_context, request, config_service)


async def _shared_aware_credential_path(
    instance: dict[str, Any], caller_id: str, config_service: ConfigurationService,
) -> str:
    """The credential path a disconnect clears. A shared credential still at the creator's
    legacy path moves into the shared slot first, so deleting the slot really removes it."""
    owner_id = mcp_service.credential_owner_id(instance, caller_id)
    if owner_id == mcp_service.SHARED_CREDENTIAL_OWNER:
        await mcp_service.adopt_legacy_shared_credential(instance, config_service)
    return get_mcp_credentials_path(instance["_id"], owner_id)


def _filter_stdio_env(
    instance: dict[str, Any],
    env_payload: Optional[dict[str, str]],
) -> dict[str, str]:
    """Keep only allowlisted, non-empty env entries from the authenticate payload."""
    if not env_payload:
        return {}
    allowed = set(instance.get("requiredEnv") or []) | set(instance.get("optionalEnv") or [])
    filtered: dict[str, str] = {}
    for key, value in env_payload.items():
        if key not in allowed or value is None:
            continue
        trimmed = str(value).strip()
        if trimmed:
            filtered[key] = trimmed
    return filtered


def _build_credential_record(instance: dict[str, Any], payload: AuthenticateRequest, user_id: str, org_id: str) -> dict[str, Any]:
    auth_mode = instance.get("authMode")
    credentials: dict[str, Any] = {}
    if auth_mode == MCPAuthMode.API_TOKEN.value:
        filtered_env = _filter_stdio_env(instance, payload.env)
        if payload.api_token:
            credentials["apiToken"] = payload.api_token
        if filtered_env:
            credentials["env"] = filtered_env

        required_env = list(instance.get("requiredEnv") or [])
        if instance.get("transport") == MCPTransport.STDIO.value and required_env:
            effective = dict(filtered_env)
            if payload.api_token:
                effective.setdefault(required_env[0], payload.api_token)
            missing = [key for key in required_env if not effective.get(key)]
            if missing:
                raise HTTPException(
                    status_code=HttpStatusCode.BAD_REQUEST.value,
                    detail=f"Missing required credentials: {', '.join(missing)}",
                )
        elif not payload.api_token:
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail="apiToken is required for this instance.",
            )
    elif auth_mode == MCPAuthMode.HEADERS.value:
        if not payload.header_value:
            raise HTTPException(status_code=HttpStatusCode.BAD_REQUEST.value, detail="headerValue is required for this instance.")
        credentials = {
            "headerName": instance.get("headerName") or "Authorization",
            "headerValue": payload.header_value,
        }

    now_ms = get_epoch_timestamp_in_ms()
    return {
        "instanceId": instance["_id"],
        "userId": user_id,
        "orgId": org_id,
        "authMode": auth_mode,
        "isAuthenticated": True,
        "credentials": credentials,
        "updatedAt": now_ms,
        # A new record, unlike a token refresh: tools cached with the old one are a miss.
        "connectedAt": now_ms,
    }


@router.post(
    "/instances/{instance_id}/authenticate",
    dependencies=[Depends(require_scopes(OAuthScopes.MCP_WRITE))],
)
async def authenticate_instance(
    request: Request,
    instance_id: str,
    payload: AuthenticateRequest,
) -> dict[str, Any]:
    config_service = _get_config_service(request)
    user_context = _get_user_context(request)
    org_id, user_id = user_context["org_id"], user_context["user_id"]

    instance = await _get_org_instance(instance_id, config_service, user_context["org_id"], user_context["user_id"])
    if not instance:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="MCP server instance not found.")

    await _assert_can_write_credentials(instance, user_context, request, config_service)
    owner_id = mcp_service.credential_owner_id(instance, user_id)
    record = _build_credential_record(instance, payload, owner_id, org_id)

    success = await config_service.set_config(get_mcp_credentials_path(instance_id, owner_id), record)
    if not success:
        raise HTTPException(status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail="Failed to save credentials.")
    return {"success": True, "isAuthenticated": True}


@router.put(
    "/instances/{instance_id}/credentials",
    dependencies=[Depends(require_scopes(OAuthScopes.MCP_WRITE))],
)
async def update_credentials(
    request: Request,
    instance_id: str,
    payload: AuthenticateRequest,
) -> dict[str, Any]:
    # Reuses the exact same guard + record-building as authenticate — the reference PR
    # duplicated (and under-guarded) this path.
    return await authenticate_instance(request, instance_id, payload)


@router.delete(
    "/instances/{instance_id}/credentials",
    dependencies=[Depends(require_scopes(OAuthScopes.MCP_DELETE))],
)
async def remove_credentials(request: Request, instance_id: str) -> dict[str, Any]:
    """Disconnect the caller's stored credentials/tokens, regardless of auth mode — an
    OAuth-authenticated instance must be disconnectable the same way an API-token one is.
    """
    config_service = _get_config_service(request)
    user_context = _get_user_context(request)
    user_id = user_context["user_id"]

    instance = await _get_org_instance(instance_id, config_service, user_context["org_id"], user_context["user_id"])
    if not instance:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="MCP server instance not found.")
    await _assert_admin_owns_shared_credential(instance, user_context, request, config_service)

    cred_path = await _shared_aware_credential_path(instance, user_id, config_service)
    await config_service.delete_config(cred_path)
    if instance.get("authMode") == MCPAuthMode.OAUTH.value:
        # Mirrors `reauthenticate_instance` — only the legacy per-owner DCR client is this
        # caller's alone to clear; the shared per-instance DCR client survives for other
        # authenticated users/agents on this instance.
        await config_service.delete_config(get_mcp_dcr_client_path(instance_id, user_id))

    from app.connectors.core.base.token_service.startup_service import startup_service

    refresh_service = startup_service.get_mcp_token_refresh_service()
    if refresh_service:
        refresh_service.cancel_refresh_task(cred_path)

    return {"success": True}


@router.post(
    "/instances/{instance_id}/auto-authenticate",
    dependencies=[Depends(require_scopes(OAuthScopes.MCP_WRITE))],
)
async def auto_authenticate_instance(request: Request, instance_id: str) -> dict[str, Any]:
    """Adopt the admin's shared credential for a `useAdminAuth` instance (verifies it exists first)."""
    config_service = _get_config_service(request)
    user_context = _get_user_context(request)

    instance = await _get_org_instance(instance_id, config_service, user_context["org_id"], user_context["user_id"])
    if not instance:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="MCP server instance not found.")
    if not mcp_service.uses_shared_credential(instance):
        raise HTTPException(status_code=HttpStatusCode.BAD_REQUEST.value, detail="This instance does not use a shared admin credential.")

    admin_record = await mcp_service.resolve_effective_user_auth(instance, user_context["user_id"], config_service)
    if not isinstance(admin_record, dict) or not admin_record.get("isAuthenticated"):
        raise HTTPException(
            status_code=HttpStatusCode.CONFLICT.value,
            detail="The administrator has not configured shared credentials for this instance yet.",
        )
    return {"success": True, "isAuthenticated": True}


@router.post(
    "/instances/{instance_id}/reauthenticate",
    dependencies=[Depends(require_scopes(OAuthScopes.MCP_WRITE))],
)
async def reauthenticate_instance(request: Request, instance_id: str) -> dict[str, Any]:
    """Clear the caller's stored credentials/tokens for this instance, forcing re-auth."""
    config_service = _get_config_service(request)
    user_context = _get_user_context(request)
    user_id = user_context["user_id"]

    instance = await _get_org_instance(instance_id, config_service, user_context["org_id"], user_context["user_id"])
    if not instance:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="MCP server instance not found.")
    await _assert_admin_owns_shared_credential(instance, user_context, request, config_service)

    cred_path = await _shared_aware_credential_path(instance, user_id, config_service)
    await config_service.delete_config(cred_path)
    # Only the legacy per-owner DCR client is this caller's alone to clear — the shared
    # per-instance client (if this instance uses one) is relied on by every other
    # authenticated user/agent and must survive a single caller's reauthenticate.
    await config_service.delete_config(get_mcp_dcr_client_path(instance_id, user_id))

    from app.connectors.core.base.token_service.startup_service import startup_service

    refresh_service = startup_service.get_mcp_token_refresh_service()
    if refresh_service:
        refresh_service.cancel_refresh_task(cred_path)

    return {"success": True}


# ============================================================================
# OAuth + DCR
# ============================================================================


def _resolve_oauth_endpoints(
    instance: dict[str, Any], discovered: Optional[DiscoveredOAuthMetadata]
) -> tuple[Optional[str], Optional[str]]:
    """Pick the authorization/token endpoints to use.

    Catalog instances copy `authorizationUrl`/`tokenUrl` verbatim from the template at
    creation time and an admin never edits them directly — for those, a live discovery
    result is authoritative (this is what fixes Notion/Atlassian, whose templates used to
    point at their *direct*-OAuth endpoints, not the ones fronting their MCP servers).

    Custom instances expose these as admin-editable fields — for those, the admin's
    explicit value wins, with discovery only filling in what's left blank.
    """
    stored_authorization_url = instance.get("authorizationUrl")
    stored_token_url = instance.get("tokenUrl")
    discovered_authorization_url = discovered.authorization_endpoint if discovered else None
    discovered_token_url = discovered.token_endpoint if discovered else None

    if instance.get("isCustom"):
        return (
            stored_authorization_url or discovered_authorization_url,
            stored_token_url or discovered_token_url,
        )
    return (
        discovered_authorization_url or stored_authorization_url,
        discovered_token_url or stored_token_url,
    )


class _SignInClient(NamedTuple):
    client_id: str
    client_secret: Optional[str]
    is_dcr: bool
    auth_method: str
    # PipesHub's Client ID Metadata Document URL as the client (`app.agents.mcp.cimd`).
    metadata_document: bool = False


async def _client_metadata_document(
    config_service: ConfigurationService, discovered: Optional[DiscoveredOAuthMetadata], redirect_uri: str,
) -> Optional[str]:
    """PipesHub's Client ID Metadata Document URL, when this sign-in can use it as its client:
    the server supports it and hasn't refused it, it isn't switched off, and the public address
    is https and serves it."""
    if not cimd.enabled() or discovered is None or not cimd.supported_by(discovered):
        return None
    if await cimd.refused_by(config_service, cimd.server_key(discovered)):
        return None
    frontend = await _get_configured_frontend_base_url(config_service)
    if not frontend.lower().startswith("https://"):
        return None
    url = cimd.client_metadata_url(frontend)
    return url if await cimd.document_is_served(url, redirect_uri) else None


async def _resolve_oauth_client(
    config_service: ConfigurationService,
    instance_id: str,
    owner_id: str,
    redirect_uri: str,
    discovered: Optional[DiscoveredOAuthMetadata],
    authorization_url: Optional[str],
    token_url: Optional[str],
    *,
    policy: MCPUrlPolicy,
) -> _SignInClient:
    """The client a new sign-in uses: the admin's static app, then a legacy per-owner DCR client,
    then the shared DCR client while it still fits (`_dcr_client_fits`), then PipesHub's client
    metadata document where the server takes one, then a new registration into the shared path.
    A client that works is never replaced by the document; it only saves registering one.

    The tokens record which client issued them (`OAuthTokens.client_id`) and how it
    authenticated, so refresh uses that exact client whatever is configured later; that's
    what lets a static app an administrator adds later take over new sign-ins.
    """
    static_client = await mcp_token_refresh.static_oauth_client(config_service, instance_id)
    if static_client:
        auth_method = dcr_module.preferred_token_auth_method(
            discovered.token_endpoint_auth_methods_supported if discovered else None,
        )
        return _SignInClient(static_client["clientId"], static_client.get("clientSecret"), False, auth_method)

    legacy_dcr = await config_service.get_config(get_mcp_dcr_client_path(instance_id, owner_id), default=None)
    if isinstance(legacy_dcr, dict) and legacy_dcr.get("clientId"):
        return _SignInClient(legacy_dcr["clientId"], legacy_dcr.get("clientSecret"), True, _registered_auth_method(legacy_dcr))

    shared_path = get_mcp_shared_dcr_client_path(instance_id)
    shared_dcr = await config_service.get_config(shared_path, default=None, use_cache=False)
    shared_exists = isinstance(shared_dcr, dict) and bool(shared_dcr.get("clientId"))
    if shared_exists and _dcr_client_fits(shared_dcr, redirect_uri):
        return _SignInClient(shared_dcr["clientId"], shared_dcr.get("clientSecret"), True, _registered_auth_method(shared_dcr))

    if document := await _client_metadata_document(config_service, discovered, redirect_uri):
        return _SignInClient(document, None, False, TokenEndpointAuthMethod.NONE.value, metadata_document=True)

    if discovered and discovered.registration_endpoint:
        try:
            dcr_client = await dcr_module.register_dynamic_client(
                registration_endpoint=discovered.registration_endpoint,
                redirect_uri=redirect_uri,
                authorization_url=authorization_url or discovered.authorization_endpoint or "",
                token_url=token_url or discovered.token_endpoint or "",
                policy=policy,
                auth_methods_supported=discovered.token_endpoint_auth_methods_supported,
            )
        except dcr_module.DCRError as e:
            # `DCRError`'s message embeds the registration endpoint's raw response body
            # (see `dcr.register_dynamic_client`) — log it server-side only; a third-party
            # AS's response text is not something to echo back to our own API caller.
            logger.error(f"MCP dynamic client registration failed for instance {instance_id}: {e}")
            raise HTTPException(
                status_code=HttpStatusCode.BAD_GATEWAY.value,
                detail="Dynamic client registration with the OAuth provider failed. Ask an administrator to configure a static OAuth app instead.",
            ) from e
        # One shared registration per instance, not one per owner — every subsequent
        # owner (any user/agent) reuses this same client instead of registering their own.
        record = dcr_client.model_dump(by_alias=True)
        if shared_exists:
            logger.info(f"Replacing the registered OAuth client for MCP instance {instance_id}")
            if not _dcr_secret_expired(shared_dcr):
                # It no longer fits new sign-ins, but the tokens it issued may still refresh.
                record[mcp_token_refresh.PREVIOUS_CLIENT_FIELD] = {
                    key: value for key, value in shared_dcr.items() if key != mcp_token_refresh.PREVIOUS_CLIENT_FIELD
                }
            await config_service.set_config(shared_path, record)
        elif not await config_service.create_config_if_absent(shared_path, record):
            # Someone registered at the same moment: use theirs, so there's still one client.
            winner = await config_service.get_config(shared_path, default=None, use_cache=False)
            if isinstance(winner, dict) and winner.get("clientId"):
                return _SignInClient(winner["clientId"], winner.get("clientSecret"), True, _registered_auth_method(winner))
            await config_service.set_config(shared_path, record)
        return _SignInClient(dcr_client.client_id, dcr_client.client_secret, True, _registered_auth_method(record))

    raise HTTPException(
        status_code=HttpStatusCode.CONFLICT.value,
        detail=(
            "No OAuth client is configured for this instance and it does not support dynamic "
            f"client registration. Ask an administrator to configure an OAuth app with redirect URI: {redirect_uri}"
        ),
    )


async def _granted_scopes(config_service: ConfigurationService, instance_id: str, owner_id: str) -> list[str]:
    """The scopes the owner's current tokens were granted (RFC 6749 §5.1 `scope`)."""
    record = await config_service.get_config(get_mcp_credentials_path(instance_id, owner_id), default=None, use_cache=False)
    tokens = record.get("oauthTokens") if isinstance(record, dict) else None
    granted = tokens.get("scope") if isinstance(tokens, dict) else None
    return challenge_scopes({"scope": granted}) if isinstance(granted, str) else []


def _registered_auth_method(record: dict[str, Any]) -> str:
    # Clients registered before the method was recorded were all registered for client_secret_post.
    return record.get("tokenEndpointAuthMethod") or TokenEndpointAuthMethod.CLIENT_SECRET_POST.value


def _sign_in_address_not_web() -> HTTPException:
    return HTTPException(
        status_code=HttpStatusCode.BAD_GATEWAY.value,
        detail="The server's sign-in address is not a web address, so it can't be opened.",
    )


async def _build_oauth_authorization_url(
    config_service: ConfigurationService,
    instance: dict[str, Any],
    instance_id: str,
    owner_id: str,
    org_id: str,
    base_url: Optional[str],
    *,
    initiated_by: str,
    owner_type: str,
    registry: Optional[MCPRegistry] = None,
) -> dict[str, Any]:
    """Core DCR-or-static-client OAuth authorize flow, shared by the per-user and
    agent-key (`/agents/{agent_key}/...`) routes. `owner_id` is a plain user for the
    former, an agentKey for the latter — the resulting state record's `userId` field
    is exactly what `/oauth/callback` uses to persist tokens at
    `/services/mcp/credentials/{instanceId}/{owner_id}`.

    `initiated_by` is the *authenticated caller* (always a real user, even for the
    agent-key path) who started this authorize request — `/oauth/callback` rejects a
    callback whose caller doesn't match, so one signed-in session can't complete an
    authorization someone else's browser started.
    """
    if instance.get("authMode") != MCPAuthMode.OAUTH.value:
        raise HTTPException(status_code=HttpStatusCode.BAD_REQUEST.value, detail="This instance does not use OAuth.")

    # `base_url` is accepted for older clients but no longer shapes the redirect URI.
    del base_url
    redirect_uri = await _mcp_oauth_redirect_uri(config_service)

    # Discover live metadata *before* resolving endpoints/client, not only as a DCR
    # fallback — a configured static client used to skip discovery entirely and fall back
    # to the (for catalog instances, often wrong) stored authorizationUrl/tokenUrl.
    policy = MCPUrlPolicy.for_instance(instance)
    discovered: Optional[DiscoveredOAuthMetadata] = None
    if instance.get("url"):
        discovered = await dcr_module.discover_oauth_metadata(
            instance["url"], policy=policy, sse=instance.get("transport") == MCPTransport.SSE.value,
        )

    authorization_url, token_url = _resolve_oauth_endpoints(instance, discovered)
    if authorization_url and not is_web_url(authorization_url):
        raise _sign_in_address_not_web()
    # Before any client is registered. Only when that server is the one signing in: an
    # administrator's own endpoints may belong to another.
    if discovered and discovered.refuses_s256_pkce and authorization_url == discovered.authorization_endpoint:
        raise HTTPException(
            status_code=HttpStatusCode.BAD_GATEWAY.value,
            detail="The server's sign-in provider doesn't support PKCE with S256, which PipesHub requires, so it can't be used.",
        )
    # The secret is intentionally discarded here — see the state_record comment below on why
    # it is re-resolved at callback/exchange time instead of being carried through this flow.
    client = await _resolve_oauth_client(
        config_service, instance_id, owner_id, redirect_uri, discovered, authorization_url, token_url,
        policy=policy,
    )
    client_id = client.client_id

    if not authorization_url or not token_url:
        raise HTTPException(status_code=HttpStatusCode.CONFLICT.value, detail="This instance is missing OAuth authorization/token URLs.")

    # Admin-configured scopes win; otherwise, as the MCP authorization spec orders them, the ones
    # the server's 401 asked for, then the ones its own metadata lists; otherwise none. Never the
    # authorization server's list: it covers every application it serves, so the user would be
    # asked to grant all of them.
    scopes = (
        instance.get("scopes")
        or (discovered.challenge_scopes if discovered else None)
        or (discovered.resource_scopes if discovered else None)
        or None
    )
    if needed := await step_up.step_up_scopes(config_service, instance_id, owner_id):
        # What a 403 asked for, on top of what the sign-in already has, so it isn't narrowed.
        scopes = list(dict.fromkeys([*(scopes or []), *await _granted_scopes(config_service, instance_id, owner_id), *needed]))
    # RFC 8707, sent only to a server that publishes RFC 9728 metadata: some identity
    # providers (Entra ID's v2 endpoints) reject an unknown `resource` outright.
    resource = discovered.resource if discovered else None

    state = dcr_module.generate_state()
    code_verifier = dcr_module.generate_code_verifier()
    code_challenge = dcr_module.generate_code_challenge(code_verifier)

    state_record = {
        "instanceId": instance_id,
        "userId": owner_id,
        "orgId": org_id,
        "initiatedBy": initiated_by,
        "ownerType": owner_type,
        "codeVerifier": code_verifier,
        "isDcr": client.is_dcr,
        "clientId": client_id,
        "tokenEndpointAuthMethod": client.auth_method,
        # clientSecret is deliberately NOT persisted here — /oauth/callback re-resolves it
        # from the DCR/static client store at exchange time, so a rotated admin secret (or
        # an expired DCR registration) fails loudly instead of a stale copy silently working.
        "tokenUrl": token_url,
        "redirectUri": redirect_uri,
        "resource": resource,
        "expiresAt": get_epoch_timestamp_in_ms() + OAUTH_STATE_TTL_SECONDS * 1000,
    }
    if client.metadata_document and discovered is not None:
        # The callback remembers a refusal for this server, so its next sign-in registers instead.
        state_record.update({"clientKind": "cimd", "authorizationServer": cimd.server_key(discovered)})

    # Resolved from the template rather than the stored instance so instances created
    # before a template gained these params still get them.
    type_id = instance.get("typeId")
    template = registry.get_template(type_id) if registry and type_id else None

    try:
        authorization_redirect_url = dcr_module.build_authorization_url(
            authorization_url=authorization_url,
            client_id=client_id,
            redirect_uri=redirect_uri,
            state=state,
            scopes=scopes,
            code_challenge=code_challenge,
            extra_params=template.authorization_params if template else None,
            resource=resource,
        )
    except dcr_module.InvalidOAuthEndpointError as e:
        raise _sign_in_address_not_web() from e
    # Stored only once the URL it belongs to is known to be safe to open. The store expires
    # the state itself; the refresh service's sweep only catches records from before TTLs.
    await config_service.create_config_if_absent(
        get_mcp_oauth_state_path(state), state_record, ttl_seconds=OAUTH_STATE_TTL_SECONDS,
    )
    return {"authorizationUrl": authorization_redirect_url}


@router.get(
    "/instances/{instance_id}/oauth/authorize",
    dependencies=[Depends(require_scopes(OAuthScopes.MCP_READ))],
)
async def get_oauth_authorization_url(
    request: Request,
    instance_id: str,
    base_url: Optional[str] = Query(default=None, alias="baseUrl"),
) -> dict[str, Any]:
    config_service = _get_config_service(request)
    user_context = _get_user_context(request)
    org_id, user_id = user_context["org_id"], user_context["user_id"]

    instance = await _get_org_instance(instance_id, config_service, user_context["org_id"], user_context["user_id"])
    if not instance:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="MCP server instance not found.")

    return await _build_oauth_authorization_url(
        config_service, instance, instance_id, user_id, org_id, base_url,
        initiated_by=user_id, owner_type="user", registry=_get_mcp_registry(request),
    )


@router.post("/oauth/discover", dependencies=[Depends(require_scopes(OAuthScopes.MCP_WRITE))])
async def discover_oauth_metadata_endpoint(request: Request, payload: OAuthDiscoveryRequest) -> dict[str, Any]:
    """Does this MCP server support OAuth dynamic client registration, and if so, what are
    its real endpoints? Needed by the config panel in *create* mode, before an instance (and
    therefore a stored authorizationUrl/tokenUrl) exists, and to correct a catalog
    template's endpoints in edit mode.

    This fetches a caller-supplied URL from inside the cluster, so it runs under the policy
    of the instance the caller could create: an administrator's (org) or a user's own
    (personal, public-only).
    """
    config_service = _get_config_service(request)
    user_context = _get_user_context(request)
    is_admin = await _check_user_is_admin(user_context["user_id"], request, config_service)
    # The URL is the caller's own, typed into the config panel, so its host is trusted as an
    # instance's would be; anything its metadata names is not.
    policy = MCPUrlPolicy.for_instance(
        {"url": payload.url, **({} if is_admin else {"scope": mcp_service.SCOPE_PERSONAL})},
    )
    try:
        await dcr_module.assert_discovery_target_allowed(payload.url, allow_private=policy.private_ok(payload.url))
    except dcr_module.DiscoveryBlockedError as e:
        raise _bad_request(str(e)) from e

    metadata = await dcr_module.discover_oauth_metadata(payload.url, policy=policy)
    if metadata is None:
        # Distinct from "confirmed no DCR" — nothing could be discovered at all (network
        # error, timeout, or the server simply publishes no RFC 8414/9728 metadata). The
        # caller should treat DCR support as unknown, not as a confirmed "false".
        return {
            "metadataFound": False,
            "supportsDcr": False,
            "authorizationEndpoint": None,
            "tokenEndpoint": None,
            "registrationEndpoint": None,
            "scopesSupported": [],
            "redirectUri": await _displayed_redirect_uri(config_service),
        }
    redirect_uri = await _displayed_redirect_uri(config_service)
    # "No app needed": PipesHub's client metadata document serves as well as registering one.
    automatic = metadata.supports_dcr or bool(
        redirect_uri and await _client_metadata_document(config_service, metadata, redirect_uri)
    )
    return {
        "metadataFound": True,
        "supportsDcr": automatic,
        "authorizationEndpoint": metadata.authorization_endpoint,
        "tokenEndpoint": metadata.token_endpoint,
        "registrationEndpoint": metadata.registration_endpoint,
        "scopesSupported": metadata.scopes_supported,
        "redirectUri": redirect_uri,
    }


# What a provider answers when it won't take a client: for PipesHub's client metadata document,
# the next sign-in at that server registers one instead.
_CLIENT_REFUSALS = frozenset({"invalid_client", "unauthorized_client", "invalid_request"})


async def _note_a_refused_client_document(
    config_service: ConfigurationService, state: Optional[str], error: str, caller_id: Optional[str],
) -> None:
    """Only for a sign-in this caller started; its state is left for the provider's answer."""
    if error not in _CLIENT_REFUSALS or not state or not caller_id:
        return
    try:
        record = await config_service.get_config(get_mcp_oauth_state_path(state), default=None, use_cache=False)
    except Exception as e:
        logger.warning(f"Couldn't read the sign-in state behind a refused MCP authorization: {e}")
        return
    if isinstance(record, dict) and record.get("clientKind") == "cimd" and record.get("initiatedBy") == caller_id:
        await cimd.remember_refusal(config_service, record.get("authorizationServer") or "")


async def _resolve_oauth_client_secret(
    config_service: ConfigurationService,
    instance_id: str,
    owner_id: str,
    client_id: str,
    is_dcr: bool,
) -> Optional[str]:
    """Re-resolve the client secret at token-exchange time rather than trusting a copy
    persisted on the authorize-time state record (see `_build_oauth_authorization_url`).

    Raises:
        ValueError: the client this authorization started against can no longer be found
            (an admin rotated/removed the OAuth app, or a DCR registration expired) — the
            caller turns this into a `{"success": False, ...}` response like every other
            callback failure mode, rather than a raw 5xx.
    """
    if is_dcr:
        for path in (get_mcp_dcr_client_path(instance_id, owner_id), get_mcp_shared_dcr_client_path(instance_id)):
            record = await config_service.get_config(path, default=None)
            for client in mcp_token_refresh.registered_clients(record):
                if client["clientId"] == client_id:
                    return client.get("clientSecret")
        raise ValueError(
            "The dynamically-registered OAuth client for this authorization could no longer be found."
        )

    static_client = await config_service.get_config(get_mcp_oauth_client_config_path(instance_id), default=None)
    if isinstance(static_client, dict) and static_client.get("clientId") == client_id:
        return static_client.get("clientSecret")

    owner_svc = await resolve_instance_owner_config_service(instance_id, config_service)
    if owner_svc is not None and owner_svc is not config_service:
        parent_client = await owner_svc.get_config(get_mcp_oauth_client_config_path(instance_id), default=None)
        if isinstance(parent_client, dict) and parent_client.get("clientId") == client_id:
            return parent_client.get("clientSecret")

    raise ValueError("The OAuth app configuration for this instance changed during authorization.")


@router.get("/oauth/callback", dependencies=[Depends(require_scopes(OAuthScopes.MCP_WRITE))])
async def handle_oauth_callback(
    request: Request,
    code: Optional[str] = Query(default=None),
    state: Optional[str] = Query(default=None),
    error: Optional[str] = Query(default=None),
) -> dict[str, Any]:
    config_service = _get_config_service(request)
    # Same extraction as `_get_user_context`, but without raising 401 — this route always
    # responds with a 200 + `{"success": false, ...}` body on any failure (including "no
    # caller identity"), matching every other error branch below.
    caller_user = getattr(request.state, "user", {}) or {}
    caller_id = caller_user.get("userId")

    if error:
        await _note_a_refused_client_document(config_service, state, error, caller_id)
        return {"success": False, "error": error, "errorMessage": f"OAuth provider returned an error: {error}"}
    if not code or not state:
        return {"success": False, "error": "missing_params", "errorMessage": "Missing code or state parameter."}

    state_path = get_mcp_oauth_state_path(state)
    claim_path = get_mcp_oauth_state_claim_path(state)
    state_record = await config_service.get_config(state_path, default=None, use_cache=False)
    if not isinstance(state_record, dict):
        # Started on an older server: claimed where that server claims it, so the two can't both
        # use one state during a rolling deploy.
        state_path = get_mcp_unhashed_oauth_state_path(state)
        claim_path = get_mcp_unhashed_oauth_state_claim_path(state)
        state_record = await config_service.get_config(state_path, default=None, use_cache=False)
    if not isinstance(state_record, dict):
        return {"success": False, "error": "invalid_state", "errorMessage": "Invalid or expired authorization state."}

    if state_record.get("expiresAt", 0) < get_epoch_timestamp_in_ms():
        return {"success": False, "error": "expired_state", "errorMessage": "The authorization request expired. Please try again."}

    # Bind the callback to whoever's session started it — without this, anyone who could
    # guess or intercept a valid `state` value could complete someone else's authorization
    # and have the resulting tokens attributed to the original initiator's credential
    # record. A state record predating this field is treated as unbound and rejected; the
    # 10-minute TTL means nothing long-lived survives a deploy of this change.
    # Checked before the state is consumed: otherwise anyone holding the value could burn
    # the initiator's sign-in.
    initiated_by = state_record.get("initiatedBy")
    instance_id = state_record.get("instanceId")
    org_id = state_record.get("orgId")
    # The org check also covers a user moved between orgs mid-flow.
    if not initiated_by or initiated_by != caller_id or not org_id or org_id != caller_user.get("orgId"):
        logger.warning(
            f"Rejected MCP OAuth callback for instance {instance_id}: "
            f"caller {caller_id!r} does not match initiator {initiated_by!r} of org {org_id!r}"
        )
        return {
            "success": False,
            "error": "caller_mismatch",
            "errorMessage": "This authorization was not started from the current session. Please try connecting again.",
        }

    # Single-use. Read-then-delete let two concurrent callbacks both pass, and etcd's delete
    # doesn't say whether the key existed, so the claim is an atomic create instead.
    claimed = await config_service.create_config_if_absent(
        claim_path, {"claimedBy": caller_id}, ttl_seconds=OAUTH_STATE_TTL_SECONDS,
    )
    await config_service.delete_config(state_path)
    if not claimed:
        return {"success": False, "error": "invalid_state", "errorMessage": "Invalid or expired authorization state."}

    if state_record.get("ownerType") == "agent":
        # Edit access to the agent may have been revoked since the sign-in started.
        try:
            await _require_mcp_agent_edit_access(state_record["userId"], request)
        except HTTPException:
            logger.warning(f"Rejected MCP OAuth callback for agent {state_record['userId']!r}: edit access is gone")
            return {
                "success": False,
                "error": "agent_access_revoked",
                "errorMessage": "You can no longer edit this agent, so its sign-in wasn't saved.",
            }
    instance = await _get_org_instance(instance_id, config_service, org_id, initiated_by)
    if not instance:
        return {
            "success": False,
            "error": "instance_not_found",
            "errorMessage": "This MCP server no longer exists. Please refresh and try again.",
        }

    user_id = state_record["userId"]
    client_id = state_record["clientId"]
    is_dcr = bool(state_record.get("isDcr"))
    metadata_document = state_record.get("clientKind") == "cimd"

    try:
        client_secret = None if metadata_document else await _resolve_oauth_client_secret(
            config_service, instance_id, user_id, client_id, is_dcr,
        )
    except ValueError as e:
        logger.error(f"MCP OAuth client resolution failed for instance {instance_id}: {e}")
        return {
            "success": False,
            "error": "client_config_changed",
            "errorMessage": (
                "The OAuth app configuration for this instance changed during authorization. "
                "Please try connecting again."
            ),
        }

    try:
        tokens = await oauth_client_module.exchange_code_for_token(
            token_url=state_record["tokenUrl"],
            client_id=client_id,
            client_secret=client_secret,
            code=code,
            redirect_uri=state_record["redirectUri"],
            code_verifier=state_record.get("codeVerifier"),
            # The token URL may have come from the server's metadata.
            allow_private=MCPUrlPolicy.for_instance(instance).private_ok(state_record["tokenUrl"]),
            resource=state_record.get("resource"),
            auth_method=state_record.get("tokenEndpointAuthMethod"),
        )
    except oauth_client_module.MCPOAuthError as e:
        logger.error(f"MCP OAuth code exchange failed for instance {instance_id}: {e}")
        if e.rejected_client and is_dcr:
            # The next attempt registers a new client instead of failing the same way again.
            await mcp_token_refresh.retire_rejected_dcr_client(config_service, instance_id, user_id, client_id)
        elif e.rejected_client and metadata_document:
            await cimd.remember_refusal(config_service, state_record.get("authorizationServer") or "")
        return {
            "success": False,
            "error": "token_exchange_failed",
            "errorMessage": "Failed to exchange the authorization code for tokens. Please try connecting again.",
        }
    tokens = tokens.model_copy(update={"client_id": client_id})

    now_ms = get_epoch_timestamp_in_ms()
    record = {
        "instanceId": instance_id,
        "userId": user_id,
        "orgId": org_id,
        "authMode": MCPAuthMode.OAUTH.value,
        "isAuthenticated": True,
        "oauthTokens": tokens.model_dump(by_alias=True, mode="json"),
        "updatedAt": now_ms,
        "connectedAt": now_ms,
    }
    await config_service.set_config(get_mcp_credentials_path(instance_id, user_id), record)
    await step_up.clear_step_up_scopes(config_service, instance_id, user_id)

    from app.connectors.core.base.token_service.startup_service import startup_service

    refresh_service = startup_service.get_mcp_token_refresh_service()
    if refresh_service:
        await refresh_service.schedule_token_refresh(
            get_mcp_credentials_path(instance_id, user_id),
            tokens,
            **build_schedule_refresh_kwargs(org_id),
        )

    return {"success": True, "instanceId": instance_id}


@router.post(
    "/instances/{instance_id}/oauth/refresh",
    dependencies=[Depends(require_scopes(OAuthScopes.MCP_WRITE))],
)
async def refresh_oauth_token(request: Request, instance_id: str) -> dict[str, Any]:
    config_service = _get_config_service(request)
    user_context = _get_user_context(request)
    user_id = user_context["user_id"]

    if not await _get_org_instance(instance_id, config_service, user_context["org_id"], user_context["user_id"]):
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="MCP server instance not found.")

    try:
        await mcp_token_refresh.refresh_credential_record(instance_id, user_id, config_service)
    except mcp_token_refresh.MCPTokenRefreshError as e:
        # Covers "no credential record", "no refresh token", "no tokenUrl", and "no
        # resolvable OAuth client" — all mean the caller must re-authenticate or an admin
        # must configure OAuth, not that the token endpoint itself rejected anything.
        raise HTTPException(status_code=HttpStatusCode.BAD_REQUEST.value, detail=str(e)) from e
    except oauth_client_module.MCPRefreshTokenInvalidError as e:
        cred_path = get_mcp_credentials_path(instance_id, user_id)
        record = await config_service.get_config(cred_path, default=None, use_cache=False)
        if isinstance(record, dict):
            record["isAuthenticated"] = False
            await config_service.set_config(cred_path, record)
        # Both `MCPRefreshTokenInvalidError` and `MCPOAuthError` embed the token endpoint's
        # raw response body (see `oauth_client._post_token_request`) — log it server-side
        # only, same treatment as `/oauth/callback`'s token-exchange-failure branch above.
        logger.warning(f"MCP OAuth refresh token rejected for instance {instance_id}: {e}")
        # Not 401: that means the caller's own PipesHub session ended, and the app signs them out.
        raise HTTPException(
            status_code=HttpStatusCode.CONFLICT.value,
            detail="The refresh token was rejected by the OAuth provider. Please reconnect this MCP server.",
        ) from e
    except oauth_client_module.MCPOAuthError as e:
        logger.error(f"MCP OAuth token refresh failed for instance {instance_id}: {e}")
        raise HTTPException(
            status_code=HttpStatusCode.BAD_GATEWAY.value,
            detail="Failed to refresh the OAuth token. Please try again or reconnect this MCP server.",
        ) from e

    return {"success": True}


@router.get(
    "/instances/{instance_id}/oauth-config",
    dependencies=[Depends(require_scopes(OAuthScopes.MCP_READ))],
)
async def get_oauth_config(request: Request, instance_id: str) -> dict[str, Any]:
    config_service = _get_config_service(request)
    user_context = _get_user_context(request)
    instance = await _get_org_instance(instance_id, config_service, user_context["org_id"], user_context["user_id"])
    if not instance:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="MCP server instance not found.")
    await _require_manage_access(instance, user_context, request, config_service, "view the OAuth app of")

    config = await config_service.get_config(get_mcp_oauth_client_config_path(instance_id), default=None)
    is_own = isinstance(config, dict)
    if not isinstance(config, dict):
        owner_svc = await resolve_instance_owner_config_service(instance_id, config_service)
        if owner_svc is not None and owner_svc is not config_service:
            config = await owner_svc.get_config(get_mcp_oauth_client_config_path(instance_id), default=None)
    redirect_uri = await _displayed_redirect_uri(config_service)
    if not isinstance(config, dict):
        return {"configured": False, "redirectUri": redirect_uri}

    if is_own and can_reveal_secrets(request):
        return {
            "configured": True,
            "clientId": config.get("clientId"),
            "clientSecret": config.get("clientSecret"),
        }

    return {
        "configured": True,
        "clientId": _mask_secret(config.get("clientId")),
        "clientSecret": _mask_secret(config.get("clientSecret")),
        "redirectUri": redirect_uri,
    }


def _mask_secret(value: Optional[str]) -> Optional[str]:
    if not value:
        return value
    if len(value) <= 8:
        return "•" * len(value)
    return f"{value[:4]}{'•' * 8}{value[-4:]}"


@router.put(
    "/instances/{instance_id}/oauth-config",
    dependencies=[Depends(require_scopes(OAuthScopes.MCP_WRITE))],
)
async def update_oauth_config(
    request: Request,
    instance_id: str,
    payload: OAuthClientConfigRequest,
) -> dict[str, Any]:
    config_service = _get_config_service(request)
    user_context = _get_user_context(request)
    instance = await _get_org_instance(instance_id, config_service, user_context["org_id"], user_context["user_id"])
    if not instance:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="MCP server instance not found.")
    await _require_manage_access(instance, user_context, request, config_service, "configure the OAuth app of")

    record = {
        "instanceId": instance_id,
        "clientId": payload.client_id,
        "clientSecret": payload.client_secret,
        "updatedAt": get_epoch_timestamp_in_ms(),
    }
    success = await config_service.set_config(get_mcp_oauth_client_config_path(instance_id), record)
    if not success:
        raise HTTPException(status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail="Failed to save OAuth client configuration.")
    return {"success": True}


# ============================================================================
# Discovery / consumption
# ============================================================================


_REAUTH_REQUIRED = "Authentication with this MCP server has expired. Reconnect it to continue."


def _connection_error_text(error: Exception, *, is_admin: bool) -> str:
    """A STDIO server's stderr is what an administrator needs to fix a broken instance, but
    it can echo environment values, so nobody else gets it."""
    if isinstance(error, MCPConnectionError) and is_admin:
        return error.detail
    return str(error)


class _ListingDiscovery:
    """One listing's tool discovery: `LISTING_DISCOVERY_CONCURRENCY` servers at a time, each for
    at most `LISTING_DISCOVERY_BUDGET_SECONDS`, all within `LISTING_DISCOVERY_DEADLINE_SECONDS`."""

    def __init__(self) -> None:
        self._slots = asyncio.Semaphore(LISTING_DISCOVERY_CONCURRENCY)
        self._deadline = asyncio.get_running_loop().time() + LISTING_DISCOVERY_DEADLINE_SECONDS

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[float]:
        """Held while one server is discovered; yields the seconds it may take, 0 when too little
        is left before the deadline."""
        async with self._slots:
            remaining = self._deadline - asyncio.get_running_loop().time()
            yield min(LISTING_DISCOVERY_BUDGET_SECONDS, remaining) if remaining >= LISTING_DISCOVERY_MIN_SECONDS else 0


def _report_listing_timeout(entry: dict[str, Any], *, cut_by_deadline: bool) -> None:
    entry["toolsTimedOut"] = True
    entry["toolsErrorCode"] = "timeout"
    entry["toolsError"] = (
        f"Listing every server's tools is limited to {LISTING_DISCOVERY_DEADLINE_SECONDS:g} seconds, "
        "and this server's tools weren't listed in time. "
        if cut_by_deadline else
        f"The server took longer than {LISTING_DISCOVERY_BUDGET_SECONDS:g} seconds to list its tools. "
    ) + "Chats still wait for it up to its own timeout."


async def _discover_listing_tools(
    entry: dict[str, Any],
    instance: dict[str, Any],
    effective_auth: dict[str, Any],
    owner_id: str,
    config_service: ConfigurationService,
    discovery: _ListingDiscovery,
    *,
    is_admin: bool,
    namespace: Optional[str],
) -> None:
    """Fill `entry`'s tools, or the reason there are none. The tool cache answers first,
    without taking one of the listing's discovery slots; a live discovery is remembered."""
    credential_owner = mcp_service.credential_owner_id(instance, owner_id)
    try:
        cached = await cached_tools_for_owner(instance, effective_auth, credential_owner, config_service, namespace=namespace)
    except Exception as e:
        logger.warning(f"Couldn't read the cached tools of MCP instance {instance['_id']}: {e}")
        cached = None
    if cached is not None:
        tools, discovered_at = cached
        entry["tools"] = [t.model_dump(by_alias=True) for t in tools]
        entry["toolsCachedAt"] = int(discovered_at * 1000)
        return
    async with discovery.slot() as budget:
        if budget <= 0:
            _report_listing_timeout(entry, cut_by_deadline=True)
            return
        try:
            tools, _ = await asyncio.wait_for(
                discover_tools_for_owner(instance, effective_auth, credential_owner, config_service, namespace=namespace),
                timeout=budget,
            )
            entry["tools"] = [t.model_dump(by_alias=True) for t in tools]
        except asyncio.TimeoutError:
            _report_listing_timeout(entry, cut_by_deadline=budget < LISTING_DISCOVERY_BUDGET_SECONDS)
        except MCPConnectionError as e:
            entry["toolsErrorCode"] = classify_mcp_failure(e).value
            entry["toolsError"] = _connection_error_text(e, is_admin=is_admin)
        except (mcp_token_refresh.MCPTokenRefreshError, oauth_client_module.MCPOAuthError) as e:
            logger.warning(f"MCP token refresh during discovery failed for instance {instance['_id']}: {e}")
            entry["toolsErrorCode"] = "auth_expired"
            entry["toolsError"] = _REAUTH_REQUIRED
        except Exception as e:
            logger.warning(f"Unexpected error discovering tools for MCP instance {instance['_id']}: {e}")
            entry["toolsErrorCode"] = "error"
            entry["toolsError"] = "Failed to discover tools."


async def _build_mcp_instance_entry(
    instance: dict[str, Any],
    owner_id: str,
    config_service: ConfigurationService,
    include_tools: bool,
    *,
    is_admin: bool = False,
    namespace: Optional[str] = None,
    discovery: Optional[_ListingDiscovery] = None,
) -> dict[str, Any]:
    """Merge one instance's metadata with `owner_id`'s auth status (+ live tools).

    `owner_id` is opaque — a plain user for `/my-mcp-servers`, or an agentKey for the
    service-account path `/agents/{agent_key}` (`_resolve_effective_user_auth` already
    treats both the same way). Shared so the two routes can't drift.

    Only administrators see how an org server is reached; a personal server is listed only to
    its owner, who sees all of it. `discovery` is the listing's shared limit on tool discovery.
    """
    scope = mcp_service.instance_scope(instance)
    visible = instance if is_admin or scope == mcp_service.SCOPE_PERSONAL else _without_connection_details(instance)
    entry = {**visible, "scope": scope}
    owner_svc = await resolve_instance_owner_config_service(instance["_id"], config_service) or config_service
    entry["hasOAuthClientConfig"] = bool(
        await owner_svc.get_config(get_mcp_oauth_client_config_path(instance["_id"]), default=None)
    )
    effective_auth = await _resolve_effective_user_auth(instance, owner_id, config_service)
    entry["isAuthenticated"] = bool(effective_auth is not None and (
        effective_auth == {} or effective_auth.get("isAuthenticated")
    ))
    entry["disabledReason"] = stdio_policy.instance_disabled_reason(instance)
    # When the sign-in was made, so a client that started another can tell it went through.
    connected_at = effective_auth.get("connectedAt") if isinstance(effective_auth, dict) else None
    entry["connectedAt"] = connected_at if isinstance(connected_at, int) else None
    entry["tools"] = []
    entry["toolsError"] = None
    # Machine-readable reason behind `toolsError`, so the UI shows the right state and action
    # (Reconnect for an expired sign-in, "can't reach" for a server error) without parsing text.
    entry["toolsErrorCode"] = None
    entry["toolsTimedOut"] = False
    # When the tools shown were discovered, if they came from the tool cache (epoch ms).
    entry["toolsCachedAt"] = None

    if include_tools and entry["isAuthenticated"]:
        await _discover_listing_tools(
            entry, instance, effective_auth or {}, owner_id, config_service, discovery or _ListingDiscovery(),
            is_admin=is_admin, namespace=namespace,
        )
    return entry


@router.get("/my-mcp-servers", dependencies=[Depends(require_scopes(OAuthScopes.MCP_READ))])
async def get_my_mcp_servers(
    request: Request,
    include_tools: bool = Query(default=True, alias="includeTools"),
) -> dict[str, Any]:
    config_service = _get_config_service(request)
    user_context = _get_user_context(request)
    user_id = user_context["user_id"]

    instances = await resolve_mcp_instances_with_inheritance(config_service, user_context["org_id"], user_context["user_id"])
    is_admin = await _check_user_is_admin(user_id, request, config_service)
    # The names chat uses for these same instances (see `get_authenticated_mcp_servers`), so a
    # tool picked here names exactly one instance.
    namespaces = assign_namespaces(instances)
    discovery = _ListingDiscovery()
    entries = await asyncio.gather(*[
        _build_mcp_instance_entry(
            i, user_id, config_service, include_tools, is_admin=is_admin, namespace=namespaces[i["_id"]],
            discovery=discovery,
        )
        for i in instances
    ])
    return {"instances": list(entries)}


@router.get(
    "/instances/{instance_id}/tools",
    dependencies=[Depends(require_scopes(OAuthScopes.MCP_READ))],
)
async def get_instance_tools(request: Request, instance_id: str, *, cached: bool = False) -> dict[str, Any]:
    """The instance's tools for this caller, live. `cached`: the tool cache's list when it has one
    (opening a server's panel), so only Refresh connects. `syncedAt`: when the list was read."""
    config_service = _get_config_service(request)
    user_context = _get_user_context(request)
    user_id = user_context["user_id"]

    instance = await _get_org_instance(instance_id, config_service, user_context["org_id"], user_context["user_id"])
    if not instance:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="MCP server instance not found.")

    effective_auth = await _resolve_effective_user_auth(instance, user_id, config_service)
    if effective_auth is None:
        raise HTTPException(status_code=HttpStatusCode.CONFLICT.value, detail="Not authenticated for this MCP server instance.")

    try:
        visible = await resolve_mcp_instances_with_inheritance(config_service, user_context["org_id"], user_id)
        namespace = assign_namespaces(visible).get(instance_id)
        owner_id = mcp_service.credential_owner_id(instance, user_id)
        hit = (
            await cached_tools_for_owner(instance, effective_auth, owner_id, config_service, namespace=namespace)
            if cached else None
        )
        if hit is not None:
            cached_tools, discovered_at = hit
            return {"tools": [t.model_dump(by_alias=True) for t in cached_tools], "syncedAt": int(discovered_at * 1000)}
        # Live (Refresh, or nothing cached), and what it finds replaces the cached tools.
        tools, _ = await discover_tools_for_owner(instance, effective_auth, owner_id, config_service, namespace=namespace)
    except MCPConnectionError as e:
        logger.warning("Tool discovery failed for MCP instance %s: %s", instance_id, e, exc_info=True)
        raise HTTPException(
            status_code=HttpStatusCode.BAD_GATEWAY.value, detail=action_failed("load this MCP server's tools"),
        ) from e
    except (mcp_token_refresh.MCPTokenRefreshError, oauth_client_module.MCPOAuthError) as e:
        logger.warning(f"MCP token refresh during discovery failed for instance {instance_id}: {e}")
        raise HTTPException(status_code=HttpStatusCode.CONFLICT.value, detail=_REAUTH_REQUIRED) from e

    return {"tools": [t.model_dump(by_alias=True) for t in tools], "syncedAt": get_epoch_timestamp_in_ms()}


# ============================================================================
# Agent-key (service-account) credentials
#
# Mirrors the per-user routes above, but the credential owner is `agent_key`
# instead of the caller's `user_id` — for service-account agents, which act with
# their own MCP credentials rather than the invoking user's. Reuses the agent
# permission helpers already used by `app.api.routes.toolsets`'s equivalent
# routes so both features share exactly one `can_edit` + `isServiceAccount` gate.
# ============================================================================


async def _require_mcp_agent_edit_access(agent_key: str, request: Request) -> dict[str, Any]:
    from app.api.routes.toolsets import _require_agent_edit_access

    return await _require_agent_edit_access(
        agent_key, request, feature_name="MCP servers", settings_path="Workspace → MCP Servers",
    )


@router.get("/agents/{agent_key}", dependencies=[Depends(require_scopes(OAuthScopes.AGENT_READ))])
async def get_agent_mcp_servers(
    request: Request,
    agent_key: str,
    include_tools: bool = Query(default=True, alias="includeTools"),
) -> dict[str, Any]:
    """Org MCP server instances merged with `agent_key`'s auth status (+ live tools).

    Accessible to any user with view access to the agent (mirrors
    `toolsets.get_agent_toolsets`); the write endpoints below additionally
    require `can_edit` + `isServiceAccount` via `_require_mcp_agent_edit_access`.

    Listing tools connects with the agent's own credentials (and may refresh its tokens or
    start a local server), so only someone who can edit the agent gets them; a viewer gets
    the connection status alone.
    """
    from app.api.routes.toolsets import _resolve_agent_with_permission

    agent = await _resolve_agent_with_permission(agent_key, request)
    include_tools = include_tools and bool(agent.get("can_edit"))
    config_service = _get_config_service(request)
    user_context = _get_user_context(request)

    instances = await resolve_mcp_instances_with_inheritance(config_service, user_context["org_id"])
    is_admin = await _check_user_is_admin(user_context["user_id"], request, config_service)
    namespaces = assign_namespaces(instances)
    discovery = _ListingDiscovery()
    entries = await asyncio.gather(*[
        _build_mcp_instance_entry(
            i, agent_key, config_service, include_tools, is_admin=is_admin, namespace=namespaces[i["_id"]],
            discovery=discovery,
        )
        for i in instances
    ])
    return {"instances": list(entries)}


@router.post(
    "/agents/{agent_key}/instances/{instance_id}/authenticate",
    dependencies=[Depends(require_scopes(OAuthScopes.AGENT_WRITE))],
)
async def authenticate_agent_instance(
    request: Request,
    agent_key: str,
    instance_id: str,
    payload: AuthenticateRequest,
) -> dict[str, Any]:
    """Save agent (service-account) credentials for a non-OAuth MCP server instance."""
    await _require_mcp_agent_edit_access(agent_key, request)
    config_service = _get_config_service(request)
    user_context = _get_user_context(request)
    org_id = user_context["org_id"]

    instance = await _get_org_instance(instance_id, config_service, user_context["org_id"])
    if not instance:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="MCP server instance not found.")

    _assert_non_oauth_auth_mode(instance)
    if mcp_service.uses_shared_credential(instance):
        raise _bad_request(
            "This instance uses a shared admin credential; agents use it automatically and cannot store their own."
        )
    record = _build_credential_record(instance, payload, agent_key, org_id)
    record["agentKey"] = agent_key

    success = await config_service.set_config(get_mcp_credentials_path(instance_id, agent_key), record)
    if not success:
        raise HTTPException(status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail="Failed to save agent credentials.")
    return {"success": True, "isAuthenticated": True}


@router.put(
    "/agents/{agent_key}/instances/{instance_id}/credentials",
    dependencies=[Depends(require_scopes(OAuthScopes.AGENT_WRITE))],
)
async def update_agent_instance_credentials(
    request: Request,
    agent_key: str,
    instance_id: str,
    payload: AuthenticateRequest,
) -> dict[str, Any]:
    # Reuses the exact same guard + record-building as authenticate (same convention
    # as the per-user `update_credentials` -> `authenticate_instance` delegation above).
    return await authenticate_agent_instance(request, agent_key, instance_id, payload)


@router.delete(
    "/agents/{agent_key}/instances/{instance_id}/credentials",
    dependencies=[Depends(require_scopes(OAuthScopes.AGENT_WRITE))],
)
async def remove_agent_instance_credentials(request: Request, agent_key: str, instance_id: str) -> dict[str, Any]:
    await _require_mcp_agent_edit_access(agent_key, request)
    config_service = _get_config_service(request)
    user_context = _get_user_context(request)

    instance = await _get_org_instance(instance_id, config_service, user_context["org_id"])
    if not instance:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="MCP server instance not found.")

    cred_path = get_mcp_credentials_path(instance_id, agent_key)
    await config_service.delete_config(cred_path)

    from app.connectors.core.base.token_service.startup_service import startup_service

    refresh_service = startup_service.get_mcp_token_refresh_service()
    if refresh_service:
        refresh_service.cancel_refresh_task(cred_path)

    return {"success": True}


@router.post(
    "/agents/{agent_key}/instances/{instance_id}/reauthenticate",
    dependencies=[Depends(require_scopes(OAuthScopes.AGENT_WRITE))],
)
async def reauthenticate_agent_instance(request: Request, agent_key: str, instance_id: str) -> dict[str, Any]:
    """Clear the agent's stored credentials/tokens for this instance, forcing re-auth."""
    await _require_mcp_agent_edit_access(agent_key, request)
    config_service = _get_config_service(request)
    user_context = _get_user_context(request)

    instance = await _get_org_instance(instance_id, config_service, user_context["org_id"])
    if not instance:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="MCP server instance not found.")

    cred_path = get_mcp_credentials_path(instance_id, agent_key)
    await config_service.delete_config(cred_path)
    # See per-user reauthenticate_instance above — only the legacy per-owner DCR client is
    # this agent's alone to clear; the shared per-instance client must survive.
    await config_service.delete_config(get_mcp_dcr_client_path(instance_id, agent_key))

    from app.connectors.core.base.token_service.startup_service import startup_service

    refresh_service = startup_service.get_mcp_token_refresh_service()
    if refresh_service:
        refresh_service.cancel_refresh_task(cred_path)

    return {"success": True}


@router.get(
    "/agents/{agent_key}/instances/{instance_id}/oauth/authorize",
    dependencies=[Depends(require_scopes(OAuthScopes.AGENT_WRITE))],
)
async def get_agent_oauth_authorization_url(
    request: Request,
    agent_key: str,
    instance_id: str,
    base_url: Optional[str] = Query(default=None, alias="baseUrl"),
) -> dict[str, Any]:
    """OAuth authorize for a service-account agent — the resulting state record's
    `userId` field carries `agent_key`, so `/oauth/callback` persists tokens at
    `/services/mcp/credentials/{instanceId}/{agent_key}`. `initiatedBy` still carries the
    real signed-in user (never `agent_key`) — they're the one whose browser session
    completes the callback.
    """
    await _require_mcp_agent_edit_access(agent_key, request)
    config_service = _get_config_service(request)
    user_context = _get_user_context(request)
    org_id, user_id = user_context["org_id"], user_context["user_id"]

    instance = await _get_org_instance(instance_id, config_service, user_context["org_id"])
    if not instance:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="MCP server instance not found.")

    return await _build_oauth_authorization_url(
        config_service, instance, instance_id, agent_key, org_id, base_url,
        initiated_by=user_id, owner_type="agent", registry=_get_mcp_registry(request),
    )


# ============================================================================
# Tool approvals: who may run which tool (`app/agents/agent_loop/tool_approvals.py`)
#
# Company rules (admins, org servers), a person's own rules (their assistant chats) and an
# agent's rules (its editors). Rules are kept apart from the instance record, which an edit
# rebuilds. Tool names aren't checked against a listing: a server may add tools later, and
# an admin may not be signed in to a per-user server.
# ============================================================================

_MAX_TOOL_RULES = 500
_MAX_TOOL_NAME_CHARS = 200


def _checked_tool_names(tools: dict[str, Any]) -> dict[str, Any]:
    if len(tools) > _MAX_TOOL_RULES:
        raise ValueError(f"At most {_MAX_TOOL_RULES} tools can have a rule.")
    for name in tools:
        if not name.strip() or len(name) > _MAX_TOOL_NAME_CHARS:
            raise ValueError(f"Tool names must be 1 to {_MAX_TOOL_NAME_CHARS} characters.")
    return tools


class ToolRulesBody(BaseModel):
    tools: dict[str, Literal["allow", "ask", "block"]] = Field(default_factory=dict)

    _bounded = field_validator("tools")(_checked_tool_names)


class ToolPolicyBody(BaseModel):
    tools: dict[str, tool_approvals.CompanyToolRule] = Field(default_factory=dict)

    _bounded = field_validator("tools")(_checked_tool_names)


async def _instance_for_tool_rules(request: Request, instance_id: str) -> tuple[dict[str, Any], dict[str, Any], ConfigurationService]:
    config_service = _get_config_service(request)
    user_context = _get_user_context(request)
    instance = await _get_org_instance(instance_id, config_service, user_context["org_id"], user_context["user_id"])
    if not instance:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="MCP server instance not found.")
    return instance, user_context, config_service


async def _org_instance_for_policy(request: Request, instance_id: str, *, write: bool) -> tuple[dict[str, Any], ConfigurationService]:
    instance, user_context, config_service = await _instance_for_tool_rules(request, instance_id)
    if mcp_service.is_personal(instance):
        raise _bad_request("Company tool rules are for organization MCP servers.")
    await _require_manage_access(instance, user_context, request, config_service, "set company tool rules for")
    if write:
        forbid_inherited_mcp_mutation(instance)
    return instance, config_service


@router.get("/instances/{instance_id}/tool-policy", dependencies=[Depends(require_scopes(OAuthScopes.MCP_READ))])
async def get_tool_policy(request: Request, instance_id: str) -> dict[str, Any]:
    """The company's rules for the server's tools. Administrators only."""
    _, config_service = await _org_instance_for_policy(request, instance_id, write=False)
    policy = await tool_approvals.load_company_policy(config_service, instance_id)
    return {"tools": {name: rule.model_dump(by_alias=True) for name, rule in policy.tools.items()}}


@router.put("/instances/{instance_id}/tool-policy", dependencies=[Depends(require_scopes(OAuthScopes.MCP_WRITE))])
async def update_tool_policy(request: Request, instance_id: str, payload: ToolPolicyBody) -> dict[str, Any]:
    """Replaces the company's rules. A tool with no rule and nothing allowed unattended is dropped."""
    _, config_service = await _org_instance_for_policy(request, instance_id, write=True)
    tools = {name: rule for name, rule in payload.tools.items() if rule.rule is not None or rule.unattended}
    if not await tool_approvals.save_company_policy(config_service, instance_id, tool_approvals.CompanyPolicy(tools=tools)):
        raise HTTPException(status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail="Failed to save the tool rules.")
    return {"tools": {name: rule.model_dump(by_alias=True) for name, rule in tools.items()}}


@router.get("/instances/{instance_id}/my-tool-rules", dependencies=[Depends(require_scopes(OAuthScopes.MCP_READ))])
async def get_my_tool_rules(request: Request, instance_id: str) -> dict[str, Any]:
    """The caller's own rules for the server's tools, used in their assistant chats."""
    _, user_context, config_service = await _instance_for_tool_rules(request, instance_id)
    rules = await tool_approvals.load_own_rules(
        config_service, agent_key=None, user_id=user_context["user_id"], instance_id=instance_id,
    )
    return {"tools": rules.tools}


@router.put("/instances/{instance_id}/my-tool-rules", dependencies=[Depends(require_scopes(OAuthScopes.MCP_WRITE))])
async def update_my_tool_rules(request: Request, instance_id: str, payload: ToolRulesBody) -> dict[str, Any]:
    _, user_context, config_service = await _instance_for_tool_rules(request, instance_id)
    if not await tool_approvals.save_own_rules(
        config_service, tool_approvals.ToolRules(tools=payload.tools),
        agent_key=None, user_id=user_context["user_id"], instance_id=instance_id,
    ):
        raise HTTPException(status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail="Failed to save the tool rules.")
    return {"tools": payload.tools}


async def _agent_tool_rules_access(request: Request, agent_key: str, instance_id: str, *, write: bool) -> ConfigurationService:
    """Anyone with access to the agent may see its rules; only its editors may change them."""
    from app.api.routes.toolsets import _resolve_agent_with_permission

    agent = await _resolve_agent_with_permission(agent_key, request)
    if write and not agent.get("can_edit"):
        raise HTTPException(
            status_code=HttpStatusCode.FORBIDDEN.value, detail="Only people who can edit this agent can change its tool rules.",
        )
    _, _, config_service = await _instance_for_tool_rules(request, instance_id)
    return config_service


@router.get("/agents/{agent_key}/instances/{instance_id}/tool-rules", dependencies=[Depends(require_scopes(OAuthScopes.AGENT_READ))])
async def get_agent_tool_rules(request: Request, agent_key: str, instance_id: str) -> dict[str, Any]:
    config_service = await _agent_tool_rules_access(request, agent_key, instance_id, write=False)
    rules = await tool_approvals.load_own_rules(
        config_service, agent_key=agent_key, user_id="", instance_id=instance_id,
    )
    return {"tools": rules.tools}


@router.put("/agents/{agent_key}/instances/{instance_id}/tool-rules", dependencies=[Depends(require_scopes(OAuthScopes.AGENT_WRITE))])
async def update_agent_tool_rules(request: Request, agent_key: str, instance_id: str, payload: ToolRulesBody) -> dict[str, Any]:
    config_service = await _agent_tool_rules_access(request, agent_key, instance_id, write=True)
    if not await tool_approvals.save_own_rules(
        config_service, tool_approvals.ToolRules(tools=payload.tools), agent_key=agent_key, user_id="", instance_id=instance_id,
    ):
        raise HTTPException(status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail="Failed to save the tool rules.")
    return {"tools": payload.tools}
