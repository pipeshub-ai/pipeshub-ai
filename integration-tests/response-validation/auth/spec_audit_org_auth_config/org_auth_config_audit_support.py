"""Client and constants for the strict OpenAPI audit of /api/v1/orgAuthConfig."""

from __future__ import annotations

import datetime
from typing import Any

import jwt
import requests

from helper.http.api_client import APIClient

ORG_AUTH_CONFIG_BASE = "/api/v1/orgAuthConfig"
SET_UP_ROUTE = ORG_AUTH_CONFIG_BASE
AUTH_METHODS_ROUTE = f"{ORG_AUTH_CONFIG_BASE}/authMethods"
UPDATE_AUTH_METHOD_ROUTE = f"{ORG_AUTH_CONFIG_BASE}/updateAuthMethod"

ALREADY_DONE = {"message": "Org config already done"}
UPDATED_MESSAGE = "Auth method updated"

BAD_REQUEST = "HTTP_BAD_REQUEST"
FORBIDDEN = "HTTP_FORBIDDEN"
NOT_FOUND = "HTTP_NOT_FOUND"
INTERNAL_ERROR = "INTERNAL_ERROR"
VALIDATION_ERROR = "VALIDATION_ERROR"
NO_AUTHORIZATION_HEADER = "Authorization header not found"
NO_TOKEN_AFTER_SCHEME = "Token not found in Authorization header"
ACCOUNT_NOT_FOUND = "Account not found"
# ADMIN_ACCESS_REQUIRED_MESSAGE in user_management/services/user-admin.service.ts.
ADMIN_ACCESS_REQUIRED = "You need admin access to do this. Ask an admin in your organisation."
# SAML_IN_MULTI_STEP_POLICY in auth/routes/orgAuthConfig.routes.ts.
SAML_IN_MULTI_STEP_POLICY = (
    "SAML single sign-on can't be combined with other sign-in steps yet. Use SAML on its own "
    "as a one-step sign-in, or remove it from the policy."
)

MALFORMED_JSON_BODY = "{not json"
JSON_HEADERS = {"Content-Type": "application/json"}
MISSING_ORG_ID = "0123456789abcdef01234567"
OTHER_SIGNING_KEY = "spec-audit-not-the-access-token-signing-key"


def mint_access_token(secret: str, *, ttl_seconds: int = 300, **claims: Any) -> str:
    """HS256 token signed like a session access token; a negative ttl gives an expired one."""
    now = datetime.datetime.now(datetime.timezone.utc)
    return jwt.encode(
        {**claims, "iat": now, "exp": now + datetime.timedelta(seconds=ttl_seconds)},
        secret,
        algorithm="HS256",
    )


class OrgAuthConfigClient(APIClient):
    """Client for /api/v1/orgAuthConfig.

    The routes accept only a session access token, so nothing is sent unless
    ``headers`` carry one; the suite's OAuth token is never added.
    """

    BASE = ORG_AUTH_CONFIG_BASE

    def auth_methods(self, **kwargs: Any) -> requests.Response:
        return self.get("/authMethods", auth=False, **kwargs)

    def set_up(self, **kwargs: Any) -> requests.Response:
        return self.post("", auth=False, **kwargs)

    def update_auth_method(self, body: Any = None, **kwargs: Any) -> requests.Response:
        if body is not None:
            kwargs.setdefault("json", body)
        return self.post("/updateAuthMethod", auth=False, **kwargs)
