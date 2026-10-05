"""Strict OpenAPI audit of PUT /api/v1/toolsets/instances/:instanceId."""

from __future__ import annotations

import uuid

import pytest
from strict_openapi import assert_strict_openapi_response
from toolsets_audit_support import (
    MISSING_INSTANCE_ID,
    UNSAFE_PATH_ID,
    JsonObject,
    SeedToolsetInstance,
    ToolsetsClient,
    toolset_store_lock,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/toolsets/instances/:instanceId"


def _rename_body() -> JsonObject:
    return {"instanceName": f"spec-audit-renamed-{uuid.uuid4().hex[:8]}"}


def test_update_instance_renames_a_seeded_instance(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    instance = seed_toolset_instance()
    body = _rename_body()
    with toolset_store_lock():
        resp = toolsets_client.update_instance(instance["_id"], body)
    assert resp.status_code == 200, resp.text[:500]
    assert resp.json()["instance"]["instanceName"] == body["instanceName"], resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_update_instance_refuses_a_name_another_instance_already_uses(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    taken = seed_toolset_instance()
    instance = seed_toolset_instance()
    with toolset_store_lock():
        resp = toolsets_client.update_instance(
            instance["_id"], {"instanceName": taken["instanceName"]}
        )
    assert resp.status_code == 409, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_update_instance_with_an_unknown_id_is_not_found(
    toolsets_client: ToolsetsClient,
) -> None:
    resp = toolsets_client.update_instance(MISSING_INSTANCE_ID, _rename_body())
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_update_instance_rejects_a_call_without_a_token(
    toolsets_client: ToolsetsClient,
) -> None:
    resp = toolsets_client.update_instance(MISSING_INSTANCE_ID, _rename_body(), auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_update_instance_refuses_an_unsafe_instance_id_before_auth(
    toolsets_client: ToolsetsClient,
) -> None:
    resp = toolsets_client.update_instance(UNSAFE_PATH_ID, _rename_body(), auth=False)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
