"""Strict OpenAPI audit of the 403 every connectors route gives for a beta type.

Python's check_beta_connector_access refuses beta connector types (Calendar,
Meet, Forms, Slides, Docs, Zendesk, Airtable) while the platform flag
ENABLE_BETA_CONNECTORS is off, which is the default. The per-instance routes
need an existing beta instance, which can only be created with the flag on: the
fixture switches it on just long enough to create one, then off again.
"""

from __future__ import annotations

import base64
import json
from typing import Any, Iterator

import pytest
from connectors_audit_support import (
    OAUTH_BASE_URL,
    ConnectorsAuditClient,
    created_connector_id,
    unique_name,
    wait_until_deleted,
)
from helper.pipeshub_client import PipeshubClient
from helper.vector_rebuild import read_platform_settings, write_platform_settings
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

BETA_FLAG = "ENABLE_BETA_CONNECTORS"
BETA_DISABLED = "Beta connectors are not enabled"
# A beta type offering personal scope and API_TOKEN auth, so nothing external is contacted.
BETA_PERSONAL_TYPE = "Airtable"
BASE = "/api/v1/connectors"


def _set_beta_flag(client: PipeshubClient, enabled: bool) -> None:
    write_platform_settings(client, read_platform_settings(client).with_flag(BETA_FLAG, enabled))


def _message(resp: Any) -> str:
    body = resp.json()
    error = body.get("error")
    if isinstance(error, dict):
        return str(error.get("message", ""))
    return str(body.get("message") or body.get("detail") or error or "")


@pytest.fixture
def beta_connectors_disabled(pipeshub_client: PipeshubClient) -> Iterator[None]:
    was_enabled = read_platform_settings(pipeshub_client).flag(BETA_FLAG)
    if was_enabled:
        _set_beta_flag(pipeshub_client, False)
    try:
        yield
    finally:
        if was_enabled:
            _set_beta_flag(pipeshub_client, True)


@pytest.fixture(scope="module")
def beta_instance(
    pipeshub_client: PipeshubClient, connectors_client: ConnectorsAuditClient
) -> Iterator[str]:
    """A personal Airtable instance of the admin, created while the beta flag was briefly on."""
    was_enabled = read_platform_settings(pipeshub_client).flag(BETA_FLAG)
    connector_id = None
    try:
        _set_beta_flag(pipeshub_client, True)
        try:
            resp = connectors_client.create_instance(
                connectorType=BETA_PERSONAL_TYPE,
                instanceName=unique_name(),
                scope="personal",
                authType="API_TOKEN",
            )
            connector_id = created_connector_id(resp)
        finally:
            _set_beta_flag(pipeshub_client, False)
        yield connector_id
    finally:
        if connector_id:
            _set_beta_flag(pipeshub_client, True)
            try:
                deleted = connectors_client.delete_instance(connector_id)
                assert deleted.status_code == 202, deleted.text[:300]
                wait_until_deleted(connectors_client, connector_id)
            finally:
                _set_beta_flag(pipeshub_client, was_enabled)
        elif was_enabled:
            _set_beta_flag(pipeshub_client, True)


def test_schema_of_a_beta_type_is_forbidden(
    connectors_client: ConnectorsAuditClient, beta_connectors_disabled: None
) -> None:
    resp = connectors_client.get("/registry/Zendesk/schema")
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, f"{BASE}/registry/{{connectorType}}/schema")
    assert BETA_DISABLED in _message(resp)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param(
            {"connectorType": "Zendesk", "scope": "team", "authType": "API_TOKEN"},
            "Beta connector 'Zendesk' cannot be created for team scope in enterprise accounts.",
            id="beta-team-scope",
        ),
        pytest.param(
            {"connectorType": BETA_PERSONAL_TYPE, "scope": "personal", "authType": "API_TOKEN"},
            BETA_DISABLED,
            id="beta-flag-off",
        ),
    ],
)
def test_create_of_a_beta_type_is_forbidden(
    connectors_client: ConnectorsAuditClient,
    beta_connectors_disabled: None,
    cleanup_connectors: list[str],
    body: dict[str, str],
    expected: str,
) -> None:
    resp = connectors_client.create_instance(instanceName=unique_name(), **body)
    if resp.status_code < 300:
        cleanup_connectors.append(created_connector_id(resp))
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, BASE)
    assert expected in _message(resp)


