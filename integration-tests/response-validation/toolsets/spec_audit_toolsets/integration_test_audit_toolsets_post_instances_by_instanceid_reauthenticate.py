"""Strict OpenAPI audit of POST /api/v1/toolsets/instances/:instanceId/reauthenticate."""

from __future__ import annotations

import pytest
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response
from toolsets_audit_support import (
    API_TOKEN_AUTH,
    MISSING_INSTANCE_ID,
    UNSAFE_PATH_ID,
    JsonObject,
    SeedToolsetInstance,
    ToolsetsClient,
    request_as,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/toolsets/instances/:instanceId/reauthenticate"

CLEARED: JsonObject = {
    "status": "success",
    "message": "Credentials cleared. Please re-authenticate.",
}


def test_reauthenticate_clears_saved_credentials(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    instance_id = seed_toolset_instance()["_id"]
    saved = toolsets_client.post(
        f"/instances/{instance_id}/authenticate", json={"auth": dict(API_TOKEN_AUTH)}
    )
    assert saved.status_code == 200, f"saving credentials: {saved.text[:500]}"

    resp = toolsets_client.post(f"/instances/{instance_id}/reauthenticate")

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == CLEARED


def test_member_reauthenticates_instance_without_saved_credentials(
    second_user: SecondUser, seed_toolset_instance: SeedToolsetInstance
) -> None:
    # No admin gate, and clearing credentials that were never saved is still a success.
    instance_id = seed_toolset_instance()["_id"]

    resp = request_as(second_user, "POST", f"/instances/{instance_id}/reauthenticate")

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == CLEARED


def test_reauthenticate_without_token_is_unauthorized(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.post(f"/instances/{MISSING_INSTANCE_ID}/reauthenticate", auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_reauthenticate_with_unsafe_instance_id_is_refused_before_auth(
    toolsets_client: ToolsetsClient,
) -> None:
    # guardPathParams is a router.param hook, so it answers before authenticate runs.
    resp = toolsets_client.post(f"/instances/{UNSAFE_PATH_ID}/reauthenticate", auth=False)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_reauthenticate_unknown_instance_is_not_found(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.post(f"/instances/{MISSING_INSTANCE_ID}/reauthenticate")

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
