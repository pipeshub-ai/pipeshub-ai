"""Strict OpenAPI audit of GET /api/v1/toolsets/instances/:instanceId/status.

Node only authenticates and proxies to Python `get_instance_status`, which has no admin
gate: any org member gets the instance summary plus their own `isAuthenticated` flag.
"""

from __future__ import annotations

import pytest
from strict_openapi import (
    assert_strict_openapi_exchange,
    assert_strict_openapi_response,
    outside_request_contract,
)
from toolsets_audit_support import (
    API_TOKEN_AUTH,
    INSTANCE_NOT_FOUND,
    MISSING_INSTANCE_ID,
    UNSAFE_PATH_ID,
    JsonObject,
    SeedToolsetInstance,
    ToolsetsClient,
    assert_not_found,
    request_as,
)

from helper.second_user import SecondUser

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/toolsets/instances/:instanceId/status"


def _expected_status(instance: JsonObject, *, is_authenticated: bool) -> JsonObject:
    return {
        "status": "success",
        "instanceId": instance["_id"],
        "instanceName": instance["instanceName"],
        "toolsetType": instance["toolsetType"],
        "authType": instance["authType"],
        "isConfigured": True,
        "isAuthenticated": is_authenticated,
    }


def test_status_reports_authentication_of_the_caller(
    toolsets_client: ToolsetsClient,
    seed_toolset_instance: SeedToolsetInstance,
) -> None:
    instance = seed_toolset_instance()
    path = f"/instances/{instance['_id']}/status"

    before = toolsets_client.get(path)
    assert before.status_code == 200, before.text[:500]
    assert_strict_openapi_exchange(before, ROUTE)
    assert before.json() == _expected_status(instance, is_authenticated=False)

    # Only stores the credentials; nothing is sent to Jira. Removed with the instance.
    saved = toolsets_client.post(
        f"/instances/{instance['_id']}/authenticate", json={"auth": dict(API_TOKEN_AUTH)}
    )
    assert saved.status_code == 200, saved.text[:500]

    after = toolsets_client.get(path)
    assert after.status_code == 200, after.text[:500]
    assert_strict_openapi_exchange(after, ROUTE)
    assert after.json() == _expected_status(instance, is_authenticated=True)


def test_a_started_oauth_flow_is_not_authenticated(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    instance = seed_toolset_instance(oauth=True)
    started = toolsets_client.get(f"/instances/{instance['_id']}/oauth/authorize")
    assert started.status_code == 200, started.text[:500]

    resp = toolsets_client.get(f"/instances/{instance['_id']}/status")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == _expected_status(instance, is_authenticated=False)


def test_member_sees_own_status_not_the_admins(
    toolsets_client: ToolsetsClient,
    second_user: SecondUser,
    seed_toolset_instance: SeedToolsetInstance,
) -> None:
    instance = seed_toolset_instance()
    saved = toolsets_client.post(
        f"/instances/{instance['_id']}/authenticate", json={"auth": dict(API_TOKEN_AUTH)}
    )
    assert saved.status_code == 200, saved.text[:500]

    resp = request_as(second_user, "GET", f"/instances/{instance['_id']}/status")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == _expected_status(instance, is_authenticated=False)


def test_status_does_not_read_the_query_string(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    instance = seed_toolset_instance()

    with outside_request_contract("the route reads no query parameter and forwards none"):
        resp = toolsets_client.get(f"/instances/{instance['_id']}/status", params={"specAuditUnknown": "1"})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == _expected_status(instance, is_authenticated=False)


def test_status_of_unknown_instance_is_not_found(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.get(f"/instances/{MISSING_INSTANCE_ID}/status")
    assert_not_found(resp, INSTANCE_NOT_FOUND)
    assert_strict_openapi_exchange(resp, ROUTE)


def test_status_without_token_is_unauthorized(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.get(f"/instances/{MISSING_INSTANCE_ID}/status", auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_status_with_unsafe_instance_id_is_rejected_before_auth(
    toolsets_client: ToolsetsClient,
) -> None:
    resp = toolsets_client.get(f"/instances/{UNSAFE_PATH_ID}/status", auth=False)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
