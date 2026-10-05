"""Strict OpenAPI audit of GET /api/v1/connectors/inactive."""

from __future__ import annotations

from typing import Any

import pytest
from connectors_audit_support import (
    SEED_CONNECTOR_TYPE,
    ConnectorsAuditClient,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/connectors/inactive"


def _seeded(body: dict[str, Any], connector_id: str) -> dict[str, Any] | None:
    return next((c for c in body["connectors"] if c.get("_key") == connector_id), None)


def test_inactive_lists_unsynced_seeded_instance(
    connectors_client: ConnectorsAuditClient, connector_id: str
) -> None:
    resp = connectors_client.get("/inactive")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    body = resp.json()
    assert body["success"] is True
    assert all(c["isActive"] is False for c in body["connectors"])
    seeded = _seeded(body, connector_id)
    assert seeded is not None, "a never-synced instance must be listed as inactive"
    assert seeded["type"] == SEED_CONNECTOR_TYPE
    assert seeded["scope"] == "team"
    assert "config" not in seeded


def test_inactive_ignores_unknown_query_params(
    connectors_client: ConnectorsAuditClient, connector_id: str
) -> None:
    # No validator on this route: paging/scope filters that /configured honours are dropped.
    resp = connectors_client.get(
        "/inactive", params={"scope": "personal", "limit": "0", "page": "abc"}
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert _seeded(resp.json(), connector_id) is not None


def test_member_sees_team_scoped_inactive_instance(
    second_user: SecondUser, connector_id: str
) -> None:
    resp = request_as(second_user, "GET", "/inactive")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert _seeded(resp.json(), connector_id) is not None


def test_inactive_requires_authentication(
    connectors_client: ConnectorsAuditClient,
) -> None:
    resp = connectors_client.get("/inactive", auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
