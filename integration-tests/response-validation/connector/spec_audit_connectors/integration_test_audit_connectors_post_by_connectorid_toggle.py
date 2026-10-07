"""Strict OpenAPI audit of POST /api/v1/connectors/:connectorId/toggle."""

from __future__ import annotations

from typing import Any, Callable

import pytest
from connectors_audit_support import (
    MISSING_CONNECTOR_ID,
    UNSAFE_CONNECTOR_ID,
    ConnectorsAuditClient,
    SeedConnector,
    bearer,
    instance_state,
    is_settled,
    is_syncing,
    request_as,
    wait_for_state,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/connectors/:connectorId/toggle"


def _path(connector_id: str) -> str:
    return f"/{connector_id}/toggle"


def _toggled(connector_id: str, kind: str) -> dict[str, Any]:
    return {"success": True, "message": f"Connector instance {connector_id} {kind} toggled successfully"}


def test_admin_enables_then_disables_sync(
    connectors_client: ConnectorsAuditClient, seed_connector: SeedConnector
) -> None:
    # Demo contacts nothing external; its sync is stopped before it writes much.
    demo = seed_connector()
    resp = connectors_client.post(_path(demo), json={"type": "sync", "fullSync": True})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == _toggled(demo, "sync")
    assert wait_for_state(connectors_client, demo, is_syncing, "a running sync")["isActive"] is True

    stop = connectors_client.post(f"/{demo}/sync/stop")
    assert stop.status_code == 200, stop.text[:300]
    wait_for_state(connectors_client, demo, is_settled, "the end of its sync")

    off = connectors_client.post(_path(demo), json={"type": "sync"})
    assert off.status_code == 200, off.text[:500]
    assert_strict_openapi_exchange(off, ROUTE)
    assert off.json() == _toggled(demo, "sync")
    assert instance_state(connectors_client, demo)["isActive"] is False


def test_admin_enables_then_disables_agent_use(
    connectors_client: ConnectorsAuditClient, agent_capable_connector: str
) -> None:
    for expected in (True, False):
        resp = connectors_client.post(_path(agent_capable_connector), json={"type": "agent"})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
        assert resp.json() == _toggled(agent_capable_connector, "agent")
        assert instance_state(connectors_client, agent_capable_connector)["isAgentActive"] is expected


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({}, id="type-missing"),
        pytest.param({"type": "index"}, id="type-not-in-enum"),
        pytest.param({"type": "agent", "fullSync": "yes"}, id="full-sync-not-a-boolean"),
        pytest.param({"type": "sync", "deviceId": ""}, id="device-id-empty"),
        pytest.param({"type": "sync", "deviceId": "d" * 256}, id="device-id-too-long"),
        pytest.param({"type": "sync", "deviceName": "n" * 256}, id="device-name-too-long"),
    ],
)
def test_toggle_refused_by_the_validator(
    connectors_client: ConnectorsAuditClient, connector_id: str, body: dict[str, Any]
) -> None:
    resp = connectors_client.post(_path(connector_id), json=body)
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("create", "toggle_type", "reason"),
    [
        pytest.param({}, "agent", "This connector does not support agent functionality", id="agent-not-supported"),
        # A new instance counts as configured, so the missing credentials only
        # surface when the connector is started.
        pytest.param(
            {"connectorType": "BookStack", "authType": "API_TOKEN"},
            "sync",
            "Failed to initialize connector. Please check your credentials and configuration.",
            id="sync-without-credentials",
        ),
        pytest.param(
            {"connectorType": "GitLab", "authType": "OAUTH"},
            "sync",
            "Connector cannot be enabled until OAuth authentication is completed",
            id="oauth-not-authenticated",
        ),
    ],
)
def test_toggle_refused_by_the_connector_service(
    connectors_client: ConnectorsAuditClient,
    seed_connector: SeedConnector,
    create: dict[str, str],
    toggle_type: str,
    reason: str,
) -> None:
    resp = connectors_client.post(_path(seed_connector(**create)), json={"type": toggle_type})
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["message"] == reason


@pytest.mark.parametrize(
    ("body", "code"),
    [
        pytest.param({"type": "sync"}, "DESKTOP_UNCLAIMED", id="no-device-claims-it"),
        # The device is not connected to this stack, so it cannot claim the connector.
        pytest.param(
            {"type": "sync", "deviceId": "spec-audit-device", "deviceName": "SPEC-AUDIT"},
            "DESKTOP_OFFLINE",
            id="device-offline",
        ),
    ],
)
def test_local_fs_sync_needs_its_desktop(
    connectors_client: ConnectorsAuditClient, local_fs_connector: str, body: dict[str, Any], code: str
) -> None:
    resp = connectors_client.post(_path(local_fs_connector), json=body)
    assert resp.status_code == 409, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    refusal = resp.json()
    assert refusal["success"] is False
    assert refusal["code"] == code
    assert refusal["details"] == {"code": code, "connectorId": local_fs_connector, "retryable": True}


def test_agent_toggle_of_an_unknown_connector_is_404(
    connectors_client: ConnectorsAuditClient,
) -> None:
    resp = connectors_client.post(_path(MISSING_CONNECTOR_ID), json={"type": "agent"})
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_sync_toggle_of_an_unknown_connector_is_500(
    connectors_client: ConnectorsAuditClient,
) -> None:
    # API bug: the gateway reads the instance first for sync toggles and reports
    # a missing one as its own failure instead of 404.
    resp = connectors_client.post(_path(MISSING_CONNECTOR_ID), json={"type": "sync"})
    assert resp.status_code == 500, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["message"] == f"Failed to fetch connector {MISSING_CONNECTOR_ID} state"


@pytest.mark.parametrize(
    ("toggle_type", "expected_status"),
    [
        pytest.param("agent", 404, id="agent"),
        # Same gateway pre-read as for an unknown id.
        pytest.param("sync", 500, id="sync"),
    ],
)
def test_member_toggle_of_an_admin_team_connector(
    second_user: SecondUser, connector_id: str, toggle_type: str, expected_status: int
) -> None:
    resp = request_as(second_user, "POST", _path(connector_id), json={"type": toggle_type})
    assert resp.status_code == expected_status, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_toggle_with_an_unsafe_connector_id_is_400(
    connectors_client: ConnectorsAuditClient,
) -> None:
    resp = connectors_client.post(_path(UNSAFE_CONNECTOR_ID), json={"type": "agent"})
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_toggle_without_a_token_is_401(
    connectors_client: ConnectorsAuditClient, connector_id: str
) -> None:
    resp = connectors_client.post(_path(connector_id), auth=False, json={"type": "agent"})
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_toggle_without_the_sync_scope_is_403(
    connectors_client: ConnectorsAuditClient,
    connector_id: str,
    token_without_connector_scopes: str,
) -> None:
    resp = connectors_client.post(
        _path(connector_id), auth=False, headers=bearer(token_without_connector_scopes), json={"type": "agent"}
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_toggle_with_only_the_write_scope_is_403(
    connectors_client: ConnectorsAuditClient,
    connector_id: str,
    token_with_scopes: Callable[..., str],
) -> None:
    resp = connectors_client.post(
        _path(connector_id), auth=False, headers=bearer(token_with_scopes("connector:write")), json={"type": "agent"}
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert "connector:sync" in resp.text, resp.text[:500]
