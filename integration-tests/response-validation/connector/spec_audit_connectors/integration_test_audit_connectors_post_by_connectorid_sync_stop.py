"""Strict OpenAPI audit of POST /api/v1/connectors/:connectorId/sync/stop."""

from __future__ import annotations

import pytest
from connectors_audit_support import (
    MALFORMED_CONNECTOR_ID,
    MISSING_CONNECTOR_ID,
    UNSAFE_CONNECTOR_ID,
    ConnectorsAuditClient,
    bearer,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

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
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert body["success"] is True, body
    assert body["stopped"] is False, body
    assert body["status"] == "IDLE", body


def test_stop_of_a_running_sync(
    connectors_client: ConnectorsAuditClient, syncing_connector: str
) -> None:
    resp = connectors_client.post(_path(syncing_connector))
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert body["success"] is True, body
    assert body["stopped"] is True, body


def test_stop_ignores_a_body(
    connectors_client: ConnectorsAuditClient, connector_id: str
) -> None:
    with outside_request_contract("the route takes no body; a sent one is not read"):
        resp = connectors_client.post(_path(connector_id), json={"force": True})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["stopped"] is False


def test_stop_without_a_token_is_401(
    connectors_client: ConnectorsAuditClient, connector_id: str
) -> None:
    resp = connectors_client.post(_path(connector_id), auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_stop_without_the_sync_scope_is_403(
    connectors_client: ConnectorsAuditClient,
    connector_id: str,
    token_without_connector_scopes: str,
) -> None:
    resp = connectors_client.post(
        _path(connector_id), auth=False, headers=bearer(token_without_connector_scopes)
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("requested_id", "expected_status"),
    [
        pytest.param(MALFORMED_CONNECTOR_ID, 400, id="id-fails-param-schema"),
        pytest.param(UNSAFE_CONNECTOR_ID, 400, id="unsafe-path-segment"),
        pytest.param(MISSING_CONNECTOR_ID, 404, id="unknown-connector"),
    ],
)
def test_stop_for_unusable_connector_id(
    connectors_client: ConnectorsAuditClient, requested_id: str, expected_status: int
) -> None:
    resp = connectors_client.post(_path(requested_id))
    assert resp.status_code == expected_status, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_stop_by_a_member_on_an_admin_team_connector_is_404(
    second_user: SecondUser, connector_id: str
) -> None:
    # The registry read gate hides a team instance from a non-admin who did not
    # create it, so the lookup returns nothing before the 403 checks are reached.
    resp = request_as(second_user, "POST", _path(connector_id))
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
