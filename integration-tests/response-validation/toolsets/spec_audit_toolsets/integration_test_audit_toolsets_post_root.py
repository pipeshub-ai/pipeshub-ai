"""Strict OpenAPI audit of POST /api/v1/toolsets.

Legacy route: Node validates the body and proxies it to POST /api/v1/toolsets/ on the
connector service, which registers no handler for it. Nothing can be created; every
body that passes validation ends in the relayed routing 404.
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
    NO_BACKEND_ROUTE,
    JsonObject,
    ToolsetsClient,
    assert_not_found,
    rejected_fields,
    request_as,
)

from helper.second_user import SecondUser

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/toolsets"

MINIMAL_BODY: JsonObject = {"name": "spec-audit-legacy-toolset", "auth": {"type": "API_TOKEN"}}
FULL_BODY: JsonObject = {
    "name": "spec-audit-legacy-toolset",
    "displayName": "Spec audit legacy toolset",
    "type": "jira",
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


def _with_auth(**fields: Any) -> JsonObject:
    return {"name": "spec-audit-legacy-toolset", "auth": {"type": "API_TOKEN", **fields}}


@pytest.mark.parametrize("body", [MINIMAL_BODY, FULL_BODY], ids=["required-fields-only", "every-field"])
def test_valid_body_reaches_a_backend_without_the_route(
    toolsets_client: ToolsetsClient, body: JsonObject
) -> None:
    resp = toolsets_client.post("", json=body)
    assert_not_found(resp, NO_BACKEND_ROUTE)
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_reaches_the_same_missing_backend_route(second_user: SecondUser) -> None:
    resp = request_as(second_user, "POST", "", json=MINIMAL_BODY)
    assert_not_found(resp, NO_BACKEND_ROUTE)
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("body", "fields"),
    [
        pytest.param(None, {"body.name", "body.auth"}, id="no-body"),
        pytest.param({"auth": {"type": "API_TOKEN"}}, {"body.name"}, id="name-missing"),
        pytest.param({**MINIMAL_BODY, "name": ""}, {"body.name"}, id="name-empty"),
        # The global sanitizer trims every string before the validator sees it.
        pytest.param({**MINIMAL_BODY, "name": "   "}, {"body.name"}, id="name-blank"),
        pytest.param({**MINIMAL_BODY, "name": 5}, {"body.name"}, id="name-not-text"),
        pytest.param({**MINIMAL_BODY, "displayName": 5}, {"body.displayName"}, id="displayName-not-text"),
        pytest.param({**MINIMAL_BODY, "type": 5}, {"body.type"}, id="type-not-text"),
        pytest.param({**MINIMAL_BODY, "baseUrl": 5}, {"body.baseUrl"}, id="baseUrl-not-text"),
        pytest.param({"name": "spec-audit-legacy-toolset"}, {"body.auth"}, id="auth-missing"),
        pytest.param({"name": "spec-audit-legacy-toolset", "auth": "API_TOKEN"}, {"body.auth"}, id="auth-not-object"),
        pytest.param({"name": "spec-audit-legacy-toolset", "auth": {}}, {"body.auth.type"}, id="auth-type-missing"),
        pytest.param(_with_auth(type=""), {"body.auth.type"}, id="auth-type-empty"),
        pytest.param(_with_auth(type=5), {"body.auth.type"}, id="auth-type-not-text"),
        pytest.param(_with_auth(clientId=5), {"body.auth.clientId"}, id="clientId-not-text"),
        pytest.param(_with_auth(clientSecret=5), {"body.auth.clientSecret"}, id="clientSecret-not-text"),
        pytest.param(_with_auth(apiToken=5), {"body.auth.apiToken"}, id="apiToken-not-text"),
        pytest.param(_with_auth(oauthAppId=5), {"body.auth.oauthAppId"}, id="oauthAppId-not-text"),
        pytest.param(_with_auth(scopes="read"), {"body.auth.scopes"}, id="scopes-not-list"),
        pytest.param(_with_auth(scopes=[5]), {"body.auth.scopes.0"}, id="scope-not-text"),
        pytest.param(_with_auth(tenantId=5), {"body.auth.tenantId"}, id="tenantId-not-text"),
    ],
)
def test_body_failing_validation_is_rejected(
    toolsets_client: ToolsetsClient, body: JsonObject | None, fields: set[str]
) -> None:
    resp = toolsets_client.post("", json=body)
    assert resp.status_code == 400, resp.text[:500]
    assert rejected_fields(resp) == fields
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unknown_fields_are_not_refused(toolsets_client: ToolsetsClient) -> None:
    body = {**MINIMAL_BODY, "specAuditUnknown": 1, "auth": {"type": "API_TOKEN", "email": 5}}
    with outside_request_contract("the validator drops fields it does not list instead of refusing them"):
        resp = toolsets_client.post("", json=body)
    assert_not_found(resp, NO_BACKEND_ROUTE)
    assert_strict_openapi_response(resp, ROUTE)


def test_without_token_is_unauthorized(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.post("", auth=False, json=MINIMAL_BODY)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
