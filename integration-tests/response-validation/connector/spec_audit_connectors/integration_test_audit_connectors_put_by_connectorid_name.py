"""Strict OpenAPI audit of PUT /api/v1/connectors/:connectorId/name."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from connectors_audit_support import (
    MISSING_CONNECTOR_ID,
    ConnectorsAuditClient,
    SeedConnector,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/connectors/:connectorId/name"


def _new_name() -> str:
    return f"spec-audit renamed {uuid.uuid4().hex[:8]}"


def test_admin_renames_connector_instance(
    connectors_client: ConnectorsAuditClient, seed_connector: SeedConnector
) -> None:
    seeded = seed_connector()
    name = _new_name()

    # Padded on purpose: the connector service stores and echoes the trimmed name.
    resp = connectors_client.put(f"/{seeded}/name", json={"instanceName": f"  {name} "})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    body = resp.json()
    assert body["success"] is True
    assert body["connector"] == {"_key": seeded, "name": name}


def test_rename_without_token_is_unauthorized(
    connectors_client: ConnectorsAuditClient, connector_id: str
) -> None:
    resp = connectors_client.put(
        f"/{connector_id}/name", auth=False, json={"instanceName": _new_name()}
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_member_cannot_see_admin_owned_team_connector_to_rename(
    second_user: SecondUser, connector_id: str
) -> None:
    # The registry read gate hides a team connector from a non-admin non-creator,
    # so the handler answers 404 before its own 403 branches are reached.
    resp = request_as(
        second_user, "PUT", f"/{connector_id}/name", json={"instanceName": _new_name()}
    )
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    ("target", "payload", "expected_status"),
    [
        pytest.param("seeded", {"instanceName": ""}, 400, id="empty-instance-name"),
        pytest.param(
            MISSING_CONNECTOR_ID, {"instanceName": "spec-audit nobody"}, 404,
            id="unknown-connector",
        ),
    ],
)
def test_rename_is_refused(
    connectors_client: ConnectorsAuditClient,
    connector_id: str,
    target: str,
    payload: dict[str, Any],
    expected_status: int,
) -> None:
    requested_id = connector_id if target == "seeded" else target
    resp = connectors_client.put(f"/{requested_id}/name", json=payload)
    assert resp.status_code == expected_status, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
