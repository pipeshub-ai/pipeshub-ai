"""Strict OpenAPI audit of PUT /api/v1/toolsets/:toolsetId/config.

Legacy route: Node validates the body and pastes the id into PUT
`/api/v1/toolsets/{id}/config` on the connector service, which has no such route, so
a valid call with an ordinary id ends in the relayed routing 404. The id
`instances` makes it Python's instance update for an instance called `config`; for
`oauth-configs` and `agents` the URL matches a route without a PUT handler, whose 405
comes back as a 500.
"""

from __future__ import annotations

from typing import Any

import pytest
from strict_openapi import (
    assert_strict_openapi_exchange,
    assert_strict_openapi_response,
    outside_request_contract,
)
from toolsets_audit_support import (
    AGENTS_SEGMENT,
    INSTANCE_NOT_FOUND,
    INSTANCES_SEGMENT,
    MISSING_TOOLSET_ID,
    NO_BACKEND_ROUTE,
    OAUTH_CONFIGS_SEGMENT,
    UNSAFE_PATH_ID,
    JsonObject,
    SeedToolsetInstance,
    ToolsetsClient,
    assert_backend_failure,
    assert_not_found,
    error_of,
    rejected_fields,
    request_as,
)

from helper.second_user import SecondUser

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/toolsets/:toolsetId/config"

MINIMAL_BODY: JsonObject = {"auth": {"type": "API_TOKEN"}}
FULL_BODY: JsonObject = {
    "auth": {
        "type": "OAUTH",
        "clientId": "spec-audit-client-id",
        "clientSecret": "spec-audit-client-secret",
        "apiToken": "spec-audit-token",
        "oauthAppId": "spec-audit-oauth-app",
        "scopes": ["read:jira-work"],
        "tenantId": "spec-audit-tenant",
    },
    "baseUrl": "https://spec-audit.invalid",
}


def _path(toolset_id: str) -> str:
    return f"/{toolset_id}/config"


def _with_auth(**fields: Any) -> JsonObject:
    return {"auth": {"type": "API_TOKEN", **fields}}


@pytest.mark.parametrize("body", [MINIMAL_BODY, FULL_BODY], ids=["required-fields-only", "every-field"])
def test_update_config_with_a_valid_body_has_no_backend_handler(
    toolsets_client: ToolsetsClient, body: JsonObject
) -> None:
    resp = toolsets_client.put(_path(MISSING_TOOLSET_ID), json=body)
    assert_not_found(resp, NO_BACKEND_ROUTE)
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_config_for_an_existing_instance_changes_nothing(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    seeded = seed_toolset_instance()

    resp = toolsets_client.put(_path(seeded["_id"]), json=FULL_BODY)
    assert_not_found(resp, NO_BACKEND_ROUTE)
    assert_strict_openapi_exchange(resp, ROUTE)

    after = toolsets_client.get_instance(seeded["_id"])
    assert after.status_code == 200, after.text[:500]
    assert after.json()["instance"]["updatedAtTimestamp"] == seeded["updatedAtTimestamp"]


def test_update_config_as_member_is_not_found(second_user: SecondUser) -> None:
    resp = request_as(second_user, "PUT", _path(MISSING_TOOLSET_ID), json=MINIMAL_BODY)
    assert_not_found(resp, NO_BACKEND_ROUTE)
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("body", "fields"),
    [
        pytest.param(None, {"body.auth"}, id="no-body"),
        pytest.param({"baseUrl": "https://spec-audit.invalid"}, {"body.auth"}, id="auth-missing"),
        pytest.param({"auth": "API_TOKEN"}, {"body.auth"}, id="auth-not-object"),
        pytest.param({"auth": {}}, {"body.auth.type"}, id="auth-type-missing"),
        pytest.param(_with_auth(type=""), {"body.auth.type"}, id="auth-type-empty"),
        # The global sanitizer trims every string before the validator sees it.
        pytest.param(_with_auth(type="   "), {"body.auth.type"}, id="auth-type-blank"),
        pytest.param(_with_auth(type=5), {"body.auth.type"}, id="auth-type-not-text"),
        pytest.param(_with_auth(clientId=5), {"body.auth.clientId"}, id="clientId-not-text"),
        pytest.param(_with_auth(clientSecret=5), {"body.auth.clientSecret"}, id="clientSecret-not-text"),
        pytest.param(_with_auth(apiToken=5), {"body.auth.apiToken"}, id="apiToken-not-text"),
        pytest.param(_with_auth(oauthAppId=5), {"body.auth.oauthAppId"}, id="oauthAppId-not-text"),
        pytest.param(_with_auth(scopes="read"), {"body.auth.scopes"}, id="scopes-not-list"),
        pytest.param(_with_auth(scopes=[5]), {"body.auth.scopes.0"}, id="scope-not-text"),
        pytest.param(_with_auth(tenantId=5), {"body.auth.tenantId"}, id="tenantId-not-text"),
        pytest.param({**MINIMAL_BODY, "baseUrl": 5}, {"body.baseUrl"}, id="baseUrl-not-text"),
    ],
)
def test_update_config_rejects_a_body_that_fails_validation(
    toolsets_client: ToolsetsClient, body: JsonObject | None, fields: set[str]
) -> None:
    resp = toolsets_client.put(_path(MISSING_TOOLSET_ID), json=body)
    assert resp.status_code == 400, resp.text[:500]
    assert rejected_fields(resp) == fields
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_config_does_not_refuse_unknown_fields(toolsets_client: ToolsetsClient) -> None:
    body = {"auth": {"type": "API_TOKEN", "email": 5}, "specAuditUnknown": 1}
    with outside_request_contract("the validator drops fields it does not list instead of refusing them"):
        resp = toolsets_client.put(_path(MISSING_TOOLSET_ID), json=body)
    assert_not_found(resp, NO_BACKEND_ROUTE)
    assert_strict_openapi_response(resp, ROUTE)


