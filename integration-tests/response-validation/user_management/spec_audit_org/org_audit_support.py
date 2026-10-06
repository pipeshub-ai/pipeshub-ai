"""Constants and helpers for the strict OpenAPI audit of /api/v1/org."""

from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass
from typing import Any

import requests
from pymongo import MongoClient

from helper.clients.org_client import OrgClient
from helper.config import MONGO_DB_NAME, MONGO_URI
from helper.second_user import SecondUser

ORG_BASE = "/api/v1/org"
ORG_ROUTE = ORG_BASE
EXISTS_ROUTE = f"{ORG_BASE}/exists"
HEALTH_ROUTE = f"{ORG_BASE}/health"
LOGO_ROUTE = f"{ORG_BASE}/logo"
ONBOARDING_ROUTE = f"{ORG_BASE}/onboarding-status"

INVALID_BEARER = {"Authorization": "Bearer not-a-jwt"}
MALFORMED_JSON_BODY = "{not json"
JSON_HEADERS = {"Content-Type": "application/json"}

ORG_EXISTS_MESSAGE = "There is already an organization"
ADMIN_SCOPE = "org:admin"
# A scope the suite's OAuth client holds and no org route accepts.
NARROW_SCOPE = "team:read"
ONBOARDING_STATUSES = ("configured", "notConfigured", "skipped")

# Satisfies passwordValidator: upper, lower, digit, one of #?!@$%^&*-, 8..72 bytes.
VALID_PASSWORD = "SpecAudit123!"

ORG_COLLECTION = "org"
LOGO_COLLECTION = "org-logos"

# 1x1 transparent PNG; the API re-encodes every raster upload as JPEG.
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)
SAFE_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"><rect width="1" height="1"/></svg>'
SCRIPT_SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
HANDLER_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"></svg>'

OrgBody = dict[str, Any]
JsonObject = dict[str, Any]


def org_creation_body(account_type: str = "individual", **overrides: Any) -> OrgBody:
    """A body that passes OrgCreationBody; pass ``field=None`` to drop a field."""
    body: OrgBody = {
        "accountType": account_type,
        "contactEmail": "spec-audit-admin@example.com",
        "adminFullName": "Spec Audit Admin",
        "password": VALID_PASSWORD,
    }
    if account_type == "business":
        body["registeredName"] = "Spec Audit Corp"
    body.update(overrides)
    return {key: value for key, value in body.items() if value is not None}


def current_org_id(org_client: OrgClient) -> str:
    """The live org's id, read through GET /org, which hides a soft-deleted org."""
    resp = org_client.get_organization()
    assert resp.status_code == 200, f"shared org is not readable: {resp.status_code} {resp.text[:300]}"
    return str(resp.json()["_id"])


def request_as(
    user: SecondUser, method: str, path: str = "", **kwargs: Any
) -> requests.Response:
    """Call an org route as the non-admin member; path is relative to the router."""
    kwargs.setdefault("timeout", user.timeout)
    headers = dict(user.headers)
    if "files" in kwargs:
        headers.pop("Content-Type", None)
    return requests.request(method, f"{user.base_url}{ORG_BASE}{path}", headers=headers, **kwargs)


def mint_narrow_scope_token(base_url: str, timeout: int = 60) -> str:
    """A client-credentials token of the suite's own OAuth client, limited to NARROW_SCOPE."""
    resp = requests.post(
        f"{base_url}/api/v1/oauth2/token",
        json={
            "grant_type": "client_credentials",
            "client_id": os.environ["CLIENT_ID"],
            "client_secret": os.environ["CLIENT_SECRET"],
            "scope": NARROW_SCOPE,
        },
        timeout=timeout,
    )
    assert resp.status_code == 200, f"minting a {NARROW_SCOPE} token: {resp.status_code} {resp.text[:300]}"
    granted = resp.json().get("scope")
    assert granted == NARROW_SCOPE, f"asked for {NARROW_SCOPE!r}, the token carries {granted!r}"
    return str(resp.json()["access_token"])


def forget_access_token(token: str) -> None:
    """Remove the stored row of an access token this suite minted (there is no delete API)."""
    client: MongoClient[JsonObject] = MongoClient(MONGO_URI)
    try:
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        client[MONGO_DB_NAME]["oauthAccessTokens"].delete_one({"tokenHash": token_hash})
    finally:
        client.close()


def error_of(resp: requests.Response) -> JsonObject:
    return resp.json()["error"]


@dataclass
class ScopedCaller:
    """Calls org routes with one fixed bearer token."""

    base_url: str
    token: str
    timeout: int

    def __call__(self, method: str, path: str = "", **kwargs: Any) -> requests.Response:
        headers = {"Authorization": f"Bearer {self.token}"}
        if "files" not in kwargs:
            headers["Content-Type"] = "application/json"
        kwargs.setdefault("timeout", self.timeout)
        return requests.request(method, f"{self.base_url}{ORG_BASE}{path}", headers=headers, **kwargs)
