"""Client ID Metadata Documents: PipesHub's OAuth client is a URL on this deployment, and the
authorization server reads the client's details from it (the MCP authorization spec's preferred
way since 2025-11-25). Nothing is registered, and there is no secret.

Used only when the server advertises support, the deployment's public address is https, and a
self-check finds the document there the way an outside server would. A server that refuses it
is remembered, so its next sign-in registers a client instead (DCR).
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import TYPE_CHECKING, Optional
from urllib.parse import urlsplit

import httpx

from app.agents.constants.mcp_server_constants import MCP_ROOT
from app.agents.mcp.url_guard import (
    MCPUrlPolicy,
    PerOriginGuardedTransport,
    assert_mcp_url_allowed,
)
from app.utils.env_utils import env_bool
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from app.agents.mcp.models import DiscoveredOAuthMetadata
    from app.config.configuration_service import ConfigurationService

logger = logging.getLogger(__name__)

# Served by the Node app at the frontend's address (`mcp_client_metadata.ts`).
CLIENT_METADATA_PATH = "/mcp-servers/oauth/client-metadata.json"
ENABLED_ENV = "MCP_OAUTH_CLIENT_METADATA_DOCUMENT"
_CHECK_TTL_SECONDS = 600.0
_CHECK_TIMEOUT_SECONDS = 5.0
_MAX_DOCUMENT_BYTES = 64 * 1024
# How long a server that refused the document gets DCR instead before it is tried again.
_REFUSAL_TTL_SECONDS = 7 * 24 * 3600

# (document URL, redirect URI) -> (checked at, served). Per process; failures are kept too, so a
# deployment that doesn't serve the document doesn't wait out the check on every sign-in.
_checks: dict[tuple[str, str], tuple[float, bool]] = {}


def enabled() -> bool:
    return env_bool(ENABLED_ENV, True)


def client_metadata_url(frontend_base: str) -> str:
    """The client id for `frontend_base`, the configured public address (no trailing slash),
    built the way the redirect URI is."""
    return f"{frontend_base}{CLIENT_METADATA_PATH}"


def is_client_metadata_url(client_id: Optional[str]) -> bool:
    if not isinstance(client_id, str):
        return False
    parts = urlsplit(client_id)
    return parts.scheme == "https" and bool(parts.hostname) and parts.path.endswith(CLIENT_METADATA_PATH)


def supported_by(discovered: Optional[DiscoveredOAuthMetadata]) -> bool:
    return bool(discovered and discovered.client_id_metadata_document_supported)


def server_key(discovered: DiscoveredOAuthMetadata) -> str:
    """The authorization server a refusal is remembered for."""
    return discovered.issuer or discovered.authorization_endpoint or discovered.token_endpoint or ""


def _refusal_path(server: str) -> str:
    return f"{MCP_ROOT}/cimd-refusals/{hashlib.sha256(server.encode('utf-8')).hexdigest()}"


async def refused_by(config_service: ConfigurationService, server: str) -> bool:
    return isinstance(await config_service.get_config(_refusal_path(server), default=None, use_cache=False), dict)


async def remember_refusal(config_service: ConfigurationService, server: str) -> None:
    if not server:
        return
    try:
        await config_service.set_config(
            _refusal_path(server), {"server": server, "refusedAt": get_epoch_timestamp_in_ms()},
            ttl_seconds=_REFUSAL_TTL_SECONDS,
        )
        logger.info("The authorization server %s refused PipesHub's client metadata document; using DCR there", server)
    except Exception:
        logger.warning("Couldn't remember that %s refused the client metadata document", server, exc_info=True)


async def document_is_served(url: str, redirect_uri: str) -> bool:
    """Whether the document is at `url` as an outside authorization server would fetch it: from a
    public address, with no redirect, JSON of at most 64 KiB, its `client_id` its own URL and
    `redirect_uri` among its `redirect_uris`. Remembered for 10 minutes, a failure too."""
    key = (url, redirect_uri)
    now = time.monotonic()
    cached = _checks.get(key)
    if cached is not None and now - cached[0] < _CHECK_TTL_SECONDS:
        return cached[1]
    served = await _fetch_and_check(url, redirect_uri)
    _checks[key] = (now, served)
    if not served:
        logger.info("PipesHub's client metadata document isn't served at %s; MCP sign-ins use DCR", url)
    return served


async def _fetch_and_check(url: str, redirect_uri: str) -> bool:
    try:
        await assert_mcp_url_allowed(url, allow_private=False)
        transport = PerOriginGuardedTransport(MCPUrlPolicy(allow_private=False))
        # Redirects stay off (the httpx default): an authorization server needn't follow them.
        async with httpx.AsyncClient(timeout=_CHECK_TIMEOUT_SECONDS, trust_env=False, transport=transport) as client:
            async with client.stream("GET", url, headers={"Accept": "application/json"}) as response:
                if response.status_code != 200 or "application/json" not in response.headers.get("content-type", "").lower():
                    return False
                body = b""
                async for chunk in response.aiter_bytes():
                    body += chunk
                    if len(body) > _MAX_DOCUMENT_BYTES:
                        return False
        document = json.loads(body)
    except Exception as e:
        logger.debug("Couldn't fetch the client metadata document at %s (%s)", url, type(e).__name__)
        return False
    if not isinstance(document, dict):
        return False
    redirect_uris = document.get("redirect_uris")
    return document.get("client_id") == url and isinstance(redirect_uris, list) and redirect_uri in redirect_uris
