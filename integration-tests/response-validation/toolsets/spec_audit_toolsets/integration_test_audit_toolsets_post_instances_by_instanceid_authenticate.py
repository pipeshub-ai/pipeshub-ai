"""Strict OpenAPI audit of POST /api/v1/toolsets/instances/:instanceId/authenticate."""

from __future__ import annotations

import pytest
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response
from toolsets_audit_support import (
    API_TOKEN_AUTH,
    MISSING_INSTANCE_ID,
    JsonObject,
    SeedToolsetInstance,
    ToolsetsClient,
    request_as,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/toolsets/instances/:instanceId/authenticate"

VALID_BODY: JsonObject = {"auth": dict(API_TOKEN_AUTH)}
AUTHENTICATED: JsonObject = {
    "status": "success",
    "message": "Toolset authenticated successfully.",
    "isAuthenticated": True,
}


def _path(instance_id: str) -> str:
    return f"/instances/{instance_id}/authenticate"


def test_authenticate_stores_credentials_for_admin_and_member(
    toolsets_client: ToolsetsClient,
    second_user: SecondUser,
    seed_toolset_instance: SeedToolsetInstance,
) -> None:
    # Credentials are stored per user and removed when the seeded instance is deleted.
    instance_id = seed_toolset_instance()["_id"]

    resp = toolsets_client.post(_path(instance_id), json=VALID_BODY)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == AUTHENTICATED

    # No admin gate: any org member may save their own credentials.
    as_member = request_as(second_user, "POST", _path(instance_id), json=VALID_BODY)
    assert as_member.status_code == 200, as_member.text[:500]
    assert_strict_openapi_response(as_member, ROUTE)
    assert as_member.json() == AUTHENTICATED


def test_authenticate_without_token_is_unauthorized(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.post(_path(MISSING_INSTANCE_ID), auth=False, json=VALID_BODY)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_authenticate_unknown_instance_is_not_found(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.post(_path(MISSING_INSTANCE_ID), json=VALID_BODY)

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_authenticate_rejects_undeclared_credential_field(
    toolsets_client: ToolsetsClient,
    seed_toolset_instance: SeedToolsetInstance,
) -> None:
    # Node has no validator here; Python refuses keys the toolset's auth schema lacks.
    instance_id = seed_toolset_instance()["_id"]
    body: JsonObject = {"auth": {**API_TOKEN_AUTH, "specAuditUndeclared": "x"}}

    resp = toolsets_client.post(_path(instance_id), json=body)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_authenticate_oauth_instance_is_refused(
    toolsets_client: ToolsetsClient,
    seed_toolset_instance: SeedToolsetInstance,
) -> None:
    instance_id = seed_toolset_instance(oauth=True)["_id"]

    resp = toolsets_client.post(_path(instance_id), json=VALID_BODY)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
