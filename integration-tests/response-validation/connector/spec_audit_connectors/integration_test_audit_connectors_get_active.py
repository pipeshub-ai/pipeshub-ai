"""Strict OpenAPI audit of GET /api/v1/connectors/active."""

from __future__ import annotations

from typing import Any

import pytest
from connectors_audit_support import ConnectorsAuditClient, request_as
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/connectors/active"
PATH = "/active"


def _assert_active_listing(body: dict[str, Any]) -> None:
    assert body["success"] is True
    assert isinstance(body["connectors"], list)
    inactive = [c.get("connectorId") for c in body["connectors"] if c.get("isActive") is not True]
    assert not inactive, f"inactive instances in the active listing: {inactive}"


def test_admin_lists_active_instances(
    connectors_client: ConnectorsAuditClient, connector_id: str
) -> None:
    # The route has no query validator, so unknown parameters are ignored, not refused.
    resp = connectors_client.get(PATH, params={"scope": "nonsense", "limit": "0"})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    body = resp.json()
    _assert_active_listing(body)
    listed = {c.get("connectorId") for c in body["connectors"]}
    assert connector_id not in listed, "a never-enabled instance must not be listed as active"


def test_member_lists_active_instances(second_user: SecondUser) -> None:
    # connector:read only gates OAuth clients; a session member is not refused.
    resp = request_as(second_user, "GET", PATH)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    _assert_active_listing(resp.json())


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
    assert_strict_openapi_response(resp, ROUTE)
