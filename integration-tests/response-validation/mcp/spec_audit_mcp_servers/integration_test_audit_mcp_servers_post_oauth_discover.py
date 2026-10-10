"""Strict OpenAPI audit of POST /api/v1/mcp-servers/oauth/discover."""

from __future__ import annotations

import pytest
from helper.second_user import SecondUser
from mcp_servers_audit_support import (
    UNREACHABLE_MCP_URL,
    JsonObject,
    McpServersClient,
    request_as,
)
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/mcp-servers/oauth/discover"
PATH = "/oauth/discover"
# A public host that publishes no OAuth metadata: discovery runs and finds nothing.
NO_METADATA_URL = "https://example.com/mcp"


def test_discover_a_server_without_oauth_metadata(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.post(PATH, json={"url": NO_METADATA_URL}, timeout=120)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {
        "metadataFound": False,
        "supportsDcr": False,
        "authorizationEndpoint": None,
        "tokenEndpoint": None,
        "registrationEndpoint": None,
        "scopesSupported": [],
    }


def test_discover_ignores_an_unknown_body_field(mcp_servers_client: McpServersClient) -> None:
    with outside_request_contract("sends a body field the route does not define"):
        accepted = mcp_servers_client.post(
            PATH, json={"url": NO_METADATA_URL, "followRedirects": True}, timeout=120
        )
        assert accepted.status_code == 200, accepted.text[:500]
        assert_strict_openapi_exchange(accepted, ROUTE)
        assert accepted.json()["metadataFound"] is False

        resp = mcp_servers_client.post(
            PATH, json={"url": UNREACHABLE_MCP_URL, "followRedirects": True}
        )
        # Still judged on url alone: the loopback target is refused.
        assert resp.status_code == 400, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)


def test_discover_without_an_mcp_write_scope_is_forbidden(
    mcp_servers_client: McpServersClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = mcp_servers_client.post(
        PATH, auth=False, headers=narrow_scope_headers, json={"url": UNREACHABLE_MCP_URL}
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_discover_without_a_token_is_401(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.post(PATH, auth=False, json={"url": UNREACHABLE_MCP_URL})
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_discover_as_a_member_is_403(second_user: SecondUser) -> None:
    # The admin check runs before the SSRF guard, so the loopback url is never judged.
    resp = request_as(second_user, "POST", PATH, json={"url": UNREACHABLE_MCP_URL})
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert "administrators" in resp.text, resp.text[:500]


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({}, id="missing-url"),
        pytest.param({"url": 42}, id="url-not-a-string"),
        pytest.param({"url": None}, id="url-null"),
    ],
)
def test_discover_with_an_invalid_body_is_unprocessable(
    mcp_servers_client: McpServersClient, body: JsonObject
) -> None:
    resp = mcp_servers_client.post(PATH, json=body)
    assert resp.status_code == 422, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


@pytest.mark.parametrize(
    "url",
    [
        pytest.param(UNREACHABLE_MCP_URL, id="loopback-url"),
        pytest.param("ftp://mcp.example.com/mcp", id="non-http-scheme"),
        pytest.param("not a url", id="not-a-url"),
    ],
)
def test_discover_as_admin_refuses_a_bad_target(
    mcp_servers_client: McpServersClient, url: str
) -> None:
    resp = mcp_servers_client.post(PATH, json={"url": url})
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
