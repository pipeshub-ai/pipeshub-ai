"""Generic OAuth 2.0 token exchange/refresh for MCP instances.

Single shared implementation used by both `api/routes/mcp_servers.py` (initial code
exchange) and `mcp_token_refresh_service.py` (background refresh) — the reference PR
duplicated this logic and its background path only parsed JSON responses, silently
breaking for form-encoded token endpoints (e.g. GitHub).
"""
import base64
import json as _json
import logging
from typing import Any, Optional
from urllib.parse import parse_qs, quote_plus

import httpx

from app.agents.mcp.errors import MCPUrlBlockedError
from app.agents.mcp.models import OAuthTokens, TokenEndpointAuthMethod, utcnow
from app.agents.mcp.url_guard import GuardedTransport

logger = logging.getLogger(__name__)

TOKEN_REQUEST_TIMEOUT_SECONDS = 15.0

# RFC 6749 §5.2 errors a retry can't fix: the grant or the client registration is no longer
# valid, so the user has to sign in again. GitHub's own codes arrive with HTTP 200.
_PERMANENT_ERROR_CODES = frozenset({
    "invalid_grant", "invalid_client", "unauthorized_client", "unsupported_grant_type",
    "invalid_scope", "invalid_request", "bad_refresh_token", "incorrect_client_credentials",
})
_PERMANENT_REFRESH_ERROR_MARKERS = (
    "invalid_grant",
    "invalid_refresh_token",
    "refresh token is invalid",
    "refresh token has expired",
    "bad_refresh_token",
)
_MAX_ERROR_TEXT_CHARS = 500


class MCPOAuthError(Exception):
    """Raised when a token exchange/refresh request fails. `error_code` is the RFC 6749 `error`
    the provider sent, when it sent one."""

    def __init__(self, message: str = "", *, error_code: Optional[str] = None) -> None:
        super().__init__(message)
        self.error_code = error_code

    @property
    def rejected_client(self) -> bool:
        """The provider doesn't know the client (or its secret) any more."""
        return (self.error_code or "").lower() == "invalid_client"


class MCPRefreshTokenInvalidError(MCPOAuthError):
    """The provider permanently rejected the refresh token — re-authentication is required."""


def _is_permanent_refresh_rejection(error_text: str, error_code: Optional[str] = None) -> bool:
    if error_code and error_code.lower() in _PERMANENT_ERROR_CODES:
        return True
    lowered = error_text.lower()
    return any(marker in lowered for marker in _PERMANENT_REFRESH_ERROR_MARKERS)


def _error_code(body: Any) -> Optional[str]:  # noqa: ANN401
    code = body.get("error") if isinstance(body, dict) else None
    return code if isinstance(code, str) and code else None


def _error_text(status_code: int, body: Any, text: str) -> str:  # noqa: ANN401
    code = _error_code(body)
    if code:
        description = body.get("error_description")
        return f"{code}: {description}" if isinstance(description, str) and description else code
    return text[:_MAX_ERROR_TEXT_CHARS] or f"HTTP {status_code}"


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


def _authenticate_client(
    data: dict[str, Any], client_id: str, client_secret: Optional[str], auth_method: Optional[str],
) -> tuple[str, dict[str, str]]:
    """Adds the client's identity to a token request, the way it was registered to send it.
    Returns the method used, which the tokens keep for their refresh, and any headers it needs.
    Without a secret there is nothing to prove, so that is `none` whatever was asked."""
    if not client_secret or auth_method == TokenEndpointAuthMethod.NONE.value:
        data["client_id"] = client_id
        return TokenEndpointAuthMethod.NONE.value, {}
    if auth_method == TokenEndpointAuthMethod.CLIENT_SECRET_BASIC.value:
        # RFC 6749 §2.3.1: each part form-encoded first, so a ':' in either survives the join.
        credentials = f"{quote_plus(client_id, safe='')}:{quote_plus(client_secret, safe='')}"
        return auth_method, {"Authorization": f"Basic {base64.b64encode(credentials.encode()).decode()}"}
    data["client_id"] = client_id
    data["client_secret"] = client_secret
    return TokenEndpointAuthMethod.CLIENT_SECRET_POST.value, {}


