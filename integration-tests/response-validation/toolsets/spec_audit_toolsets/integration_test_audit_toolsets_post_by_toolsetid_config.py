"""Strict OpenAPI audit of POST /api/v1/toolsets/:toolsetId/config."""

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


def test_save_config_without_token_is_unauthorized(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.post(f"/{MISSING_TOOLSET_ID}/config", auth=False, json=VALID_BODY)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_save_config_with_unsafe_toolset_id_is_refused_before_auth(
    toolsets_client: ToolsetsClient,
) -> None:
    # guardPathParams is a router.param hook, so it answers before authenticate runs.
    resp = toolsets_client.post(f"/{UNSAFE_PATH_ID}/config", auth=False, json=VALID_BODY)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"baseUrl": "https://spec-audit.invalid"}, id="missing-auth"),
        pytest.param({"auth": {"type": ""}}, id="empty-auth-type"),
    ],
)
def test_save_config_rejects_invalid_body(toolsets_client: ToolsetsClient, body: JsonObject) -> None:
    resp = toolsets_client.post(f"/{MISSING_TOOLSET_ID}/config", json=body)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_save_config_with_valid_body_has_no_backend_handler(
    toolsets_client: ToolsetsClient,
) -> None:
    # Python registers no POST /{toolsetId}/config, so a body that passes Node's
    # validation only ever reaches FastAPI's routing 404, relayed by mapBackendError.
    resp = toolsets_client.post(f"/{MISSING_TOOLSET_ID}/config", json=VALID_BODY)

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
