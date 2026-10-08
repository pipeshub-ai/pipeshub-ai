"""Strict OpenAPI audit of GET /api/v1/toolsets/oauth-configs/:toolsetType."""

from __future__ import annotations

import pytest
from strict_openapi import (
    assert_strict_openapi_exchange,
    assert_strict_openapi_response,
    outside_request_contract,
)
from toolsets_audit_support import (
    MISSING_TOOLSET_TYPE,
    NO_OAUTH_CONFIGS,
    OAUTH_CLIENT_AUTH,
    TOOLSET_TYPE,
    UNSAFE_PATH_ID,
    JsonObject,
    SeedToolsetInstance,
    ToolsetsClient,
    request_as,
)

from helper.second_user import SecondUser

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/toolsets/oauth-configs/:toolsetType"
SECRET_FIELDS = {"clientSecret", "clientSecretSet"}


def _seeded_entry(body: JsonObject, oauth_config_id: str) -> JsonObject:
    assert body["status"] == "success", body
    configs = body["oauthConfigs"]
    assert body["total"] == len(configs), body
    matches = [cfg for cfg in configs if cfg.get("_id") == oauth_config_id]
    assert len(matches) == 1, f"seeded OAuth config {oauth_config_id} listed {len(matches)} times"
    return matches[0]


def test_admin_lists_oauth_configs_with_the_client_secret_masked(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    instance = seed_toolset_instance(oauth=True)

    # Python lower-cases the type for the store path, so an upper-case type lists the same configs.
    resp = toolsets_client.list_oauth_configs(TOOLSET_TYPE.upper())
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    entry = _seeded_entry(resp.json(), instance["oauthConfigId"])
    assert "config" not in entry, sorted(entry)
    assert entry["toolsetType"] == TOOLSET_TYPE, entry
    assert entry["oauthInstanceName"] == instance["instanceName"], entry
    assert entry["inherited"] is False, entry
    assert entry["clientId"] == OAUTH_CLIENT_AUTH["clientId"], sorted(entry)
    assert entry["clientSecretSet"] is True, sorted(entry)
    assert entry["clientSecret"] != OAUTH_CLIENT_AUTH["clientSecret"], "client secret returned in clear"


def test_member_lists_oauth_configs_without_any_client_credentials(
    second_user: SecondUser, seed_toolset_instance: SeedToolsetInstance
) -> None:
    instance = seed_toolset_instance(oauth=True)

    resp = request_as(second_user, "GET", f"/oauth-configs/{TOOLSET_TYPE}")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    entry = _seeded_entry(resp.json(), instance["oauthConfigId"])
    assert not ({"config", "clientId", "redirectUri"} | SECRET_FIELDS) & set(entry), sorted(entry)
    assert OAUTH_CLIENT_AUTH["clientSecret"] not in resp.text


def test_unknown_toolset_type_is_an_empty_list_not_a_404(toolsets_client: ToolsetsClient) -> None:
    # The handler never consults the registry; it only reads the store path for that type.
    resp = toolsets_client.list_oauth_configs(MISSING_TOOLSET_TYPE)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == NO_OAUTH_CONFIGS, resp.text[:500]


def test_list_oauth_configs_does_not_read_the_query_string(toolsets_client: ToolsetsClient) -> None:
    with outside_request_contract("the route reads no query parameter and forwards none"):
        resp = toolsets_client.get(f"/oauth-configs/{MISSING_TOOLSET_TYPE}", params={"specAuditUnknown": "1"})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == NO_OAUTH_CONFIGS, resp.text[:500]


def test_list_oauth_configs_rejects_a_call_without_a_token(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.list_oauth_configs(TOOLSET_TYPE, auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_list_oauth_configs_refuses_an_unsafe_toolset_type_before_auth(
    toolsets_client: ToolsetsClient,
) -> None:
    resp = toolsets_client.list_oauth_configs(UNSAFE_PATH_ID, auth=False)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
