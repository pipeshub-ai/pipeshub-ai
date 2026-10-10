"""Strict OpenAPI audit of POST /api/v1/toolsets/instances/:instanceId/authenticate.

Node has no validator here; Python reads the body by hand, after it has looked the
instance up.
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
    UNSAFE_PATH_ID,
    JsonObject,
    SeedToolsetInstance,
    ToolsetsClient,
    assert_backend_failure,
    assert_bad_request,
    assert_not_found,
    request_as,
)

from helper.second_user import SecondUser

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/toolsets/instances/:instanceId/authenticate"

VALID_BODY: JsonObject = {"auth": dict(API_TOKEN_AUTH)}
AUTHENTICATED: JsonObject = {
    "status": "success",
    "message": "Toolset authenticated successfully.",
    "isAuthenticated": True,
}
API_TOKEN_MISSING = "Invalid authentication configuration: apiToken is required for API_TOKEN auth type"


def _path(instance_id: str) -> str:
    return f"/instances/{instance_id}/authenticate"


def _saved_auth(client: ToolsetsClient, instance: JsonObject) -> Any:
    listed = client.get("/my-toolsets", params={"search": instance["instanceName"]})
    assert listed.status_code == 200, listed.text[:500]
    return next(t for t in listed.json()["toolsets"] if t["instanceId"] == instance["_id"])["auth"]


def test_authenticate_stores_credentials_for_admin_and_member(
    toolsets_client: ToolsetsClient,
    second_user: SecondUser,
    seed_toolset_instance: SeedToolsetInstance,
) -> None:
    # Credentials are stored per user and removed when the seeded instance is deleted.
    instance = seed_toolset_instance()

    resp = toolsets_client.post(_path(instance["_id"]), json=VALID_BODY)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == AUTHENTICATED
    assert _saved_auth(toolsets_client, instance) == API_TOKEN_AUTH

    # No admin gate: any org member may save their own credentials.
    as_member = request_as(second_user, "POST", _path(instance["_id"]), json=VALID_BODY)
    assert as_member.status_code == 200, as_member.text[:500]
    assert_strict_openapi_exchange(as_member, ROUTE)
    assert as_member.json() == AUTHENTICATED


def test_authenticate_again_replaces_the_saved_credentials(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    instance = seed_toolset_instance()
    first = toolsets_client.post(_path(instance["_id"]), json=VALID_BODY)
    assert first.status_code == 200, first.text[:500]

    resp = toolsets_client.post(_path(instance["_id"]), json={"auth": {"apiToken": "spec-audit-token-2"}})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _saved_auth(toolsets_client, instance) == {"apiToken": "spec-audit-token-2"}


def test_authenticate_ignores_unknown_top_level_fields(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    instance = seed_toolset_instance()

    with outside_request_contract("only `auth` is read from the body"):
        resp = toolsets_client.post(_path(instance["_id"]), json={**VALID_BODY, "specAuditUnknown": 1})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        pytest.param(None, CREDENTIALS_REQUIRED, id="no-body"),
        pytest.param({}, CREDENTIALS_REQUIRED, id="no-auth"),
        pytest.param({"auth": {}}, CREDENTIALS_REQUIRED, id="auth-empty"),
        pytest.param({"auth": "spec-audit"}, "auth must be an object.", id="auth-text"),
        pytest.param({"auth": ["spec-audit"]}, "auth must be an object.", id="auth-list"),
    ],
)
def test_authenticate_refuses_a_body_without_credentials(
    toolsets_client: ToolsetsClient,
    seed_toolset_instance: SeedToolsetInstance,
    body: JsonObject | None,
    message: str,
) -> None:
    instance_id = seed_toolset_instance()["_id"]

    resp = toolsets_client.post(_path(instance_id), json=body)
    assert_bad_request(resp, message)
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


@pytest.mark.parametrize(
    ("auth", "message"),
    [
        pytest.param({"email": "spec-audit@example.com"}, API_TOKEN_MISSING, id="apiToken-missing"),
        pytest.param({"apiToken": "   "}, API_TOKEN_MISSING, id="apiToken-blank"),
        pytest.param({"apiToken": 5}, API_TOKEN_MISSING, id="apiToken-not-text"),
        pytest.param(
            {**API_TOKEN_AUTH, "specAuditUndeclared": "x"},
            "Unexpected credential fields for this toolset: specAuditUndeclared",
            id="undeclared-field",
        ),
    ],
)
def test_authenticate_refuses_credentials_the_toolset_does_not_accept(
    toolsets_client: ToolsetsClient,
    seed_toolset_instance: SeedToolsetInstance,
    auth: JsonObject,
    message: str,
) -> None:
    # Which fields are allowed and required depends on the instance's toolset and auth
    # type, so the request schema cannot express these refusals.
    instance_id = seed_toolset_instance()["_id"]

    resp = toolsets_client.post(_path(instance_id), json={"auth": auth})
    assert_bad_request(resp, message)
    assert_strict_openapi_exchange(resp, ROUTE)


def test_authenticate_crashes_on_a_body_that_is_not_an_object(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    # API bug: a JSON list reaches Python, which calls .get() on it.
    instance_id = seed_toolset_instance()["_id"]

    resp = toolsets_client.post(_path(instance_id), json=[VALID_BODY])
    assert_backend_failure(resp)
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


def test_authenticate_oauth_instance_is_refused(
    toolsets_client: ToolsetsClient,
    seed_toolset_instance: SeedToolsetInstance,
) -> None:
    instance_id = seed_toolset_instance(oauth=True)["_id"]

    resp = toolsets_client.post(_path(instance_id), json=VALID_BODY)
    assert_bad_request(
        resp,
        "For OAuth toolsets, use the /instances/{instance_id}/oauth/authorize endpoint to authenticate.",
    )
    assert_strict_openapi_exchange(resp, ROUTE)


def test_authenticate_unknown_instance_is_not_found_before_the_body_is_read(
    toolsets_client: ToolsetsClient,
) -> None:
    resp = toolsets_client.post(_path(MISSING_INSTANCE_ID), json=VALID_BODY)
    assert_not_found(resp, INSTANCE_NOT_FOUND)
    assert_strict_openapi_exchange(resp, ROUTE)

    with outside_request_contract("the instance is looked up before the body is read"):
        empty = toolsets_client.post(_path(MISSING_INSTANCE_ID), json={})
    assert_not_found(empty, INSTANCE_NOT_FOUND)
    assert_strict_openapi_response(empty, ROUTE)


def test_authenticate_without_token_is_unauthorized(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.post(_path(MISSING_INSTANCE_ID), auth=False, json=VALID_BODY)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_authenticate_refuses_an_unsafe_instance_id_before_auth(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.post(_path(UNSAFE_PATH_ID), auth=False, json=VALID_BODY)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
