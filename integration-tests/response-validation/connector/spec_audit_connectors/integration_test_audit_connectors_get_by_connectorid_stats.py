"""Strict OpenAPI audit of GET /api/v1/connectors/:connectorId/stats."""

from __future__ import annotations

from typing import Callable

import pytest
from connectors_audit_support import (
    MISSING_CONNECTOR_ID,
    UNSAFE_CONNECTOR_ID,
    ConnectorsAuditClient,
    KbRecords,
    SeedConnector,
    bearer,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/connectors/:connectorId/stats"


def test_admin_reads_stats_of_never_synced_connector(
    connectors_client: ConnectorsAuditClient, connector_id: str
) -> None:
    resp = connectors_client.get(f"/{connector_id}/stats")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    body = resp.json()
    assert body["success"] is True
    data = body["data"]
    assert data["connectorId"] == connector_id
    assert data["origin"] == "CONNECTOR"
    assert data["stats"]["total"] == 0
    assert set(data["stats"]["indexingStatus"].values()) == {0}
    assert data["byRecordType"] == []


def test_stats_of_a_knowledge_base_count_its_records_by_type(
    connectors_client: ConnectorsAuditClient, kb_records: KbRecords
) -> None:
    resp = connectors_client.get(f"/{kb_records['kb_id']}/stats")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    data = resp.json()["data"]
    assert data["connectorId"] == kb_records["kb_id"]
    # A knowledge base reports COLLECTION where a connector reports CONNECTOR.
    assert data["origin"] == "COLLECTION"
    assert data["stats"]["total"] == 2
    statuses = data["stats"]["indexingStatus"]
    assert sum(statuses.values()) == 2
    assert statuses["FILE_TYPE_NOT_SUPPORTED"] == 1
    assert statuses[kb_records["text_record_status"]] == 1
    assert data["byRecordType"] == [
        {"recordType": "FILE", "total": 2, "indexingStatus": statuses}
    ]


def test_member_reads_stats_of_own_personal_connector(
    second_user: SecondUser, member_connector: SeedConnector
) -> None:
    connector_id = member_connector()

    resp = request_as(second_user, "GET", f"/{connector_id}/stats")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["data"]["connectorId"] == connector_id


def test_stats_ignore_query_parameters(
    connectors_client: ConnectorsAuditClient, connector_id: str
) -> None:
    # The controller builds the upstream query itself, so a client connector_id cannot redirect it.
    with outside_request_contract("the route validates only the path parameter"):
        resp = connectors_client.get(
            f"/{connector_id}/stats", params={"connector_id": MISSING_CONNECTOR_ID}
        )
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["data"]["connectorId"] == connector_id


def test_stats_without_token_is_unauthorized(
    connectors_client: ConnectorsAuditClient, connector_id: str
) -> None:
    resp = connectors_client.get(f"/{connector_id}/stats", auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_stats_with_neither_read_scope_is_forbidden(
    connectors_client: ConnectorsAuditClient,
    connector_id: str,
    token_without_connector_scopes: str,
) -> None:
    resp = connectors_client.get(
        f"/{connector_id}/stats", auth=False, headers=bearer(token_without_connector_scopes)
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


NOT_OPENABLE_MESSAGE = (
    "This connector was removed, or you no longer have access. Refresh the page and try again."
)


def test_member_is_refused_stats_of_admin_owned_team_connector(
    second_user: SecondUser, connector_id: str
) -> None:
    # Stats share the read gate of GET /connectors/{connectorId}: a connector the
    # caller cannot open is not found, so its existence is not confirmed.
    resp = request_as(second_user, "GET", f"/{connector_id}/stats")
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["message"] == NOT_OPENABLE_MESSAGE


def test_admin_stats_of_a_members_personal_connector(
    connectors_client: ConnectorsAuditClient, member_connector: SeedConnector
) -> None:
    resp = connectors_client.get(f"/{member_connector()}/stats")
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["message"] == NOT_OPENABLE_MESSAGE


@pytest.mark.parametrize(
    ("requested_id", "expected_status"),
    [
        pytest.param(MISSING_CONNECTOR_ID, 404, id="unknown-connector"),
        # No id pattern on this route: a malformed id is simply not found.
        pytest.param("bad.id", 404, id="id-the-instance-routes-reject"),
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
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("scope", ["connector:read", "kb:read"])
def test_stats_take_either_read_scope(
    connectors_client: ConnectorsAuditClient,
    connector_id: str,
    token_with_scopes: Callable[..., str],
    scope: str,
) -> None:
    resp = connectors_client.get(f"/{connector_id}/stats", auth=False, headers=bearer(token_with_scopes(scope)))
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
