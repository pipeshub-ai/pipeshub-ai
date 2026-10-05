"""Strict OpenAPI audit of GET /api/v1/connectors/:connectorId/stats."""

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

ROUTE = "/api/v1/connectors/:connectorId/stats"


def test_admin_reads_stats_of_never_synced_connector(
    connectors_client: ConnectorsAuditClient, connector_id: str
) -> None:
    resp = connectors_client.get(f"/{connector_id}/stats")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    body = resp.json()
    assert body["success"] is True
    assert body["data"]["connectorId"] == connector_id
    assert body["data"]["origin"] == "CONNECTOR"


def test_stats_without_token_is_unauthorized(
    connectors_client: ConnectorsAuditClient, connector_id: str
) -> None:
    resp = connectors_client.get(f"/{connector_id}/stats", auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_member_is_refused_stats_of_admin_owned_team_connector(
    second_user: SecondUser, connector_id: str
) -> None:
    # Listing a team connector is open to members; its stats are creator/admin only.
    resp = request_as(second_user, "GET", f"/{connector_id}/stats")
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    ("requested_id", "expected_status"),
    [
        pytest.param(MISSING_CONNECTOR_ID, 404, id="unknown-connector"),
        # Refused by guardPathParams; the stats schema itself accepts any non-empty id.
        pytest.param(UNSAFE_CONNECTOR_ID, 400, id="unsafe-path-segment"),
    ],
)
def test_stats_for_unusable_connector_id(
    connectors_client: ConnectorsAuditClient,
    requested_id: str,
    expected_status: int,
) -> None:
    resp = connectors_client.get(f"/{requested_id}/stats")
    assert resp.status_code == expected_status, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
