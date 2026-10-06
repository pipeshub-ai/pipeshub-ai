"""Strict OpenAPI audit of GET /api/v1/connectors/active."""

from __future__ import annotations

from typing import Any

import pytest
from connectors_audit_support import (
    ConnectorsAuditClient,
    KbRecords,
    SeedConnector,
    bearer,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/connectors/active"
PATH = "/active"


def _assert_active_listing(body: dict[str, Any]) -> None:
    assert body["success"] is True
    assert isinstance(body["connectors"], list)
    assert set(body) == {"success", "connectors"}, "this list is not paginated"
    inactive = [c.get("_key") for c in body["connectors"] if c.get("isActive") is not True]
    assert not inactive, f"inactive instances in the active listing: {inactive}"


def test_admin_lists_active_instances(
    connectors_client: ConnectorsAuditClient, connector_id: str, kb_records: KbRecords
) -> None:
    resp = connectors_client.get(PATH)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    body = resp.json()
    _assert_active_listing(body)
    listed = {c["_key"]: c for c in body["connectors"]}
    assert connector_id not in listed, "a never-enabled instance must not be listed as active"
    # Unlike GET /connectors, this list includes knowledge bases, which are always active.
    assert listed[kb_records["kb_id"]]["type"] == "KB"
    assert "config" not in listed[kb_records["kb_id"]]


def test_active_ignores_query_parameters(
    connectors_client: ConnectorsAuditClient,
) -> None:
    # No query validator here: the scope and paging that GET /connectors checks are not even read.
    with outside_request_contract("the route has no query validator and reads no query parameter"):
        resp = connectors_client.get(PATH, params={"scope": "nonsense", "limit": "0", "page": "abc"})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    _assert_active_listing(resp.json())


def test_member_lists_active_instances(
    second_user: SecondUser, member_connector: SeedConnector
) -> None:
    connector_id = member_connector()

    # connector:read only gates OAuth clients; a session member is not refused.
    resp = request_as(second_user, "GET", PATH)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    _assert_active_listing(resp.json())
    assert connector_id not in {c["_key"] for c in resp.json()["connectors"]}


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param(None, id="no-token"),
        pytest.param({"Authorization": "Bearer not.a.jwt"}, id="invalid-token"),
    ],
)
def test_unauthenticated_is_rejected(
    connectors_client: ConnectorsAuditClient, headers: dict[str, str] | None
) -> None:
    resp = connectors_client.get(PATH, auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_active_without_the_connector_read_scope_is_forbidden(
    connectors_client: ConnectorsAuditClient, token_without_connector_scopes: str
) -> None:
    resp = connectors_client.get(PATH, auth=False, headers=bearer(token_without_connector_scopes))
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
