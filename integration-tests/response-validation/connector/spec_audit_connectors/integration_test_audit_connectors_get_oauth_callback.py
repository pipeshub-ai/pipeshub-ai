"""Strict OpenAPI audit of GET /api/v1/connectors/oauth/callback.

The completed flow uses a GitLab instance whose instance URL is a local stand-in
for the provider: its token endpoint answers the code exchange.
"""

from __future__ import annotations

import base64
import json

import pytest
from connectors_audit_support import (
    MISSING_CONNECTOR_ID,
    OAUTH_BASE_URL,
    ConnectorsAuditClient,
    StubSource,
    bearer,
    instance_state,
)
from strict_openapi import assert_spec_forbids_request, assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/connectors/oauth/callback"

BASE_URL = "https://spec-audit.invalid"
# Same encoding Python's _encode_state_with_instance uses for the authorize URL.
UNKNOWN_INSTANCE_STATE = base64.urlsafe_b64encode(
    json.dumps({"state": "spec-audit", "connector_id": MISSING_CONNECTOR_ID}).encode()
).decode()


def _authorize(client: ConnectorsAuditClient, connector_id: str) -> str:
    resp = client.get(f"/{connector_id}/oauth/authorize", params={"baseUrl": OAUTH_BASE_URL})
    assert resp.status_code == 200, resp.text[:300]
    return str(resp.json()["state"])


def test_callback_completes_the_flow_and_authenticates_the_connector(
    connectors_client: ConnectorsAuditClient, gitlab_oauth_connector: str, oauth_provider: StubSource
) -> None:
    state = _authorize(connectors_client, gitlab_oauth_connector)
    resp = connectors_client.get(
        "/oauth/callback",
        params={"code": "spec-audit-code", "state": state, "baseUrl": OAUTH_BASE_URL},
        allow_redirects=False,
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {
        "success": True,
        "redirectUrl": f"{OAUTH_BASE_URL}/account/company-settings/settings/connector/{gitlab_oauth_connector}",
    }
    assert ("POST", "/oauth/token") in oauth_provider.requests
    assert instance_state(connectors_client, gitlab_oauth_connector)["isAuthenticated"] is True


def test_callback_without_an_access_token_from_the_provider(
    connectors_client: ConnectorsAuditClient, gitlab_oauth_connector: str, oauth_provider: StubSource
) -> None:
    oauth_provider.post_body = {"error": "invalid_grant"}
    state = _authorize(connectors_client, gitlab_oauth_connector)
    resp = connectors_client.get(
        "/oauth/callback",
        params={"code": "spec-audit-code", "state": state, "baseUrl": OAUTH_BASE_URL},
        allow_redirects=False,
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert body["success"] is False, body
    # The token exchange raises on a reply without access_token, so this is a
    # server_error with a message, not invalid_token.
    assert body["error"] == "server_error", body
    assert body["errorMessage"], body
    assert body["redirectUrl"] == f"{OAUTH_BASE_URL}/connectors/oauth/callback?oauth_error=server_error"
    assert instance_state(connectors_client, gitlab_oauth_connector)["isAuthenticated"] is False


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
    assert_strict_openapi_exchange(resp, ROUTE)

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
    assert_strict_openapi_exchange(resp, ROUTE)


def test_callback_without_the_write_scope_is_forbidden(
    connectors_client: ConnectorsAuditClient, token_without_connector_scopes: str
) -> None:
    resp = connectors_client.get(
        "/oauth/callback",
        auth=False,
        headers=bearer(token_without_connector_scopes),
        params={"code": "spec-audit", "state": "spec-audit"},
        allow_redirects=False,
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"error": "access_denied"}, id="only-error"),
        pytest.param({"code": "spec-audit"}, id="state-missing"),
        pytest.param({"state": "spec-audit"}, id="code-missing"),
        pytest.param({"code": "", "state": "spec-audit"}, id="code-empty"),
        pytest.param({"code": " ", "state": "spec-audit"}, id="code-blank"),
        pytest.param({"code": "spec-audit", "state": " "}, id="state-blank"),
    ],
)
def test_callback_without_code_and_state_is_bad_request(
    connectors_client: ConnectorsAuditClient, params: dict[str, str]
) -> None:
    # zod treats both as optional; the controller is what refuses, even when
    # the provider only sent ?error=.
    resp = connectors_client.get("/oauth/callback", params=params, allow_redirects=False)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)
