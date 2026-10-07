"""Strict OpenAPI audit of POST /api/v1/mcp-servers/instances/:instanceId/oauth/refresh.

The success and provider-failure cases run the real authorize and callback flow against a
local fake token endpoint, which then answers the refresh.
"""

from __future__ import annotations

from urllib.parse import parse_qs

import pytest
from helper.second_user import SecondUser
from mcp_servers_audit_support import (
    MISSING_INSTANCE_ID,
    STATIC_OAUTH_CLIENT,
    UNSAFE_PATH_ID,
    JsonObject,
    McpServersClient,
    SeedMcpInstance,
    connect_oauth_instance,
    fake_token_endpoint,
    oauth_instance_body,
    request_as,
)
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/mcp-servers/instances/:instanceId/oauth/refresh"
ISSUED: JsonObject = {
    "access_token": "spec-audit-access-token",
    "refresh_token": "spec-audit-refresh-token",
    "token_type": "Bearer",
    "expires_in": 3600,
}


def _refresh_path(instance_id: str) -> str:
    return f"/instances/{instance_id}/oauth/refresh"


def test_refresh_exchanges_the_stored_refresh_token(
    mcp_servers_client: McpServersClient, seed_mcp_instance: SeedMcpInstance
) -> None:
    with fake_token_endpoint() as token_endpoint:
        token_endpoint.reply = (200, ISSUED)
        instance_id = seed_mcp_instance(**oauth_instance_body(tokenUrl=token_endpoint.url))["_id"]
        connect_oauth_instance(mcp_servers_client, instance_id, token_endpoint)
        token_endpoint.reply = (200, {**ISSUED, "access_token": "spec-audit-refreshed"})

        resp = mcp_servers_client.post(_refresh_path(instance_id))
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
        assert resp.json() == {"success": True}

        sent = parse_qs(token_endpoint.received[-1])
        assert sent["grant_type"] == ["refresh_token"]
        assert sent["refresh_token"] == [ISSUED["refresh_token"]]
        assert sent["client_id"] == [STATIC_OAUTH_CLIENT["clientId"]]


def test_a_body_is_ignored(
    mcp_servers_client: McpServersClient, seed_mcp_instance: SeedMcpInstance
) -> None:
    instance_id = seed_mcp_instance(**oauth_instance_body())["_id"]
    with outside_request_contract("the route takes no body; one is sent to show it is ignored"):
        resp = mcp_servers_client.post(_refresh_path(instance_id), json={"refreshToken": "ignored"})
        assert resp.status_code == 400, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert "No MCP credential record" in resp.json()["error"]["message"]


@pytest.mark.parametrize(
    ("reply", "expected_status"),
    [
        # "invalid_grant" is what marks a refresh token as permanently rejected.
        pytest.param((400, {"error": "invalid_grant"}), 401, id="refresh-token-rejected"),
        # Python answers 502; the gateway turns every upstream 5xx into a 500.
        pytest.param((503, {"error": "temporarily_unavailable"}), 500, id="token-endpoint-fails"),
    ],
)
def test_refresh_refused_by_the_provider(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
    reply: tuple[int, JsonObject],
    expected_status: int,
) -> None:
    with fake_token_endpoint() as token_endpoint:
        token_endpoint.reply = (200, ISSUED)
        instance_id = seed_mcp_instance(**oauth_instance_body(tokenUrl=token_endpoint.url))["_id"]
        connect_oauth_instance(mcp_servers_client, instance_id, token_endpoint)
        token_endpoint.reply = reply

        resp = mcp_servers_client.post(_refresh_path(instance_id))
        assert resp.status_code == expected_status, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)


def test_tokens_without_a_refresh_token_are_bad_request(
    mcp_servers_client: McpServersClient, seed_mcp_instance: SeedMcpInstance
) -> None:
    with fake_token_endpoint() as token_endpoint:
        token_endpoint.reply = (200, {"access_token": "spec-audit-access-token", "token_type": "Bearer"})
        instance_id = seed_mcp_instance(**oauth_instance_body(tokenUrl=token_endpoint.url))["_id"]
        connect_oauth_instance(mcp_servers_client, instance_id, token_endpoint)

        resp = mcp_servers_client.post(_refresh_path(instance_id))
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert "No refresh token" in resp.json()["error"]["message"]


def test_oauth_instance_without_credential_is_bad_request(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    instance_id = seed_mcp_instance(**oauth_instance_body())["_id"]

    resp = mcp_servers_client.post(_refresh_path(instance_id))
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unknown_instance_is_a_bad_request(mcp_servers_client: McpServersClient) -> None:
    # API bug: the instance is never loaded, so an unknown id is a 400 naming the internal
    # credential store path, not the 404 every sibling route answers.
    resp = mcp_servers_client.post(_refresh_path(MISSING_INSTANCE_ID))
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert f"/services/mcp/credentials/{MISSING_INSTANCE_ID}/" in resp.json()["error"]["message"]


def test_member_is_not_admin_gated(
    second_user: SecondUser,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    instance_id = seed_mcp_instance(**oauth_instance_body())["_id"]

    # No admin check in Python: a member gets past auth and fails on its own missing credential.
    resp = request_as(second_user, "POST", _refresh_path(instance_id))
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_without_an_mcp_write_scope_is_forbidden(
    mcp_servers_client: McpServersClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = mcp_servers_client.post(_refresh_path(MISSING_INSTANCE_ID), auth=False, headers=narrow_scope_headers)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_without_token_is_unauthorized(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.post(_refresh_path(MISSING_INSTANCE_ID), auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unsafe_instance_id_is_rejected_before_auth(
    mcp_servers_client: McpServersClient,
) -> None:
    resp = mcp_servers_client.post(_refresh_path(UNSAFE_PATH_ID), auth=False)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
