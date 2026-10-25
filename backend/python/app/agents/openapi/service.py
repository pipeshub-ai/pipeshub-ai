"""What API Toolsets do, apart from HTTP routing: build an instance from an uploaded spec,
decide whose credential a call uses, run the OAuth sign-in, and build the executor.

Both the admin routes and the agent runtime go through `build_executor`, so a test call
from the admin page behaves exactly like a call from a chat.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional
from urllib.parse import urlencode, urljoin, urlsplit

from app.agents.constants.api_toolset_constants import (
    DEFAULT_TIMEOUT_SECONDS,
    MAX_INSTANCE_RECORD_BYTES,
    MAX_OPERATIONS_PER_TOOLSET,
    MAX_TIMEOUT_SECONDS,
    PRIVATE_NETWORK_ENV,
    SHARED_CREDENTIAL_OWNER,
    SIGN_IN_STATE_TTL_SECONDS,
)
from app.agents.mcp.errors import MCPUrlBlockedError
from app.agents.mcp.models import is_web_url
from app.agents.mcp.oauth_client import MCPOAuthError, exchange_code_for_token
from app.agents.mcp.url_guard import check_mcp_url_at_save
from app.agents.openapi.auth import ApiToolsetAuthError, build_auth
from app.agents.openapi.executor import ApiResponse, OpenAPIHttpExecutor
from app.agents.openapi.models import (
    NO_AUTH,
    ApiToolsetCredentials,
    ApiToolsetInstance,
    AuthScheme,
    AuthType,
    CompiledOperation,
    OAuthClient,
)
from app.agents.openapi.spec_compiler import SpecError, compile_spec
from app.utils.desktop_state import mark_desktop_state
from app.utils.env_utils import env_bool

if TYPE_CHECKING:
    import httpx

    from app.agents.openapi.store import ApiToolsetStore


class ApiToolsetError(ValueError):
    """A request the admin or user can fix. The message is safe to show."""


def private_network_allowed() -> bool:
    """Whether base URLs may resolve to the deployment's own network. On unless turned off."""
    return env_bool(PRIVATE_NETWORK_ENV, True)


# ---------------------------------------------------------------------------
# Instances
# ---------------------------------------------------------------------------


@dataclass
class InstanceInput:
    """What the admin sends to create or change a toolset. `spec` may be left out of a
    change; the operations are then picked from those already stored."""

    name: str
    base_url: str
    operations: list[str]
    spec: Optional[bytes] = None
    auth_scheme_name: Optional[str] = None
    description: str = ""
    scopes: list[str] = field(default_factory=list)
    use_shared_credential: bool = False
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS


def build_instance(
    data: InstanceInput,
    *,
    instance_id: str,
    org_id: str,
    user_id: str,
    existing: Optional[ApiToolsetInstance] = None,
) -> ApiToolsetInstance:
    """A validated instance record. Raises `ApiToolsetError` with what to fix."""
    name = data.name.strip()
    if not name:
        raise ApiToolsetError("Give the API action a name.")
    if data.spec is not None:
        try:
            compiled = compile_spec(data.spec)
        except SpecError as e:
            raise ApiToolsetError(str(e)) from e
        available = compiled.operations
        schemes = [NO_AUTH, *compiled.auth_schemes]
        spec_fields = {"spec_title": compiled.title, "spec_version": compiled.version,
                       "openapi_version": compiled.openapi_version, "spec_hash": compiled.spec_hash}
    elif existing is not None:
        available = existing.operations
        schemes = [existing.auth_scheme]
        spec_fields = {"spec_title": existing.spec_title, "spec_version": existing.spec_version,
                       "openapi_version": existing.openapi_version, "spec_hash": existing.spec_hash}
    else:
        raise ApiToolsetError("Upload the API's OpenAPI spec.")

    scheme = _chosen_scheme(schemes, data.auth_scheme_name, existing)
    base_url = _checked_base_url(data.base_url)
    _check_oauth_urls(scheme, base_url)
    now = int(time.time() * 1000)
    instance = ApiToolsetInstance(
        id=instance_id,
        org_id=org_id,
        created_by=existing.created_by if existing else user_id,
        name=name,
        description=data.description.strip(),
        base_url=base_url,
        auth_scheme=scheme,
        scopes=_checked_scopes(scheme, data.scopes),
        # A client-credentials token acts as the client, never as a user, so it is always shared.
        use_shared_credential=data.use_shared_credential or scheme.type is AuthType.OAUTH2_CLIENT_CREDENTIALS,
        timeout_seconds=_checked_timeout(data.timeout_seconds),
        operations=_selected_operations(available, data.operations),
        created_at=existing.created_at if existing else now,
        updated_at=now,
        **spec_fields,
    )
    if len(json.dumps(instance.model_dump(by_alias=True, mode="json"))) > MAX_INSTANCE_RECORD_BYTES:
        raise ApiToolsetError("The selected operations are too large to store. Select fewer operations.")
    return instance


