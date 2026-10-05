"""Strict OpenAPI audit of GET /api/v1/toolsets/instances/:instanceId/status.

Node only authenticates and proxies to Python `get_instance_status`, which has no admin
gate: any org member gets the instance summary plus their own `isAuthenticated` flag.
"""

from __future__ import annotations

import pytest
from toolsets_audit_support import (
    API_TOKEN_AUTH,
    MISSING_INSTANCE_ID,
    UNSAFE_PATH_ID,
    JsonObject,
    SeedToolsetInstance,
    ToolsetsClient,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

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
    assert_strict_openapi_response(before, ROUTE)
    assert before.json() == _expected_status(instance, is_authenticated=False)

    # Only stores the credentials; nothing is sent to Jira. Removed with the instance.
    saved = toolsets_client.post(
        f"/instances/{instance['_id']}/authenticate", json={"auth": dict(API_TOKEN_AUTH)}
    )
    assert saved.status_code == 200, saved.text[:500]

    after = toolsets_client.get(path)
    assert after.status_code == 200, after.text[:500]
    assert_strict_openapi_response(after, ROUTE)
    assert after.json() == _expected_status(instance, is_authenticated=True)


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
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == _expected_status(instance, is_authenticated=False)


def test_status_of_unknown_instance_is_not_found(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.get(f"/instances/{MISSING_INSTANCE_ID}/status")
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_status_without_token_is_unauthorized(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.get(f"/instances/{MISSING_INSTANCE_ID}/status", auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_status_with_unsafe_instance_id_is_rejected_before_auth(
    toolsets_client: ToolsetsClient,
) -> None:
    resp = toolsets_client.get(f"/instances/{UNSAFE_PATH_ID}/status", auth=False)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
