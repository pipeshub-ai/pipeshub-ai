"""Constants and helpers for the strict OpenAPI audit of /api/v1/projects."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Callable

import requests

from helper.agui_sse import (
    is_root_error,
    is_root_finished,
    iter_sse_envelopes,
    run_finished_result,
)
from helper.clients.projects_client import ProjectsClient
from helper.local_auth import obtain_user_session_token
from helper.second_user import SecondUser

PROJECTS_BASE = "/api/v1/projects"
MISSING_PROJECT_ID = "0123456789abcdef01234567"
MALFORMED_PROJECT_ID = "not-an-object-id"
# A well-formed id that names no user in the org's IAM service.
UNKNOWN_USER_ID = "ffffffffffffffffffffffff"

ROOT_TEMPLATE = PROJECTS_BASE
PROJECT_TEMPLATE = f"{PROJECTS_BASE}/:projectId"
ARCHIVE_TEMPLATE = f"{PROJECTS_BASE}/:projectId/archive"
UNARCHIVE_TEMPLATE = f"{PROJECTS_BASE}/:projectId/unarchive"
PIN_TEMPLATE = f"{PROJECTS_BASE}/:projectId/pin"
UNPIN_TEMPLATE = f"{PROJECTS_BASE}/:projectId/unpin"
CONVERSATIONS_TEMPLATE = f"{PROJECTS_BASE}/:projectId/conversations"
MEMBERS_TEMPLATE = f"{PROJECTS_BASE}/:projectId/members"

OAUTH_CLIENTS_PATH = "/api/v1/oauth-clients"
OAUTH_TOKEN_PATH = "/api/v1/oauth2/token"

JSON_HEADERS = {"Content-Type": "application/json"}
MALFORMED_JSON_BODY = "{not json"

PROJECT_FIELDS = {
    "_id", "orgId", "userId", "name", "description", "icon", "color", "instructions",
    "knowledgeScope", "appliedFilters", "tools", "linkedKnowledgeBaseId", "visibility",
    "chatSharing", "members", "isPinned", "isArchived", "archivedBy", "isDeleted",
    "deletedBy", "lastActivityAt", "metadata", "createdAt", "updatedAt", "__v", "role",
}

FILTER_NODE = {"id": "app-1", "name": "App one", "nodeType": "app", "connector": "KB"}

# seed_project(**create_fields) -> the created project object (its id is "_id").
SeedProject = Callable[..., dict[str, Any]]


def request_as(
    user: SecondUser, method: str, path: str = "", **kwargs: Any
) -> requests.Response:
    """Call a projects route as the non-admin member; path is relative to the router."""
    kwargs.setdefault("timeout", user.timeout)
    return requests.request(
        method,
        f"{user.base_url}{PROJECTS_BASE}{path}",
        headers=user.headers,
        **kwargs,
    )


def validation_fields(resp: requests.Response) -> set[str]:
    """Fields named by a Node validator 400; fails the test for any other response."""
    assert resp.status_code == 400, resp.text[:500]
    error = resp.json()["error"]
    assert error["code"] == "VALIDATION_ERROR", resp.text[:500]
    return {str(e["field"]) for e in error["metadata"]["errors"]}


def add_member(
    projects_client: ProjectsClient, project_id: str, user_id: str, role: str
) -> None:
    resp = projects_client.upsert_members(
        project_id, [{"principalId": user_id, "role": role}]
    )
    assert resp.status_code == 200, f"could not add the member: {resp.status_code} {resp.text[:300]}"


def conversation_id_from_stream(resp: requests.Response) -> str:
    """Read a conversation stream to its end and return the new conversation's id."""
    assert resp.status_code == 200, f"{resp.status_code}: {resp.text[:500]}"
    assert resp.headers.get("Content-Type", "").startswith("text/event-stream"), resp.headers
    for envelope in iter_sse_envelopes(resp):
        payload = json.loads(envelope["data"])
        if is_root_error(envelope["event"], payload):
            raise AssertionError(f"stream emitted RUN_ERROR: {payload!r}")
        if is_root_finished(envelope["event"], payload):
            conversation_id = (run_finished_result(payload).get("conversation") or {}).get("_id")
            assert isinstance(conversation_id, str) and conversation_id, payload
            return conversation_id
    raise AssertionError("conversation stream ended without a RUN_FINISHED event")


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


@contextmanager
def oauth_token_with_scopes(base_url: str, scopes: list[str], timeout: int = 60) -> Iterator[str]:
    """A client-credentials token limited to ``scopes``, from an OAuth app that is deleted on exit.

    The suite's own token carries every scope and a session JWT is never scope-checked, so this is
    the only caller that can be refused for a missing scope.
    """
    admin = bearer(obtain_user_session_token(base_url, timeout))
    created = requests.post(
        f"{base_url}{OAUTH_CLIENTS_PATH}",
        headers=admin,
        json={
            "name": f"spec-audit-projects-scope-{uuid.uuid4().hex[:8]}",
            "allowedGrantTypes": ["client_credentials"],
            "allowedScopes": scopes,
        },
        timeout=timeout,
    )
    assert created.status_code == 201, f"creating an OAuth app failed: {created.status_code} {created.text[:300]}"
    app = created.json()["app"]
    try:
        issued = requests.post(
            f"{base_url}{OAUTH_TOKEN_PATH}",
            json={
                "grant_type": "client_credentials",
                "client_id": app["clientId"],
                "client_secret": app["clientSecret"],
            },
            timeout=timeout,
        )
        assert issued.status_code == 200, f"token request failed: {issued.status_code}"
        yield issued.json()["access_token"]
    finally:
        requests.delete(f"{base_url}{OAUTH_CLIENTS_PATH}/{app['id']}", headers=admin, timeout=timeout)