def _chosen_scheme(schemes: list[AuthScheme], name: Optional[str], existing: Optional[ApiToolsetInstance]) -> AuthScheme:
    wanted = name or (existing.auth_scheme.name if existing else None)
    if not wanted:
        raise ApiToolsetError("Choose how PipesHub authenticates with the API.")
    for scheme in schemes:
        if scheme.name == wanted:
            return scheme
    raise ApiToolsetError(f"The spec has no security scheme named {wanted}.")


def _checked_base_url(raw: str) -> str:
    base_url = raw.strip().rstrip("/")
    if not is_web_url(base_url):
        raise ApiToolsetError("The base URL must be an http(s) URL, without a username or password in it.")
    _check_url_allowed(base_url, "base URL")
    return base_url


def _check_url_allowed(url: str, what: str) -> None:
    try:
        check_mcp_url_at_save(url, allow_private=private_network_allowed())
    except MCPUrlBlockedError as e:
        where = "a loopback, link-local or cloud metadata address" if private_network_allowed() else "a private address"
        raise ApiToolsetError(
            f"The {what} is not allowed: it must not point to {where}. "
            "Use the address PipesHub's server reaches the API at, such as its host name on your network.",
        ) from e


def _check_oauth_urls(scheme: AuthScheme, base_url: str) -> None:
    if scheme.type not in (AuthType.OAUTH2_CLIENT_CREDENTIALS, AuthType.OAUTH2_AUTHORIZATION_CODE):
        return
    token_url = resolve_url(base_url, scheme.token_url)
    if not is_web_url(token_url):
        raise ApiToolsetError("The spec's OAuth token URL is not an http(s) URL.")
    _check_url_allowed(token_url, "OAuth token URL")
    if scheme.type is AuthType.OAUTH2_AUTHORIZATION_CODE and not is_web_url(resolve_url(base_url, scheme.authorization_url)):
        raise ApiToolsetError("The spec's OAuth authorization URL is not an http(s) URL.")


def resolve_url(base_url: str, url: Optional[str]) -> str:
    """A URL from the spec, which OpenAPI allows to be relative to the server URL."""
    return urljoin(base_url.rstrip("/") + "/", url or "")


def _checked_scopes(scheme: AuthScheme, scopes: list[str]) -> list[str]:
    unknown = [s for s in scopes if s not in scheme.scopes]
    if unknown:
        raise ApiToolsetError(f"The security scheme has no scope {unknown[0]}.")
    return list(dict.fromkeys(scopes))


def _checked_timeout(seconds: float) -> float:
    if not 1 <= seconds <= MAX_TIMEOUT_SECONDS:
        raise ApiToolsetError(f"The timeout must be between 1 and {MAX_TIMEOUT_SECONDS} seconds.")
    return seconds


def _selected_operations(available: list[CompiledOperation], names: list[str]) -> list[CompiledOperation]:
    if not names:
        raise ApiToolsetError("Select at least one operation.")
    if len(set(names)) > MAX_OPERATIONS_PER_TOOLSET:
        raise ApiToolsetError(f"Select at most {MAX_OPERATIONS_PER_TOOLSET} operations.")
    by_name = {op.tool_name: op for op in available}
    missing = [n for n in names if n not in by_name]
    if missing:
        raise ApiToolsetError(f"The spec has no operation {missing[0]}.")
    wanted = set(names)
    return [op for op in available if op.tool_name in wanted]


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


def credentials_still_apply(old: ApiToolsetInstance, new: ApiToolsetInstance) -> bool:
    """False when an edit changes the scheme, or where the credentials would be sent: a
    credential a member gave for one host must not go to another without them reconnecting."""
    if old.auth_scheme.name != new.auth_scheme.name:
        return False
    if _origin(old.base_url) != _origin(new.base_url):
        return False
    return _origin(resolve_url(old.base_url, old.auth_scheme.token_url)) == _origin(
        resolve_url(new.base_url, new.auth_scheme.token_url))


def _origin(url: str) -> tuple[str, str, Optional[int]]:
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    return scheme, (parts.hostname or "").lower(), parts.port or {"http": 80, "https": 443}.get(scheme)


