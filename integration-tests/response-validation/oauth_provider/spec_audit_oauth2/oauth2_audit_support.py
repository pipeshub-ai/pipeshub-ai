"""Client, constants and helpers for the strict OpenAPI audit of /api/v1/oauth2."""

from __future__ import annotations

import uuid
import warnings
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator

import requests
from pymongo import MongoClient

from helper.config import MONGO_DB_NAME, MONGO_URI
from helper.http.api_client import APIClient
from helper.mcp_oauth import (
    authorization_code_token,
    create_oauth_app,
    delete_oauth_app,
)
from helper.second_user import SecondUser

OAUTH2_BASE = "/api/v1/oauth2"

# The instance-wide public client; the only one an API caller can use for the
# device grant, since POST /oauth-clients does not accept the device_code grant.
FIRST_PARTY_DEVICE_CLIENT_ID = "pipeshub-agent"
DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
UNKNOWN_CLIENT_ID = "spec-audit-no-such-client"

# "0" is outside the alphabet user codes are drawn from, so this is never issued.
UNKNOWN_USER_CODE = "0000-0000"
# Longer than the 32 characters the validator allows.
MALFORMED_USER_CODE = "A" * 33

MALFORMED_ACCESS_TOKEN = "not-a-jwt"
OIDC_SCOPES = ("openid", "profile", "email")
DCR_REDIRECT_URI = "http://localhost/callback"

DEVICE_CODES_COLLECTION = "oauthDeviceCodes"
OAUTH_APPS_COLLECTION = "oauthApps"


@dataclass
class DeviceGrant:
    """One pending device authorization; ``response`` is the 200 that created it."""

    device_code: str
    user_code: str
    response: requests.Response


StartDeviceGrant = Callable[..., DeviceGrant]


class OAuth2Client(APIClient):
    """Client for /api/v1/oauth2, acting as whoever the wrapped HTTP client is."""

    BASE = OAUTH2_BASE

    def userinfo(self, access_token: str | None = None) -> requests.Response:
        """GET /userinfo with exactly this bearer token, or with no Authorization header."""
        headers = {"Authorization": f"Bearer {access_token}"} if access_token else None
        return self.get("/userinfo", auth=False, headers=headers)

    def register(self, **metadata: Any) -> requests.Response:
        return self.post("/register", auth=False, json=metadata)

    def device_authorization(self, **body: Any) -> requests.Response:
        return self.post("/device_authorization", auth=False, json=body)

    def device_verify(self, *, auth: bool = True, **body: Any) -> requests.Response:
        return self.post("/device/verify", auth=auth, json=body)

    def device_consent(self, *, auth: bool = True, **body: Any) -> requests.Response:
        return self.post("/device/consent", auth=auth, json=body)


def request_as(
    user: SecondUser, method: str, path: str = "", **kwargs: Any
) -> requests.Response:
    """Call an oauth2 route with the non-admin member's session JWT; path is relative to the router."""
    kwargs.setdefault("timeout", user.timeout)
    return requests.request(
        method,
        f"{user.base_url}{OAUTH2_BASE}{path}",
        headers=user.headers,
        **kwargs,
    )


@contextmanager
def user_bound_access_token(
    base_url: str, session_jwt: str, scopes: tuple[str, ...] = OIDC_SCOPES
) -> Iterator[str]:
    """An OAuth access token issued to the session's user, from a throwaway app.

    A client-credentials token (what ``pipeshub_client`` holds) carries no user,
    so /userinfo answers it with 401; only a user-bound token reaches the 200.
    """
    app = create_oauth_app(
        base_url,
        session_jwt,
        f"spec-audit-oauth2-{uuid.uuid4().hex[:8]}",
        scopes=list(scopes),
        grant_types=["authorization_code", "refresh_token"],
    )
    try:
        tokens = authorization_code_token(base_url, session_jwt, app)
        yield tokens["access_token"]
    finally:
        delete_oauth_app(base_url, session_jwt, app.id)


def _delete_documents(collection: str, query: dict[str, Any]) -> None:
    """No route removes these rows; they outlive the test if Mongo cannot be reached."""
    client: MongoClient = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    try:
        client[MONGO_DB_NAME][collection].delete_many(query)
    except Exception as exc:  # noqa: BLE001 - teardown must not mask the test result
        warnings.warn(f"could not clean {collection} {query}: {exc}", stacklevel=2)
    finally:
        client.close()


def delete_device_grants(user_codes: list[str]) -> None:
    """Remove device authorizations, pending or approved, by the user_code shown to the user."""
    stored = [code.replace("-", "").upper() for code in user_codes]
    if stored:
        _delete_documents(DEVICE_CODES_COLLECTION, {"userCode": {"$in": stored}})


def delete_dynamic_client(client_id: str) -> None:
    """Remove a client POST /register created; /oauth-clients hides dynamic clients."""
    _delete_documents(
        OAUTH_APPS_COLLECTION, {"clientId": client_id, "isDynamic": True}
    )
