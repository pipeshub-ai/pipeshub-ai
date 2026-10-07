"""Strict OpenAPI audit of GET /api/v1/connectors/:connectorId/config."""

from __future__ import annotations

import pytest
from connectors_audit_support import (
    MALFORMED_CONNECTOR_ID,
    MEMBER_TOKEN_AUTH,
    MISSING_CONNECTOR_ID,
    SEED_CONNECTOR_AUTH_TYPE,
    SEED_CONNECTOR_SCOPE,
    SEED_CONNECTOR_TYPE,
    UNSAFE_CONNECTOR_ID,
    ConnectorsAuditClient,
    bearer,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/connectors/:connectorId/config"


def _path(connector_id: str) -> str:
    return f"/{connector_id}/config"


def test_config_as_admin_returns_instance_metadata_and_stored_config(
    connectors_client: ConnectorsAuditClient, connector_id: str
) -> None:
    resp = connectors_client.get(_path(connector_id))
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
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


def test_member_reads_the_saved_auth_of_own_personal_connector(
    second_user: SecondUser, member_configured_connector: str
) -> None:
    resp = request_as(second_user, "GET", _path(member_configured_connector))
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    auth = resp.json()["config"]["config"]["auth"]
    # The saved form values come back for editing, the token included.
    assert {k: auth[k] for k in MEMBER_TOKEN_AUTH} == MEMBER_TOKEN_AUTH, auth
    assert auth["connectorScope"] == "personal", auth


def test_config_of_an_admin_owned_team_instance_is_404_for_a_member(
    second_user: SecondUser, connector_id: str
) -> None:
    # The registry lookup hides a team instance from anyone but an admin or its
    # creator, so the member gets "not found", not 403.
    resp = request_as(second_user, "GET", _path(connector_id))
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_config_of_a_members_personal_instance_is_404_for_an_admin(
    connectors_client: ConnectorsAuditClient, member_configured_connector: str
) -> None:
    # Personal instances are hidden from everyone but their creator, admins
    # included, so the handler's own 403 for a non-creator is never reached.
    resp = connectors_client.get(_path(member_configured_connector))
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("requested_id", "expected_status"),
    [
        pytest.param(MISSING_CONNECTOR_ID, 404, id="unknown-connector"),
        pytest.param(MALFORMED_CONNECTOR_ID, 400, id="id-fails-param-schema"),
        pytest.param(UNSAFE_CONNECTOR_ID, 400, id="unsafe-path-segment"),
    ],
)
def test_config_for_unusable_connector_id(
    connectors_client: ConnectorsAuditClient, requested_id: str, expected_status: int
) -> None:
    resp = connectors_client.get(_path(requested_id))
    assert resp.status_code == expected_status, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_config_without_a_token_is_401(
    connectors_client: ConnectorsAuditClient, connector_id: str
) -> None:
    resp = connectors_client.get(_path(connector_id), auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_config_without_the_read_scope_is_403(
    connectors_client: ConnectorsAuditClient,
    connector_id: str,
    token_without_connector_scopes: str,
) -> None:
    resp = connectors_client.get(
        _path(connector_id), auth=False, headers=bearer(token_without_connector_scopes)
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
