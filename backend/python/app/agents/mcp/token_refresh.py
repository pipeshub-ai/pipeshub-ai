"""Shared MCP OAuth token-refresh orchestration.

`app/agents/mcp/oauth_client.py` already centralizes the raw HTTP token exchange/refresh
call (JSON + form-encoded parsing). What used to be duplicated on top of it was the
"resolve which OAuth client (DCR vs admin-configured) backs this instance/owner, load the
credential record, refresh, persist" plumbing — present almost identically in
`api/routes/mcp_servers.py::refresh_oauth_token` and
`connectors/core/base/token_service/mcp_token_refresh_service.py`. This module is the one
place that plumbing lives now; both call sites, plus the agent-loop runtime's on-demand
refresh (`agent_loop/mcp_session.py`), delegate to it.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Optional

from app.agents.constants.mcp_server_constants import (
    get_mcp_credentials_path,
    get_mcp_dcr_client_path,
    get_mcp_instance_path,
    get_mcp_oauth_client_config_path,
    get_mcp_refresh_lock_path,
    get_mcp_shared_dcr_client_path,
)
from app.agents.mcp import cimd
from app.agents.mcp import oauth_client as oauth_client_module
from app.agents.mcp.models import OAuthTokens
from app.agents.mcp.url_guard import MCPUrlPolicy
from app.config.configuration_service import ConfigurationService
from app.utils.time_conversion import get_epoch_timestamp_in_ms

logger = logging.getLogger(__name__)

__all__ = [
    "MCPTokenRefreshError",
    "resolve_client_credentials",
    "refresh_credential_record",
]


# Two refreshers that read the same refresh token both call the token endpoint, and with a
# provider that rotates refresh tokens the loser persists one the provider has already
# invalidated — the user is silently logged out. The refreshers live in different processes
# (the connectors service's timer and `/oauth/refresh`, the query service's on-demand refresh,
# and every replica of each), so the lock that prevents it is a key in the shared config
# store; the asyncio lock only keeps one process from polling that key against itself.
# Never evicted: one entry per instance/owner pair ever refreshed in this process.
_refresh_locks: "defaultdict[tuple[str, str], asyncio.Lock]" = defaultdict(asyncio.Lock)

# Longer than one refresh can take (the token request times out at 15s), so a live holder
# never loses the lock; short enough that a crashed holder blocks refreshes only briefly.
CLUSTER_LOCK_TTL_SECONDS = 60
CLUSTER_LOCK_WAIT_SECONDS = 20.0
_CLUSTER_LOCK_POLL_SECONDS = 0.25


@asynccontextmanager
async def _cluster_refresh_lock(instance_id: str, owner_id: str, config_service: ConfigurationService):  # noqa: ANN202
    key = get_mcp_refresh_lock_path(instance_id, owner_id)
    token = uuid.uuid4().hex
    deadline = time.monotonic() + CLUSTER_LOCK_WAIT_SECONDS
    while not await config_service.create_config_if_absent(key, {"owner": token}, ttl_seconds=CLUSTER_LOCK_TTL_SECONDS):
        if time.monotonic() >= deadline:
            raise MCPTokenRefreshError(
                f"Another process is still refreshing the token at {get_mcp_credentials_path(instance_id, owner_id)}"
            )
        await asyncio.sleep(_CLUSTER_LOCK_POLL_SECONDS)
    try:
        yield
    finally:
        # Only our own lock: after an expiry another holder may own the key now.
        current = await config_service.get_config(key, default=None, use_cache=False)
        if isinstance(current, dict) and current.get("owner") == token:
            await config_service.delete_config(key)


class MCPTokenRefreshError(ValueError):
    """Raised when a credential record cannot be refreshed (missing refresh token,
    missing tokenUrl, or no resolvable OAuth client) — distinct from
    `oauth_client_module.MCPOAuthError`/`MCPRefreshTokenInvalidError`, which are raised by
    the HTTP call itself and propagate through this module unchanged. Subclasses
    `ValueError` for compatibility with callers (and tests) written against the pre-Phase-2
    `_perform_token_refresh`, which raised a bare `ValueError` for these same conditions."""


async def resolve_client_credentials(
    instance_id: str,
    owner_id: str,
    config_service: ConfigurationService,
    *,
    issued_by: Optional[str] = None,
) -> tuple[Optional[str], Optional[str]]:
    """Resolve `(clientId, clientSecret)` for `instance_id`/`owner_id`.

    `issued_by` (the client the tokens record) is found wherever it is stored — legacy
    per-owner DCR, shared DCR (current or retired) or the admin's static app — or not at all,
    since refreshing with any other client fails. Tokens from before that was recorded use
    the order sign-in used then: legacy per-owner DCR, shared DCR, static app; a retired
    shared client is the one that issued them, so it comes before the current one.
    `owner_id` is a user id or, for a service-account agent, its agentKey.
    """
    legacy = await config_service.get_config(get_mcp_dcr_client_path(instance_id, owner_id), default=None)
    shared = await config_service.get_config(get_mcp_shared_dcr_client_path(instance_id), default=None)
    static = await static_oauth_client(config_service, instance_id)
    if issued_by:
        candidates = [
            client for client in (*registered_clients(legacy), *registered_clients(shared), static)
            if client and client["clientId"] == issued_by
        ]
    else:
        candidates = [
            client for client in (*registered_clients(legacy), *reversed(registered_clients(shared)), static)
            if client
        ]
    if candidates:
        return candidates[0]["clientId"], candidates[0].get("clientSecret")
    # A client metadata document is its own client: nothing stored, nothing secret.
    if cimd.is_client_metadata_url(issued_by):
        return issued_by, None
    return None, None


# A shared client replaced while its secret was still good is kept inside the new record, so
# the tokens it issued keep refreshing instead of signing everyone out.
PREVIOUS_CLIENT_FIELD = "previousClient"
# Set on a shared client the provider rejected: new sign-ins register another, while the
# tokens it issued keep trying it, so one spurious rejection doesn't sign everyone out.
REJECTED_AT_FIELD = "rejectedAt"


def registered_clients(record: object) -> list[dict]:
    """The dynamically registered client in `record`, then the one it replaced, if kept."""
    if not isinstance(record, dict) or not record.get("clientId"):
        return []
    previous = record.get(PREVIOUS_CLIENT_FIELD)
    return [record, previous] if isinstance(previous, dict) and previous.get("clientId") else [record]


async def static_oauth_client(config_service: ConfigurationService, instance_id: str) -> Optional[dict]:
    """The admin's static OAuth app for the instance, from its own org or the one it inherits from."""
    from app.edition_config import resolve_instance_owner_config_service

    config = await config_service.get_config(get_mcp_oauth_client_config_path(instance_id), default=None)
    if not isinstance(config, dict) or not config.get("clientId"):
        owner_svc = await resolve_instance_owner_config_service(instance_id, config_service)
        if owner_svc is not None and owner_svc is not config_service:
            config = await owner_svc.get_config(get_mcp_oauth_client_config_path(instance_id), default=None)
    return config if isinstance(config, dict) and config.get("clientId") else None


