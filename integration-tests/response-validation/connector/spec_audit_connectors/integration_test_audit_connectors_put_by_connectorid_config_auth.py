"""Strict OpenAPI audit of PUT /api/v1/connectors/:connectorId/config/auth."""

from __future__ import annotations

from typing import Any

import pytest
from connectors_audit_support import (
    MALFORMED_CONNECTOR_ID,
    MEMBER_TOKEN_AUTH,
    MISSING_CONNECTOR_ID,
    REDACTED_PLACEHOLDER,
    UNSAFE_CONNECTOR_ID,
    ConnectorsAuditClient,
    SeedConnector,
    bearer,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_spec_forbids_request, assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/connectors/:connectorId/config/auth"

# Demo's auth form has no fields; a key the form does not know is still saved as sent.
AUTH_BODY = {"auth": {"specAuditMarker": "spec-audit"}}


def _path(connector_id: str) -> str:
    return f"/{connector_id}/config/auth"


def test_admin_saves_auth_config_of_inactive_connector(
    connectors_client: ConnectorsAuditClient, seed_connector: SeedConnector
) -> None:
    resp = connectors_client.put(_path(seed_connector()), json=AUTH_BODY)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    body = resp.json()
    assert body["success"] is True
    assert body["message"] == "Authentication configuration saved successfully."
    # authType NONE is not OAUTH, so the submitted auth fields are stored as sent.
    assert body["config"]["auth"] == {
        "specAuditMarker": "spec-audit",
        "connectorType": "Demo",
        "connectorScope": "team",
    }
    # The owner's tokens and OAuth flow state are never part of a config response.
    assert not {"credentials", "oauth"} & body["config"].keys(), body["config"]


def test_member_saves_api_token_credentials_of_own_connector(
    second_user: SecondUser, member_connector: SeedConnector
) -> None:
    resp = request_as(
        second_user, "PUT", _path(member_connector()), json={"auth": {**MEMBER_TOKEN_AUTH, "email": "  padded@example.com "}}
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    auth = resp.json()["config"]["auth"]
    assert auth["email"] == "padded@example.com", auth
    # A field the connector's auth schema marks secret comes back masked.
    assert auth["apiToken"] == REDACTED_PLACEHOLDER, auth
    assert auth["connectorScope"] == "personal", auth


def test_member_sends_the_masked_token_back_unchanged(
    second_user: SecondUser, member_configured_connector: str
) -> None:
    # The settings form round-trips the config it read; the mask means "keep the stored token".
    read = request_as(second_user, "GET", f"/{member_configured_connector}/config")
    assert read.status_code == 200, read.text[:500]
    assert_strict_openapi_exchange(read, "/api/v1/connectors/:connectorId/config")
    saved_auth = read.json()["config"]["config"]["auth"]
    assert saved_auth["apiToken"] == REDACTED_PLACEHOLDER, saved_auth

    resp = request_as(
        second_user,
        "PUT",
        _path(member_configured_connector),
        json={"auth": {key: saved_auth[key] for key in MEMBER_TOKEN_AUTH}},
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    auth = resp.json()["config"]["auth"]
    assert {key: auth[key] for key in MEMBER_TOKEN_AUTH} == {
        **MEMBER_TOKEN_AUTH,
        "apiToken": REDACTED_PLACEHOLDER,
    }, auth


def test_admin_oauth_credentials_update_the_linked_oauth_app(
    connectors_client: ConnectorsAuditClient, gitlab_oauth_connector: str
) -> None:
    # The fixture saved clientId and clientSecret, which created a shared OAuth app;
    # the instance keeps only a link to it, never the secret.
    app_id = connectors_client.get(_path(gitlab_oauth_connector).removesuffix("/auth")).json()[
        "config"]["config"]["auth"]["oauthConfigId"]
    resp = connectors_client.put(
        _path(gitlab_oauth_connector),
        json={"auth": {"oauthConfigId": app_id, "clientId": "spec-audit-client", "clientSecret": "spec-audit-secret-2"}},
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    auth = resp.json()["config"]["auth"]
    assert "clientSecret" not in auth, auth
    assert auth["oauthConfigId"] == app_id, auth
    assert auth["authType"] == "OAUTH", auth
    assert auth["redirectUri"].endswith("connectors/oauth/callback/Gitlab"), auth


def test_admin_oauth_credentials_without_the_app_id_clash_with_the_app_already_made(
    connectors_client: ConnectorsAuditClient, gitlab_oauth_connector: str
) -> None:
    # Without oauthConfigId a new app named after the instance is created, and the
    # instance's first save already took that name.
    resp = connectors_client.put(
        _path(gitlab_oauth_connector),
        json={"auth": {"clientId": "spec-audit-client", "clientSecret": "spec-audit-secret-2"}},
    )
    assert resp.status_code == 409, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert "already exists" in resp.json()["error"]["message"]


def test_auth_config_of_an_active_connector_is_400(
    connectors_client: ConnectorsAuditClient, syncing_connector: str
) -> None:
    resp = connectors_client.put(_path(syncing_connector), json=AUTH_BODY)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert "while connector is active" in resp.json()["error"]["message"]


@pytest.mark.parametrize(
    "body",
    [
        # zod declares auth as z.any(), so the controller is what refuses these.
        pytest.param({"baseUrl": "http://localhost:3001"}, id="auth-missing"),
        pytest.param({"auth": None}, id="auth-null"),
    ],
)
def test_auth_config_without_auth_is_refused_by_the_controller(
    connectors_client: ConnectorsAuditClient, connector_id: str, body: dict[str, Any]
) -> None:
    resp = connectors_client.put(_path(connector_id), json=body)
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["message"] == "Auth configuration is required"
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


def test_auth_config_with_a_non_string_base_url_is_refused_by_the_validator(
    connectors_client: ConnectorsAuditClient, connector_id: str
) -> None:
    resp = connectors_client.put(_path(connector_id), json={"auth": {}, "baseUrl": 5})
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_auth_that_is_not_an_object_is_an_internal_error(
    connectors_client: ConnectorsAuditClient, connector_id: str
) -> None:
    # API bug: a string passes Node's truthiness check and breaks the connector service.
    resp = connectors_client.put(_path(connector_id), json={"auth": "spec-audit"})
    assert resp.status_code == 500, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


def test_auth_config_without_token_is_unauthorized(
    connectors_client: ConnectorsAuditClient, connector_id: str
) -> None:
    resp = connectors_client.put(_path(connector_id), auth=False, json=AUTH_BODY)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_auth_config_without_the_write_scope_is_forbidden(
    connectors_client: ConnectorsAuditClient,
    connector_id: str,
    token_without_connector_scopes: str,
) -> None:
    resp = connectors_client.put(
        _path(connector_id), auth=False, headers=bearer(token_without_connector_scopes), json=AUTH_BODY
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_auth_config_of_admin_owned_team_connector_is_not_found_for_member(
    second_user: SecondUser, connector_id: str
) -> None:
    # The registry hides a team instance from anyone but an admin or its creator, so the
    # handler answers 404 and never reaches its own "administrators only" 403.
    resp = request_as(second_user, "PUT", _path(connector_id), json=AUTH_BODY)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("requested_id", "expected_status"),
    [
        pytest.param(MISSING_CONNECTOR_ID, 404, id="unknown-connector"),
        pytest.param(MALFORMED_CONNECTOR_ID, 404, id="id-the-instance-routes-reject"),
        pytest.param(UNSAFE_CONNECTOR_ID, 400, id="unsafe-path-segment"),
    ],
)
def test_auth_config_for_unusable_connector_id(
    connectors_client: ConnectorsAuditClient, requested_id: str, expected_status: int
) -> None:
    resp = connectors_client.put(_path(requested_id), json=AUTH_BODY)
    assert resp.status_code == expected_status, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_auth_config_takes_a_base_url_that_is_not_a_url(
    connectors_client: ConnectorsAuditClient, connector_id: str
) -> None:
    # The validator is z.string().optional(): any string is accepted.
    resp = connectors_client.put(_path(connector_id), json={"auth": {}, "baseUrl": "not a url"})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
