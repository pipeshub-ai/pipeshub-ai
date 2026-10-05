"""Strict OpenAPI audit of GET /api/v1/connectors/:connectorId/filters."""

from __future__ import annotations

import pytest
from connectors_audit_support import (
    MALFORMED_CONNECTOR_ID,
    MISSING_CONNECTOR_ID,
    ConnectorsAuditClient,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/connectors/:connectorId/filters"


def test_filters_of_connector_without_credentials_is_bad_request(
    connectors_client: ConnectorsAuditClient, connector_id: str
) -> None:
    # Filter options are fetched with the instance's credentials; authType NONE has
    # none, and a success needs a connector authenticated against a real source.
    resp = connectors_client.get(f"/{connector_id}/filters")
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_filters_without_token_is_unauthorized(
    connectors_client: ConnectorsAuditClient, connector_id: str
) -> None:
    resp = connectors_client.get(f"/{connector_id}/filters", auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_member_cannot_see_filters_of_admin_owned_team_connector(
    second_user: SecondUser, connector_id: str
) -> None:
    # The registry read gate hides the instance from a non-creator member, so the
    # handler's own 403 permission check is never reached.
    resp = request_as(second_user, "GET", f"/{connector_id}/filters")
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    ("requested_id", "expected_status"),
    [
        pytest.param(MISSING_CONNECTOR_ID, 404, id="unknown-connector"),
        pytest.param(MALFORMED_CONNECTOR_ID, 400, id="id-fails-param-schema"),
    ],
)
def test_filters_for_unusable_connector_id(
    connectors_client: ConnectorsAuditClient,
    requested_id: str,
    expected_status: int,
) -> None:
    resp = connectors_client.get(f"/{requested_id}/filters")
    assert resp.status_code == expected_status, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