def test_update_config_without_token_is_unauthorized(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.put(_path(MISSING_TOOLSET_ID), auth=False, json=MINIMAL_BODY)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_config_with_unsafe_toolset_id_is_refused_before_auth(
    toolsets_client: ToolsetsClient,
) -> None:
    # guardPathParams is a router.param hook, so it answers before authenticate runs.
    resp = toolsets_client.put(_path(UNSAFE_PATH_ID), auth=False, json=MINIMAL_BODY)
    assert resp.status_code == 400, resp.text[:500]
    assert error_of(resp)["code"] == "HTTP_BAD_REQUEST"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_config_for_the_id_instances_is_served_as_an_instance_update(
    toolsets_client: ToolsetsClient, second_user: SecondUser
) -> None:
    # API bug: PUT /toolsets/instances/config is Python's "update the instance with id
    # 'config'". No instance has that id, so the admin gets that handler's 404 and a
    # member its admin check.
    resp = toolsets_client.put(_path(INSTANCES_SEGMENT), json=MINIMAL_BODY)
    assert_not_found(resp, INSTANCE_NOT_FOUND)
    assert_strict_openapi_exchange(resp, ROUTE)

    as_member = request_as(second_user, "PUT", _path(INSTANCES_SEGMENT), json=MINIMAL_BODY)
    assert as_member.status_code == 403, as_member.text[:500]
    assert error_of(as_member)["message"] == "Only administrators can update toolset instances."
    assert_strict_openapi_exchange(as_member, ROUTE)


@pytest.mark.parametrize("toolset_id", [OAUTH_CONFIGS_SEGMENT, AGENTS_SEGMENT])
def test_update_config_for_a_reserved_id_is_an_internal_error(
    toolsets_client: ToolsetsClient, toolset_id: str
) -> None:
    # API bug: the pasted URL matches a Python route that has no PUT handler. FastAPI
    # answers 405, and Node turns every backend status it does not list into a 500.
    resp = toolsets_client.put(_path(toolset_id), json=MINIMAL_BODY)
    assert_backend_failure(resp)
    assert_strict_openapi_exchange(resp, ROUTE)
