"""
Dynamic Client Registration (DCR) helpers for MCP servers.

Implements RFC 7591 dynamic client registration + PKCE (RFC 7636) for MCP
servers that expose /.well-known/oauth-authorization-server metadata.

Usage flow:
  1. discover_auth_metadata(mcp_url)            — probe well-known endpoint
  2. register_dcr_client(...)                   — create OAuth client dynamically
  3. build_dcr_authorization_url(...)           — build redirect URL with PKCE
  4. exchange_dcr_code(...)                     — exchange auth code for tokens
  5. refresh_dcr_token(...)                     — refresh access token
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
from typing import Any
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

_WELL_KNOWN_PATH = "/.well-known/oauth-authorization-server"
_TIMEOUT = 15.0


async def discover_auth_metadata(mcp_url: str) -> dict[str, Any] | None:
    """
    Probe the MCP server's OAuth authorization server metadata endpoint.

    Returns parsed metadata dict if the server supports DCR (i.e. the response
    contains a `registration_endpoint`), otherwise returns None.

    Args:
        mcp_url: Base URL of the MCP server (e.g. https://mcp.atlassian.com/v1/mcp)

    Returns:
        Metadata dict on success, None if not available / not DCR-capable.
    """
    base = mcp_url.rstrip("/")
    # Strip any path suffix to get the server root
    # e.g. https://mcp.atlassian.com/v1/mcp -> https://mcp.atlassian.com
    from urllib.parse import urlparse, urlunparse
    parsed = urlparse(base)
    server_root = urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))
    well_known_url = f"{server_root}{_WELL_KNOWN_PATH}"

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(well_known_url)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict) and data.get("registration_endpoint"):
                logger.info(
                    "DCR metadata discovered at %s (registration_endpoint: %s)",
                    well_known_url,
                    data.get("registration_endpoint"),
                )
                return data
            logger.debug(
                "Well-known at %s returned 200 but no registration_endpoint — treating as standard OAuth",
                well_known_url,
            )
            return None
        logger.debug(
            "Well-known probe at %s returned HTTP %d — treating as standard OAuth",
            well_known_url,
            resp.status_code,
        )
        return None
    except Exception as exc:
        logger.debug(
            "Well-known probe at %s failed (%s) — treating as standard OAuth",
            well_known_url,
            exc,
        )
        return None


async def register_dcr_client(
    registration_endpoint: str,
    redirect_uri: str,
    scopes: list[str],
    client_name: str = "PipesHub MCP",
) -> dict[str, Any]:
    """
    Dynamically register an OAuth client with the authorization server.

    Args:
        registration_endpoint: URL from the well-known metadata.
        redirect_uri: The OAuth redirect URI for this app.
        scopes: List of OAuth scopes to request.
        client_name: Human-readable name registered with the provider.

    Returns:
        Dict containing at least `client_id` and optionally `client_secret`.

    Raises:
        RuntimeError: If registration fails.
    """
    payload: dict[str, Any] = {
        "client_name": client_name,
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "client_secret_post",
        "scope": " ".join(scopes),
    }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                registration_endpoint,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
    except httpx.RequestError as exc:
        raise RuntimeError(f"DCR registration network error: {exc}") from exc

    if resp.status_code not in (200, 201):
        raise RuntimeError(
            f"DCR registration failed (HTTP {resp.status_code}): {resp.text[:400]}"
        )

    data = resp.json()
    if not data.get("client_id"):
        raise RuntimeError(f"DCR registration response missing client_id: {data}")

    logger.info("DCR client registered successfully (client_id: %s)", data["client_id"])
    return data


def generate_pkce_pair() -> tuple[str, str]:
    """
    Generate a PKCE code_verifier and code_challenge pair.

    Returns:
        (code_verifier, code_challenge) — code_challenge is S256-hashed.
    """
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


def build_dcr_authorization_url(
    authorization_endpoint: str,
    client_id: str,
    redirect_uri: str,
    scopes: list[str],
    state: str,
    code_verifier: str,
) -> str:
    """
    Build the OAuth authorization URL with PKCE.

    Args:
        authorization_endpoint: The authorization URL from metadata.
        client_id: DCR-generated client ID.
        redirect_uri: OAuth redirect URI.
        scopes: List of OAuth scopes.
        state: CSRF state token.
        code_verifier: PKCE code verifier (plain text).

    Returns:
        Full authorization URL to redirect the user to.
    """
    code_challenge = _derive_code_challenge(code_verifier)

    params: dict[str, str] = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(scopes),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }

    if scopes and "offline_access" in scopes:
        params["prompt"] = "consent"

    return f"{authorization_endpoint}?{urlencode(params)}"


def _derive_code_challenge(code_verifier: str) -> str:
    """Compute S256 code_challenge from a code_verifier."""
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


async def exchange_dcr_code(
    token_endpoint: str,
    code: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str,
    code_verifier: str,
) -> dict[str, Any]:
    """
    Exchange an authorization code for tokens using DCR client credentials + PKCE.

    Args:
        token_endpoint: Token URL from DCR metadata.
        code: Authorization code from the callback.
        redirect_uri: Must match the redirect URI used during authorization.
        client_id: DCR-registered client ID.
        client_secret: DCR-registered client secret (may be empty).
        code_verifier: PKCE code verifier (plain text).

    Returns:
        Token response dict containing access_token, refresh_token, etc.

    Raises:
        RuntimeError: If the exchange fails.
    """
    payload: dict[str, str] = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": code_verifier,
    }
    if client_secret:
        payload["client_secret"] = client_secret

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                token_endpoint,
                data=payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    except httpx.RequestError as exc:
        raise RuntimeError(f"DCR token exchange network error: {exc}") from exc

    if resp.status_code != 200:
        raise RuntimeError(
            f"DCR token exchange failed (HTTP {resp.status_code}): {resp.text[:400]}"
        )

    data = resp.json()
    if not data.get("access_token"):
        raise RuntimeError(f"DCR token exchange returned no access_token: {data}")

    return data


async def refresh_dcr_token(
    token_endpoint: str,
    refresh_token: str,
    client_id: str,
    client_secret: str,
) -> dict[str, Any]:
    """
    Refresh an access token using DCR client credentials.

    Args:
        token_endpoint: Token URL from DCR metadata.
        refresh_token: The current refresh token.
        client_id: DCR-registered client ID.
        client_secret: DCR-registered client secret (may be empty).

    Returns:
        Token response dict containing new access_token, etc.

    Raises:
        RuntimeError: If the refresh fails.
    """
    payload: dict[str, str] = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }
    if client_secret:
        payload["client_secret"] = client_secret

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                token_endpoint,
                data=payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    except httpx.RequestError as exc:
        raise RuntimeError(f"DCR token refresh network error: {exc}") from exc

    if resp.status_code != 200:
        raise RuntimeError(
            f"DCR token refresh failed (HTTP {resp.status_code}): {resp.text[:400]}"
        )

    data = resp.json()
    if not data.get("access_token"):
        raise RuntimeError(f"DCR token refresh returned no access_token: {data}")

    return data
