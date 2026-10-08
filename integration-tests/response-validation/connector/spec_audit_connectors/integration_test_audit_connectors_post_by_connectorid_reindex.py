"""Strict OpenAPI audit of POST /api/v1/connectors/:connectorId/reindex."""

from __future__ import annotations

from typing import Any

import pytest
from connectors_audit_support import (
    MALFORMED_CONNECTOR_ID,
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

ROUTE = "/api/v1/connectors/:connectorId/reindex"


def _path(connector_id: str) -> str:
    return f"/{connector_id}/reindex"


@pytest.mark.parametrize(
    "status_filters",
    [
        pytest.param(["FAILED"], id="known-status"),
        # Neither the validator nor the connector service checks the values; an
        # unknown one simply matches no record.
        pytest.param(["SPEC_AUDIT_NOT_A_STATUS"], id="unknown-status"),
    ],
)
def test_admin_reindexes_a_knowledge_base(
    connectors_client: ConnectorsAuditClient, kb_records: KbRecords, status_filters: list[str]
) -> None:
    kb_id = kb_records["kb_id"]
    resp = connectors_client.post(_path(kb_id), json={"statusFilters": status_filters})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {
        "success": True,
        "message": f"Reindex initiated for connector {kb_id}",
        "connectorId": kb_id,
        "connector": "kb",
        "eventPublished": True,
    }


def test_reindex_of_a_disabled_connector_is_a_conflict(
    connectors_client: ConnectorsAuditClient, connector_id: str
) -> None:
    resp = connectors_client.post(_path(connector_id), json={"statusFilters": ["FAILED"]})
    assert resp.status_code == 409, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert "currently disabled" in resp.json()["error"]["message"]


def test_reindex_without_a_body_is_accepted_by_the_validator(
    connectors_client: ConnectorsAuditClient, connector_id: str
) -> None:
    # The body is optional; the disabled instance then answers 409 instead of reindexing anything.
    resp = connectors_client.post(_path(connector_id))
    assert resp.status_code == 409, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"statusFilters": "FAILED"}, id="status-filters-not-a-list"),
        pytest.param({"statusFilters": [1]}, id="status-filter-not-a-string"),
    ],
)
def test_reindex_with_an_invalid_body_is_refused_by_the_validator(
    connectors_client: ConnectorsAuditClient, connector_id: str, body: dict[str, Any]
) -> None:
    resp = connectors_client.post(_path(connector_id), json=body)
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_reindex_without_a_token_is_401(
    connectors_client: ConnectorsAuditClient, connector_id: str
) -> None:
    resp = connectors_client.post(_path(connector_id), auth=False, json={})
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_reindex_without_a_sync_or_kb_write_scope_is_403(
    connectors_client: ConnectorsAuditClient,
    connector_id: str,
    token_without_connector_scopes: str,
) -> None:
    resp = connectors_client.post(
        _path(connector_id), auth=False, headers=bearer(token_without_connector_scopes), json={}
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_without_a_role_on_the_knowledge_base_is_403(
    second_user: SecondUser, kb_records: KbRecords
) -> None:
    resp = request_as(second_user, "POST", _path(kb_records["kb_id"]), json={"statusFilters": ["FAILED"]})
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_is_told_an_admin_team_connector_does_not_exist(
    second_user: SecondUser, connector_id: str
) -> None:
    resp = request_as(second_user, "POST", _path(connector_id), json={})
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("requested_id", "expected_status"),
    [
        pytest.param(MISSING_CONNECTOR_ID, 404, id="unknown-connector"),
        # No id pattern on this route: a malformed id is simply not found.
        pytest.param(MALFORMED_CONNECTOR_ID, 404, id="id-the-instance-routes-reject"),
        pytest.param(UNSAFE_CONNECTOR_ID, 400, id="unsafe-path-segment"),
    ],
)
def test_reindex_for_unusable_connector_id(
    connectors_client: ConnectorsAuditClient, requested_id: str, expected_status: int
) -> None:
    resp = connectors_client.post(_path(requested_id), json={})
    assert resp.status_code == expected_status, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
