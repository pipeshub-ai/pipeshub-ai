"""Client, constants and helpers for the strict OpenAPI audit of /api/v1/saml."""

from __future__ import annotations

import base64
import datetime
import hashlib
import json
import os
from typing import Any

import jwt
import requests

from helper.http.api_client import APIClient

SAML_BASE = "/api/v1/saml"
SIGN_IN_ROUTE = f"{SAML_BASE}/signIn"
SIGN_IN_CALLBACK_ROUTE = f"{SAML_BASE}/signIn/callback"
DESKTOP_EXCHANGE_ROUTE = f"{SAML_BASE}/desktop/exchange"
UPDATE_APP_CONFIG_ROUTE = f"{SAML_BASE}/updateAppConfig"

FETCH_CONFIG_SCOPE = "fetch:config"
# A real scope updateAppConfig does not accept.
OTHER_SCOPE = "mail:send"
WRONG_SECRET = "spec-audit-not-the-deployment-secret"

# Well-formed (64 lowercase hex) but never issued, so redeem finds nothing in Redis.
MISSING_HANDOFF_CODE = "0123456789abcdef" * 4
MALFORMED_HANDOFF_CODE = "not-a-handoff-code"
# 43-128 chars of [A-Za-z0-9._~-], the PKCE verifier shape redeem requires.
CODE_VERIFIER = "spec-audit-verifier-" + "a" * 30
MALFORMED_CODE_VERIFIER = "too-short"
CODE_CHALLENGE = (
    base64.urlsafe_b64encode(hashlib.sha256(CODE_VERIFIER.encode()).digest())
    .rstrip(b"=")
    .decode()
)
DESKTOP_STATE = "phd.specaudit0123456789"


def scoped_jwt_secret() -> str:
    return os.getenv("SCOPED_JWT_SECRET", "").strip()


def mint_scoped_token(
    secret: str,
    scopes: list[str] | None,
    *,
    ttl_seconds: int = 3600,
    **claims: Any,
) -> str:
    """Service token shaped like Node's createJwt ones; a negative ttl gives an expired one."""
    now = datetime.datetime.now(datetime.timezone.utc)
    payload: dict[str, Any] = {
        **claims,
        "iat": now,
        "exp": now + datetime.timedelta(seconds=ttl_seconds),
    }
    if scopes is not None:
        payload["scopes"] = scopes
    return jwt.encode(payload, secret, algorithm="HS256")


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def relay_state(**fields: Any) -> str:
    """Base64 JSON, the RelayState encoding the sign-in and callback routes read."""
    return base64.b64encode(json.dumps(fields).encode()).decode()


class SamlClient(APIClient):
    """Client for /api/v1/saml.

    No route takes a session token: three are public and updateAppConfig takes
    a scoped service token (``token=``). The browser-facing routes answer with
    redirects, which are returned as-is so the 302 itself can be checked.
    """

    BASE = SAML_BASE

    def sign_in(self, *, auth: bool = False, **params: Any) -> requests.Response:
        return self.get("/signIn", auth=auth, params=params, allow_redirects=False)

    def sign_in_callback(
        self,
        form: dict[str, Any] | None = None,
        *,
        auth: bool = False,
        **kwargs: Any,
    ) -> requests.Response:
        """POST the IdP's form-encoded body (SAMLResponse, RelayState, ...)."""
        if form is not None:
            kwargs.setdefault("data", form)
        kwargs.setdefault("allow_redirects", False)
        return self.post("/signIn/callback", auth=auth, **kwargs)

    def desktop_exchange(
        self, body: Any = None, *, auth: bool = False, **kwargs: Any
    ) -> requests.Response:
        if body is not None:
            kwargs.setdefault("json", body)
        return self.post("/desktop/exchange", auth=auth, **kwargs)

    def update_app_config(
        self, *, token: str | None = None, auth: bool = True, **kwargs: Any
    ) -> requests.Response:
        """``token`` sends a scoped token; otherwise ``auth`` picks admin session token or none."""
        if token is not None:
            # auth=False so the helper neither overwrites the header nor
            # retries a 401 with a refreshed admin token.
            return self.post(
                "/updateAppConfig", auth=False, headers=bearer(token), **kwargs
            )
        return self.post("/updateAppConfig", auth=auth, **kwargs)
