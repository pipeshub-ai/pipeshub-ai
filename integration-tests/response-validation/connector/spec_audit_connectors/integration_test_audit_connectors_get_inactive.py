"""Strict OpenAPI audit of GET /api/v1/connectors/inactive."""

from __future__ import annotations

from typing import Any

import pytest
from connectors_audit_support import (
    PERSONAL_CONNECTOR_TYPE,
    SEED_CONNECTOR_TYPE,
    ConnectorsAuditClient,
    KbRecords,
    SeedConnector,
    bearer,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/connectors/inactive"


def _seeded(body: dict[str, Any], connector_id: str) -> dict[str, Any] | None:
    return next((c for c in body["connectors"] if c.get("_key") == connector_id), None)


def test_inactive_lists_unsynced_seeded_instance(
    connectors_client: ConnectorsAuditClient, connector_id: str, kb_records: KbRecords
) -> None:
    resp = connectors_client.get("/inactive")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    body = resp.json()
    assert body["success"] is True
    assert set(body) == {"success", "connectors"}, "this list is not paginated"
    assert all(c["isActive"] is False for c in body["connectors"])
    seeded = _seeded(body, connector_id)
    assert seeded is not None, "a never-synced instance must be listed as inactive"
    assert seeded["type"] == SEED_CONNECTOR_TYPE
    assert seeded["scope"] == "team"
    assert "config" not in seeded
    # A knowledge base is always active, so it is never in this list.
    assert _seeded(body, kb_records["kb_id"]) is None


def test_inactive_ignores_query_parameters(
    connectors_client: ConnectorsAuditClient, connector_id: str
) -> None:
    # No validator on this route: paging/scope filters that /configured honours are dropped.
    with outside_request_contract("the route has no query validator and reads no query parameter"):
        resp = connectors_client.get(
            "/inactive", params={"scope": "personal", "limit": "0", "page": "abc"}
        )
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert _seeded(resp.json(), connector_id) is not None


def test_member_sees_team_and_own_personal_inactive_instances(
    second_user: SecondUser, connector_id: str, member_connector: SeedConnector
) -> None:
    personal_id = member_connector()

    resp = request_as(second_user, "GET", "/inactive")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    body = resp.json()
    # Listed here although GET /connectors/{connectorId} answers this member 404 for it.
    assert _seeded(body, connector_id) is not None
    personal = _seeded(body, personal_id)
    assert personal is not None
    assert personal["type"] == PERSONAL_CONNECTOR_TYPE
    assert personal["scope"] == "personal"


def test_admin_does_not_see_a_members_personal_instance(
    connectors_client: ConnectorsAuditClient, member_connector: SeedConnector
) -> None:
    personal_id = member_connector()

    resp = connectors_client.get("/inactive")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _seeded(resp.json(), personal_id) is None


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param(None, id="no-token"),
        pytest.param({"Authorization": "Bearer not.a.jwt"}, id="invalid-token"),
    ],
)
def test_inactive_requires_authentication(
    connectors_client: ConnectorsAuditClient, headers: dict[str, str] | None
) -> None:
    resp = connectors_client.get("/inactive", auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_inactive_without_the_connector_read_scope_is_forbidden(
    connectors_client: ConnectorsAuditClient, token_without_connector_scopes: str
) -> None:
    resp = connectors_client.get(
        "/inactive", auth=False, headers=bearer(token_without_connector_scopes)
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
