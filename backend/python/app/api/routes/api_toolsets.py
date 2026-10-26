"""API Toolsets routes: admins add an internal REST API from its OpenAPI spec, users connect
their credentials, and agents call the selected operations as tools.

Each route has one rule. The admin routes (adding, changing and deleting toolsets, their
settings, and a shared credential) refuse every member; the others never ask for the role.
Business rules live in `app.agents.openapi.service`; this module only maps HTTP to it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import TYPE_CHECKING, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import Field

from app.agents.constants.api_toolset_constants import OAUTH_CALLBACK_PATH
from app.agents.openapi import service
from app.agents.openapi.auth import ApiToolsetAuthError
from app.agents.openapi.executor import ApiCallError
from app.agents.openapi.models import (
    ApiToolsetCredentials,
    ApiToolsetInstance,
    ApiToolsetModel,
    AuthType,
)
from app.agents.openapi.request_builder import RequestBuildError
from app.agents.openapi.spec_compiler import MAX_SPEC_BYTES, SpecError, compile_spec
from app.agents.openapi.store import ApiToolsetStore
from app.api.middlewares.auth import require_scopes
from app.api.middlewares.caller_role import fetch_caller_role
from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import DefaultEndpoints, OAuthScopes
from app.services.featureflag.platform_settings import is_api_toolsets_enabled
from app.utils.desktop_state import strip_desktop_state

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService

logger = logging.getLogger(__name__)

DEFAULT_ENDPOINTS_PATH = "/services/endpoints"
# What a test call shows of the API's answer.
_TEST_BODY_CHARS = 4000


def _get_config_service(request: Request) -> ConfigurationService:
    config_service = getattr(request.app.state, "config_service", None)
    if not config_service:
        raise HTTPException(status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail="Configuration service not initialized.")
    return config_service


def _get_user_context(request: Request) -> tuple[str, str]:
    user = getattr(request.state, "user", {}) or {}
    user_id, org_id = user.get("userId"), user.get("orgId")
    if not user_id or not org_id:
        raise HTTPException(status_code=HttpStatusCode.UNAUTHORIZED.value, detail="Authentication required.")
    return user_id, org_id


async def _require_admin(request: Request, action: str) -> None:
    # The live role Node reports for the caller's own token, never a client-supplied flag.
    if not (await fetch_caller_role(request, _get_config_service(request))).is_admin:
        raise HTTPException(status_code=HttpStatusCode.FORBIDDEN.value, detail=f"Only administrators can {action}.")


async def _require_enabled(request: Request) -> None:
    if not await is_api_toolsets_enabled(_get_config_service(request)):
        raise HTTPException(status_code=HttpStatusCode.FORBIDDEN.value, detail="API Toolsets are disabled for this organization.")


router = APIRouter(prefix="/api/v1/api-toolsets", tags=["api-toolsets"], dependencies=[Depends(_require_enabled)])


def _store(request: Request) -> ApiToolsetStore:
    return ApiToolsetStore(_get_config_service(request))


async def _instance_or_404(store: ApiToolsetStore, org_id: str, instance_id: str) -> ApiToolsetInstance:
    instance = await store.get_instance(org_id, instance_id)
    if instance is None:
        raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="API action not found.")
    return instance


def _bad_request(e: Exception) -> HTTPException:
    return HTTPException(status_code=HttpStatusCode.BAD_REQUEST.value, detail=str(e))


async def _oauth_redirect_uri(config_service: ConfigurationService) -> str:
    """The configured public frontend address, never one taken from the browser."""
    endpoints = await config_service.get_config(DEFAULT_ENDPOINTS_PATH, use_cache=False)
    frontend = endpoints.get("frontend") if isinstance(endpoints, dict) else None
    base = frontend.get("publicEndpoint") if isinstance(frontend, dict) else None
    base = base.strip() if isinstance(base, str) and base.strip() else DefaultEndpoints.FRONTEND_ENDPOINT.value
    return base.rstrip("/") + OAUTH_CALLBACK_PATH


# ============================================================================
# Views
# ============================================================================


def _operation_summary(op: Any) -> dict[str, Any]:  # noqa: ANN401
    return {"toolName": op.tool_name, "method": op.method, "path": op.path, "summary": op.summary, "kind": op.kind,
            "requiresAuth": op.requires_auth}


async def _view(store: ApiToolsetStore, instance: ApiToolsetInstance, user_id: str, *, full: bool = False) -> dict[str, Any]:
    """What every member sees: no connection details. Input schemas only on request."""
    return {
        "id": instance.id,
        "name": instance.name,
        "description": instance.description,
        "specTitle": instance.spec_title,
        "authType": instance.auth_scheme.type.value,
        "useSharedCredential": instance.use_shared_credential,
        "isConnected": await service.is_connected(store, instance, user_id),
        "operations": [op.model_dump(by_alias=True, mode="json") if full else _operation_summary(op) for op in instance.operations],
        "updatedAt": instance.updated_at,
    }


async def _admin_view(store: ApiToolsetStore, instance: ApiToolsetInstance, user_id: str) -> dict[str, Any]:
    """Everything but secrets: how the API is reached and authenticated."""
    oauth_app = await store.get_oauth_client(instance.id)
    return {
        **await _view(store, instance, user_id, full=True),
        "baseUrl": instance.base_url,
        "authScheme": instance.auth_scheme.model_dump(by_alias=True, mode="json"),
        "scopes": instance.scopes,
        "timeoutSeconds": instance.timeout_seconds,
        "specVersion": instance.spec_version,
        "openapiVersion": instance.openapi_version,
        "oauthClientId": oauth_app.client_id if oauth_app else None,
        "hasOauthClientSecret": bool(oauth_app and oauth_app.client_secret),
        "createdBy": instance.created_by,
        "createdAt": instance.created_at,
    }


# ============================================================================
# Spec preview
# ============================================================================


class SpecUpload(ApiToolsetModel):
    spec: str = Field(max_length=MAX_SPEC_BYTES)


@router.post("/validate", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
async def validate_api_toolset_spec(request: Request, body: SpecUpload) -> dict[str, Any]:
    """Compile an uploaded spec without saving anything: the operations, auth schemes and warnings."""
    _get_user_context(request)
    await _require_admin(request, "add API actions")
    try:
        # A big YAML spec takes seconds to parse; on a thread, the service keeps answering meanwhile.
        compiled = await asyncio.to_thread(compile_spec, body.spec.encode())
    except SpecError as e:
        raise _bad_request(e) from e
    return compiled.model_dump(by_alias=True, mode="json")


# ============================================================================
# Instances
# ============================================================================


class InstanceRequest(ApiToolsetModel):
    name: str = Field(max_length=200)
    base_url: str = Field(max_length=2000)
    operations: list[str]
    spec: Optional[str] = Field(default=None, max_length=MAX_SPEC_BYTES)
    auth_scheme_name: Optional[str] = None
    description: str = Field(default="", max_length=2000)
    scopes: list[str] = Field(default_factory=list)
    use_shared_credential: bool = False
    timeout_seconds: float = 30
    oauth_client_id: Optional[str] = None
    oauth_client_secret: Optional[str] = None

    def to_input(self) -> service.InstanceInput:
        return service.InstanceInput(
            name=self.name, base_url=self.base_url, operations=self.operations,
            spec=self.spec.encode() if self.spec is not None else None, auth_scheme_name=self.auth_scheme_name,
            description=self.description, scopes=self.scopes, use_shared_credential=self.use_shared_credential,
            timeout_seconds=self.timeout_seconds,
        )


@router.get("/instances", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
async def list_api_toolsets(request: Request) -> dict[str, Any]:
    user_id, org_id = _get_user_context(request)
    store = _store(request)
    instances = await store.list_instances(org_id)
    return {"instances": [await _view(store, i, user_id) for i in instances]}


@router.get("/instances/{instance_id}", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
async def get_api_toolset(request: Request, instance_id: str) -> dict[str, Any]:
    user_id, org_id = _get_user_context(request)
    store = _store(request)
    instance = await _instance_or_404(store, org_id, instance_id)
    return await _view(store, instance, user_id, full=True)


@router.get("/instances/{instance_id}/config", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))])
async def get_api_toolset_config(request: Request, instance_id: str) -> dict[str, Any]:
    """The toolset's settings, for the admin page."""
    await _require_admin(request, "see an API action's settings")
    user_id, org_id = _get_user_context(request)
    store = _store(request)
    return await _admin_view(store, await _instance_or_404(store, org_id, instance_id), user_id)


