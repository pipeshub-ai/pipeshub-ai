"""Strict OpenAPI audit of DELETE /api/v1/toolsets/oauth-configs/:toolsetType/:oauthConfigId."""

from __future__ import annotations

import pytest
from strict_openapi import assert_strict_openapi_exchange
from toolsets_audit_support import (
    MISSING_OAUTH_CONFIG_ID,
    OAUTH_ONLY_LOCAL_TOOLSET_TYPE,
    TOOLSET_TYPE,
    UNSAFE_PATH_ID,
    SeedToolsetInstance,
    ToolsetsClient,
    assert_forbidden,
    assert_not_found,
    error_of,
    request_as,
    toolset_store_lock,
)

from helper.second_user import SecondUser

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
    assert error_of(in_use)["message"] == (
        "Cannot delete OAuth configuration: it is referenced by 1 toolset instance(s) "
        f"('{record['instanceName']}'). Update or delete those instances first."
    )
    assert_strict_openapi_exchange(in_use, ROUTE)

    with toolset_store_lock():
        removed = toolsets_client.delete_instance(record["_id"])
        assert removed.status_code == 200, removed.text[:500]
        resp = toolsets_client.delete_oauth_config(TOOLSET_TYPE, oauth_config_id)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"status": "success", "message": "OAuth configuration deleted successfully."}

    with toolset_store_lock():
        again = toolsets_client.delete_oauth_config(TOOLSET_TYPE, oauth_config_id)
    assert_not_found(again, f"OAuth configuration '{oauth_config_id}' not found.")
    assert_strict_openapi_exchange(again, ROUTE)


def test_delete_under_another_toolset_type_is_not_found(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    oauth_config_id = seed_toolset_instance(oauth=True)["oauthConfigId"]

    with toolset_store_lock():
        resp = toolsets_client.delete_oauth_config(OAUTH_ONLY_LOCAL_TOOLSET_TYPE, oauth_config_id)
    assert_not_found(resp, f"OAuth configuration '{oauth_config_id}' not found.")
    assert_strict_openapi_exchange(resp, ROUTE)


def test_delete_unknown_oauth_config_is_not_found(toolsets_client: ToolsetsClient) -> None:
    with toolset_store_lock():
        resp = toolsets_client.delete_oauth_config(TOOLSET_TYPE, MISSING_OAUTH_CONFIG_ID)
    assert_not_found(resp, f"OAuth configuration '{MISSING_OAUTH_CONFIG_ID}' not found.")
    assert_strict_openapi_exchange(resp, ROUTE)


def test_delete_without_token_is_unauthorized(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.delete_oauth_config(TOOLSET_TYPE, MISSING_OAUTH_CONFIG_ID, auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_delete_with_unsafe_oauth_config_id_is_refused_before_auth(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.delete_oauth_config(TOOLSET_TYPE, UNSAFE_PATH_ID, auth=False)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_cannot_delete_an_existing_oauth_config(
    toolsets_client: ToolsetsClient,
    second_user: SecondUser,
    seed_toolset_instance: SeedToolsetInstance,
) -> None:
    oauth_config_id = seed_toolset_instance(oauth=True)["oauthConfigId"]

    resp = request_as(second_user, "DELETE", f"/oauth-configs/{TOOLSET_TYPE}/{oauth_config_id}")
    assert_forbidden(resp, "Only administrators can delete OAuth configurations.")
    assert_strict_openapi_exchange(resp, ROUTE)

    # Still referenced by its instance, so the admin's own attempt proves it was kept.
    with toolset_store_lock():
        kept = toolsets_client.delete_oauth_config(TOOLSET_TYPE, oauth_config_id)
    assert kept.status_code == 409, kept.text[:500]
