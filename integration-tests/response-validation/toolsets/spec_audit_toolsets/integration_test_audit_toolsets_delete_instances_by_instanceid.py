"""Strict OpenAPI audit of DELETE /api/v1/toolsets/instances/:instanceId."""

from __future__ import annotations

import pytest
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response
from toolsets_audit_support import (
    MISSING_INSTANCE_ID,
    UNSAFE_PATH_ID,
    SeedToolsetInstance,
    ToolsetsClient,
    request_as,
    toolset_store_lock,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/toolsets/instances/:instanceId"


def test_admin_deletes_instance_then_it_is_gone(
    toolsets_client: ToolsetsClient,
    seed_toolset_instance: SeedToolsetInstance,
) -> None:
    instance_id = seed_toolset_instance()["_id"]

    with toolset_store_lock():
        resp = toolsets_client.delete_instance(instance_id)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    body = resp.json()
    assert body["status"] == "success"
    assert body["instanceId"] == instance_id
    # Nobody authenticated against the fresh instance, so no credentials were removed.
    assert body["deletedCredentialsCount"] == 0

    with toolset_store_lock():
        again = toolsets_client.delete_instance(instance_id)
    assert again.status_code == 404, again.text[:500]
    assert_strict_openapi_response(again, ROUTE)


def test_delete_unknown_instance_is_not_found(toolsets_client: ToolsetsClient) -> None:
    with toolset_store_lock():
        resp = toolsets_client.delete_instance(MISSING_INSTANCE_ID)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_delete_without_token_is_unauthorized(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.delete_instance(MISSING_INSTANCE_ID, auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_delete_with_unsafe_instance_id_is_bad_request(toolsets_client: ToolsetsClient) -> None:
    # guardPathParams refuses the id in Node before the request is proxied.
    resp = toolsets_client.delete_instance(UNSAFE_PATH_ID)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_member_cannot_delete_an_existing_instance(
    second_user: SecondUser,
    seed_toolset_instance: SeedToolsetInstance,
) -> None:
    instance_id = seed_toolset_instance()["_id"]

    resp = request_as(second_user, "DELETE", f"/instances/{instance_id}")
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
