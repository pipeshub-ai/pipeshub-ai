"""Strict OpenAPI audit of POST /api/v1/toolsets."""

from __future__ import annotations

import pytest
from toolsets_audit_support import JsonObject, ToolsetsClient
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/toolsets"

VALID_BODY: JsonObject = {
    "name": "spec-audit-legacy-toolset",
    "displayName": "Spec audit legacy toolset",
    "type": "jira",
    "auth": {"type": "API_TOKEN", "apiToken": "spec-audit-token"},
}


def test_valid_body_reaches_a_backend_without_the_route(
    toolsets_client: ToolsetsClient,
) -> None:
    # Node proxies to POST /api/v1/toolsets/ on the connector service, which registers
    # no handler for it, so the route cannot create anything and has no success path.
    resp = toolsets_client.post("", json=VALID_BODY)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"auth": {"type": "API_TOKEN"}}, id="missing-name"),
        pytest.param({"name": "spec-audit-legacy-toolset", "auth": {}}, id="missing-auth-type"),
    ],
)
def test_body_failing_validation_is_rejected(
    toolsets_client: ToolsetsClient, body: JsonObject
) -> None:
    resp = toolsets_client.post("", json=body)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_without_token_is_unauthorized(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.post("", auth=False, json=VALID_BODY)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
