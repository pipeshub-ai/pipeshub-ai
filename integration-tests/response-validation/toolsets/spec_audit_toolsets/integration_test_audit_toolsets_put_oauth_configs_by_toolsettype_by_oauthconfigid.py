"""Strict OpenAPI audit of PUT /api/v1/toolsets/oauth-configs/:toolsetType/:oauthConfigId.

Node has no validator here and forwards the body as is; Python reads `authConfig` and
`baseUrl` from it without a schema.
"""

from __future__ import annotations

from typing import Any

import pytest
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    assert_strict_openapi_response,
    outside_request_contract,
)
from toolsets_audit_support import (
    MISSING_OAUTH_CONFIG_ID,
    OAUTH_CLIENT_AUTH,
    OAUTH_ONLY_LOCAL_TOOLSET_TYPE,
    TOOLSET_TYPE,
    UNSAFE_PATH_ID,
    JsonObject,
    SeedToolsetInstance,
    ToolsetsClient,
    assert_backend_failure,
    assert_forbidden,
    assert_not_found,
    error_of,
    request_as,
    toolset_store_lock,
)

from helper.second_user import SecondUser

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/toolsets/oauth-configs/:toolsetType/:oauthConfigId"


def _path(oauth_config_id: str, toolset_type: str = TOOLSET_TYPE) -> str:
    return f"/oauth-configs/{toolset_type}/{oauth_config_id}"


def _update(client: ToolsetsClient, oauth_config_id: str, body: Any = None) -> Any:
    with toolset_store_lock():
        if body is None:
            return client.put(_path(oauth_config_id))
        return client.put(_path(oauth_config_id), json=body)


def _stored(client: ToolsetsClient, oauth_config_id: str) -> JsonObject:
    listed = client.list_oauth_configs(TOOLSET_TYPE)
    assert listed.status_code == 200, listed.text[:500]
    config: JsonObject = next(c for c in listed.json()["oauthConfigs"] if c["_id"] == oauth_config_id)
    return config


def _assert_updated(resp: Any, oauth_config_id: str, deauthenticated: int = 0) -> None:
    assert resp.status_code == 200, resp.text[:500]
    message = "OAuth configuration updated."
    if deauthenticated:
        message += (
            f" {deauthenticated} user(s) across 1 instance(s) have been deauthenticated and must re-authenticate."
        )
    assert resp.json() == {
        "status": "success",
        "oauthConfigId": oauth_config_id,
        "message": message,
        "deauthenticatedUserCount": deauthenticated,
    }, resp.text[:500]


def test_update_oauth_config_rewrites_a_seeded_config(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    oauth_config_id = seed_toolset_instance(oauth=True)["oauthConfigId"]

    resp = _update(
        toolsets_client,
        oauth_config_id,
        {"authConfig": {**OAUTH_CLIENT_AUTH, "clientId": "spec-audit-client-id-2"}, "baseUrl": "https://other.invalid"},
    )
    _assert_updated(resp, oauth_config_id)
    assert_strict_openapi_exchange(resp, ROUTE)
    stored = _stored(toolsets_client, oauth_config_id)
    assert stored["clientId"] == "spec-audit-client-id-2"
    assert stored["redirectUri"].startswith("https://other.invalid/")


def test_update_oauth_config_signs_out_every_user_of_its_instances(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    instance = seed_toolset_instance(oauth=True)
    started = toolsets_client.get(f"/instances/{instance['_id']}/oauth/authorize")
    assert started.status_code == 200, started.text[:500]

    resp = _update(toolsets_client, instance["oauthConfigId"], {"authConfig": dict(OAUTH_CLIENT_AUTH)})
    _assert_updated(resp, instance["oauthConfigId"], deauthenticated=1)
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("body", [None, {}, {"authConfig": {"clientSecret": ""}}], ids=["no-body", "empty", "empty-secret"])
def test_update_oauth_config_keeps_the_client_credentials_it_is_not_given(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance, body: JsonObject | None
) -> None:
    oauth_config_id = seed_toolset_instance(oauth=True)["oauthConfigId"]

    resp = _update(toolsets_client, oauth_config_id, body)
    _assert_updated(resp, oauth_config_id)
    assert_strict_openapi_exchange(resp, ROUTE)
    stored = _stored(toolsets_client, oauth_config_id)
    assert (stored["clientId"], stored["clientSecretSet"]) == (OAUTH_CLIENT_AUTH["clientId"], True)


def test_update_oauth_config_ignores_unknown_fields(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    oauth_config_id = seed_toolset_instance(oauth=True)["oauthConfigId"]

    with outside_request_contract("only authConfig and baseUrl are read from the body"):
        resp = _update(toolsets_client, oauth_config_id, {"specAuditUnknown": 1})
    _assert_updated(resp, oauth_config_id)
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param([], id="body-is-a-list"),
        pytest.param({"authConfig": "spec-audit"}, id="authConfig-not-object"),
        pytest.param({"authConfig": None}, id="authConfig-null"),
        pytest.param({"baseUrl": 5}, id="baseUrl-not-text"),
    ],
)
def test_update_oauth_config_crashes_on_a_field_of_the_wrong_type(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance, body: Any
) -> None:
    # API bug: no validator, and Python calls string and dict methods on whatever it receives.
    oauth_config_id = seed_toolset_instance(oauth=True)["oauthConfigId"]

    resp = _update(toolsets_client, oauth_config_id, body)
    assert_backend_failure(resp)
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


def test_update_oauth_config_refuses_a_salesforce_login_url_outside_salesforce(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    oauth_config_id = seed_toolset_instance(oauth=True, toolsetType="salesforce")["oauthConfigId"]

    with toolset_store_lock():
        resp = toolsets_client.put(
            _path(oauth_config_id, "salesforce"), json={"authConfig": {"loginUrl": "http://spec-audit.invalid"}}
        )
    assert resp.status_code == 400, resp.text[:500]
    error = error_of(resp)
    assert error["code"] == "HTTP_BAD_REQUEST", resp.text[:500]
    assert error["message"].startswith(
        "Invalid authentication configuration: The Salesforce Login URL must be an https address"
    ), resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_oauth_config_under_another_toolset_type_is_not_found(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    oauth_config_id = seed_toolset_instance(oauth=True)["oauthConfigId"]

    with toolset_store_lock():
        resp = toolsets_client.put(_path(oauth_config_id, OAUTH_ONLY_LOCAL_TOOLSET_TYPE), json={})
    assert_not_found(resp, f"OAuth configuration '{oauth_config_id}' not found.")
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_oauth_config_with_an_unknown_id_is_not_found(toolsets_client: ToolsetsClient) -> None:
    resp = _update(toolsets_client, MISSING_OAUTH_CONFIG_ID, {"authConfig": dict(OAUTH_CLIENT_AUTH)})
    assert_not_found(resp, f"OAuth configuration '{MISSING_OAUTH_CONFIG_ID}' not found.")
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_oauth_config_is_forbidden_for_a_member(second_user: SecondUser) -> None:
    # Python checks admin before it looks the config up, so no seeded config is needed.
    resp = request_as(second_user, "PUT", _path(MISSING_OAUTH_CONFIG_ID), json={"authConfig": dict(OAUTH_CLIENT_AUTH)})
    assert_forbidden(resp, "Only administrators can update OAuth configurations.")
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_oauth_config_rejects_a_call_without_a_token(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.put(_path(MISSING_OAUTH_CONFIG_ID), auth=False, json={})
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_oauth_config_refuses_an_unsafe_config_id_before_auth(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.put(_path(UNSAFE_PATH_ID), auth=False, json={})
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
