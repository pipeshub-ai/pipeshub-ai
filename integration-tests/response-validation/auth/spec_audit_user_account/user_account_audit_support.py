"""Client, constants and helpers for the strict OpenAPI audit of /api/v1/userAccount."""

from __future__ import annotations

import datetime
import hashlib
import hmac
import os
import uuid
from dataclasses import dataclass
from typing import Any, Callable

import jwt
import requests

from helper.http.api_client import APIClient

USER_ACCOUNT_BASE = "/api/v1/userAccount"
OAUTH_EXCHANGE_ROUTE = f"{USER_ACCOUNT_BASE}/oauth/exchange"
VALIDATE_EMAIL_CHANGE_ROUTE = f"{USER_ACCOUNT_BASE}/validateEmailChange"

VALIDATE_EMAIL_SCOPE = "email:validate"
# A real scope validateEmailChange does not accept.
OTHER_SCOPE = "password:reset"
WRONG_SECRET = "spec-audit-not-the-deployment-secret"

MISSING_USER_ID = "0123456789abcdef01234567"
MALFORMED_USER_ID = "not-an-object-id"

# Mirrors USER_ACTION_KEY_CONTEXT in backend/nodejs/apps/src/libs/utils/jwtKeys.ts.
_USER_ACTION_KEY_CONTEXT = "pipeshub/jwt/user-action/v1"

MintEmailChangeToken = Callable[..., str]


@dataclass(frozen=True)
class DisposableMember:
    """A non-admin account made for one test; ``email`` is the one it was created with."""

    user_id: str
    org_id: str
    email: str


def scoped_jwt_secret() -> str:
    return os.getenv("SCOPED_JWT_SECRET", "").strip()


def user_action_key(secret: str) -> str:
    """The key Node signs user-held tokens with (deriveUserActionSecret), not the raw secret."""
    return hmac.new(
        secret.encode(), _USER_ACTION_KEY_CONTEXT.encode(), hashlib.sha256
    ).hexdigest()


def mint_scoped_token(
    key: str,
    scopes: list[str] | None,
    *,
    ttl_seconds: int = 1200,
    **claims: Any,
) -> str:
    """HS256 token signed with ``key`` as given; a negative ttl gives an expired one."""
    now = datetime.datetime.now(datetime.timezone.utc)
    payload: dict[str, Any] = {
        **claims,
        "iat": now,
        "exp": now + datetime.timedelta(seconds=ttl_seconds),
    }
    if scopes is not None:
        payload["scopes"] = scopes
    return jwt.encode(payload, key, algorithm="HS256")


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def unused_email(prefix: str = "spec-audit-email") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}@test-pipeshub.com"


def oauth_exchange_body(**overrides: Any) -> dict[str, Any]:
    """All three fields the controller requires; pass ``field=None`` to drop one."""
    body: dict[str, Any] = {
        "code": "spec-audit-not-a-real-code",
        "provider": "oauth",
        "redirectUri": "http://localhost:3000/auth/oauth/callback",
        **overrides,
    }
    return {k: v for k, v in body.items() if v is not None}


class UserAccountAuditClient(APIClient):
    """Client for the two audited /api/v1/userAccount routes; neither takes a session token."""

    BASE = USER_ACCOUNT_BASE

    def oauth_exchange(
        self, body: Any = None, *, auth: bool = False, **kwargs: Any
    ) -> requests.Response:
        if body is not None:
            kwargs.setdefault("json", body)
        return self.post("/oauth/exchange", auth=auth, **kwargs)

    def validate_email_change(
        self, *, token: str | None = None, auth: bool = False, **kwargs: Any
    ) -> requests.Response:
        """``token`` sends a scoped token; otherwise ``auth`` picks admin session token or none."""
        if token is not None:
            # auth=False so the helper neither overwrites the header nor
            # retries a 401 with a refreshed admin token.
            return self.put(
                "/validateEmailChange", auth=False, headers=bearer(token), **kwargs
            )
        return self.put("/validateEmailChange", auth=auth, **kwargs)
