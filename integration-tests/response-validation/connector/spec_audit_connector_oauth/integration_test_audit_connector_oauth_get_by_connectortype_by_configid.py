"""Strict OpenAPI audit of GET /api/v1/oauth/:connectorType/:configId."""

from __future__ import annotations

import pytest
from connector_oauth_audit_support import (
    MISSING_CONFIG_ID,
    OAUTH_BASE,
    SEED_CONNECTOR_TYPE,
    UNSAFE_PATH_SEGMENT,
    ConnectorOAuthClient,
    SeedOAuthConfig,
    bearer,
    request_as,
)
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/oauth/:connectorType/:configId"


def test_admin_gets_config_with_credentials(
    connector_oauth_client: ConnectorOAuthClient, seed_oauth_config: SeedOAuthConfig
) -> None:
    cfg = seed_oauth_config()

    resp = connector_oauth_client.fetch(cfg["connector_type"], cfg["id"])
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    body = resp.json()
    assert body["success"] is True
    oauth_config = body["oauthConfig"]
    assert oauth_config["_id"] == cfg["id"]
    assert oauth_config["oauthInstanceName"] == cfg["name"]
    assert oauth_config["connectorType"] == cfg["connector_type"]
    assert oauth_config["config"]["clientId"] == cfg["body"]["config"]["clientId"]


def test_member_gets_config_without_credentials(
    second_user: SecondUser, seed_oauth_config: SeedOAuthConfig
) -> None:
    # No admin gate in Node or Python: a member gets the metadata, minus the `config` block.
    cfg = seed_oauth_config()

    resp = request_as(second_user, "GET", f"/{cfg['connector_type']}/{cfg['id']}")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    oauth_config = resp.json()["oauthConfig"]
    assert oauth_config["_id"] == cfg["id"]
    assert oauth_config["oauthInstanceName"] == cfg["name"]
    assert "config" not in oauth_config


def test_unknown_config_id_is_not_found(connector_oauth_client: ConnectorOAuthClient) -> None:
    resp = connector_oauth_client.fetch(SEED_CONNECTOR_TYPE, MISSING_CONFIG_ID)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_unsafe_config_id_is_rejected(connector_oauth_client: ConnectorOAuthClient) -> None:
    resp = connector_oauth_client.fetch(SEED_CONNECTOR_TYPE, UNSAFE_PATH_SEGMENT)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_token_without_connector_read_is_forbidden(
    pipeshub_client: PipeshubClient, token_without_connector_read: str
) -> None:
    resp = pipeshub_client.request(
        "GET",
        f"{OAUTH_BASE}/{SEED_CONNECTOR_TYPE}/{MISSING_CONFIG_ID}",
        auth=False,
        headers=bearer(token_without_connector_read),
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