async def retire_rejected_dcr_client(
    config_service: ConfigurationService, instance_id: str, owner_id: str, client_id: Optional[str],
) -> None:
    """The provider answered `invalid_client` for `client_id`, a dynamically registered client:
    stop new sign-ins from using it, so the next one registers a new client. An owner's legacy
    client serves only that owner and is deleted; the shared one is marked (`REJECTED_AT_FIELD`),
    and a retired one, whose tokens are all that still use it, is dropped. An admin's static
    app is left alone; only an admin can fix that."""
    if not client_id:
        return
    legacy_path = get_mcp_dcr_client_path(instance_id, owner_id)
    legacy = registered_clients(await config_service.get_config(legacy_path, default=None, use_cache=False))
    if legacy and legacy[0]["clientId"] == client_id:
        logger.warning(f"Removing OAuth client {client_id} for MCP instance {instance_id}: the provider rejected it")
        await config_service.delete_config(legacy_path)

    shared_path = get_mcp_shared_dcr_client_path(instance_id)
    shared = registered_clients(await config_service.get_config(shared_path, default=None, use_cache=False))
    if shared and shared[0]["clientId"] == client_id and not shared[0].get(REJECTED_AT_FIELD):
        logger.warning(f"OAuth client {client_id} for MCP instance {instance_id} was rejected; the next sign-in registers another")
        await config_service.set_config(shared_path, {**shared[0], REJECTED_AT_FIELD: get_epoch_timestamp_in_ms()})
    elif len(shared) > 1 and shared[1]["clientId"] == client_id:
        logger.warning(f"Removing retired OAuth client {client_id} for MCP instance {instance_id}: the provider rejected it")
        await config_service.set_config(shared_path, {key: value for key, value in shared[0].items() if key != PREVIOUS_CLIENT_FIELD})


async def refresh_credential_record(
    instance_id: str,
    owner_id: str,
    config_service: ConfigurationService,
    *,
    stale_access_token: Optional[str] = None,
) -> OAuthTokens:
    """Refresh the OAuth tokens stored at `/services/mcp/credentials/{instance_id}/{owner_id}`
    and persist the result back to the same record.

    `stale_access_token` is the token the caller found wanting. If the stored one differs by
    the time the lock is held, another process already refreshed, and its tokens are returned
    without calling the endpoint again (which would rotate the refresh token a second time).
    `None` always refreshes.

    Raises:
        MCPTokenRefreshError: no credential record, no refresh token, no persisted tokenUrl,
            or no resolvable OAuth client — none of these reach the token endpoint at all.
        oauth_client_module.MCPRefreshTokenInvalidError: the provider permanently rejected
            the refresh token (re-authentication required) — subclass of MCPOAuthError.
        oauth_client_module.MCPOAuthError: the token endpoint request failed otherwise.
    """
    # A provider that rotates refresh tokens has spent the old one once it answers, so a caller
    # that gives up (Stop, a listing's deadline) must not abandon the save halfway.
    refresh = asyncio.ensure_future(
        _refresh_credential_record_under_locks(instance_id, owner_id, config_service, stale_access_token)
    )
    refresh.add_done_callback(_log_abandoned_refresh_failure)
    return await asyncio.shield(refresh)