async def _save(request: Request, body: InstanceRequest, instance_id: str, existing: Optional[ApiToolsetInstance]) -> dict[str, Any]:
    user_id, org_id = _get_user_context(request)
    store = _store(request)
    try:
        instance = await asyncio.to_thread(  # it compiles the spec, as /validate does
            service.build_instance, body.to_input(), instance_id=instance_id, org_id=org_id, user_id=user_id, existing=existing,
        )
    except service.ApiToolsetError as e:
        raise _bad_request(e) from e
    oauth_app = service.oauth_client_from(body.oauth_client_id, body.oauth_client_secret)
    if instance.auth_scheme.type is AuthType.OAUTH2_AUTHORIZATION_CODE:
        current = await store.get_oauth_client(instance_id)
        if oauth_app is None and current is None:
            raise _bad_request(service.ApiToolsetError("Enter the client ID of the OAuth app registered with the API."))
        if oauth_app is not None:
            if oauth_app.client_secret is None and current is not None and current.client_id == oauth_app.client_id:
                # A blank secret on an edit keeps the one already stored.
                oauth_app = oauth_app.model_copy(update={"client_secret": current.client_secret})
            await store.save_oauth_client(instance_id, oauth_app)
    if existing is not None and not service.credentials_still_apply(existing, instance):
        await store.delete_instance_credentials(instance_id)
    await store.save_instance(instance)
    return await _admin_view(store, instance, user_id)


