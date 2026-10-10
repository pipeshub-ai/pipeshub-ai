"""Strict OpenAPI audit of POST /api/v1/toolsets/instances/:instanceId/reauthenticate."""

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

ROUTE = "/api/v1/toolsets/instances/:instanceId/reauthenticate"

CLEARED: JsonObject = {
    "status": "success",
    "message": "Credentials cleared. Please re-authenticate.",
}


def _is_authenticated(toolsets_client: ToolsetsClient, instance_id: str) -> bool:
    status = toolsets_client.get(f"/instances/{instance_id}/status")
    assert status.status_code == 200, status.text[:500]
    return bool(status.json()["isAuthenticated"])


def test_reauthenticate_clears_saved_credentials(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    instance_id = seed_toolset_instance()["_id"]
    saved = toolsets_client.post(
        f"/instances/{instance_id}/authenticate", json={"auth": dict(API_TOKEN_AUTH)}
    )
    assert saved.status_code == 200, f"saving credentials: {saved.text[:500]}"
    assert _is_authenticated(toolsets_client, instance_id)

    resp = toolsets_client.post(f"/instances/{instance_id}/reauthenticate")

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == CLEARED
    assert not _is_authenticated(toolsets_client, instance_id)


def test_reauthenticate_clears_a_started_oauth_flow(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    instance_id = seed_toolset_instance(oauth=True)["_id"]
    started = toolsets_client.get(f"/instances/{instance_id}/oauth/authorize")
    assert started.status_code == 200, started.text[:500]
    assert toolsets_client.get_instance(instance_id).json()["instance"]["authenticatedUserCount"] == 1

    resp = toolsets_client.post(f"/instances/{instance_id}/reauthenticate")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == CLEARED
    assert toolsets_client.get_instance(instance_id).json()["instance"]["authenticatedUserCount"] == 0


def test_member_reauthenticates_instance_without_saved_credentials(
    second_user: SecondUser, seed_toolset_instance: SeedToolsetInstance
) -> None:
    # No admin gate, and clearing credentials that were never saved is still a success.
    instance_id = seed_toolset_instance()["_id"]

    resp = request_as(second_user, "POST", f"/instances/{instance_id}/reauthenticate")

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == CLEARED


def test_reauthenticate_does_not_read_a_body(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    instance_id = seed_toolset_instance()["_id"]

    with outside_request_contract("Node forwards no body to the backend for this route"):
        resp = toolsets_client.post(f"/instances/{instance_id}/reauthenticate", json={"specAuditUnknown": 1})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == CLEARED


def test_reauthenticate_without_token_is_unauthorized(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.post(f"/instances/{MISSING_INSTANCE_ID}/reauthenticate", auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_reauthenticate_with_unsafe_instance_id_is_refused_before_auth(
    toolsets_client: ToolsetsClient,
) -> None:
    # guardPathParams is a router.param hook, so it answers before authenticate runs.
    resp = toolsets_client.post(f"/instances/{UNSAFE_PATH_ID}/reauthenticate", auth=False)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_reauthenticate_unknown_instance_is_not_found(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.post(f"/instances/{MISSING_INSTANCE_ID}/reauthenticate")

    assert_not_found(resp, INSTANCE_NOT_FOUND)
    assert_strict_openapi_exchange(resp, ROUTE)
