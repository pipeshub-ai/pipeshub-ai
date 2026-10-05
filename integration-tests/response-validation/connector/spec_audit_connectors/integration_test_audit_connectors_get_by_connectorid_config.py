"""Strict OpenAPI audit of GET /api/v1/connectors/:connectorId/config."""

from __future__ import annotations

import pytest
from connectors_audit_support import (
    MALFORMED_CONNECTOR_ID,
    MISSING_CONNECTOR_ID,
    SEED_CONNECTOR_AUTH_TYPE,
    SEED_CONNECTOR_SCOPE,
    SEED_CONNECTOR_TYPE,
    ConnectorsAuditClient,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/connectors/:connectorId/config"


def _path(connector_id: str) -> str:
    return f"/{connector_id}/config"


def test_config_as_admin_returns_instance_metadata_and_stored_config(
    connectors_client: ConnectorsAuditClient, connector_id: str
) -> None:
    resp = connectors_client.get(_path(connector_id))
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    body = resp.json()
    assert body["success"] is True, body
    config = body["config"]
    # Python names this one key in snake_case; every other key is camelCase.
    assert config["connector_id"] == connector_id, config
    assert config["type"] == SEED_CONNECTOR_TYPE, config
    assert config["scope"] == SEED_CONNECTOR_SCOPE, config
    assert config["authType"] == SEED_CONNECTOR_AUTH_TYPE, config
    assert isinstance(config["config"], dict), config
    assert not {"credentials", "oauth"} & config["config"].keys(), config


def test_config_of_an_admin_owned_team_instance_is_404_for_a_member(
    second_user: SecondUser, connector_id: str
) -> None:
    # The registry lookup hides a team instance from anyone but an admin or its
    # creator, so the member gets "not found", not 403.
    resp = request_as(second_user, "GET", _path(connector_id))
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_config_of_an_unknown_instance_is_404(
    connectors_client: ConnectorsAuditClient,
) -> None:
    resp = connectors_client.get(_path(MISSING_CONNECTOR_ID))
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_config_with_a_malformed_connector_id_is_400(
    connectors_client: ConnectorsAuditClient,
) -> None:
    resp = connectors_client.get(_path(MALFORMED_CONNECTOR_ID))
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_config_without_a_token_is_401(
    connectors_client: ConnectorsAuditClient, connector_id: str
) -> None:
    resp = connectors_client.get(_path(connector_id), auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
