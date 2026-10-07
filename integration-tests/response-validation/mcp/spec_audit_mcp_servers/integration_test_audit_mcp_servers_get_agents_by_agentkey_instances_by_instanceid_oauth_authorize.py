"""Strict OpenAPI audit of GET /api/v1/mcp-servers/agents/:agentKey/instances/:instanceId/oauth/authorize.

The success case only builds the authorization URL: the instance url is unreachable, so
discovery finds nothing and the stored endpoints plus a static client are used.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from helper.second_user import SecondUser
from mcp_servers_audit_support import (
    MISSING_AGENT_KEY,
    MISSING_INSTANCE_ID,
    UNSAFE_PATH_ID,
    McpServersClient,
    SeedAgent,
    SeedMcpInstance,
    agent_path,
    oauth_instance_body,
    request_as,
)
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/mcp-servers/agents/:agentKey/instances/:instanceId/oauth/authorize"
CLIENT_ID = "spec-audit-client-id"
CALLBACK_SUFFIX = "/mcp-servers/oauth/callback/"


def _authorize_path(agent_key: str, instance_id: str) -> str:
    return agent_path(agent_key, instance_id, "/oauth/authorize")


def _configured_oauth_instance(client: McpServersClient, seed: SeedMcpInstance) -> str:
    instance_id = seed(**oauth_instance_body())["_id"]
    configured = client.put(
        f"/instances/{instance_id}/oauth-config",
        json={"clientId": CLIENT_ID, "clientSecret": "spec-audit-client-secret"},
    )
    assert configured.status_code == 200, configured.text[:500]
    return instance_id


def _redirect_uri(resp_json: dict[str, str]) -> str:
    return parse_qs(urlparse(resp_json["authorizationUrl"]).query)["redirect_uri"][0]


def test_service_account_agent_gets_authorization_url(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
    seed_agent: SeedAgent,
) -> None:
    instance_id = _configured_oauth_instance(mcp_servers_client, seed_mcp_instance)

    resp = mcp_servers_client.get(_authorize_path(seed_agent(), instance_id))
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    body = resp.json()
    assert set(body) == {"authorizationUrl"}
    url = urlparse(body["authorizationUrl"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == "https://auth.invalid/authorize"
    query = parse_qs(url.query)
    assert query["client_id"] == [CLIENT_ID]
    assert query.get("state"), "the authorization URL must carry the state the callback looks up"


def test_base_url_is_used_only_on_the_configured_origin(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
    seed_agent: SeedAgent,
) -> None:
    instance_id = _configured_oauth_instance(mcp_servers_client, seed_mcp_instance)
    path = _authorize_path(seed_agent(), instance_id)
    default = mcp_servers_client.get(path)
    assert default.status_code == 200, default.text[:500]
    origin = _redirect_uri(default.json()).removesuffix(CALLBACK_SUFFIX)

    same_origin = mcp_servers_client.get(path, params={"baseUrl": f"{origin}/spec-audit"})
    assert same_origin.status_code == 200, same_origin.text[:500]
    assert_strict_openapi_exchange(same_origin, ROUTE)
    assert _redirect_uri(same_origin.json()) == f"{origin}/spec-audit{CALLBACK_SUFFIX}"

    foreign = mcp_servers_client.get(path, params={"baseUrl": "https://spec-audit.invalid"})
    assert foreign.status_code == 200, foreign.text[:500]
    assert_strict_openapi_exchange(foreign, ROUTE)
    assert _redirect_uri(foreign.json()) == f"{origin}{CALLBACK_SUFFIX}"


def test_unknown_query_parameter_is_dropped(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
    seed_agent: SeedAgent,
) -> None:
    instance_id = _configured_oauth_instance(mcp_servers_client, seed_mcp_instance)
    with outside_request_contract("sends a query parameter the route does not define"):
        resp = mcp_servers_client.get(_authorize_path(seed_agent(), instance_id), params={"scope": "spec-audit"})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)


def test_oauth_instance_without_client_config_is_conflict(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
    seed_agent: SeedAgent,
) -> None:
    instance_id = seed_mcp_instance(**oauth_instance_body())["_id"]

    # No static client, and nothing to discover at the unreachable url, so no DCR either.
    resp = mcp_servers_client.get(_authorize_path(seed_agent(), instance_id))
    assert resp.status_code == 409, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_non_oauth_instance_is_bad_request(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
    seed_agent: SeedAgent,
) -> None:
    instance_id = seed_mcp_instance(authMode="api_token")["_id"]
    resp = mcp_servers_client.get(_authorize_path(seed_agent(), instance_id))
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_regular_agent_is_bad_request(
    mcp_servers_client: McpServersClient,
    seed_agent: SeedAgent,
) -> None:
    agent_key = seed_agent(is_service_account=False)

    # The service-account check runs before the instance lookup, so the unknown id is never read.
    resp = mcp_servers_client.get(_authorize_path(agent_key, MISSING_INSTANCE_ID))
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_who_cannot_edit_the_agent_is_forbidden(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
    seed_agent: SeedAgent,
    second_user: SecondUser,
) -> None:
    instance_id = _configured_oauth_instance(mcp_servers_client, seed_mcp_instance)
    resp = request_as(second_user, "GET", _authorize_path(seed_agent(), instance_id))
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unknown_agent_is_not_found(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.get(_authorize_path(MISSING_AGENT_KEY, MISSING_INSTANCE_ID))
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unknown_instance_is_not_found(mcp_servers_client: McpServersClient, seed_agent: SeedAgent) -> None:
    resp = mcp_servers_client.get(_authorize_path(seed_agent(), MISSING_INSTANCE_ID))
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unsafe_instance_id_is_rejected_before_auth(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.get(_authorize_path(MISSING_AGENT_KEY, UNSAFE_PATH_ID), auth=False)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_without_an_agent_write_scope_is_forbidden(
    mcp_servers_client: McpServersClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = mcp_servers_client.get(
        _authorize_path(MISSING_AGENT_KEY, MISSING_INSTANCE_ID), auth=False, headers=narrow_scope_headers
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_without_token_is_unauthorized(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.get(
        _authorize_path(MISSING_AGENT_KEY, MISSING_INSTANCE_ID), auth=False
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
