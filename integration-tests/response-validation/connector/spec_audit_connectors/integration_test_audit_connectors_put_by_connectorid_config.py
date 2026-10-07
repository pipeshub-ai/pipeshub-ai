"""Strict OpenAPI audit of PUT /api/v1/connectors/:connectorId/config."""

from __future__ import annotations

from typing import Any

import pytest
from connectors_audit_support import (
    MALFORMED_CONNECTOR_ID,
    MISSING_CONNECTOR_ID,
    UNSAFE_CONNECTOR_ID,
    ConnectorsAuditClient,
    SeedConnector,
    bearer,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_spec_forbids_request, assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/connectors/:connectorId/config"

# No "sync" section: a saved sync block makes Node reconcile the crawl schedule.
INDEXING_ONLY_BODY: dict[str, Any] = {"filters": {"indexing": {}}}


def _path(connector_id: str) -> str:
    return f"/{connector_id}/config"


def test_update_config_as_admin_returns_the_merged_config(
    connectors_client: ConnectorsAuditClient,
    seed_connector: SeedConnector,
) -> None:
    resp = connectors_client.put(_path(seed_connector()), json=INDEXING_ONLY_BODY)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert body["success"] is True, body
    assert body["config"]["filters"]["indexing"] == {}, body
    assert body["message"] == "Configuration saved successfully.", body


def test_saving_auth_clears_credentials_and_oauth_state(
    connectors_client: ConnectorsAuditClient, seed_connector: SeedConnector
) -> None:
    resp = connectors_client.put(
        _path(seed_connector()),
        json={"auth": {"connectorScope": "team"}, "sync": {"selectedStrategy": "MANUAL"}},
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    config = resp.json()["config"]
    assert config["credentials"] is None and config["oauth"] is None, config
    assert config["auth"]["connectorType"] == "Demo", config
    assert config["sync"] == {"selectedStrategy": "MANUAL"}, config


def test_an_empty_body_saves_nothing(
    connectors_client: ConnectorsAuditClient, seed_connector: SeedConnector
) -> None:
    resp = connectors_client.put(_path(seed_connector()), json={})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["config"] == {}


@pytest.mark.parametrize(
    ("body", "saved_sync"),
    [
        # Nothing checks the strategy name; it is stored as sent.
        pytest.param({"sync": {"selectedStrategy": "SPEC_AUDIT"}}, {"selectedStrategy": "SPEC_AUDIT"}, id="unknown-strategy"),
        # A section that is not an object is skipped without an error.
        pytest.param({"sync": "MANUAL"}, None, id="sync-not-an-object"),
    ],
)
def test_sections_are_not_validated(
    connectors_client: ConnectorsAuditClient,
    seed_connector: SeedConnector,
    body: dict[str, Any],
    saved_sync: dict[str, Any] | None,
) -> None:
    with outside_request_contract("the sync section is outside its documented shape on purpose"):
        resp = connectors_client.put(_path(seed_connector()), json=body)
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["config"].get("sync") == saved_sync


def test_update_config_of_an_active_connector_is_400(
    connectors_client: ConnectorsAuditClient, syncing_connector: str
) -> None:
    resp = connectors_client.put(_path(syncing_connector), json=INDEXING_ONLY_BODY)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert "while connector is active" in resp.json()["error"]["message"]


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"baseUrl": 123}, id="node-zod-baseurl-not-a-string"),
    ],
)
def test_update_config_refused_by_the_validator(
    connectors_client: ConnectorsAuditClient, connector_id: str, body: dict[str, Any]
) -> None:
    resp = connectors_client.put(_path(connector_id), json=body)
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"filters": {"sync": None}}, id="filters-sync-not-an-object"),
        pytest.param({"filters": {"indexing": []}}, id="filters-indexing-not-an-object"),
    ],
)
def test_update_config_refused_by_the_connector_service(
    connectors_client: ConnectorsAuditClient, connector_id: str, body: dict[str, Any]
) -> None:
    resp = connectors_client.put(_path(connector_id), json=body)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


def test_auth_type_cannot_be_changed(
    connectors_client: ConnectorsAuditClient, connector_id: str
) -> None:
    resp = connectors_client.put(_path(connector_id), json={"auth": {"authType": "OAUTH"}})
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert "Cannot change auth type" in resp.json()["error"]["message"]


def test_update_config_without_a_token_is_401(
    connectors_client: ConnectorsAuditClient,
    connector_id: str,
) -> None:
    resp = connectors_client.put(
        _path(connector_id), auth=False, json=INDEXING_ONLY_BODY
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_config_without_the_write_scope_is_403(
    connectors_client: ConnectorsAuditClient,
    connector_id: str,
    token_without_connector_scopes: str,
) -> None:
    resp = connectors_client.put(
        _path(connector_id), auth=False, headers=bearer(token_without_connector_scopes), json=INDEXING_ONLY_BODY
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_config_of_a_team_connector_as_member_is_404(
    second_user: SecondUser,
    connector_id: str,
) -> None:
    # The registry hides a team instance from a non-admin who did not create it,
    # so the handler's own 403 "Only administrators..." branch is never reached.
    resp = request_as(
        second_user, "PUT", _path(connector_id), json=INDEXING_ONLY_BODY
    )
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
def test_update_config_for_unusable_connector_id(
    connectors_client: ConnectorsAuditClient, requested_id: str, expected_status: int
) -> None:
    resp = connectors_client.put(_path(requested_id), json=INDEXING_ONLY_BODY)
    assert resp.status_code == expected_status, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_config_takes_a_base_url_that_is_not_a_url(
    connectors_client: ConnectorsAuditClient, connector_id: str
) -> None:
    # The validator is z.string().optional(): any string is accepted.
    resp = connectors_client.put(_path(connector_id), json={"baseUrl": "not a url"})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
