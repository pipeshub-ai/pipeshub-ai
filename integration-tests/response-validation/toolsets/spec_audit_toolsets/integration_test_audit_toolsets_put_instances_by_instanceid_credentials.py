"""Strict OpenAPI audit of PUT /api/v1/toolsets/instances/:instanceId/credentials.

Node's validator requires `auth` and keeps only email/apiToken/username/password inside
it (and nothing else at the top level); Python then replaces the caller's saved `auth`
with what is left.
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
    API_TOKEN_AUTH,
    CREDENTIALS_REQUIRED,
    INSTANCE_NOT_FOUND,
    MISSING_INSTANCE_ID,
    NO_SAVED_CREDENTIALS,
    OAUTH_INSTANCE_CREDENTIALS_REFUSAL,
    UNSAFE_PATH_ID,
    JsonObject,
    SeedToolsetInstance,
    ToolsetsClient,
    assert_bad_request,
    assert_not_found,
    rejected_fields,
    request_as,
)

from helper.second_user import SecondUser

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/toolsets/instances/:instanceId/credentials"
VALID_BODY: JsonObject = {
    "auth": {"email": "spec-audit-updated@example.com", "apiToken": "spec-audit-token-2"}
}
UPDATED: JsonObject = {"status": "success", "message": "Credentials updated successfully."}


def _credentials_path(instance_id: str) -> str:
    return f"/instances/{instance_id}/credentials"


def _authenticate(client: ToolsetsClient, instance_id: str) -> None:
    saved = client.post(f"/instances/{instance_id}/authenticate", json={"auth": dict(API_TOKEN_AUTH)})
    assert saved.status_code == 200, saved.text[:500]


def _saved_auth(client: ToolsetsClient, instance: JsonObject) -> Any:
    listed = client.get("/my-toolsets", params={"search": instance["instanceName"]})
    assert listed.status_code == 200, listed.text[:500]
    return next(t for t in listed.json()["toolsets"] if t["instanceId"] == instance["_id"])["auth"]


def test_update_credentials_replaces_the_callers_saved_credentials(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    instance = seed_toolset_instance()
    # The route only updates: the caller's credentials must already be saved.
    _authenticate(toolsets_client, instance["_id"])

    resp = toolsets_client.put(_credentials_path(instance["_id"]), json=VALID_BODY)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == UPDATED
    # Replaced, not merged: the baseUrl saved by /authenticate is gone.
    assert _saved_auth(toolsets_client, instance) == VALID_BODY["auth"]


def test_update_credentials_drops_fields_the_validator_does_not_list(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    # API bug: baseUrl is one of Jira's credential fields, yet this route can never keep it.
    instance = seed_toolset_instance()
    _authenticate(toolsets_client, instance["_id"])

    with outside_request_contract("the validator drops auth.baseUrl and unknown top-level fields"):
        resp = toolsets_client.put(
            _credentials_path(instance["_id"]),
            json={"auth": dict(API_TOKEN_AUTH), "specAuditUnknown": 1},
        )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert _saved_auth(toolsets_client, instance) == {
        k: v for k, v in API_TOKEN_AUTH.items() if k != "baseUrl"
    }


def test_member_updates_only_their_own_credentials(
    toolsets_client: ToolsetsClient,
    second_user: SecondUser,
    seed_toolset_instance: SeedToolsetInstance,
) -> None:
    instance = seed_toolset_instance()
    _authenticate(toolsets_client, instance["_id"])

    # The admin's saved credentials do not count for the member.
    resp = request_as(second_user, "PUT", _credentials_path(instance["_id"]), json=VALID_BODY)
    assert_not_found(resp, NO_SAVED_CREDENTIALS)
    assert_strict_openapi_exchange(resp, ROUTE)

    saved = request_as(
        second_user, "POST", f"/instances/{instance['_id']}/authenticate", json={"auth": dict(API_TOKEN_AUTH)}
    )
    assert saved.status_code == 200, saved.text[:500]
    resp = request_as(second_user, "PUT", _credentials_path(instance["_id"]), json=VALID_BODY)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _saved_auth(toolsets_client, instance) == API_TOKEN_AUTH


def test_update_credentials_is_not_found_before_the_caller_authenticated(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    instance_id = seed_toolset_instance()["_id"]
    resp = toolsets_client.put(_credentials_path(instance_id), json=VALID_BODY)
    assert_not_found(resp, NO_SAVED_CREDENTIALS)
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_credentials_of_an_unknown_instance_is_not_found(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.put(_credentials_path(MISSING_INSTANCE_ID), json=VALID_BODY)
    assert_not_found(resp, INSTANCE_NOT_FOUND)
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_credentials_refuses_an_oauth_instance(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    instance_id = seed_toolset_instance(oauth=True)["_id"]
    resp = toolsets_client.put(_credentials_path(instance_id), json=VALID_BODY)
    assert_bad_request(resp, OAUTH_INSTANCE_CREDENTIALS_REFUSAL)
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("body", "fields"),
    [
        pytest.param(None, {"body.auth"}, id="no-body"),
        pytest.param({"apiToken": "spec-audit-token-2"}, {"body.auth"}, id="auth-missing"),
        pytest.param({"auth": "spec-audit"}, {"body.auth"}, id="auth-text"),
        pytest.param({"auth": ["spec-audit"]}, {"body.auth"}, id="auth-list"),
        pytest.param({"auth": {"email": 5}}, {"body.auth.email"}, id="email-not-text"),
        pytest.param({"auth": {"apiToken": 5}}, {"body.auth.apiToken"}, id="apiToken-not-text"),
        pytest.param({"auth": {"username": 5}}, {"body.auth.username"}, id="username-not-text"),
        pytest.param({"auth": {"password": True}}, {"body.auth.password"}, id="password-not-text"),
    ],
)
def test_update_credentials_rejects_a_body_that_fails_validation(
    toolsets_client: ToolsetsClient, body: JsonObject | None, fields: set[str]
) -> None:
    resp = toolsets_client.put(_credentials_path(MISSING_INSTANCE_ID), json=body)
    assert resp.status_code == 400, resp.text[:500]
    assert rejected_fields(resp) == fields
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_credentials_refuses_an_empty_auth_before_the_instance_is_looked_up(
    toolsets_client: ToolsetsClient,
) -> None:
    resp = toolsets_client.put(_credentials_path(MISSING_INSTANCE_ID), json={"auth": {}})
    assert_bad_request(resp, CREDENTIALS_REQUIRED)
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


def test_update_credentials_with_only_dropped_fields_is_an_empty_auth(
    toolsets_client: ToolsetsClient,
) -> None:
    with outside_request_contract("the validator drops auth.baseUrl, leaving an empty auth"):
        resp = toolsets_client.put(
            _credentials_path(MISSING_INSTANCE_ID), json={"auth": {"baseUrl": "https://spec-audit.invalid"}}
        )
    assert_bad_request(resp, CREDENTIALS_REQUIRED)
    assert_strict_openapi_response(resp, ROUTE)


def test_update_credentials_refuses_a_field_the_toolset_does_not_declare(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    # Jira's API_TOKEN form has no username; which fields are allowed depends on the toolset.
    instance_id = seed_toolset_instance()["_id"]
    resp = toolsets_client.put(_credentials_path(instance_id), json={"auth": {"username": "spec-audit"}})
    assert_bad_request(resp, "Unexpected credential fields for this toolset: username")
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_credentials_rejects_a_call_without_a_token(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.put(_credentials_path(MISSING_INSTANCE_ID), auth=False, json=VALID_BODY)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_credentials_refuses_an_unsafe_instance_id_before_auth(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.put(_credentials_path(UNSAFE_PATH_ID), auth=False, json=VALID_BODY)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
