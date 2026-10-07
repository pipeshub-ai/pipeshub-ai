"""Strict OpenAPI audit of GET /api/v1/mcp-servers/instances/:instanceId/oauth/authorize."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from helper.second_user import SecondUser
from mcp_servers_audit_support import (
    MISSING_INSTANCE_ID,
    UNSAFE_PATH_ID,
    McpServersClient,
    SeedMcpInstance,
    oauth_instance_body,
    request_as,
)
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/mcp-servers/instances/:instanceId/oauth/authorize"
CLIENT_ID = "spec-audit-client"
CALLBACK_SUFFIX = "/mcp-servers/oauth/callback/"


def _authorize_path(instance_id: str) -> str:
    return f"/instances/{instance_id}/oauth/authorize"


def _configured_oauth_instance(client: McpServersClient, seed: SeedMcpInstance) -> str:
    instance_id = seed(**oauth_instance_body())["_id"]
    configured = client.put(
        f"/instances/{instance_id}/oauth-config",
        json={"clientId": CLIENT_ID, "clientSecret": "spec-audit-secret"},
    )
    assert configured.status_code == 200, f"storing the OAuth client: {configured.text[:500]}"
    return instance_id


def _redirect_uri(resp_json: dict[str, str]) -> str:
    return parse_qs(urlparse(resp_json["authorizationUrl"]).query)["redirect_uri"][0]


def test_admin_gets_authorization_url_for_configured_oauth_instance(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    instance_id = _configured_oauth_instance(mcp_servers_client, seed_mcp_instance)

    # Nothing is dialled at the provider: this only builds the URL and stores a 10-minute state.
    resp = mcp_servers_client.get(_authorize_path(instance_id))
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    url = urlparse(resp.json()["authorizationUrl"])
    query = parse_qs(url.query)
    assert f"{url.scheme}://{url.netloc}{url.path}" == "https://auth.invalid/authorize"
    assert query["client_id"] == [CLIENT_ID]
    assert query["state"][0]
    assert query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"][0].endswith(CALLBACK_SUFFIX)


def test_base_url_is_used_only_on_the_configured_origin(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    instance_id = _configured_oauth_instance(mcp_servers_client, seed_mcp_instance)
    default = mcp_servers_client.get(_authorize_path(instance_id))
    assert default.status_code == 200, default.text[:500]
    origin = _redirect_uri(default.json()).removesuffix(CALLBACK_SUFFIX)

    same_origin = mcp_servers_client.get(_authorize_path(instance_id), params={"baseUrl": f"{origin}/spec-audit"})
    assert same_origin.status_code == 200, same_origin.text[:500]
    assert_strict_openapi_exchange(same_origin, ROUTE)
    assert _redirect_uri(same_origin.json()) == f"{origin}/spec-audit{CALLBACK_SUFFIX}"

    foreign = mcp_servers_client.get(_authorize_path(instance_id), params={"baseUrl": "https://spec-audit.invalid"})
    assert foreign.status_code == 200, foreign.text[:500]
    assert_strict_openapi_exchange(foreign, ROUTE)
    assert _redirect_uri(foreign.json()) == f"{origin}{CALLBACK_SUFFIX}"


def test_unknown_query_parameter_is_dropped(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    instance_id = _configured_oauth_instance(mcp_servers_client, seed_mcp_instance)
    with outside_request_contract("sends a query parameter the route does not define"):
        resp = mcp_servers_client.get(_authorize_path(instance_id), params={"scope": "spec-audit-scope"})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert "scope" not in parse_qs(urlparse(resp.json()["authorizationUrl"]).query)


def test_member_gets_their_own_authorization_url(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
    second_user: SecondUser,
) -> None:
    # Not admin-gated: every member connects their own account.
    instance_id = _configured_oauth_instance(mcp_servers_client, seed_mcp_instance)
    resp = request_as(second_user, "GET", _authorize_path(instance_id))
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_non_oauth_instance_is_bad_request(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    instance_id = seed_mcp_instance(authMode="api_token")["_id"]

    resp = mcp_servers_client.get(_authorize_path(instance_id))
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_oauth_instance_without_client_config_is_conflict(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    # The unreachable url publishes no metadata, so there is no DCR fallback either.
    instance_id = seed_mcp_instance(**oauth_instance_body())["_id"]

    resp = mcp_servers_client.get(_authorize_path(instance_id))
    assert resp.status_code == 409, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_oauth_instance_without_endpoints_is_conflict(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    instance_id = seed_mcp_instance(**oauth_instance_body(authorizationUrl=None, tokenUrl=None))["_id"]
    configured = mcp_servers_client.put(
        f"/instances/{instance_id}/oauth-config", json={"clientId": CLIENT_ID, "clientSecret": "s"}
    )
    assert configured.status_code == 200, configured.text[:500]

    resp = mcp_servers_client.get(_authorize_path(instance_id))
    assert resp.status_code == 409, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert "authorization/token URLs" in resp.json()["error"]["message"]


def test_unknown_instance_is_not_found(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.get(_authorize_path(MISSING_INSTANCE_ID))
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unsafe_instance_id_is_rejected_before_auth(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.get(_authorize_path(UNSAFE_PATH_ID), auth=False)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_without_an_mcp_scope_is_forbidden(
    mcp_servers_client: McpServersClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = mcp_servers_client.get(_authorize_path(MISSING_INSTANCE_ID), auth=False, headers=narrow_scope_headers)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_without_token_is_unauthorized(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.get(_authorize_path(MISSING_INSTANCE_ID), auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
