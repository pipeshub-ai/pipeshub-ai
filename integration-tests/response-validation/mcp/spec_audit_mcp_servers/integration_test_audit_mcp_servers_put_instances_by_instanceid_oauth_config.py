"""Strict OpenAPI audit of PUT /api/v1/mcp-servers/instances/:instanceId/oauth-config."""

from __future__ import annotations

from typing import Any

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
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/mcp-servers/instances/:instanceId/oauth-config"

CLIENT_ID = "spec-audit-client-id"
CLIENT_SECRET = "spec-audit-client-secret"
CLIENT_CONFIG = {"clientId": CLIENT_ID, "clientSecret": CLIENT_SECRET}
MASK = "•" * 8


def _path(instance_id: str) -> str:
    return f"/instances/{instance_id}/oauth-config"


def test_update_stores_client_config_and_read_masks_it(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    instance_id = seed_mcp_instance(**oauth_instance_body())["_id"]

    resp = mcp_servers_client.put(_path(instance_id), json=CLIENT_CONFIG)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"success": True}

    stored = mcp_servers_client.get(_path(instance_id))
    assert stored.status_code == 200, stored.text[:500]
    config = stored.json()
    assert config["configured"] is True
    assert config["clientId"] == f"{CLIENT_ID[:4]}{MASK}{CLIENT_ID[-4:]}"
    assert CLIENT_SECRET not in stored.text


def test_update_reads_snake_case_and_ignores_unknown_fields(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    instance_id = seed_mcp_instance(**oauth_instance_body())["_id"]
    with outside_request_contract("the client is sent under snake_case names next to an unknown field"):
        resp = mcp_servers_client.put(
            _path(instance_id),
            json={"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "redirectUri": "ignored"},
        )
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)

    stored = mcp_servers_client.get(_path(instance_id))
    assert stored.json()["clientId"] == f"{CLIENT_ID[:4]}{MASK}{CLIENT_ID[-4:]}"


def test_update_accepts_empty_strings(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    # Nothing checks the values, so an empty client is stored and reported as configured.
    instance_id = seed_mcp_instance(**oauth_instance_body())["_id"]
    resp = mcp_servers_client.put(_path(instance_id), json={"clientId": "", "clientSecret": ""})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    stored = mcp_servers_client.get(_path(instance_id))
    assert stored.json() == {"configured": True, "clientId": "", "clientSecret": ""}


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"clientId": CLIENT_ID}, id="missing-client-secret"),
        pytest.param({"clientSecret": CLIENT_SECRET}, id="missing-client-id"),
        pytest.param({}, id="empty-object"),
        pytest.param({"clientId": 12345, "clientSecret": CLIENT_SECRET}, id="client-id-not-a-string"),
        pytest.param({"clientId": CLIENT_ID, "clientSecret": None}, id="client-secret-null"),
    ],
)
def test_update_with_a_bad_body_is_unprocessable(
    mcp_servers_client: McpServersClient, body: dict[str, Any]
) -> None:
    # Pydantic rejects the body before the admin check and the instance lookup.
    resp = mcp_servers_client.put(_path(MISSING_INSTANCE_ID), json=body)
    assert resp.status_code == 422, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


def test_update_unknown_instance_is_not_found(
    mcp_servers_client: McpServersClient,
) -> None:
    resp = mcp_servers_client.put(_path(MISSING_INSTANCE_ID), json=CLIENT_CONFIG)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    # Nothing may be stored for an instance that does not exist.
    stored = mcp_servers_client.get(_path(MISSING_INSTANCE_ID))
    assert stored.status_code == 200, stored.text[:500]
    assert stored.json() == {"configured": False}


def test_update_as_member_is_forbidden(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
    second_user: SecondUser,
) -> None:
    instance_id = seed_mcp_instance(**oauth_instance_body())["_id"]

    resp = request_as(second_user, "PUT", _path(instance_id), json=CLIENT_CONFIG)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    stored = mcp_servers_client.get(_path(instance_id))
    assert stored.status_code == 200, stored.text[:500]
    assert stored.json() == {"configured": False}


def test_update_without_an_mcp_write_scope_is_forbidden(
    mcp_servers_client: McpServersClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = mcp_servers_client.put(
        _path(MISSING_INSTANCE_ID), auth=False, headers=narrow_scope_headers, json=CLIENT_CONFIG
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_unsafe_instance_id_is_rejected_before_auth(
    mcp_servers_client: McpServersClient,
) -> None:
    resp = mcp_servers_client.put(_path(UNSAFE_PATH_ID), auth=False, json=CLIENT_CONFIG)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_without_token_is_unauthorized(
    mcp_servers_client: McpServersClient,
) -> None:
    resp = mcp_servers_client.put(
        _path(MISSING_INSTANCE_ID), json=CLIENT_CONFIG, auth=False
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
