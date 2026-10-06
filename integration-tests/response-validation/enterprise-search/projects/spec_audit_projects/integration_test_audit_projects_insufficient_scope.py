"""An OAuth access token without the route's project scope is refused with 403.

Reading needs `project:read`, changing needs `project:write`, and deleting a project needs
`project:delete`; `project:write` does not imply the other two. The scope check runs after
authentication and before the request is validated, so even a malformed project id gets 403.
"""

from __future__ import annotations

from typing import Any, Iterator

import pytest
import requests
from helper.pipeshub_client import PipeshubClient
from projects_audit_support import (
    ARCHIVE_TEMPLATE,
    CONVERSATIONS_TEMPLATE,
    MALFORMED_PROJECT_ID,
    MEMBERS_TEMPLATE,
    MISSING_PROJECT_ID,
    PIN_TEMPLATE,
    PROJECT_TEMPLATE,
    PROJECTS_BASE,
    ROOT_TEMPLATE,
    UNARCHIVE_TEMPLATE,
    UNPIN_TEMPLATE,
    UNKNOWN_USER_ID,
    bearer,
    oauth_token_with_scopes,
)
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

P = MISSING_PROJECT_ID
MEMBERS_BODY = {"members": [{"principalId": UNKNOWN_USER_ID, "role": "viewer"}]}


@pytest.fixture(scope="module")
def tokens(pipeshub_client: PipeshubClient) -> Iterator[dict[str, str]]:
    """Tokens by their only scope."""
    base_url = pipeshub_client.base_url
    with (
        oauth_token_with_scopes(base_url, ["project:read"]) as read,
        oauth_token_with_scopes(base_url, ["project:write"]) as write,
    ):
        yield {"project:read": read, "project:write": write}


@pytest.mark.parametrize(
    ("scope", "method", "path", "route", "body", "required"),
    [
        pytest.param("project:write", "GET", "", ROOT_TEMPLATE, None, "project:read", id="list"),
        pytest.param("project:write", "GET", f"/{P}", PROJECT_TEMPLATE, None, "project:read", id="get"),
        pytest.param(
            "project:write", "GET", f"/{P}/conversations", CONVERSATIONS_TEMPLATE, None, "project:read",
            id="conversations",
        ),
        pytest.param("project:write", "GET", f"/{P}/members", MEMBERS_TEMPLATE, None, "project:read", id="members"),
        pytest.param("project:write", "DELETE", f"/{P}", PROJECT_TEMPLATE, None, "project:delete", id="delete"),
        pytest.param(
            "project:write", "DELETE", f"/{MALFORMED_PROJECT_ID}", PROJECT_TEMPLATE, None, "project:delete",
            id="delete-malformed-id",
        ),
        pytest.param("project:read", "POST", "", ROOT_TEMPLATE, {"name": "x"}, "project:write", id="create"),
        pytest.param("project:read", "POST", "", ROOT_TEMPLATE, {}, "project:write", id="create-invalid-body"),
        pytest.param("project:read", "PATCH", f"/{P}", PROJECT_TEMPLATE, {"name": "x"}, "project:write", id="update"),
        pytest.param("project:read", "POST", f"/{P}/archive", ARCHIVE_TEMPLATE, None, "project:write", id="archive"),
        pytest.param(
            "project:read", "POST", f"/{P}/unarchive", UNARCHIVE_TEMPLATE, None, "project:write", id="unarchive"
        ),
        pytest.param("project:read", "POST", f"/{P}/pin", PIN_TEMPLATE, None, "project:write", id="pin"),
        pytest.param("project:read", "POST", f"/{P}/unpin", UNPIN_TEMPLATE, None, "project:write", id="unpin"),
        pytest.param(
            "project:read", "PUT", f"/{P}/members", MEMBERS_TEMPLATE, MEMBERS_BODY, "project:write",
            id="upsert-members",
        ),
    ],
)
def test_token_without_the_route_scope_is_forbidden(
    pipeshub_client: PipeshubClient,
    tokens: dict[str, str],
    scope: str,
    method: str,
    path: str,
    route: str,
    body: dict[str, Any] | None,
    required: str,
) -> None:
    resp = requests.request(
        method,
        f"{pipeshub_client.base_url}{PROJECTS_BASE}{path}",
        headers=bearer(tokens[scope]),
        json=body,
        timeout=pipeshub_client.timeout_seconds,
    )

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, route)
    error = resp.json()["error"]
    assert error["code"] == "HTTP_FORBIDDEN"
    assert error["message"] == f"Insufficient scope. Required: {required}"


def test_read_scope_is_enough_to_list(pipeshub_client: PipeshubClient, tokens: dict[str, str]) -> None:
    resp = requests.get(
        f"{pipeshub_client.base_url}{PROJECTS_BASE}",
        headers=bearer(tokens["project:read"]),
        timeout=pipeshub_client.timeout_seconds,
    )

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROOT_TEMPLATE)
