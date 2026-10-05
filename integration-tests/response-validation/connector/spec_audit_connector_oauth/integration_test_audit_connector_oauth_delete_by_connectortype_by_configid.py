"""Strict OpenAPI audit of DELETE /api/v1/oauth/:connectorType/:configId."""

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
)
from helper.pipeshub_client import PipeshubClient
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/oauth/:connectorType/:configId"


def test_delete_removes_seeded_config(
    connector_oauth_client: ConnectorOAuthClient,
    seed_oauth_config: SeedOAuthConfig,
) -> None:
    cfg = seed_oauth_config()

    resp = connector_oauth_client.remove(cfg["connector_type"], cfg["id"])
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    body = resp.json()
    assert body["success"] is True
    assert cfg["id"] in body["message"]

    gone = connector_oauth_client.fetch(cfg["connector_type"], cfg["id"])
    assert gone.status_code == 404, gone.text[:500]


def test_delete_unknown_config_is_not_found(
    connector_oauth_client: ConnectorOAuthClient,
) -> None:
    resp = connector_oauth_client.remove(SEED_CONNECTOR_TYPE, MISSING_CONFIG_ID)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_delete_rejects_unsafe_config_id(
    connector_oauth_client: ConnectorOAuthClient,
) -> None:
    resp = connector_oauth_client.remove(SEED_CONNECTOR_TYPE, UNSAFE_PATH_SEGMENT)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_delete_requires_authentication(
    connector_oauth_client: ConnectorOAuthClient,
) -> None:
    resp = connector_oauth_client.remove(SEED_CONNECTOR_TYPE, MISSING_CONFIG_ID, auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_delete_requires_connector_delete_scope(
    pipeshub_client: PipeshubClient,
    token_without_connector_read: str,
) -> None:
    resp = pipeshub_client.request(
        "DELETE",
        f"{OAUTH_BASE}/{SEED_CONNECTOR_TYPE}/{MISSING_CONFIG_ID}",
        auth=False,
        headers=bearer(token_without_connector_read),
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
