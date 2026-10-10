"""Strict OpenAPI audit of POST /api/v1/connectors/:connectorId/resync."""

from __future__ import annotations

from typing import Any

import pytest
from connectors_audit_support import (
    MISSING_CONNECTOR_ID,
    UNSAFE_CONNECTOR_ID,
    ConnectorsAuditClient,
    KbRecords,
    bearer,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/connectors/:connectorId/resync"


def _path(connector_id: str) -> str:
    return f"/{connector_id}/resync"


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"connectorName": "KB"}, id="incremental"),
        pytest.param({"connectorName": "KB", "fullSync": True}, id="full-sync"),
    ],
)
def test_admin_resyncs_an_active_knowledge_base(
    connectors_client: ConnectorsAuditClient, kb_records: KbRecords, body: dict[str, Any]
) -> None:
    # A knowledge base is listed among the active connectors, so it passes the gate
    # a never-enabled connector fails; the reply only acknowledges the published event.
    resp = connectors_client.post(_path(kb_records["kb_id"]), json=body)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"resyncConnectorResponse": {"success": True}}


def test_resync_while_a_sync_runs_is_a_conflict(
    connectors_client: ConnectorsAuditClient, syncing_connector: str
) -> None:
    resp = connectors_client.post(_path(syncing_connector), json={"connectorName": "Demo"})
    assert resp.status_code == 409, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["code"] == "HTTP_CONFLICT"


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({}, id="connector-name-missing"),
        pytest.param({"connectorName": ""}, id="connector-name-empty"),
        pytest.param({"connectorName": 5}, id="connector-name-not-a-string"),
        pytest.param({"connectorName": "Demo", "fullSync": "yes"}, id="full-sync-not-a-boolean"),
    ],
)
def test_resync_with_an_invalid_body_is_refused_by_the_validator(
    connectors_client: ConnectorsAuditClient, connector_id: str, body: dict[str, Any]
) -> None:
    resp = connectors_client.post(_path(connector_id), json=body)
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "target",
    [
        pytest.param("never-enabled", id="never-enabled-connector"),
        pytest.param(MISSING_CONNECTOR_ID, id="unknown-connector"),
    ],
)
def test_resync_of_a_connector_that_is_not_active_is_400(
    connectors_client: ConnectorsAuditClient, connector_id: str, target: str
) -> None:
    # Only ids in the caller's active-connector list pass, so an unknown id is a 400 too, not a 404.
    requested = connector_id if target == "never-enabled" else target
    resp = connectors_client.post(_path(requested), json={"connectorName": "Demo"})
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["message"] == f"Connector {requested} not allowed"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_resync_of_an_admin_team_connector_is_400(
    second_user: SecondUser, connector_id: str
) -> None:
    resp = request_as(second_user, "POST", _path(connector_id), json={"connectorName": "Demo"})
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_resync_with_an_unsafe_connector_id_is_400(
    connectors_client: ConnectorsAuditClient,
) -> None:
    resp = connectors_client.post(_path(UNSAFE_CONNECTOR_ID), json={"connectorName": "Demo"})
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_resync_without_a_token_is_401(
    connectors_client: ConnectorsAuditClient, connector_id: str
) -> None:
    resp = connectors_client.post(_path(connector_id), auth=False, json={"connectorName": "Demo"})
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_resync_without_a_write_scope_is_403(
    connectors_client: ConnectorsAuditClient,
    connector_id: str,
    token_without_connector_scopes: str,
) -> None:
    resp = connectors_client.post(
        _path(connector_id),
        auth=False,
        headers=bearer(token_without_connector_scopes),
        json={"connectorName": "Demo"},
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
