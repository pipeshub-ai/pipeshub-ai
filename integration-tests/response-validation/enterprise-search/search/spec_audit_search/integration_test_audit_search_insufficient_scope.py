"""An OAuth access token without the route's semantic scope is refused with 403.

Reading needs `semantic:read`, searching, sharing and archiving need `semantic:write`, and
deleting needs `semantic:delete`; no one of them implies another. The scope check runs after
authentication and before the request is validated, so even a malformed search id gets 403.
"""

from __future__ import annotations

from typing import Any, Iterator

import pytest
import requests
from helper.pipeshub_client import PipeshubClient
from search_audit_support import (
    ARCHIVE_TEMPLATE,
    MALFORMED_SEARCH_ID,
    MISSING_SEARCH_ID,
    ROOT_TEMPLATE,
    SEARCH_BASE,
    SEARCH_QUERY,
    SEARCH_TEMPLATE,
    SHARE_TEMPLATE,
    UNARCHIVE_TEMPLATE,
    UNKNOWN_USER_ID,
    UNSHARE_TEMPLATE,
    bearer,
    oauth_token_with_scopes,
)
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

S = MISSING_SEARCH_ID
SHARE_BODY = {"userIds": [UNKNOWN_USER_ID]}


@pytest.fixture(scope="module")
def tokens(pipeshub_client: PipeshubClient) -> Iterator[dict[str, str]]:
    """Tokens by their only scope."""
    base_url = pipeshub_client.base_url
    with (
        oauth_token_with_scopes(base_url, ["semantic:read"]) as read,
        oauth_token_with_scopes(base_url, ["semantic:write"]) as write,
    ):
        yield {"semantic:read": read, "semantic:write": write}


@pytest.mark.parametrize(
    ("scope", "method", "path", "route", "body", "required"),
    [
        pytest.param(
            "semantic:read", "POST", "", ROOT_TEMPLATE, {"query": SEARCH_QUERY, "limit": 1}, "semantic:write",
            id="search",
        ),
        pytest.param("semantic:read", "POST", "", ROOT_TEMPLATE, {}, "semantic:write", id="search-invalid-body"),
        pytest.param("semantic:write", "GET", "", ROOT_TEMPLATE, None, "semantic:read", id="history"),
        pytest.param("semantic:write", "GET", f"/{S}", SEARCH_TEMPLATE, None, "semantic:read", id="get"),
        pytest.param("semantic:write", "DELETE", "", ROOT_TEMPLATE, None, "semantic:delete", id="clear-history"),
        pytest.param("semantic:write", "DELETE", f"/{S}", SEARCH_TEMPLATE, None, "semantic:delete", id="delete"),
        pytest.param(
            "semantic:write", "DELETE", f"/{MALFORMED_SEARCH_ID}", SEARCH_TEMPLATE, None, "semantic:delete",
            id="delete-malformed-id",
        ),
        pytest.param("semantic:read", "PATCH", f"/{S}/share", SHARE_TEMPLATE, SHARE_BODY, "semantic:write", id="share"),
        pytest.param(
            "semantic:read", "PATCH", f"/{S}/unshare", UNSHARE_TEMPLATE, SHARE_BODY, "semantic:write", id="unshare"
        ),
        pytest.param("semantic:read", "PATCH", f"/{S}/archive", ARCHIVE_TEMPLATE, None, "semantic:write", id="archive"),
        pytest.param(
            "semantic:read", "PATCH", f"/{S}/unarchive", UNARCHIVE_TEMPLATE, None, "semantic:write", id="unarchive"
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
        f"{pipeshub_client.base_url}{SEARCH_BASE}{path}",
        headers=bearer(tokens[scope]),
        json=body,
        timeout=pipeshub_client.timeout_seconds,
    )

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, route)
    error = resp.json()["error"]
    assert error["code"] == "HTTP_FORBIDDEN"
    assert error["message"] == f"Insufficient scope. Required: {required}"


@pytest.mark.parametrize("path", ["", f"/{S}"])
def test_read_scope_is_enough_to_read(
    pipeshub_client: PipeshubClient, tokens: dict[str, str], path: str
) -> None:
    resp = requests.get(
        f"{pipeshub_client.base_url}{SEARCH_BASE}{path}",
        headers=bearer(tokens["semantic:read"]),
        timeout=pipeshub_client.timeout_seconds,
    )

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, f"{SEARCH_BASE}{'/:searchId' if path else ''}")
