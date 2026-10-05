"""Strict OpenAPI audit of DELETE /api/v1/toolsets/oauth-configs/:toolsetType/:oauthConfigId."""

from __future__ import annotations

import pytest
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response
from toolsets_audit_support import (
    MISSING_OAUTH_CONFIG_ID,
    TOOLSET_TYPE,
    UNSAFE_PATH_ID,
    SeedToolsetInstance,
    ToolsetsClient,
    request_as,
    toolset_store_lock,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/toolsets/oauth-configs/:toolsetType/:oauthConfigId"


def test_delete_is_refused_while_in_use_then_succeeds_once_the_instance_is_gone(
    toolsets_client: ToolsetsClient,
    seed_toolset_instance: SeedToolsetInstance,
) -> None:
    record = seed_toolset_instance(oauth=True)
    oauth_config_id = record["oauthConfigId"]

    with toolset_store_lock():
        in_use = toolsets_client.delete_oauth_config(TOOLSET_TYPE, oauth_config_id)
    assert in_use.status_code == 409, in_use.text[:500]
    assert_strict_openapi_response(in_use, ROUTE)

    with toolset_store_lock():
        removed = toolsets_client.delete_instance(record["_id"])
        assert removed.status_code == 200, removed.text[:500]
        resp = toolsets_client.delete_oauth_config(TOOLSET_TYPE, oauth_config_id)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json()["status"] == "success"

    with toolset_store_lock():
        again = toolsets_client.delete_oauth_config(TOOLSET_TYPE, oauth_config_id)
    assert again.status_code == 404, again.text[:500]
    assert_strict_openapi_response(again, ROUTE)


def test_delete_unknown_oauth_config_is_not_found(toolsets_client: ToolsetsClient) -> None:
    with toolset_store_lock():
        resp = toolsets_client.delete_oauth_config(TOOLSET_TYPE, MISSING_OAUTH_CONFIG_ID)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_delete_without_token_is_unauthorized(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.delete_oauth_config(TOOLSET_TYPE, MISSING_OAUTH_CONFIG_ID, auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_delete_with_unsafe_oauth_config_id_is_bad_request(toolsets_client: ToolsetsClient) -> None:
    # guardPathParams refuses the id in Node before the request is proxied.
    resp = toolsets_client.delete_oauth_config(TOOLSET_TYPE, UNSAFE_PATH_ID)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_member_cannot_delete_an_existing_oauth_config(
    toolsets_client: ToolsetsClient,
    second_user: SecondUser,
    seed_toolset_instance: SeedToolsetInstance,
) -> None:
    oauth_config_id = seed_toolset_instance(oauth=True)["oauthConfigId"]

    resp = request_as(second_user, "DELETE", f"/oauth-configs/{TOOLSET_TYPE}/{oauth_config_id}")
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    # Still referenced by its instance, so the admin's own attempt proves it was kept.
    with toolset_store_lock():
        kept = toolsets_client.delete_oauth_config(TOOLSET_TYPE, oauth_config_id)
    assert kept.status_code == 409, kept.text[:500]
