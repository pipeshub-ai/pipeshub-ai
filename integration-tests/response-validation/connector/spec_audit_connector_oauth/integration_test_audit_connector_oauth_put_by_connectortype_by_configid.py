"""Strict OpenAPI audit of PUT /api/v1/oauth/:connectorType/:configId."""

from __future__ import annotations

import pytest
from connector_oauth_audit_support import (
    MISSING_CONFIG_ID,
    SEED_CONNECTOR_TYPE,
    ConnectorOAuthClient,
    SeedOAuthConfig,
    request_as,
    unique_name,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/oauth/:connectorType/:configId"


def test_admin_renames_config_and_merges_new_fields(
    connector_oauth_client: ConnectorOAuthClient, seed_oauth_config: SeedOAuthConfig
) -> None:
    cfg = seed_oauth_config()
    new_name = unique_name()

    resp = connector_oauth_client.update(
        cfg["connector_type"],
        cfg["id"],
        {"oauthInstanceName": new_name, "config": {"clientId": "spec-audit-client-id-2"}},
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    body = resp.json()
    assert body["success"] is True
    updated = body["oauthConfig"]
    assert updated["_id"] == cfg["id"]
    assert updated["oauthInstanceName"] == new_name
    assert updated["connectorType"] == cfg["connector_type"]
    # The reply carries only the essential fields: secrets never come back from an update.
    assert "config" not in updated


def test_rename_to_a_name_already_taken_is_a_conflict(
    connector_oauth_client: ConnectorOAuthClient, seed_oauth_config: SeedOAuthConfig
) -> None:
    taken = seed_oauth_config()
    cfg = seed_oauth_config()

    resp = connector_oauth_client.update(
        cfg["connector_type"], cfg["id"], {"oauthInstanceName": taken["name"]}
    )
    assert resp.status_code == 409, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_unknown_config_id_is_not_found(connector_oauth_client: ConnectorOAuthClient) -> None:
    resp = connector_oauth_client.update(
        SEED_CONNECTOR_TYPE, MISSING_CONFIG_ID, {"oauthInstanceName": unique_name()}
    )
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_body_with_neither_name_nor_config_is_rejected(
    connector_oauth_client: ConnectorOAuthClient,
) -> None:
    # Every body field is optional in the zod schema; the controller refuses this before proxying.
    resp = connector_oauth_client.update(
        SEED_CONNECTOR_TYPE, MISSING_CONFIG_ID, {"baseUrl": "http://localhost:3001"}
    )
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_member_cannot_update(
    second_user: SecondUser, seed_oauth_config: SeedOAuthConfig
) -> None:
    cfg = seed_oauth_config()

    # Node has no admin gate here; the 403 is Python's, relayed by handleBackendError.
    resp = request_as(
        second_user,
        "PUT",
        f"/{cfg['connector_type']}/{cfg['id']}",
        json={"oauthInstanceName": unique_name()},
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