def credential_owner(instance: ApiToolsetInstance, user_id: str) -> str:
    return SHARED_CREDENTIAL_OWNER if instance.use_shared_credential else user_id


def checked_credentials(scheme: AuthScheme, submitted: ApiToolsetCredentials) -> ApiToolsetCredentials:
    """Only the fields `scheme` uses, all present. Raises `ApiToolsetError` naming what is missing."""
    def need(value: Optional[str], what: str) -> str:
        if not value or not value.strip():
            raise ApiToolsetError(f"Enter the {what}.")
        return value.strip()

    if scheme.type is AuthType.API_KEY:
        return ApiToolsetCredentials(api_key=need(submitted.api_key, "API key"))
    if scheme.type is AuthType.HTTP_BEARER:
        return ApiToolsetCredentials(token=need(submitted.token, "token"))
    if scheme.type is AuthType.HTTP_BASIC:
        return ApiToolsetCredentials(username=need(submitted.username, "username"), password=need(submitted.password, "password"))
    if scheme.type is AuthType.OAUTH2_CLIENT_CREDENTIALS:
        return ApiToolsetCredentials(client_id=need(submitted.client_id, "client ID"),
                                     client_secret=(submitted.client_secret or "").strip() or None)
    if scheme.type is AuthType.OAUTH2_AUTHORIZATION_CODE:
        raise ApiToolsetError("This API action uses OAuth sign-in. Sign in instead of entering credentials.")
    raise ApiToolsetError("This API action needs no credentials.")


async def is_connected(store: ApiToolsetStore, instance: ApiToolsetInstance, user_id: str) -> bool:
    if instance.auth_scheme.type is AuthType.NONE:
        return True
    creds = await store.get_credentials(instance.id, credential_owner(instance, user_id))
    if creds is None:
        return False
    return creds.oauth is not None if instance.auth_scheme.type is AuthType.OAUTH2_AUTHORIZATION_CODE else True


async def connected_tool_refs(store: ApiToolsetStore, org_id: str, user_id: str) -> list[str]:
    """Every operation of every toolset `user_id` can call now, as `"<instanceId>:<toolName>"`:
    what the assistant, which has no attachments of its own, offers."""
    refs: list[str] = []
    for instance in await store.list_instances(org_id):
        if await is_connected(store, instance, user_id):
            refs += [f"{instance.id}:{op.tool_name}" for op in instance.operations]
    return refs


async def build_executor(
    store: ApiToolsetStore,
    instance: ApiToolsetInstance,
    owner_id: str,
    *,
    transport: Optional[httpx.AsyncBaseTransport] = None,
) -> OpenAPIHttpExecutor:
    """The executor for `owner_id`'s calls to `instance`. Raises `ApiToolsetAuthError` when
    that owner has no credential yet."""
    scheme = instance.auth_scheme
    creds = ApiToolsetCredentials()
    if scheme.type is not AuthType.NONE:
        stored = await store.get_credentials(instance.id, owner_id)
        if stored is None:
            raise ApiToolsetAuthError(f"{instance.name} is not connected yet. Add its credentials first.")
        creds = stored
    oauth_app = await store.get_oauth_client(instance.id) if scheme.type is AuthType.OAUTH2_AUTHORIZATION_CODE else None

    if oauth_app is not None:
        creds = creds.model_copy(update={"client_id": oauth_app.client_id, "client_secret": oauth_app.client_secret})

    async def save_tokens(updated: ApiToolsetCredentials) -> None:
        if oauth_app is not None:
            # The app's secret lives with the instance, not in every user's credential.
            updated = updated.model_copy(update={"client_id": None, "client_secret": None})
        await store.save_credentials(instance.id, owner_id, updated)

    async def load_tokens() -> Optional[ApiToolsetCredentials]:
        return await store.get_credentials(instance.id, owner_id)

    auth = build_auth(scheme, creds, base_url=instance.base_url, allow_private=private_network_allowed(),
                      scopes=instance.scopes or None, save_tokens=save_tokens, load_tokens=load_tokens,
                      lock_key=f"{instance.id}/{owner_id}")
    return OpenAPIHttpExecutor(instance.base_url, auth, allow_private=private_network_allowed(),
                               timeout_seconds=instance.timeout_seconds, transport=transport)


