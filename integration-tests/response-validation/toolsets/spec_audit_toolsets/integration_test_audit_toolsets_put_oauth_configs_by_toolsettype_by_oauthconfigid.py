"""Strict OpenAPI audit of PUT /api/v1/toolsets/oauth-configs/:toolsetType/:oauthConfigId."""

from __future__ import annotations

import pytest
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response
from toolsets_audit_support import (
    MISSING_OAUTH_CONFIG_ID,
    OAUTH_CLIENT_AUTH,
    TOOLSET_TYPE,
    UNSAFE_PATH_ID,
    JsonObject,
    SeedToolsetInstance,
    ToolsetsClient,
    request_as,
    toolset_store_lock,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/toolsets/oauth-configs/:toolsetType/:oauthConfigId"


def _path(oauth_config_id: str) -> str:
    return f"/oauth-configs/{TOOLSET_TYPE}/{oauth_config_id}"


def _update_body() -> JsonObject:
    return {"authConfig": dict(OAUTH_CLIENT_AUTH)}


def test_update_oauth_config_rewrites_a_seeded_config(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    instance = seed_toolset_instance(oauth=True)
    oauth_config_id = instance["oauthConfigId"]
    with toolset_store_lock():
        resp = toolsets_client.put(_path(oauth_config_id), json=_update_body())
    assert resp.status_code == 200, resp.text[:500]
    body = resp.json()
    assert body["oauthConfigId"] == oauth_config_id, resp.text[:500]
    # Nobody authenticated against the freshly seeded instance.
    assert body["deauthenticatedUserCount"] == 0, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_update_oauth_config_is_forbidden_for_a_member(second_user: SecondUser) -> None:
    # Python checks admin before it looks the config up, so no seeded config is needed.
    resp = request_as(
        second_user, "PUT", _path(MISSING_OAUTH_CONFIG_ID), json=_update_body()
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_update_oauth_config_with_an_unknown_id_is_not_found(
    toolsets_client: ToolsetsClient,
) -> None:
    resp = toolsets_client.put(_path(MISSING_OAUTH_CONFIG_ID), json=_update_body())
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_update_oauth_config_rejects_a_call_without_a_token(
    toolsets_client: ToolsetsClient,
) -> None:
    resp = toolsets_client.put(
        _path(MISSING_OAUTH_CONFIG_ID), auth=False, json=_update_body()
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_update_oauth_config_refuses_an_unsafe_config_id_before_auth(
    toolsets_client: ToolsetsClient,
) -> None:
    resp = toolsets_client.put(_path(UNSAFE_PATH_ID), auth=False, json=_update_body())
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
