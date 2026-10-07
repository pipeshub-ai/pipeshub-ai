"""Strict OpenAPI audit of PUT /api/v1/connectors/:connectorId/name."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from connectors_audit_support import (
    MALFORMED_CONNECTOR_ID,
    MISSING_CONNECTOR_ID,
    UNSAFE_CONNECTOR_ID,
    ConnectorsAuditClient,
    SeedConnector,
    bearer,
    instance_state,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_spec_forbids_request, assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/connectors/:connectorId/name"


def _new_name() -> str:
    return f"spec-audit renamed {uuid.uuid4().hex[:8]}"


def _path(connector_id: str) -> str:
    return f"/{connector_id}/name"


@pytest.mark.parametrize(
    "suffix",
    [
        pytest.param("", id="short-name"),
        # No length limit is enforced anywhere.
        pytest.param(" " + "x" * 150, id="name-over-100-characters"),
    ],
)
def test_admin_renames_connector_instance(
    connectors_client: ConnectorsAuditClient, seed_connector: SeedConnector, suffix: str
) -> None:
    seeded = seed_connector()
    name = _new_name() + suffix

    # Padded on purpose: the connector service stores and echoes the trimmed name.
    resp = connectors_client.put(_path(seeded), json={"instanceName": f"  {name} "})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    body = resp.json()
    assert body["success"] is True
    assert body["connector"] == {"_key": seeded, "name": name}
    assert instance_state(connectors_client, seeded)["name"] == name


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="instance-name-missing"),
        pytest.param({"instanceName": ""}, id="instance-name-empty"),
        pytest.param({"instanceName": 5}, id="instance-name-not-a-string"),
    ],
)
def test_rename_refused_by_the_validator(
    connectors_client: ConnectorsAuditClient, connector_id: str, payload: dict[str, Any]
) -> None:
    resp = connectors_client.put(_path(connector_id), json=payload)
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_rename_to_only_whitespace_is_refused_by_the_controller(
    connectors_client: ConnectorsAuditClient, connector_id: str
) -> None:
    resp = connectors_client.put(_path(connector_id), json={"instanceName": "   "})
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["message"] == "instanceName is required"
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


def test_rename_to_a_name_in_use_is_400(
    connectors_client: ConnectorsAuditClient, connector_id: str, seed_connector: SeedConnector
) -> None:
    taken = instance_state(connectors_client, seed_connector())["name"]
    resp = connectors_client.put(_path(connector_id), json={"instanceName": taken})
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert "already used" in resp.json()["error"]["message"]


def test_rename_without_token_is_unauthorized(
    connectors_client: ConnectorsAuditClient, connector_id: str
) -> None:
    resp = connectors_client.put(_path(connector_id), auth=False, json={"instanceName": _new_name()})
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_rename_without_the_write_scope_is_forbidden(
    connectors_client: ConnectorsAuditClient,
    connector_id: str,
    token_without_connector_scopes: str,
) -> None:
    resp = connectors_client.put(
        _path(connector_id),
        auth=False,
        headers=bearer(token_without_connector_scopes),
        json={"instanceName": _new_name()},
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_cannot_see_admin_owned_team_connector_to_rename(
    second_user: SecondUser, connector_id: str
) -> None:
    # The registry read gate hides a team connector from a non-admin non-creator,
    # so the handler answers 404 before its own 403 branches are reached.
    resp = request_as(second_user, "PUT", _path(connector_id), json={"instanceName": _new_name()})
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
def test_rename_for_unusable_connector_id(
    connectors_client: ConnectorsAuditClient, requested_id: str, expected_status: int
) -> None:
    resp = connectors_client.put(_path(requested_id), json={"instanceName": "spec-audit nobody"})
    assert resp.status_code == expected_status, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