async def test_call(
    executor: OpenAPIHttpExecutor,
    instance: ApiToolsetInstance,
    tool_name: Optional[str] = None,
    arguments: Optional[dict[str, Any]] = None,
) -> ApiResponse:
    """Run one read: the one named, else the first with no required arguments. Only reads,
    because this route skips the approval an agent's writes and deletes go through."""
    if tool_name:
        op = next((o for o in instance.operations if o.tool_name == tool_name), None)
        if op is None:
            raise ApiToolsetError(f"The API action has no operation {tool_name}.")
        if op.kind != "read":
            raise ApiToolsetError(f"{tool_name} changes data, so it can't be used to test the connection. Choose a read.")
    else:
        anonymous = instance.auth_scheme.type is AuthType.NONE
        op = next((o for o in instance.operations
                   if o.kind == "read" and not o.input_schema.get("required") and not (anonymous and o.requires_auth)), None)
        if op is None:
            raise ApiToolsetError("Choose an operation to test: none of the selected reads runs without arguments.")
    return await executor.call(op, arguments or {})


# ---------------------------------------------------------------------------
# OAuth authorization-code sign-in
# ---------------------------------------------------------------------------


async def start_sign_in(
    store: ApiToolsetStore,
    instance: ApiToolsetInstance,
    *,
    user_id: str,
    redirect_uri: str,
    desktop: bool = False,
) -> str:
    """The URL to send the browser to. The sign-in is remembered for 10 minutes. `desktop` marks
    the state so the callback page hands the result back to the desktop app."""
    scheme = instance.auth_scheme
    if scheme.type is not AuthType.OAUTH2_AUTHORIZATION_CODE:
        raise ApiToolsetError("This API action doesn't use OAuth sign-in.")
    oauth_app = await store.get_oauth_client(instance.id)
    if oauth_app is None:
        raise ApiToolsetError("An administrator has to add the OAuth app's client ID first.")
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    await store.save_sign_in_state(state, {
        "instanceId": instance.id,
        "orgId": instance.org_id,
        "userId": user_id,
        "ownerId": credential_owner(instance, user_id),
        "codeVerifier": verifier,
        "expiresAt": int(time.time()) + SIGN_IN_STATE_TTL_SECONDS,
    })
    params = {
        "response_type": "code",
        "client_id": oauth_app.client_id,
        "redirect_uri": redirect_uri,
        "state": mark_desktop_state(state, desktop=desktop),
        "code_challenge": _pkce_challenge(verifier),
        "code_challenge_method": "S256",
    }
    if instance.scopes:
        params["scope"] = " ".join(instance.scopes)
    authorization_url = resolve_url(instance.base_url, scheme.authorization_url)
    separator = "&" if "?" in authorization_url else "?"
    return f"{authorization_url}{separator}{urlencode(params)}"


async def finish_sign_in(
    store: ApiToolsetStore,
    *,
    state: str,
    code: str,
    user_id: str,
    org_id: str,
    redirect_uri: str,
) -> ApiToolsetInstance:
    """Exchange the code the API sent back and store the tokens. Only the person who
    started the sign-in can finish it, once."""
    record = await store.get_sign_in_state(state)
    # Checked before claiming, so someone else's callback can't use up this person's sign-in.
    if (record is None or record.get("userId") != user_id or record.get("orgId") != org_id
            or int(record.get("expiresAt") or 0) < time.time() or not await store.claim_sign_in_state(state)):
        raise ApiToolsetError("This sign-in link has expired or was already used. Start the sign-in again.")
    instance = await store.get_instance(org_id, str(record["instanceId"]))
    oauth_app = await store.get_oauth_client(str(record["instanceId"])) if instance else None
    if instance is None or oauth_app is None:
        raise ApiToolsetError("The API action was changed or removed during sign-in. Start the sign-in again.")
    token_url = resolve_url(instance.base_url, instance.auth_scheme.token_url)
    try:
        tokens = await exchange_code_for_token(
            token_url, oauth_app.client_id, oauth_app.client_secret, code, redirect_uri,
            code_verifier=str(record["codeVerifier"]), allow_private=private_network_allowed(),
        )
    except MCPOAuthError as e:
        raise ApiToolsetError("The API's OAuth server refused the sign-in. Try again, or check the OAuth app.") from e
    await store.save_credentials(instance.id, str(record["ownerId"]), ApiToolsetCredentials(oauth=tokens))
    return instance


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def oauth_client_from(client_id: Optional[str], client_secret: Optional[str]) -> Optional[OAuthClient]:
    """The admin's OAuth app from the form, or None when no client ID was entered."""
    if not client_id or not client_id.strip():
        return None
    return OAuthClient(client_id=client_id.strip(), client_secret=(client_secret or "").strip() or None)