@router.post("/instances", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
async def create_api_toolset(request: Request, body: InstanceRequest) -> dict[str, Any]:
    await _require_admin(request, "add API actions")
    return await _save(request, body, uuid.uuid4().hex, None)


@router.put("/instances/{instance_id}", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
async def update_api_toolset(request: Request, instance_id: str, body: InstanceRequest) -> dict[str, Any]:
    await _require_admin(request, "change API actions")
    _, org_id = _get_user_context(request)
    existing = await _instance_or_404(_store(request), org_id, instance_id)
    return await _save(request, body, instance_id, existing)


@router.delete("/instances/{instance_id}", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_DELETE))])
async def delete_api_toolset(request: Request, instance_id: str) -> dict[str, Any]:
    await _require_admin(request, "delete API actions")
    _, org_id = _get_user_context(request)
    store = _store(request)
    instance = await _instance_or_404(store, org_id, instance_id)
    await _assert_not_in_use(request, instance)
    await store.delete_instance(instance_id)
    return {"success": True}


_MAX_AGENT_NAMES_SHOWN = 3


async def _assert_not_in_use(request: Request, instance: ApiToolsetInstance) -> None:
    """Refuse while an agent uses the toolset, as for actions and MCP servers. Fails closed:
    when usage can't be read, nothing is deleted."""
    graph_provider = getattr(request.app.state, "graph_provider", None)
    try:
        agent_names = await graph_provider.check_api_toolset_in_use(instance.id)
    except Exception as e:
        logger.error("Could not check which agents use API toolset %s: %s", instance.id, e)
        raise HTTPException(
            status_code=HttpStatusCode.SERVICE_UNAVAILABLE.value,
            detail="Couldn't check which agents use this API action, so it wasn't deleted. Try again.",
        ) from e
    if agent_names:
        shown = ", ".join(f"'{name}'" for name in agent_names[:_MAX_AGENT_NAMES_SHOWN])
        more = f" and {len(agent_names) - _MAX_AGENT_NAMES_SHOWN} more" if len(agent_names) > _MAX_AGENT_NAMES_SHOWN else ""
        raise HTTPException(
            status_code=HttpStatusCode.CONFLICT.value,
            detail=f"{instance.name} is used by {shown}{more}. Remove it from those agents first.",
        )


# ============================================================================
# Credentials
# ============================================================================


async def _own_credential_toolset(request: Request, instance_id: str) -> tuple[ApiToolsetStore, ApiToolsetInstance, str]:
    """A toolset each member connects to with their own credential."""
    user_id, org_id = _get_user_context(request)
    store = _store(request)
    instance = await _instance_or_404(store, org_id, instance_id)
    if instance.use_shared_credential:
        raise _bad_request(service.ApiToolsetError("An administrator manages this API action's shared credential."))
    return store, instance, user_id


async def _shared_credential_toolset(request: Request, instance_id: str) -> tuple[ApiToolsetStore, ApiToolsetInstance, str]:
    """A toolset whose one shared credential an administrator manages."""
    await _require_admin(request, "manage an API action's shared credential")
    user_id, org_id = _get_user_context(request)
    store = _store(request)
    instance = await _instance_or_404(store, org_id, instance_id)
    if not instance.use_shared_credential:
        raise _bad_request(service.ApiToolsetError("This toolset doesn't use a shared credential; each member connects their own."))
    return store, instance, user_id


async def _save_credentials(store: ApiToolsetStore, instance: ApiToolsetInstance, user_id: str, body: ApiToolsetCredentials) -> dict[str, Any]:
    try:
        credentials = service.checked_credentials(instance.auth_scheme, body)
    except service.ApiToolsetError as e:
        raise _bad_request(e) from e
    await store.save_credentials(instance.id, service.credential_owner(instance, user_id), credentials)
    return {"success": True, "isConnected": True}


