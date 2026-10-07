"""Constants and helpers for the strict OpenAPI audit of /api/v1/userGroups."""

from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from typing import Any

import requests
from bson import ObjectId
from pymongo import MongoClient

from helper.clients.user_groups_client import UserGroupsClient
from helper.config import MONGO_DB_NAME, MONGO_URI
from helper.second_user import SecondUser

GROUPS_BASE = "/api/v1/userGroups"
GROUPS_ROUTE = GROUPS_BASE
GROUP_ROUTE = f"{GROUPS_BASE}/{{groupId}}"
GROUP_USERS_ROUTE = f"{GROUPS_BASE}/{{groupId}}/users"
ADD_USERS_ROUTE = f"{GROUPS_BASE}/add-users"
REMOVE_USERS_ROUTE = f"{GROUPS_BASE}/remove-users"
USER_GROUPS_ROUTE = f"{GROUPS_BASE}/users/{{userId}}"
STATS_ROUTE = f"{GROUPS_BASE}/stats/list"
HEALTH_ROUTE = f"{GROUPS_BASE}/health"

INVALID_BEARER = {"Authorization": "Bearer not-a-jwt"}
MALFORMED_JSON_BODY = "{not json"
JSON_HEADERS = {"Content-Type": "application/json"}

# A well-formed Mongo id that names no group and no user.
MISSING_ID = "ffffffffffffffffffffffff"
ADMIN_REQUIRED = "You need admin access to do this. Ask an admin in your organisation."
RESERVED_ON_CREATE = 'Group name or type "admin", "everyone", or "standard" cannot be created'
RESERVED_ON_RENAME = 'Group name "admin", "everyone", or "standard" cannot be used'
GROUP_EXISTS = "Group already exists"
NO_GROUPS_UPDATED = "No groups found or updated"

NO_GROUP_SCOPE = "org:read"
GROUP_READ_SCOPE = "usergroup:read"

JsonObject = dict[str, Any]


def unique_group_name(label: str = "group") -> str:
    return f"spec-audit-{label}-{uuid.uuid4().hex[:10]}"


def error_of(resp: requests.Response) -> JsonObject:
    return resp.json()["error"]


def validation_fields(resp: requests.Response) -> set[str]:
    error = error_of(resp)
    assert error["code"] == "VALIDATION_ERROR", error
    return {e["field"] for e in error["metadata"]["errors"]}


def system_group(client: UserGroupsClient, group_type: str) -> JsonObject:
    """The organization's built-in group of ``group_type`` (``everyone`` or ``standard``)."""
    resp = client.get("/", params={"search": group_type, "limit": 100})
    assert resp.status_code == 200, resp.text[:300]
    found = [g for g in resp.json()["groups"] if g["type"] == group_type]
    assert len(found) == 1, f"expected one {group_type} group, got {found}"
    return found[0]


def request_as(user: SecondUser, method: str, path: str = "", **kwargs: Any) -> requests.Response:
    """Call a userGroups route as the non-admin member; path is relative to the router."""
    kwargs.setdefault("timeout", user.timeout)
    return requests.request(method, f"{user.base_url}{GROUPS_BASE}{path}", headers=user.headers, **kwargs)


def purge_groups(group_ids: list[str]) -> None:
    """Remove the rows of groups this suite created; the API only soft-deletes them."""
    if not group_ids:
        return
    client: MongoClient[JsonObject] = MongoClient(MONGO_URI)
    try:
        client[MONGO_DB_NAME]["userGroups"].delete_many({"_id": {"$in": [ObjectId(i) for i in group_ids]}})
    finally:
        client.close()


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
    """Calls userGroups routes with one fixed bearer token."""

    base_url: str
    token: str
    timeout: int

    def __call__(self, method: str, path: str = "", **kwargs: Any) -> requests.Response:
        headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}
        kwargs.setdefault("timeout", self.timeout)
        return requests.request(method, f"{self.base_url}{GROUPS_BASE}{path}", headers=headers, **kwargs)
