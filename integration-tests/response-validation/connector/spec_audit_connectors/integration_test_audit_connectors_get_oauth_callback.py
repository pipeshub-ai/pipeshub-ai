"""Strict OpenAPI audit of GET /api/v1/connectors/oauth/callback."""

from __future__ import annotations

import base64
import json

import pytest
from connectors_audit_support import MISSING_CONNECTOR_ID, ConnectorsAuditClient
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/connectors/oauth/callback"

BASE_URL = "https://spec-audit.invalid"
# Same encoding Python's _encode_state_with_instance uses for the authorize URL.
UNKNOWN_INSTANCE_STATE = base64.urlsafe_b64encode(
    json.dumps({"state": "spec-audit", "connector_id": MISSING_CONNECTOR_ID}).encode()
).decode()


# Python answers every refused callback with 200 and a redirect_url, never a 302;
# none of these reach the token exchange, so no provider is contacted.
@pytest.mark.parametrize(
    ("params", "oauth_error"),
    [
        pytest.param(
            {"code": "spec-audit", "state": "spec-audit", "error": "access_denied"},
            "access_denied",
            id="provider-error",
        ),
        pytest.param(
            {"code": "spec-audit", "state": "not-a-valid-state"},
            "invalid_state",
            id="undecodable-state",
        ),
        pytest.param(
            {"code": "spec-audit", "state": UNKNOWN_INSTANCE_STATE},
            "instance_not_found",
            id="unknown-instance",
        ),
    ],
)
def test_refused_callback_returns_redirect_url_as_json(
    connectors_client: ConnectorsAuditClient,
    params: dict[str, str],
    oauth_error: str,
) -> None:
    resp = connectors_client.get(
        "/oauth/callback",
        params={**params, "baseUrl": BASE_URL},
        allow_redirects=False,
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    body = resp.json()
    assert body["success"] is False
    assert body["error"] == oauth_error
    assert body["redirectUrl"] == (
        f"{BASE_URL}/connectors/oauth/callback?oauth_error={oauth_error}"
    )


def test_callback_without_token_is_unauthorized(
    connectors_client: ConnectorsAuditClient,
) -> None:
    resp = connectors_client.get(
        "/oauth/callback",
        auth=False,
        params={"code": "spec-audit", "state": "spec-audit"},
        allow_redirects=False,
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_callback_without_code_and_state_is_bad_request(
    connectors_client: ConnectorsAuditClient,
) -> None:
    # zod treats both as optional; the controller is what refuses, even when
    # the provider only sent ?error=.
    resp = connectors_client.get(
        "/oauth/callback", params={"error": "access_denied"}, allow_redirects=False
    )
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
