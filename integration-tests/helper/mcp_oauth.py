"""
OAuth app + token helpers for MCP integration tests.

An MCP host (Cursor, Claude, ...) connects to ``{base}/mcp`` with an OAuth
access token minted for an OAuth app registered in PipesHub. These helpers
do the same with API calls only — no browser:

  1. ``create_oauth_app``      POST /api/v1/oauth-clients      (user session JWT)
  2. ``authorization_code_token``
        POST /api/v1/oauth2/authorize  consent=granted + PKCE  (user session JWT)
        POST /api/v1/oauth2/token      grant_type=authorization_code
  3. ``client_credentials_token``
        POST /api/v1/oauth2/token      grant_type=client_credentials

The user session JWT comes from ``helper.local_auth.obtain_user_session_token``.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import parse_qs, urlparse

import requests

DEFAULT_REDIRECT_URI = "http://localhost/callback"
ALL_GRANT_TYPES = ["authorization_code", "refresh_token", "client_credentials"]


@dataclass
class OAuthApp:
    id: str
    client_id: str
    client_secret: str
    scopes: list[str] = field(default_factory=list)
    redirect_uri: str = DEFAULT_REDIRECT_URI


def _error_detail(resp: requests.Response) -> str:
    try:
        payload = resp.json()
    except ValueError:
        return resp.text.strip()[:200]
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        if payload.get("message"):
            return str(payload["message"])
    return str(payload)[:200]


def _check(resp: requests.Response, what: str) -> dict:
    if resp.status_code >= 400:
        raise RuntimeError(f"{what} failed: HTTP {resp.status_code} - {_error_detail(resp)}")
    try:
        return resp.json()
    except ValueError:
        raise RuntimeError(f"{what} returned non-JSON response")


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def list_scope_names(base_url: str, user_jwt: str, timeout: int = 30) -> list[str]:
    """Every OAuth scope the caller may grant. Admins see the full list."""
    data = _check(
        requests.get(
            f"{base_url.rstrip('/')}/api/v1/oauth-clients/scopes",
            headers=_bearer(user_jwt),
            timeout=timeout,
        ),
        "list OAuth scopes",
    )
    scopes = data.get("scopes", data)
    # The backend groups scopes by category: {"Identity": [{"name": ...}, ...], ...}
    groups = scopes.values() if isinstance(scopes, dict) else [scopes]
    names = [s["name"] if isinstance(s, dict) else str(s) for group in groups for s in group]
    if not names:
        raise RuntimeError("list OAuth scopes returned no scopes")
    return names


def create_oauth_app(
    base_url: str,
    user_jwt: str,
    name: str,
    *,
    scopes: Optional[list[str]] = None,
    grant_types: Optional[list[str]] = None,
    redirect_uri: str = DEFAULT_REDIRECT_URI,
    timeout: int = 30,
) -> OAuthApp:
    """Register an OAuth app. Defaults to every scope and every grant type."""
    base_url = base_url.rstrip("/")
    scopes = scopes or list_scope_names(base_url, user_jwt, timeout)
    data = _check(
        requests.post(
            f"{base_url}/api/v1/oauth-clients",
            headers=_bearer(user_jwt),
            json={
                "name": name,
                "allowedGrantTypes": grant_types or ALL_GRANT_TYPES,
                "allowedScopes": scopes,
                "redirectUris": [redirect_uri],
            },
            timeout=timeout,
        ),
        "create OAuth app",
    )
    app = data.get("app") or {}
    app_id = app.get("id") or app.get("_id")
    if not app_id or not app.get("clientId") or not app.get("clientSecret"):
        raise RuntimeError(f"create OAuth app response is missing fields: {sorted(app.keys())}")
    return OAuthApp(
        id=str(app_id),
        client_id=app["clientId"],
        client_secret=app["clientSecret"],
        scopes=scopes,
        redirect_uri=redirect_uri,
    )


def delete_oauth_app(base_url: str, user_jwt: str, app_id: str, timeout: int = 30) -> None:
    _check(
        requests.delete(
            f"{base_url.rstrip('/')}/api/v1/oauth-clients/{app_id}",
            headers=_bearer(user_jwt),
            timeout=timeout,
        ),
        "delete OAuth app",
    )


def _pkce_pair() -> tuple[str, str]:
    """RFC 7636 verifier and S256 challenge. token_urlsafe(64) gives 86 allowed chars."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def authorization_code_token(
    base_url: str,
    user_jwt: str,
    app: OAuthApp,
    *,
    scopes: Optional[list[str]] = None,
    timeout: int = 30,
) -> dict:
    """
    Run the authorization-code + PKCE flow with API calls and return the token response.

    ``POST /api/v1/oauth2/authorize`` with a user session JWT and ``consent: granted``
    answers with ``{ redirectUrl }`` that carries the code, so no browser is needed.
    """
    base_url = base_url.rstrip("/")
    scope = " ".join(scopes or app.scopes)
    state = secrets.token_urlsafe(16)
    verifier, challenge = _pkce_pair()

    consent = _check(
        requests.post(
            f"{base_url}/api/v1/oauth2/authorize",
            headers=_bearer(user_jwt),
            json={
                "client_id": app.client_id,
                "redirect_uri": app.redirect_uri,
                "scope": scope,
                "state": state,
                "consent": "granted",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
            timeout=timeout,
        ),
        "OAuth consent",
    )
    redirect_url = consent.get("redirectUrl")
    if not redirect_url:
        raise RuntimeError(f"OAuth consent returned no redirectUrl: {sorted(consent.keys())}")
    query = parse_qs(urlparse(redirect_url).query)
    if query.get("state", [None])[0] != state:
        raise RuntimeError("OAuth consent redirect carries a different state")
    code = query.get("code", [None])[0]
    if not code:
        raise RuntimeError(f"OAuth consent redirect carries no code: {redirect_url}")

    return _check(
        requests.post(
            f"{base_url}/api/v1/oauth2/token",
            json={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": app.redirect_uri,
                "client_id": app.client_id,
                "client_secret": app.client_secret,
                "code_verifier": verifier,
            },
            timeout=timeout,
        ),
        "OAuth token exchange (authorization_code)",
    )


def client_credentials_token(base_url: str, app: OAuthApp, timeout: int = 30) -> dict:
    """Mint a token with no user interaction. The backend maps it to the app owner."""
    return _check(
        requests.post(
            f"{base_url.rstrip('/')}/api/v1/oauth2/token",
            json={
                "grant_type": "client_credentials",
                "client_id": app.client_id,
                "client_secret": app.client_secret,
            },
            timeout=timeout,
        ),
        "OAuth token exchange (client_credentials)",
    )
