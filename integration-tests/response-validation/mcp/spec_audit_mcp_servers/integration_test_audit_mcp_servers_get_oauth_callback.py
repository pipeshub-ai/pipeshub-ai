"""Strict OpenAPI audit of GET /api/v1/mcp-servers/oauth/callback."""

from __future__ import annotations

import uuid
from urllib.parse import parse_qs, urlparse

import pytest
from helper.second_user import SecondUser
from mcp_servers_audit_support import (
    McpServersClient,
    SeedMcpInstance,
    fake_token_endpoint,
    oauth_instance_body,
    request_as,
)
from runner_stub import needs_runner_stub
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/mcp-servers/oauth/callback"
OAUTH_CLIENT = {"clientId": "spec-audit-client", "clientSecret": "spec-audit-secret"}


def _start_authorization(
    client: McpServersClient, seed: SeedMcpInstance, **overrides: str
) -> tuple[str, str]:
    """An OAuth instance with a static client and a pending authorization: ``(instance_id, state)``."""
    instance_id = seed(**oauth_instance_body(**overrides))["_id"]
    configured = client.put(f"/instances/{instance_id}/oauth-config", json=OAUTH_CLIENT)
    if configured.status_code != 200:
        pytest.fail(f"storing the OAuth client: {configured.status_code} {configured.text[:500]}")
    authorize = client.get(f"/instances/{instance_id}/oauth/authorize")
    if authorize.status_code != 200:
        pytest.fail(f"starting the OAuth flow: {authorize.status_code} {authorize.text[:500]}")
    state = parse_qs(urlparse(authorize.json()["authorizationUrl"]).query)["state"][0]
    return instance_id, state


@needs_runner_stub
def test_callback_exchanges_the_code_and_stores_the_tokens(
    mcp_servers_client: McpServersClient, seed_mcp_instance: SeedMcpInstance
) -> None:
    with fake_token_endpoint() as token_endpoint:
        instance_id, state = _start_authorization(
            mcp_servers_client, seed_mcp_instance, tokenUrl=token_endpoint.url
        )
        resp = mcp_servers_client.get("/oauth/callback", params={"code": "spec-audit-code", "state": state})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
        assert resp.json() == {"success": True, "instanceId": instance_id}
        assert len(token_endpoint.received) == 1
        sent = parse_qs(token_endpoint.received[0])
        assert sent["grant_type"] == ["authorization_code"]
        assert sent["code"] == ["spec-audit-code"]
        assert sent["client_id"] == [OAUTH_CLIENT["clientId"]]

    listed = mcp_servers_client.get("/my-mcp-servers", params={"includeTools": "false"})
    assert listed.status_code == 200, listed.text[:500]
    entry = next(i for i in listed.json()["instances"] if i["_id"] == instance_id)
    assert entry["isAuthenticated"] is True


@needs_runner_stub
def test_callback_reports_a_refused_code_exchange(
    mcp_servers_client: McpServersClient, seed_mcp_instance: SeedMcpInstance
) -> None:
    with fake_token_endpoint() as token_endpoint:
        token_endpoint.reply = (500, {"error": "server_error"})
        _, state = _start_authorization(mcp_servers_client, seed_mcp_instance, tokenUrl=token_endpoint.url)
        resp = mcp_servers_client.get("/oauth/callback", params={"code": "spec-audit-code", "state": state})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["success"] is False
    assert resp.json()["error"] == "token_exchange_failed", resp.text[:500]


def test_callback_reports_an_oauth_client_replaced_mid_flow(
    mcp_servers_client: McpServersClient, seed_mcp_instance: SeedMcpInstance
) -> None:
    instance_id, state = _start_authorization(mcp_servers_client, seed_mcp_instance)
    rotated = mcp_servers_client.put(
        f"/instances/{instance_id}/oauth-config",
        json={"clientId": "spec-audit-rotated-client", "clientSecret": "spec-audit-secret"},
    )
    assert rotated.status_code == 200, rotated.text[:500]

    resp = mcp_servers_client.get("/oauth/callback", params={"code": "spec-audit-code", "state": state})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["success"] is False
    assert resp.json()["error"] == "client_config_changed", resp.text[:500]


def test_callback_is_a_500_when_the_token_endpoint_cannot_be_reached(
    mcp_servers_client: McpServersClient, seed_mcp_instance: SeedMcpInstance
) -> None:
    # API bug: only a token endpoint that answers is turned into token_exchange_failed; a
    # host that does not resolve escapes the handler as a 500, despite "always 200".
    _, state = _start_authorization(mcp_servers_client, seed_mcp_instance)
    resp = mcp_servers_client.get("/oauth/callback", params={"code": "spec-audit-code", "state": state})
    assert resp.status_code == 500, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


# Python never raises on these: every refusal past auth is a 200 with success:false.
@pytest.mark.parametrize(
    ("params", "error"),
    [
        pytest.param(
            {"error": "access_denied", "code": "c", "state": "s"},
            "access_denied",
            id="provider-error-wins",
        ),
        pytest.param({"code": "only-a-code"}, "missing_params", id="missing-state"),
        pytest.param({"state": "only-a-state"}, "missing_params", id="missing-code"),
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
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert body["success"] is False, body
    assert body["error"] == error, body
    assert body["errorMessage"], body


def test_callback_drops_an_unknown_query_parameter(mcp_servers_client: McpServersClient) -> None:
    # Node forwards only code, state and error; error_description never reaches Python.
    with outside_request_contract("sends a query parameter the route does not define"):
        resp = mcp_servers_client.get(
            "/oauth/callback", params={"code": "c", "error_description": "ignored"}
        )
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"] == "missing_params", resp.text[:500]


def test_callback_refuses_a_state_started_by_another_user(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
    second_user: SecondUser,
) -> None:
    _, state = _start_authorization(mcp_servers_client, seed_mcp_instance)

    # The admin started the flow; the member's callback is refused before any token exchange.
    resp = request_as(
        second_user, "GET", "/oauth/callback", params={"code": "any-code", "state": state}
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert body["success"] is False, body
    assert body["error"] == "caller_mismatch", body
    assert "instanceId" not in body, body

    # The state is single-use, so the initiator cannot replay it either.
    replay = mcp_servers_client.get(
        "/oauth/callback", params={"code": "any-code", "state": state}
    )
    assert replay.status_code == 200, replay.text[:500]
    assert_strict_openapi_exchange(replay, ROUTE)
    assert replay.json()["error"] == "invalid_state", replay.text[:500]


def test_callback_without_an_mcp_write_scope_is_forbidden(
    mcp_servers_client: McpServersClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = mcp_servers_client.get(
        "/oauth/callback", auth=False, headers=narrow_scope_headers, params={"code": "c", "state": "s"}
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_callback_without_a_token_is_401(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.get(
        "/oauth/callback", auth=False, params={"code": "any-code", "state": "any-state"}
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
