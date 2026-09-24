"""Generic OAuth 2.0 token exchange/refresh for MCP instances.

Single shared implementation used by both `api/routes/mcp_servers.py` (initial code
exchange) and `mcp_token_refresh_service.py` (background refresh) — the reference PR
duplicated this logic and its background path only parsed JSON responses, silently
breaking for form-encoded token endpoints (e.g. GitHub).
"""
import json as _json
import logging
from typing import Any, Optional
from urllib.parse import parse_qs

import httpx

from app.agents.mcp.models import OAuthTokens, utcnow

logger = logging.getLogger(__name__)

TOKEN_REQUEST_TIMEOUT_SECONDS = 15.0

_PERMANENT_REFRESH_ERROR_MARKERS = (
    "invalid_grant",
    "invalid_refresh_token",
    "refresh token is invalid",
    "refresh token has expired",
    "bad_refresh_token",
)


class MCPOAuthError(Exception):
    """Raised when a token exchange/refresh request fails."""


class MCPRefreshTokenInvalidError(MCPOAuthError):
    """The provider permanently rejected the refresh token — re-authentication is required."""


def _is_permanent_refresh_rejection(error_text: str) -> bool:
    lowered = error_text.lower()
    return any(marker in lowered for marker in _PERMANENT_REFRESH_ERROR_MARKERS)


def _parse_token_response(content_type: str, text: str, json_body: Optional[dict]) -> dict[str, Any]:
    """Parse a token endpoint response as JSON or form-encoded (GitHub-style)."""
    if json_body is not None:
        return json_body

    if "application/x-www-form-urlencoded" in content_type or "text/plain" in content_type:
        return _parse_form_encoded(text)

    # Declared content-type didn't match either — try JSON, then fall back to form-encoded.
    try:
        return _json.loads(text)
    except Exception:
        return _parse_form_encoded(text)


def _parse_form_encoded(text: str) -> dict[str, Any]:
    parsed = parse_qs(text, keep_blank_values=True)
    data: dict[str, Any] = {k: (v[0] if v else None) for k, v in parsed.items()}
    if data.get("expires_in"):
        try:
            data["expires_in"] = int(data["expires_in"])
        except (TypeError, ValueError):
            pass
    return data


async def _post_token_request(token_url: str, data: dict[str, Any]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=TOKEN_REQUEST_TIMEOUT_SECONDS) as client:
        resp = await client.post(token_url, data=data, headers={"Accept": "application/json"})
        content_type = resp.headers.get("content-type", "").lower()
        text = resp.text
        json_body = None
        if "application/json" in content_type:
            try:
                json_body = resp.json()
            except Exception:
                json_body = None

        if resp.status_code >= 400:
            if _is_permanent_refresh_rejection(text):
                raise MCPRefreshTokenInvalidError(
                    f"OAuth token request rejected ({resp.status_code}): {text}"
                )
            raise MCPOAuthError(f"OAuth token request failed ({resp.status_code}): {text}")

        return _parse_token_response(content_type, text, json_body)


async def exchange_code_for_token(
    token_url: str,
    client_id: str,
    client_secret: Optional[str],
    code: str,
    redirect_uri: str,
    code_verifier: Optional[str] = None,
) -> OAuthTokens:
    data: dict[str, Any] = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
    }
    if client_secret:
        data["client_secret"] = client_secret
    if code_verifier:
        data["code_verifier"] = code_verifier

    token_data = await _post_token_request(token_url, data)
    if "access_token" not in token_data:
        raise MCPOAuthError("OAuth token response missing access_token")

    return OAuthTokens(
        access_token=token_data["access_token"],
        token_type=token_data.get("token_type") or "Bearer",
        refresh_token=token_data.get("refresh_token"),
        expires_in=token_data.get("expires_in"),
        scope=token_data.get("scope"),
        token_url=token_url,
        created_at=utcnow(),
    )


async def refresh_access_token(
    token_url: str,
    client_id: str,
    client_secret: Optional[str],
    refresh_token: str,
) -> OAuthTokens:
    data: dict[str, Any] = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }
    if client_secret:
        data["client_secret"] = client_secret

    token_data = await _post_token_request(token_url, data)
    if "access_token" not in token_data:
        raise MCPOAuthError("OAuth refresh response missing access_token")

    return OAuthTokens(
        access_token=token_data["access_token"],
        token_type=token_data.get("token_type") or "Bearer",
        refresh_token=token_data.get("refresh_token") or refresh_token,
        expires_in=token_data.get("expires_in"),
        scope=token_data.get("scope"),
        token_url=token_url,
        created_at=utcnow(),
    )