def _callback_state(connector_id: str) -> str:
    # Same encoding Python's _encode_state_with_instance uses for the authorize URL.
    return base64.urlsafe_b64encode(
        json.dumps({"state": "spec-audit", "connector_id": connector_id}).encode()
    ).decode()


# (method, path under the router with {id}, documented route, request kwargs)
INSTANCE_CALLS: list[Any] = [
    pytest.param("GET", "/{id}", "/{connectorId}", {}, id="get-instance"),
    pytest.param("GET", "/{id}/config", "/{connectorId}/config", {}, id="get-config"),
    pytest.param("PUT", "/{id}/name", "/{connectorId}/name", {"json": {"instanceName": "spec-audit beta"}}, id="put-name"),
    pytest.param(
        "GET", "/{id}/oauth/authorize", "/{connectorId}/oauth/authorize",
        {"params": {"baseUrl": OAUTH_BASE_URL}}, id="oauth-authorize",
    ),
    pytest.param("GET", "/{id}/filters", "/{connectorId}/filters", {}, id="get-filters"),
    pytest.param(
        "POST", "/{id}/filters", "/{connectorId}/filters",
        {"json": {"filters": {"spec_audit_filter": ["one"]}}}, id="post-filters",
    ),
    pytest.param(
        "GET", "/{id}/filters/spec_audit/options", "/{connectorId}/filters/{filterKey}/options",
        {}, id="filter-options",
    ),
    pytest.param("POST", "/{id}/toggle", "/{connectorId}/toggle", {"json": {"type": "agent"}}, id="toggle-agent"),
    pytest.param("POST", "/{id}/toggle", "/{connectorId}/toggle", {"json": {"type": "sync"}}, id="toggle-sync"),
    pytest.param("PUT", "/{id}/config", "/{connectorId}/config", {"json": {"baseUrl": "https://spec-audit.invalid"}}, id="put-config"),
    pytest.param("PUT", "/{id}/config/auth", "/{connectorId}/config/auth", {"json": {"auth": {}}}, id="put-config-auth"),
    pytest.param(
        "PUT", "/{id}/config/filters-sync", "/{connectorId}/config/filters-sync",
        {"json": {"filters": {}}}, id="put-config-filters-sync",
    ),
    pytest.param("POST", "/{id}/sync/stop", "/{connectorId}/sync/stop", {}, id="sync-stop"),
    pytest.param("DELETE", "/{id}", "/{connectorId}", {}, id="delete"),
]


@pytest.mark.parametrize(("method", "path", "route", "kwargs"), INSTANCE_CALLS)
def test_routes_of_a_beta_instance_are_forbidden_while_beta_is_off(
    connectors_client: ConnectorsAuditClient,
    beta_instance: str,
    method: str,
    path: str,
    route: str,
    kwargs: dict[str, Any],
) -> None:
    resp = connectors_client.send(method, path.format(id=beta_instance), **kwargs)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, f"{BASE}{route}")
    assert BETA_DISABLED in _message(resp)


def test_callback_for_a_beta_instance_reports_a_server_error(
    connectors_client: ConnectorsAuditClient, beta_instance: str
) -> None:
    # API bug: the callback's catch-all handler turns the beta 403 into a server_error.
    resp = connectors_client.get(
        "/oauth/callback",
        params={"code": "spec-audit", "state": _callback_state(beta_instance), "baseUrl": OAUTH_BASE_URL},
        allow_redirects=False,
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, f"{BASE}/oauth/callback")
    body = resp.json()
    assert body["success"] is False, body
    assert body["error"] == "server_error", body
    assert body["redirectUrl"] == f"{OAUTH_BASE_URL}/connectors/oauth/callback?oauth_error=server_error"
