"""Strict OpenAPI audit of GET /api/v1/toolsets/oauth/callback.

No case completes an OAuth flow: that needs a real provider consent. Python answers
every refusal with a 200 JSON body, which Node reshapes to camelCase.
"""

from __future__ import annotations

import pytest
from toolsets_audit_support import ToolsetsClient
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/toolsets/oauth/callback"
BASE_URL = "https://spec-audit.invalid"


@pytest.mark.parametrize(
    ("params", "error"),
    [
        ({"base_url": BASE_URL}, "missing_parameters"),
        ({"base_url": BASE_URL, "error": "access_denied", "code": "c", "state": "s"}, "access_denied"),
    ],
    ids=["no-code-or-state", "provider-error"],
)
def test_callback_refusal_is_a_200_with_redirect_url(
    toolsets_client: ToolsetsClient, params: dict[str, str], error: str
) -> None:
    resp = toolsets_client.get("/oauth/callback", params=params)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    body = resp.json()
    assert body["success"] is False
    assert body["error"] == error
    assert body["redirectUrl"] == f"{BASE_URL}/tools?oauth_error={error}"
    assert "redirect_url" not in body
    assert "errorMessage" not in body


def test_callback_with_undecodable_state_reports_oauth_config_error(
    toolsets_client: ToolsetsClient,
) -> None:
    resp = toolsets_client.get(
        "/oauth/callback",
        params={"base_url": BASE_URL, "code": "spec-audit-code", "state": "not-a-state"},
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    body = resp.json()
    assert body["success"] is False
    assert body["error"] == "OAuthConfigError"
    assert body["errorMessage"].startswith("OAuth configuration error")
    assert body["redirectUrl"] == f"{BASE_URL}/tools?oauth_error=OAuthConfigError"


def test_callback_rejects_a_repeated_query_parameter(toolsets_client: ToolsetsClient) -> None:
    # Express parses a repeated key into an array, which the zod string refuses.
    resp = toolsets_client.get("/oauth/callback", params={"state": ["a", "b"]})
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_callback_without_token_is_unauthorized(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.get("/oauth/callback", params={"code": "c", "state": "s"}, auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