async def _refresh_credential_record_under_locks(
    instance_id: str,
    owner_id: str,
    config_service: ConfigurationService,
    stale_access_token: Optional[str],
) -> OAuthTokens:
    async with _refresh_locks[(instance_id, owner_id)], _cluster_refresh_lock(instance_id, owner_id, config_service):
        return await _refresh_credential_record_locked(
            instance_id, owner_id, config_service, stale_access_token,
        )


def _log_abandoned_refresh_failure(task: "asyncio.Future[OAuthTokens]") -> None:
    # Retrieved here so a refresh whose caller left doesn't end as "exception never retrieved";
    # a caller that stayed gets the same exception from the shield.
    if not task.cancelled() and task.exception() is not None:
        logger.debug(f"MCP token refresh failed: {task.exception()}")


async def _refresh_credential_record_locked(
    instance_id: str,
    owner_id: str,
    config_service: ConfigurationService,
    stale_access_token: Optional[str],
) -> OAuthTokens:
    cred_path = get_mcp_credentials_path(instance_id, owner_id)
    record = await config_service.get_config(cred_path, default=None, use_cache=False)
    if not isinstance(record, dict):
        raise MCPTokenRefreshError(f"No MCP credential record found at {cred_path}")

    tokens_dict = record.get("oauthTokens")
    if tokens_dict is None:
        tokens_dict = {}
    elif not isinstance(tokens_dict, dict):
        raise MCPTokenRefreshError(f"Invalid OAuth token record at {cred_path}; re-authentication is required")

    stored_access_token = tokens_dict.get("accessToken") or tokens_dict.get("access_token")
    if stale_access_token and stored_access_token and stored_access_token != stale_access_token:
        return OAuthTokens.model_validate(tokens_dict)

    refresh_token = tokens_dict.get("refreshToken") or tokens_dict.get("refresh_token")
    if not refresh_token:
        raise MCPTokenRefreshError(f"No refresh token available at {cred_path}; re-authentication is required")

    token_url = tokens_dict.get("tokenUrl") or tokens_dict.get("token_url")
    if not token_url:
        raise MCPTokenRefreshError(f"No tokenUrl persisted for {cred_path}; cannot refresh without re-authenticating")

    client_id, client_secret = await resolve_client_credentials(
        instance_id, owner_id, config_service, issued_by=tokens_dict.get("clientId"),
    )
    if not client_id:
        raise MCPTokenRefreshError(f"No OAuth client configuration found for {cred_path}")

    # Only an org instance may reach the private network; a personal one, like a missing one,
    # refreshes public-only, so it needn't be looked up.
    instance = await config_service.get_config(get_mcp_instance_path(instance_id), default=None, use_cache=False)
    try:
        new_tokens = await oauth_client_module.refresh_access_token(
            token_url=token_url, client_id=client_id, client_secret=client_secret, refresh_token=refresh_token,
            # The token URL may have come from the server's metadata; a missing instance is public-only.
            allow_private=isinstance(instance, dict) and MCPUrlPolicy.for_instance(instance).private_ok(token_url),
            resource=tokens_dict.get("resource"),
            auth_method=tokens_dict.get("tokenEndpointAuthMethod") or tokens_dict.get("token_endpoint_auth_method"),
        )
    except oauth_client_module.MCPOAuthError as e:
        if e.rejected_client:
            await retire_rejected_dcr_client(config_service, instance_id, owner_id, client_id)
        raise
    new_tokens = new_tokens.model_copy(update={"client_id": client_id})

    # The record may have changed during the request: Disconnect, Reconnect and a credentials
    # reset delete it without this lock, and a fresh sign-in replaces it. Writing our copy back
    # would restore credentials the user removed, or overwrite the newer grant.
    current = await config_service.get_config(cred_path, default=None, use_cache=False)
    if not isinstance(current, dict):
        raise MCPTokenRefreshError(f"The MCP credential at {cred_path} was removed while it was being refreshed")
    current_tokens = current.get("oauthTokens") if isinstance(current.get("oauthTokens"), dict) else {}
    if (current_tokens.get("refreshToken") or current_tokens.get("refresh_token")) != refresh_token:
        return OAuthTokens.model_validate(current_tokens)

    current["oauthTokens"] = new_tokens.model_dump(by_alias=True, mode="json")
    current["updatedAt"] = get_epoch_timestamp_in_ms()
    await _save_refreshed_record(config_service, cred_path, current)
    return new_tokens


async def _save_refreshed_record(config_service: ConfigurationService, cred_path: str, record: dict) -> None:
    """A provider that rotates refresh tokens has already invalidated the old one, so a lost write
    would sign the user out at the next refresh. One retry; then say so loudly. The new tokens
    are still returned, since they work for this request."""
    for attempt in (1, 2):
        try:
            if await config_service.set_config(cred_path, record):
                return
        except Exception as e:
            logger.warning(f"Saving refreshed MCP tokens to {cred_path} failed (attempt {attempt}): {e}")
    logger.error(f"Refreshed MCP tokens could not be saved to {cred_path}; the user may have to sign in again")
