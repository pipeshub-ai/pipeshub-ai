"""Strict OpenAPI audit of GET /api/v1/toolsets/instances/:instanceId."""

from __future__ import annotations

import pytest
from strict_openapi import (
    assert_strict_openapi_exchange,
    assert_strict_openapi_response,
    outside_request_contract,
)
from toolsets_audit_support import (
    INSTANCE_NOT_FOUND,
    MISSING_INSTANCE_ID,
    OAUTH_CLIENT_AUTH,
    TOOLSET_TYPE,
    UNSAFE_PATH_ID,
    SeedToolsetInstance,
    ToolsetsClient,
    assert_not_found,
    request_as,
)

from helper.second_user import SecondUser

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/toolsets/instances/:instanceId"
MASK = "\u2022" * 8
REGISTRY_FIELDS = {"displayName", "description", "iconPath", "supportedAuthTypes", "toolCount"}
ADMIN_ONLY_FIELDS = {"auth", "oauthConfig", "authenticatedUserCount"}


def test_admin_gets_an_oauth_instance_with_its_oauth_config_and_user_count(
    toolsets_client: ToolsetsClient,
    seed_toolset_instance: SeedToolsetInstance,
) -> None:
    seeded = seed_toolset_instance(oauth=True)

    resp = toolsets_client.get_instance(seeded["_id"])
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    body = resp.json()
    assert body["status"] == "success", body
    instance = body["instance"]
    assert set(instance) == set(seeded) | REGISTRY_FIELDS | {"oauthConfig", "authenticatedUserCount"}
    assert instance["_id"] == seeded["_id"], instance
    assert instance["toolsetType"] == TOOLSET_TYPE, instance
    assert instance["authType"] == "OAUTH", instance
    oauth_config = instance["oauthConfig"]
    assert oauth_config["_id"] == seeded["oauthConfigId"], oauth_config
    assert oauth_config["oauthInstanceName"] == seeded["instanceName"]
    assert oauth_config["clientId"] == OAUTH_CLIENT_AUTH["clientId"]
    assert (oauth_config["clientSecret"], oauth_config["clientSecretSet"]) == (MASK, True)
    assert instance["authenticatedUserCount"] == 0, instance


def test_user_count_includes_a_user_who_only_started_the_oauth_flow(
    toolsets_client: ToolsetsClient,
    seed_toolset_instance: SeedToolsetInstance,
) -> None:
    seeded = seed_toolset_instance(oauth=True)
    # Asking for the authorization URL stores the pending flow under the user's key.
    started = toolsets_client.get(f"/instances/{seeded['_id']}/oauth/authorize")
    assert started.status_code == 200, started.text[:500]

    resp = toolsets_client.get_instance(seeded["_id"])
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    # The count is of stored records, not of completed sign-ins.
    assert resp.json()["instance"]["authenticatedUserCount"] == 1

    status = toolsets_client.get(f"/instances/{seeded['_id']}/status")
    assert status.status_code == 200, status.text[:500]
    assert status.json()["isAuthenticated"] is False


def test_admin_gets_inline_credentials_masked_and_no_oauth_fields(
    toolsets_client: ToolsetsClient,
    seed_toolset_instance: SeedToolsetInstance,
) -> None:
    seeded = seed_toolset_instance(authConfig={"apiToken": "spec-audit-inline-token", "extra": {"nested": True}})

    resp = toolsets_client.get_instance(seeded["_id"])
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    instance = resp.json()["instance"]
    assert set(instance) == set(seeded) | REGISTRY_FIELDS, sorted(instance)
    # Only client secrets and nested objects are masked; the inline API token is not.
    assert instance["auth"] == {"apiToken": "spec-audit-inline-token", "extra": MASK}


def test_admin_gets_an_oauth_instance_without_a_config_with_its_placeholder(
    toolsets_client: ToolsetsClient,
    seed_toolset_instance: SeedToolsetInstance,
) -> None:
    seeded = seed_toolset_instance(authType="OAUTH")

    resp = toolsets_client.get_instance(seeded["_id"])
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    instance = resp.json()["instance"]
    assert "oauthConfig" not in instance
    assert instance["auth"] == {"type": "OAUTH", "clientId": "", "clientSecret": MASK}
    assert instance["authenticatedUserCount"] == 0


@pytest.mark.parametrize("oauth", [False, True], ids=["api-token-instance", "oauth-instance"])
def test_member_gets_an_instance_without_the_admin_only_fields(
    second_user: SecondUser,
    seed_toolset_instance: SeedToolsetInstance,
    oauth: bool,
) -> None:
    seeded = (
        seed_toolset_instance(oauth=True)
        if oauth
        else seed_toolset_instance(authConfig={"apiToken": "spec-audit-inline-token"})
    )

    resp = request_as(second_user, "GET", f"/instances/{seeded['_id']}")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    instance = resp.json()["instance"]
    assert instance["_id"] == seeded["_id"], instance
    assert REGISTRY_FIELDS <= set(instance), sorted(instance)
    assert not ADMIN_ONLY_FIELDS & set(instance), sorted(instance)


def test_get_instance_does_not_read_the_query_string(
    toolsets_client: ToolsetsClient,
    seed_toolset_instance: SeedToolsetInstance,
) -> None:
    seeded = seed_toolset_instance()
    plain = toolsets_client.get_instance(seeded["_id"])

    with outside_request_contract("the route reads no query parameter and forwards none"):
        resp = toolsets_client.get(f"/instances/{seeded['_id']}", params={"specAuditUnknown": "1"})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == plain.json()


def test_an_unknown_instance_id_is_not_found(
    toolsets_client: ToolsetsClient, second_user: SecondUser
) -> None:
    resp = toolsets_client.get_instance(MISSING_INSTANCE_ID)
    assert_not_found(resp, INSTANCE_NOT_FOUND)
    assert_strict_openapi_exchange(resp, ROUTE)

    as_member = request_as(second_user, "GET", f"/instances/{MISSING_INSTANCE_ID}")
    assert_not_found(as_member, INSTANCE_NOT_FOUND)
    assert_strict_openapi_exchange(as_member, ROUTE)


def test_get_instance_rejects_a_call_without_a_token(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.get_instance(MISSING_INSTANCE_ID, auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_get_instance_refuses_an_unsafe_instance_id_before_auth(
    toolsets_client: ToolsetsClient,
) -> None:
    resp = toolsets_client.get_instance(UNSAFE_PATH_ID, auth=False)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
