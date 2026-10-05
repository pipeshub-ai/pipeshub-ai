"""Strict OpenAPI audit of GET /api/v1/mcp-servers/oauth/callback."""

from __future__ import annotations

import uuid
from urllib.parse import parse_qs, urlparse

import pytest
from helper.second_user import SecondUser
from mcp_servers_audit_support import (
    McpServersClient,
    SeedMcpInstance,
    oauth_instance_body,
    request_as,
)
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/mcp-servers/oauth/callback"


# Python never raises on this route: every refusal past auth is a 200 with success:false.
@pytest.mark.parametrize(
    ("params", "error"),
    [
        pytest.param(
            {"error": "access_denied", "code": "c", "state": "s"},
            "access_denied",
            id="provider-error-wins",
        ),
        pytest.param({"code": "only-a-code"}, "missing_params", id="missing-state"),
        pytest.param(
            {"code": "any-code", "state": f"specaudit{uuid.uuid4().hex}"},
            "invalid_state",
            id="unknown-state",
        ),
    ],
)
def test_callback_reports_failures_in_a_200_body(
    mcp_servers_client: McpServersClient, params: dict[str, str], error: str
) -> None:
    resp = mcp_servers_client.get("/oauth/callback", params=params)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    body = resp.json()
    assert body["success"] is False, body
    assert body["error"] == error, body
    assert body["errorMessage"], body


def test_callback_refuses_a_state_started_by_another_user(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
    second_user: SecondUser,
) -> None:
    instance_id = seed_mcp_instance(**oauth_instance_body())["_id"]
    configured = mcp_servers_client.put(
        f"/instances/{instance_id}/oauth-config",
        json={"clientId": "spec-audit-client", "clientSecret": "spec-audit-secret"},
    )
    if configured.status_code != 200:
        pytest.fail(f"storing the OAuth client: {configured.status_code} {configured.text[:500]}")
    authorize = mcp_servers_client.get(f"/instances/{instance_id}/oauth/authorize")
    if authorize.status_code != 200:
        pytest.fail(f"starting the OAuth flow: {authorize.status_code} {authorize.text[:500]}")
    state = parse_qs(urlparse(authorize.json()["authorizationUrl"]).query)["state"][0]

    # The admin started the flow; the member's callback is refused before any token exchange.
    resp = request_as(
        second_user, "GET", "/oauth/callback", params={"code": "any-code", "state": state}
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    body = resp.json()
    assert body["success"] is False, body
    assert body["error"] == "caller_mismatch", body
    assert "instanceId" not in body, body

    # The state is single-use, so the initiator cannot replay it either.
    replay = mcp_servers_client.get(
        "/oauth/callback", params={"code": "any-code", "state": state}
    )
    assert replay.status_code == 200, replay.text[:500]
    assert_strict_openapi_response(replay, ROUTE)
    assert replay.json()["error"] == "invalid_state", replay.text[:500]


def test_callback_without_a_token_is_401(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.get(
        "/oauth/callback", auth=False, params={"code": "any-code", "state": "any-state"}
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
