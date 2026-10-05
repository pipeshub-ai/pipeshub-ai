"""Strict OpenAPI audit of PUT /api/v1/connectors/:connectorId/config/auth."""

from __future__ import annotations

import pytest
from connectors_audit_support import (
    MISSING_CONNECTOR_ID,
    ConnectorsAuditClient,
    SeedConnector,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/connectors/:connectorId/config/auth"

AUTH_BODY = {"auth": {"specAuditMarker": "spec-audit"}}


def test_admin_saves_auth_config_of_inactive_connector(
    connectors_client: ConnectorsAuditClient, seed_connector: SeedConnector
) -> None:
    seeded_id = seed_connector()

    resp = connectors_client.put(f"/{seeded_id}/config/auth", json=AUTH_BODY)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    body = resp.json()
    assert body["success"] is True
    assert body["message"] == "Authentication configuration saved successfully."
    # authType NONE is not OAUTH, so the submitted auth fields are stored as sent.
    assert body["config"]["auth"]["specAuditMarker"] == "spec-audit"
    assert body["config"]["credentials"] is None
    assert body["config"]["oauth"] is None


def test_auth_config_without_token_is_unauthorized(
    connectors_client: ConnectorsAuditClient, connector_id: str
) -> None:
    resp = connectors_client.put(
        f"/{connector_id}/config/auth", auth=False, json=AUTH_BODY
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_auth_config_of_admin_owned_team_connector_is_not_found_for_member(
    second_user: SecondUser, connector_id: str
) -> None:
    # The registry hides a team instance from anyone but an admin or its creator, so the
    # handler answers 404 and never reaches its own "administrators only" 403.
    resp = request_as(
        second_user, "PUT", f"/{connector_id}/config/auth", json=AUTH_BODY
    )
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_auth_config_without_auth_field_is_bad_request(
    connectors_client: ConnectorsAuditClient, connector_id: str
) -> None:
    # zod declares auth as z.any(), so the missing field is caught by the controller instead.
    resp = connectors_client.put(
        f"/{connector_id}/config/auth", json={"baseUrl": "http://localhost:3001"}
    )
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_auth_config_of_unknown_connector_is_not_found(
    connectors_client: ConnectorsAuditClient,
) -> None:
    resp = connectors_client.put(
        f"/{MISSING_CONNECTOR_ID}/config/auth", json=AUTH_BODY
    )
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
