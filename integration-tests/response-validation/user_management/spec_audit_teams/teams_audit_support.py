"""Constants and helpers for the strict OpenAPI audit of /api/v1/teams."""

from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from typing import Any

import requests
from pymongo import MongoClient

from helper.config import MONGO_DB_NAME, MONGO_URI
from helper.second_user import SecondUser

TEAMS_BASE = "/api/v1/teams"
TEAMS_ROUTE = TEAMS_BASE
TEAM_ROUTE = f"{TEAMS_BASE}/{{teamId}}"
TEAM_USERS_ROUTE = f"{TEAMS_BASE}/{{teamId}}/users"
USER_TEAMS_ROUTE = f"{TEAMS_BASE}/user/teams"

INVALID_BEARER = {"Authorization": "Bearer not-a-jwt"}
MALFORMED_JSON_BODY = "{not json"
JSON_HEADERS = {"Content-Type": "application/json"}

# A well-formed version-4 UUID that names no team.
MISSING_TEAM_ID = "00000000-0000-4000-8000-000000000001"
# A well-formed Mongo id that names no user.
GHOST_USER_ID = "ffffffffffffffffffffffff"
PEOPLE_GONE = "Some people you picked are no longer in this workspace. Remove them and try sharing again."
NOT_OWNER_ON_UPDATE = "User does not have permission to update this team"
LAST_OWNER = "Cannot remove all owners from the team. At least one owner must remain."
INVALID_PATH_SEGMENT = "This address contains an ID that isn't valid. Check the link you followed and try again."
# Percent-encoded so they reach Express as one segment; the path guard refuses them before the validator.
UNSAFE_TEAM_IDS = ["a%25b", "a%5Cb", "a%3Fb"]

# Scopes the suite's OAuth client holds: one without any team scope, one with read only.
NO_TEAM_SCOPE = "org:read"
TEAM_READ_SCOPE = "team:read"

JsonObject = dict[str, Any]


def unique_team_name(label: str = "team") -> str:
    return f"spec-audit-{label}-{uuid.uuid4().hex[:10]}"


def error_of(resp: requests.Response) -> JsonObject:
    return resp.json()["error"]


def validation_fields(resp: requests.Response) -> set[str]:
    error = error_of(resp)
    assert error["code"] == "VALIDATION_ERROR", error
    return {e["field"] for e in error["metadata"]["errors"]}


def members_by_user(team: JsonObject) -> dict[str, JsonObject]:
    return {m["userId"]: m for m in team.get("members") or []}


def request_as(user: SecondUser, method: str, path: str = "", **kwargs: Any) -> requests.Response:
    """Call a teams route as the non-admin member; path is relative to the router."""
    kwargs.setdefault("timeout", user.timeout)
    return requests.request(method, f"{user.base_url}{TEAMS_BASE}{path}", headers=user.headers, **kwargs)


def mint_scoped_token(base_url: str, scope: str, timeout: int = 60) -> str:
    """A client-credentials token of the suite's own OAuth client, limited to ``scope``."""
    resp = requests.post(
        f"{base_url}/api/v1/oauth2/token",
        json={
            "grant_type": "client_credentials",
            "client_id": os.environ["CLIENT_ID"],
            "client_secret": os.environ["CLIENT_SECRET"],
            "scope": scope,
        },
        timeout=timeout,
    )
    assert resp.status_code == 200, f"minting a {scope} token: {resp.status_code} {resp.text[:300]}"
    granted = resp.json().get("scope")
    assert granted == scope, f"asked for {scope!r}, the token carries {granted!r}"
    return str(resp.json()["access_token"])


def forget_access_token(token: str) -> None:
    """Remove the stored row of an access token this suite minted (there is no delete API)."""
    client: MongoClient[JsonObject] = MongoClient(MONGO_URI)
    try:
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        client[MONGO_DB_NAME]["oauthAccessTokens"].delete_one({"tokenHash": token_hash})
    finally:
        client.close()


@dataclass
class ScopedCaller:
    """Calls teams routes with one fixed bearer token."""

    base_url: str
    token: str
    timeout: int

    def __call__(self, method: str, path: str = "", **kwargs: Any) -> requests.Response:
        headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}
        kwargs.setdefault("timeout", self.timeout)
        return requests.request(method, f"{self.base_url}{TEAMS_BASE}{path}", headers=headers, **kwargs)