async def _post_token_request(
    token_url: str, data: dict[str, Any], *, allow_private: bool, headers: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    # The token URL can come from the remote server's own OAuth metadata, so it gets the
    # same guard as the MCP connection itself.
    transport = GuardedTransport(token_url, allow_private=allow_private)
    async with httpx.AsyncClient(timeout=TOKEN_REQUEST_TIMEOUT_SECONDS, trust_env=False, transport=transport) as client:
        try:
            resp = await client.post(token_url, data=data, headers={"Accept": "application/json", **(headers or {})})
        except MCPUrlBlockedError as e:
            raise MCPOAuthError(str(e)) from e
        content_type = resp.headers.get("content-type", "").lower()
        text = resp.text
        json_body = None
        if "application/json" in content_type:
            try:
                json_body = resp.json()
            except Exception:
                json_body = None

        body = _parse_token_response(content_type, text, json_body) if text or json_body is not None else {}
        # Some providers (GitHub) report an error with HTTP 200, so the body decides too.
        error_code = _error_code(body)
        if resp.status_code >= 400 or error_code:
            detail = _error_text(resp.status_code, body, text)
            if _is_permanent_refresh_rejection(text, error_code):
                raise MCPRefreshTokenInvalidError(
                    f"OAuth token request rejected ({resp.status_code}): {detail}", error_code=error_code,
                )
            raise MCPOAuthError(f"OAuth token request failed ({resp.status_code}): {detail}", error_code=error_code)
        return body


async def exchange_code_for_token(
    token_url: str,
    client_id: str,
    client_secret: Optional[str],
    code: str,
    redirect_uri: str,
    code_verifier: Optional[str] = None,
    *,
    allow_private: bool,
    resource: Optional[str] = None,
    auth_method: Optional[str] = None,
) -> OAuthTokens:
    data: dict[str, Any] = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    }
    used_method, headers = _authenticate_client(data, client_id, client_secret, auth_method)
    if code_verifier:
        data["code_verifier"] = code_verifier
    if resource:
        data["resource"] = resource

    token_data = await _post_token_request(token_url, data, allow_private=allow_private, headers=headers)
    if "access_token" not in token_data:
        raise MCPOAuthError("OAuth token response missing access_token")

    return OAuthTokens(
        access_token=token_data["access_token"],
        token_type=token_data.get("token_type") or "Bearer",
        refresh_token=token_data.get("refresh_token"),
        expires_in=token_data.get("expires_in"),
        scope=token_data.get("scope"),
        token_url=token_url,
        resource=resource,
        token_endpoint_auth_method=used_method,
        created_at=utcnow(),
    )


async def refresh_access_token(
    token_url: str,
    client_id: str,
    client_secret: Optional[str],
    refresh_token: str,
    *,
    allow_private: bool,
    resource: Optional[str] = None,
    auth_method: Optional[str] = None,
) -> OAuthTokens:
    data: dict[str, Any] = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    used_method, headers = _authenticate_client(data, client_id, client_secret, auth_method)
    if resource:
        data["resource"] = resource

    token_data = await _post_token_request(token_url, data, allow_private=allow_private, headers=headers)
    if "access_token" not in token_data:
        raise MCPOAuthError("OAuth refresh response missing access_token")

    return OAuthTokens(
        access_token=token_data["access_token"],
        token_type=token_data.get("token_type") or "Bearer",
        refresh_token=token_data.get("refresh_token") or refresh_token,
        expires_in=token_data.get("expires_in"),
        scope=token_data.get("scope"),
        token_url=token_url,
        resource=resource,
        token_endpoint_auth_method=used_method,
        created_at=utcnow(),
    )
