"""Strict OpenAPI audit of PUT /api/v1/toolsets/instances/:instanceId.

Node has no validator here and forwards the body as is; Python reads it field by field
without a schema, so a field of the wrong type either is ignored or crashes the handler.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    assert_strict_openapi_response,
    outside_request_contract,
)
from toolsets_audit_support import (
    INSTANCE_NOT_FOUND,
    MISSING_INSTANCE_ID,
    MISSING_OAUTH_CONFIG_ID,
    OAUTH_CLIENT_AUTH,
    TOOLSET_TYPE,
    UNSAFE_PATH_ID,
    JsonObject,
    SeedToolsetInstance,
    ToolsetsClient,
    assert_backend_failure,
    assert_bad_request,
    assert_forbidden,
    assert_not_found,
    error_of,
    request_as,
    toolset_store_lock,
)

from helper.second_user import SecondUser

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/toolsets/instances/:instanceId"
MASK = "•" * 8
UPDATED = "Toolset instance updated successfully."


def _rename_body() -> JsonObject:
    return {"instanceName": f"spec-audit-renamed-{uuid.uuid4().hex[:8]}"}


def _update(client: ToolsetsClient, instance_id: str, body: Any = None) -> Any:
    with toolset_store_lock():
        if body is None:
            return client.put(f"/instances/{instance_id}")
        return client.put(f"/instances/{instance_id}", json=body)


def _updated(resp: Any, deauthenticated: int = 0) -> JsonObject:
    assert resp.status_code == 200, resp.text[:500]
    body = resp.json()
    assert body["status"] == "success", body
    assert body["deauthenticatedUserCount"] == deauthenticated, body
    if deauthenticated:
        assert body["message"] == (
            f"{UPDATED} {deauthenticated} user(s) have been deauthenticated and must re-authenticate."
        ), body
    else:
        assert body["message"] == UPDATED, body
    instance: JsonObject = body["instance"]
    return instance


def test_update_instance_renames_a_seeded_instance(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    seeded = seed_toolset_instance()
    body = _rename_body()

    resp = _update(toolsets_client, seeded["_id"], body)
    instance = _updated(resp)
    assert_strict_openapi_exchange(resp, ROUTE)
    assert instance["instanceName"] == body["instanceName"]
    assert instance["updatedAtTimestamp"] >= seeded["updatedAtTimestamp"]
    assert {k: v for k, v in instance.items() if k not in {"instanceName", "updatedAtTimestamp"}} == {
        k: v for k, v in seeded.items() if k not in {"instanceName", "updatedAtTimestamp"}
    }


@pytest.mark.parametrize("body", [None, {}], ids=["no-body", "empty-object"])
def test_update_instance_without_changes_only_touches_the_timestamp(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance, body: JsonObject | None
) -> None:
    seeded = seed_toolset_instance()

    resp = _update(toolsets_client, seeded["_id"], body)
    instance = _updated(resp)
    assert_strict_openapi_exchange(resp, ROUTE)
    assert instance["instanceName"] == seeded["instanceName"]


def test_update_instance_ignores_a_rename_that_only_changes_letter_case(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    # API bug: the new name is compared case-insensitively with the old one, so it is dropped.
    seeded = seed_toolset_instance()

    resp = _update(toolsets_client, seeded["_id"], {"instanceName": seeded["instanceName"].upper()})
    instance = _updated(resp)
    assert_strict_openapi_exchange(resp, ROUTE)
    assert instance["instanceName"] == seeded["instanceName"]


def test_update_instance_replaces_the_inline_credentials_of_a_non_oauth_instance(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    seeded = seed_toolset_instance(authConfig={"apiToken": "spec-audit-old-token", "email": "old@example.com"})

    resp = _update(
        toolsets_client,
        seeded["_id"],
        {"authConfig": {"apiToken": "spec-audit-new-token", "extra": {"nested": True}}},
    )
    instance = _updated(resp)
    assert_strict_openapi_exchange(resp, ROUTE)
    # Replaced whole, not merged; nested objects come back masked.
    assert instance["auth"] == {"apiToken": "spec-audit-new-token", "extra": MASK}


def test_update_instance_with_a_null_auth_config_clears_the_inline_credentials(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    seeded = seed_toolset_instance(authConfig={"apiToken": "spec-audit-old-token"})

    with outside_request_contract("Python reads a null authConfig as an empty one"):
        resp = _update(toolsets_client, seeded["_id"], {"authConfig": None})
    instance = _updated(resp)
    assert_strict_openapi_response(resp, ROUTE)
    assert "auth" not in instance, instance


def test_update_instance_ignores_fields_only_oauth_instances_read(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    seeded = seed_toolset_instance()

    with outside_request_contract("baseUrl and oauthConfigId are not read at all for a non-OAuth instance"):
        resp = _update(toolsets_client, seeded["_id"], {"baseUrl": 5, "oauthConfigId": 5, "specAuditUnknown": 1})
    instance = _updated(resp)
    assert_strict_openapi_response(resp, ROUTE)
    assert "oauthConfigId" not in instance


def test_update_oauth_instance_credentials_signs_out_every_user_of_the_instance(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    seeded = seed_toolset_instance(oauth=True)
    # Starting the flow stores a record under the caller's key; that is what gets counted.
    started = toolsets_client.get(f"/instances/{seeded['_id']}/oauth/authorize")
    assert started.status_code == 200, started.text[:500]

    resp = _update(
        toolsets_client,
        seeded["_id"],
        {"authConfig": {**OAUTH_CLIENT_AUTH, "clientId": "spec-audit-client-id-2"}, "baseUrl": "https://spec-audit.invalid"},
    )
    instance = _updated(resp, deauthenticated=1)
    assert_strict_openapi_exchange(resp, ROUTE)
    assert instance["oauthConfigId"] == seeded["oauthConfigId"]

    configs = toolsets_client.list_oauth_configs(TOOLSET_TYPE)
    assert configs.status_code == 200, configs.text[:500]
    config = next(c for c in configs.json()["oauthConfigs"] if c["_id"] == seeded["oauthConfigId"])
    assert config["clientId"] == "spec-audit-client-id-2"
    assert config["redirectUri"].startswith("https://spec-audit.invalid/")


def test_update_oauth_instance_links_another_existing_oauth_config(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    seeded = seed_toolset_instance(oauth=True)
    other = seed_toolset_instance(oauth=True)

    resp = _update(toolsets_client, seeded["_id"], {"oauthConfigId": other["oauthConfigId"]})
    instance = _updated(resp)
    assert_strict_openapi_exchange(resp, ROUTE)
    assert instance["oauthConfigId"] == other["oauthConfigId"]


def test_update_oauth_instance_to_an_unknown_oauth_config_is_not_found(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    seeded = seed_toolset_instance(oauth=True)

    resp = _update(toolsets_client, seeded["_id"], {"oauthConfigId": MISSING_OAUTH_CONFIG_ID})
    assert_not_found(
        resp, f"OAuth configuration '{MISSING_OAUTH_CONFIG_ID}' not found for toolset '{TOOLSET_TYPE}'."
    )
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_instance_refuses_an_auth_config_that_is_not_an_object(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    seeded = seed_toolset_instance()

    resp = _update(toolsets_client, seeded["_id"], {"authConfig": "spec-audit"})
    assert_bad_request(resp, "authConfig must be an object")
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


@pytest.mark.parametrize(
    ("oauth", "body"),
    [
        pytest.param(False, [], id="body-is-a-list"),
        pytest.param(False, {"instanceName": 5}, id="instanceName-not-text"),
        pytest.param(True, {"baseUrl": 5}, id="oauth-baseUrl-not-text"),
        pytest.param(True, {"oauthConfigId": 5}, id="oauth-oauthConfigId-not-text"),
        pytest.param(True, {"authConfig": "spec-audit"}, id="oauth-authConfig-not-object"),
    ],
)
def test_update_instance_crashes_on_a_field_of_the_wrong_type(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance, oauth: bool, body: Any
) -> None:
    # API bug: no validator, and Python calls string methods on whatever it receives.
    seeded = seed_toolset_instance(oauth=oauth)

    resp = _update(toolsets_client, seeded["_id"], body)
    assert_backend_failure(resp)
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


@pytest.mark.parametrize("change_case", [False, True], ids=["same-case", "other-case"])
def test_update_instance_refuses_a_name_another_instance_already_uses(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance, change_case: bool
) -> None:
    taken = seed_toolset_instance()
    instance = seed_toolset_instance()
    name = taken["instanceName"].upper() if change_case else taken["instanceName"]

    resp = _update(toolsets_client, instance["_id"], {"instanceName": name})
    assert resp.status_code == 409, resp.text[:500]
    assert error_of(resp)["message"] == (
        f"A toolset instance named '{name}' already exists for toolset type '{TOOLSET_TYPE}'."
    )
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_instance_with_an_unknown_id_is_not_found(toolsets_client: ToolsetsClient) -> None:
    resp = _update(toolsets_client, MISSING_INSTANCE_ID, _rename_body())
    assert_not_found(resp, INSTANCE_NOT_FOUND)
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_cannot_update_an_instance(
    second_user: SecondUser, seed_toolset_instance: SeedToolsetInstance
) -> None:
    seeded = seed_toolset_instance()
    resp = request_as(second_user, "PUT", f"/instances/{seeded['_id']}", json=_rename_body())
    assert_forbidden(resp, "Only administrators can update toolset instances.")
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_instance_rejects_a_call_without_a_token(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.update_instance(MISSING_INSTANCE_ID, _rename_body(), auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_instance_refuses_an_unsafe_instance_id_before_auth(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.update_instance(UNSAFE_PATH_ID, _rename_body(), auth=False)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
