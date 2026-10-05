"""Strict OpenAPI audit of GET /api/v1/connectors/:connectorId/oauth/authorize.

Negative cases only: a real authorization URL needs an OAuth connector instance
holding third-party client credentials.
"""

from __future__ import annotations

import pytest
from connectors_audit_support import (
    MISSING_CONNECTOR_ID,
    UNSAFE_CONNECTOR_ID,
    ConnectorsAuditClient,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/connectors/:connectorId/oauth/authorize"


def test_authorize_without_token_is_unauthorized(
    connectors_client: ConnectorsAuditClient, connector_id: str
) -> None:
    resp = connectors_client.get(f"/{connector_id}/oauth/authorize", auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_authorize_on_connector_without_oauth_is_bad_request(
    connectors_client: ConnectorsAuditClient, connector_id: str
) -> None:
    # The seeded Demo instance has authType NONE; Python refuses before reading any config.
    resp = connectors_client.get(
        f"/{connector_id}/oauth/authorize",
        params={"baseUrl": "https://spec-audit.invalid"},
    )
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_member_cannot_see_admin_owned_team_connector(
    second_user: SecondUser, connector_id: str
) -> None:
    # The registry access gate hides the instance from a non-creator member, so the
    # handler answers 404 and never reaches its own "administrators only" 403.
    resp = request_as(second_user, "GET", f"/{connector_id}/oauth/authorize")
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    ("requested_id", "expected_status"),
    [
        pytest.param(MISSING_CONNECTOR_ID, 404, id="unknown-connector"),
        # Refused by guardPathParams; the route schema accepts any non-empty id.
        pytest.param(UNSAFE_CONNECTOR_ID, 400, id="unsafe-path-segment"),
    ],
)
def test_authorize_for_unusable_connector_id(
    connectors_client: ConnectorsAuditClient,
    requested_id: str,
    expected_status: int,
) -> None:
    resp = connectors_client.get(f"/{requested_id}/oauth/authorize")
    assert resp.status_code == expected_status, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
