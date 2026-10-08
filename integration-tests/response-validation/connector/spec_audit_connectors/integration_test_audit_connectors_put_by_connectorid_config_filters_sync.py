"""Strict OpenAPI audit of PUT /api/v1/connectors/:connectorId/config/filters-sync."""

from __future__ import annotations

from typing import Any

import pytest
from connectors_audit_support import (
    MALFORMED_CONNECTOR_ID,
    MISSING_CONNECTOR_ID,
    UNSAFE_CONNECTOR_ID,
    ConnectorsAuditClient,
    SeedConnector,
    bearer,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_spec_forbids_request, assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/connectors/:connectorId/config/filters-sync"

SYNC_BODY: dict[str, Any] = {"sync": {"selectedStrategy": "MANUAL"}}


def _path(connector_id: str) -> str:
    return f"/{connector_id}/config/filters-sync"


def test_admin_saves_sync_settings(
    connectors_client: ConnectorsAuditClient, seed_connector: SeedConnector
) -> None:
    resp = connectors_client.put(_path(seed_connector()), json=SYNC_BODY)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {
        "success": True,
        "config": {"sync": {"selectedStrategy": "MANUAL"}},
        "message": "Filters and sync configuration saved successfully.",
        "syncFiltersChanged": False,
    }


def test_saving_sync_filters_for_the_first_time_asks_for_a_full_resync(
    connectors_client: ConnectorsAuditClient, seed_connector: SeedConnector
) -> None:
    connector = seed_connector()
    body = {"filters": {"sync": {"values": {}}, "indexing": {"values": {}}}}
    resp = connectors_client.put(_path(connector), json=body)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["syncFiltersChanged"] is True
    assert resp.json()["config"]["filters"] == body["filters"]

    # The same selections again change nothing.
    again = connectors_client.put(_path(connector), json=body)
    assert again.status_code == 200, again.text[:500]
    assert_strict_openapi_exchange(again, ROUTE)
    assert again.json()["syncFiltersChanged"] is False


def test_base_url_is_dropped(
    connectors_client: ConnectorsAuditClient, seed_connector: SeedConnector
) -> None:
    # The frontend sends baseUrl with every save; the gateway's validator does not list it.
    with outside_request_contract("baseUrl is not part of this route's body"):
        resp = connectors_client.put(
            _path(seed_connector()), json={**SYNC_BODY, "baseUrl": "http://localhost:3001"}
        )
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert "baseUrl" not in resp.json()["config"]


def test_a_sync_section_that_is_not_an_object_is_skipped(
    connectors_client: ConnectorsAuditClient, seed_connector: SeedConnector
) -> None:
    with outside_request_contract("sync is not an object on purpose"):
        resp = connectors_client.put(_path(seed_connector()), json={"sync": "MANUAL"})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert "sync" not in resp.json()["config"]


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({}, id="neither-sync-nor-filters"),
        pytest.param({"filters": {"sync": None}}, id="filters-sync-not-an-object"),
    ],
)
def test_filters_sync_refused_by_the_connector_service(
    connectors_client: ConnectorsAuditClient, connector_id: str, body: dict[str, Any]
) -> None:
    resp = connectors_client.put(_path(connector_id), json=body)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


def test_filters_sync_of_an_active_connector_is_400(
    connectors_client: ConnectorsAuditClient, syncing_connector: str
) -> None:
    resp = connectors_client.put(_path(syncing_connector), json=SYNC_BODY)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert "while connector is active" in resp.json()["error"]["message"]


def test_filters_sync_without_a_token_is_401(
    connectors_client: ConnectorsAuditClient, connector_id: str
) -> None:
    resp = connectors_client.put(_path(connector_id), auth=False, json=SYNC_BODY)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_filters_sync_without_the_write_scope_is_403(
    connectors_client: ConnectorsAuditClient,
    connector_id: str,
    token_without_connector_scopes: str,
) -> None:
    resp = connectors_client.put(
        _path(connector_id), auth=False, headers=bearer(token_without_connector_scopes), json=SYNC_BODY
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_is_told_an_admin_team_connector_does_not_exist(
    second_user: SecondUser, connector_id: str
) -> None:
    resp = request_as(second_user, "PUT", _path(connector_id), json=SYNC_BODY)
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
def test_filters_sync_for_unusable_connector_id(
    connectors_client: ConnectorsAuditClient, requested_id: str, expected_status: int
) -> None:
    resp = connectors_client.put(_path(requested_id), json=SYNC_BODY)
    assert resp.status_code == expected_status, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
