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
from collections import defaultdict
from typing import Optional

from app.agents.constants.mcp_server_constants import (
    get_mcp_credentials_path,
    get_mcp_dcr_client_path,
    get_mcp_oauth_client_config_path,
    get_mcp_shared_dcr_client_path,
)
from app.agents.mcp import oauth_client as oauth_client_module
from app.agents.mcp.models import OAuthTokens
from app.config.configuration_service import ConfigurationService
from app.utils.time_conversion import get_epoch_timestamp_in_ms

logger = logging.getLogger(__name__)

__all__ = [
    "MCPTokenRefreshError",
    "resolve_client_credentials",
    "refresh_credential_record",
]


# Keyed by (instance_id, owner_id) so the three independent callers named in this module's
# docstring — the `/oauth/refresh` route, `MCPTokenRefreshService`'s background sweep, and
# the agent-loop's on-demand refresh (`mcp_session.py::_refresh_and_reopen`) — serialize
# against EACH OTHER, not just against themselves. Without this, two callers racing on an
# expired token could both read the same (still-valid) refresh token, both call the token
# endpoint, and — with a provider that rotates refresh tokens — the loser's write silently
# clobbers the winner's freshly-issued tokens with a refresh token the provider already
# invalidated. Never evicted, same tradeoff as `MCPTokenRefreshService`'s own per-path lock
# dicts: unbounded but slow-growing (one entry per instance/owner pair ever refreshed).
_refresh_locks: "defaultdict[tuple[str, str], asyncio.Lock]" = defaultdict(asyncio.Lock)


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
    fallback_config_services: Optional[list[ConfigurationService]] = None,
) -> tuple[Optional[str], Optional[str]]:
    """Resolve `(clientId, clientSecret)` for `instance_id`/`owner_id` — legacy per-owner DCR
    client first (so already-issued tokens keep refreshing against the exact client they were
    minted against), then the shared per-instance DCR client, then the admin-configured
    static OAuth client. `owner_id` is a user id or, for a service-account agent, its
    agentKey — both are opaque owner identifiers in the credential path.

    ``fallback_config_services``, when provided, are tried for the static OAuth client
    config when the primary service doesn't have one.
    """
    dcr_client = await config_service.get_config(get_mcp_dcr_client_path(instance_id, owner_id), default=None)
    if isinstance(dcr_client, dict) and dcr_client.get("clientId"):
        return dcr_client["clientId"], dcr_client.get("clientSecret")

    shared_dcr_client = await config_service.get_config(get_mcp_shared_dcr_client_path(instance_id), default=None)
    if isinstance(shared_dcr_client, dict) and shared_dcr_client.get("clientId"):
        return shared_dcr_client["clientId"], shared_dcr_client.get("clientSecret")

    oauth_client_config = await config_service.get_config(get_mcp_oauth_client_config_path(instance_id), default=None)
    if isinstance(oauth_client_config, dict):
        return oauth_client_config.get("clientId"), oauth_client_config.get("clientSecret")

    for fb_svc in (fallback_config_services or []):
        fb_config = await fb_svc.get_config(get_mcp_oauth_client_config_path(instance_id), default=None)
        if isinstance(fb_config, dict) and fb_config.get("clientId"):
            return fb_config.get("clientId"), fb_config.get("clientSecret")

    return None, None


async def refresh_credential_record(
    instance_id: str,
    owner_id: str,
    config_service: ConfigurationService,
    fallback_config_services: Optional[list[ConfigurationService]] = None,
) -> OAuthTokens:
    """Refresh the OAuth tokens stored at `/services/mcp/credentials/{instance_id}/{owner_id}`
    and persist the result back to the same record.

    ``fallback_config_services`` are forwarded to ``resolve_client_credentials``.

    Raises:
        MCPTokenRefreshError: no credential record, no refresh token, no persisted tokenUrl,
            or no resolvable OAuth client — none of these reach the token endpoint at all.
        oauth_client_module.MCPRefreshTokenInvalidError: the provider permanently rejected
            the refresh token (re-authentication required) — subclass of MCPOAuthError.
        oauth_client_module.MCPOAuthError: the token endpoint request failed otherwise.
    """
    async with _refresh_locks[(instance_id, owner_id)]:
        return await _refresh_credential_record_locked(
            instance_id, owner_id, config_service, fallback_config_services,
        )


async def _refresh_credential_record_locked(
    instance_id: str,
    owner_id: str,
    config_service: ConfigurationService,
    fallback_config_services: Optional[list[ConfigurationService]] = None,
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

    refresh_token = tokens_dict.get("refreshToken") or tokens_dict.get("refresh_token")
    if not refresh_token:
        raise MCPTokenRefreshError(f"No refresh token available at {cred_path}; re-authentication is required")

    token_url = tokens_dict.get("tokenUrl") or tokens_dict.get("token_url")
    if not token_url:
        raise MCPTokenRefreshError(f"No tokenUrl persisted for {cred_path}; cannot refresh without re-authenticating")

    client_id, client_secret = await resolve_client_credentials(
        instance_id, owner_id, config_service, fallback_config_services,
    )
    if not client_id:
        raise MCPTokenRefreshError(f"No OAuth client configuration found for {cred_path}")

    new_tokens = await oauth_client_module.refresh_access_token(
        token_url=token_url, client_id=client_id, client_secret=client_secret, refresh_token=refresh_token,
    )

    record["oauthTokens"] = new_tokens.model_dump(by_alias=True, mode="json")
    record["updatedAt"] = get_epoch_timestamp_in_ms()
    await config_service.set_config(cred_path, record)
    return new_tokens
