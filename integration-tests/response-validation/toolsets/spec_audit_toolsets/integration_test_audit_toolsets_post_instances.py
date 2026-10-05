"""Strict OpenAPI audit of POST /api/v1/toolsets/instances."""

from __future__ import annotations

import pytest
from helper.second_user import SecondUser
from toolsets_audit_support import (
    MISSING_TOOLSET_TYPE,
    SeedToolsetInstance,
    ToolsetsClient,
    instance_body,
    request_as,
    toolset_store_lock,
)
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/toolsets/instances"


def test_admin_creates_an_api_token_instance(toolsets_client: ToolsetsClient) -> None:
    body = instance_body()
    with toolset_store_lock():
        resp = toolsets_client.create_instance(body)
        try:
            # The Python handler sets no status code, and Node forwards FastAPI's 200.
            assert resp.status_code == 200, resp.text[:500]
            instance = resp.json()["instance"]
            assert instance["instanceName"] == body["instanceName"]
            assert instance["authType"] == "API_TOKEN"
            assert_strict_openapi_response(resp, ROUTE)
        finally:
            if resp.status_code < 300:
                instance_id = (resp.json().get("instance") or {}).get("_id")
                if instance_id:
                    toolsets_client.delete_instance(instance_id)


def test_duplicate_instance_name_is_a_conflict(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    existing = seed_toolset_instance()
    with toolset_store_lock():
        resp = toolsets_client.create_instance(
            instance_body(instanceName=existing["instanceName"])
        )
    assert resp.status_code == 409, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_unknown_toolset_type_is_not_found(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.create_instance(instance_body(toolsetType=MISSING_TOOLSET_TYPE))
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "POST", "/instances", json=instance_body())
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_without_token_is_unauthorized(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.create_instance(instance_body(), auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
