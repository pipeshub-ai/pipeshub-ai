"""Strict OpenAPI audit of GET /api/v1/toolsets/instances/:instanceId."""

from __future__ import annotations

import pytest
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response
from toolsets_audit_support import (
    MISSING_INSTANCE_ID,
    TOOLSET_TYPE,
    UNSAFE_PATH_ID,
    SeedToolsetInstance,
    ToolsetsClient,
    request_as,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/toolsets/instances/:instanceId"
REGISTRY_FIELDS = {"displayName", "description", "iconPath", "supportedAuthTypes", "toolCount"}
ADMIN_ONLY_FIELDS = {"auth", "oauthConfig", "authenticatedUserCount"}


def test_admin_gets_an_oauth_instance_with_its_oauth_config_and_user_count(
    toolsets_client: ToolsetsClient,
    seed_toolset_instance: SeedToolsetInstance,
) -> None:
    seeded = seed_toolset_instance(oauth=True)

    resp = toolsets_client.get_instance(seeded["_id"])
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    body = resp.json()
    assert body["status"] == "success", body
    instance = body["instance"]
    assert instance["_id"] == seeded["_id"], instance
    assert instance["toolsetType"] == TOOLSET_TYPE, instance
    assert instance["authType"] == "OAUTH", instance
    assert REGISTRY_FIELDS <= set(instance), sorted(instance)
    assert instance["oauthConfig"]["_id"] == seeded["oauthConfigId"], instance["oauthConfig"]
    assert instance["authenticatedUserCount"] == 0, instance


def test_member_gets_an_instance_without_the_admin_only_fields(
    second_user: SecondUser,
    seed_toolset_instance: SeedToolsetInstance,
) -> None:
    seeded = seed_toolset_instance()

    resp = request_as(second_user, "GET", f"/instances/{seeded['_id']}")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    instance = resp.json()["instance"]
    assert instance["_id"] == seeded["_id"], instance
    assert instance["authType"] == "API_TOKEN", instance
    assert REGISTRY_FIELDS <= set(instance), sorted(instance)
    assert not ADMIN_ONLY_FIELDS & set(instance), sorted(instance)


def test_an_unknown_instance_id_is_not_found(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.get_instance(MISSING_INSTANCE_ID)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_get_instance_rejects_a_call_without_a_token(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.get_instance(MISSING_INSTANCE_ID, auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_get_instance_refuses_an_unsafe_instance_id_before_auth(
    toolsets_client: ToolsetsClient,
) -> None:
    resp = toolsets_client.get_instance(UNSAFE_PATH_ID, auth=False)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
