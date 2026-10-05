"""Strict OpenAPI audit of PUT /api/v1/toolsets/:toolsetId/config."""

from __future__ import annotations

import pytest
from strict_openapi import assert_strict_openapi_response
from toolsets_audit_support import (
    MISSING_TOOLSET_ID,
    UNSAFE_PATH_ID,
    JsonObject,
    ToolsetsClient,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/toolsets/:toolsetId/config"
VALID_BODY: JsonObject = {
    "auth": {"type": "API_TOKEN", "apiToken": "spec-audit-token"},
    "baseUrl": "https://spec-audit.invalid",
}


def _config_path(toolset_id: str) -> str:
    return f"/{toolset_id}/config"


def test_update_config_with_a_valid_body_is_not_found(
    toolsets_client: ToolsetsClient,
) -> None:
    # Legacy route: Node validates and proxies, but Python registers no
    # PUT /{toolset_id}/config, so every well-formed call ends in its 404.
    resp = toolsets_client.put(_config_path(MISSING_TOOLSET_ID), json=VALID_BODY)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"baseUrl": "https://spec-audit.invalid"}, id="auth-missing"),
        pytest.param({"auth": {"type": ""}}, id="auth-type-empty"),
    ],
)
def test_update_config_rejects_a_body_that_fails_validation(
    toolsets_client: ToolsetsClient, body: JsonObject
) -> None:
    resp = toolsets_client.put(_config_path(MISSING_TOOLSET_ID), json=body)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_update_config_rejects_a_call_without_a_token(
    toolsets_client: ToolsetsClient,
) -> None:
    resp = toolsets_client.put(
        _config_path(MISSING_TOOLSET_ID), auth=False, json=VALID_BODY
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_update_config_refuses_an_unsafe_toolset_id_before_auth(
    toolsets_client: ToolsetsClient,
) -> None:
    resp = toolsets_client.put(
        _config_path(UNSAFE_PATH_ID), auth=False, json=VALID_BODY
    )
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
