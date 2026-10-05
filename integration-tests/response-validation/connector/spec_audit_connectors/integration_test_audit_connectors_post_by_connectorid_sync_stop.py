"""Strict OpenAPI audit of POST /api/v1/connectors/:connectorId/sync/stop."""

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

ROUTE = "/api/v1/connectors/:connectorId/sync/stop"


def _path(connector_id: str) -> str:
    return f"/{connector_id}/sync/stop"


def test_stop_with_nothing_running_is_a_200_no_op(
    connectors_client: ConnectorsAuditClient, connector_id: str
) -> None:
    # The seeded instance never synced, so Python takes the idempotent branch
    # and writes nothing.
    resp = connectors_client.post(_path(connector_id))
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    body = resp.json()
    assert body["success"] is True, body
    assert body["stopped"] is False, body
    assert isinstance(body["status"], str), body


def test_stop_without_a_token_is_401(
    connectors_client: ConnectorsAuditClient, connector_id: str
) -> None:
    resp = connectors_client.post(_path(connector_id), auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_stop_with_a_malformed_id_is_400(
    connectors_client: ConnectorsAuditClient,
) -> None:
    resp = connectors_client.post(_path(MALFORMED_CONNECTOR_ID))
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_stop_of_an_unknown_connector_is_404(
    connectors_client: ConnectorsAuditClient,
) -> None:
    resp = connectors_client.post(_path(MISSING_CONNECTOR_ID))
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_stop_by_a_member_on_an_admin_team_connector_is_404(
    second_user: SecondUser, connector_id: str
) -> None:
    # The registry read gate hides a team instance from a non-admin who did not
    # create it, so the lookup returns nothing before the 403 checks are reached.
    resp = request_as(second_user, "POST", _path(connector_id))
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
