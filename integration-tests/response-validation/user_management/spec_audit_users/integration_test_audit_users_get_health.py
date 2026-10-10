"""Strict OpenAPI audit of GET /api/v1/users/health."""

from __future__ import annotations

import datetime

import pytest
from helper.clients.users_client import UsersClient
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract
from users_audit_support import INVALID_BEARER

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/users/health"


@pytest.mark.parametrize(
    "headers", [{}, INVALID_BEARER], ids=["no-token", "not-a-jwt"]
)
def test_health_needs_no_credentials(users_client: UsersClient, headers: dict[str, str]) -> None:
    resp = users_client.get("/health", auth=False, headers=headers)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert body["status"] == "healthy", body
    stamp = datetime.datetime.fromisoformat(body["timestamp"].replace("Z", "+00:00"))
    assert abs((datetime.datetime.now(datetime.timezone.utc) - stamp).total_seconds()) < 300, body


def test_a_query_is_ignored(users_client: UsersClient) -> None:
    with outside_request_contract("the route takes no query; this shows extra ones are ignored"):
        resp = users_client.get("/health", auth=False, params={"verbose": "true"})
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.status_code == 200, resp.text[:500]
    assert set(resp.json()) == {"status", "timestamp"}, resp.json()


@pytest.mark.parametrize("headers", [{}, INVALID_BEARER], ids=["no-token", "not-a-jwt"])
def test_post_falls_through_to_the_express_default_404(
    users_client: UsersClient, headers: dict[str, str]
) -> None:
    resp = users_client.post("/health", auth=False, headers=headers)
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.status_code == 404, resp.text[:500]
    assert resp.headers["content-type"].startswith("text/html"), resp.headers
    assert "Cannot POST /api/v1/users/health" in resp.text, resp.text[:500]