async def _delete_credentials(store: ApiToolsetStore, instance: ApiToolsetInstance, user_id: str) -> dict[str, Any]:
    await store.delete_credentials(instance.id, service.credential_owner(instance, user_id))
    return {"success": True, "isConnected": instance.auth_scheme.type is AuthType.NONE}


async def _start_sign_in(
    request: Request, store: ApiToolsetStore, instance: ApiToolsetInstance, user_id: str, *, desktop: bool,
) -> dict[str, Any]:
    try:
        url = await service.start_sign_in(
            store, instance, user_id=user_id, redirect_uri=await _oauth_redirect_uri(_get_config_service(request)),
            desktop=desktop,
        )
    except service.ApiToolsetError as e:
        raise _bad_request(e) from e
    return {"authorizationUrl": url}


@router.put("/instances/{instance_id}/credentials", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
async def save_api_toolset_credentials(request: Request, instance_id: str, body: ApiToolsetCredentials) -> dict[str, Any]:
    return await _save_credentials(*await _own_credential_toolset(request, instance_id), body)


@router.delete("/instances/{instance_id}/credentials", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_DELETE))])
async def delete_api_toolset_credentials(request: Request, instance_id: str) -> dict[str, Any]:
    return await _delete_credentials(*await _own_credential_toolset(request, instance_id))


@router.get("/instances/{instance_id}/oauth/authorize", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
async def start_api_toolset_oauth(request: Request, instance_id: str, *, desktop: bool = Query(False)) -> dict[str, Any]:
    return await _start_sign_in(request, *await _own_credential_toolset(request, instance_id), desktop=desktop)


@router.put("/instances/{instance_id}/shared-credentials", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
async def save_api_toolset_shared_credentials(request: Request, instance_id: str, body: ApiToolsetCredentials) -> dict[str, Any]:
    return await _save_credentials(*await _shared_credential_toolset(request, instance_id), body)


@router.delete("/instances/{instance_id}/shared-credentials", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_DELETE))])
async def delete_api_toolset_shared_credentials(request: Request, instance_id: str) -> dict[str, Any]:
    return await _delete_credentials(*await _shared_credential_toolset(request, instance_id))


@router.get("/instances/{instance_id}/shared-credentials/oauth/authorize", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
async def start_api_toolset_shared_oauth(request: Request, instance_id: str, *, desktop: bool = Query(False)) -> dict[str, Any]:
    return await _start_sign_in(request, *await _shared_credential_toolset(request, instance_id), desktop=desktop)


@router.get("/oauth/callback", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
async def finish_api_toolset_oauth(request: Request, code: str = Query(...), state: str = Query(...)) -> dict[str, Any]:
    user_id, org_id = _get_user_context(request)
    try:
        instance = await service.finish_sign_in(
            _store(request), state=strip_desktop_state(state) or "", code=code, user_id=user_id, org_id=org_id,
            redirect_uri=await _oauth_redirect_uri(_get_config_service(request)),
        )
    except service.ApiToolsetError as e:
        raise _bad_request(e) from e
    return {"success": True, "instanceId": instance.id, "name": instance.name}


# ============================================================================
# Test call
# ============================================================================


class TestRequest(ApiToolsetModel):
    tool_name: Optional[str] = None
    arguments: dict[str, Any] = Field(default_factory=dict)


@router.post("/instances/{instance_id}/test", dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))])
async def test_api_toolset(request: Request, instance_id: str, body: TestRequest) -> dict[str, Any]:
    """Call one operation with the caller's credentials, to check the connection works."""
    user_id, org_id = _get_user_context(request)
    store = _store(request)
    instance = await _instance_or_404(store, org_id, instance_id)
    try:
        executor = await service.build_executor(store, instance, service.credential_owner(instance, user_id))
        response = await service.test_call(executor, instance, body.tool_name, body.arguments)
    except (service.ApiToolsetError, ApiToolsetAuthError, RequestBuildError) as e:
        raise _bad_request(e) from e
    except ApiCallError as e:
        raise HTTPException(status_code=HttpStatusCode.BAD_GATEWAY.value, detail=str(e)) from e
    body_text = response.body if isinstance(response.body, str) else _json_text(response.body)
    return {
        "ok": response.ok,
        "status": response.status,
        "body": body_text[:_TEST_BODY_CHARS],
        "truncated": response.truncated or len(body_text) > _TEST_BODY_CHARS,
    }


def _json_text(value: Any) -> str:  # noqa: ANN401
    return "" if value is None else json.dumps(value, indent=2, default=str)
