"""Strict OpenAPI audit of GET /api/v1/oauth/registry/:connectorType."""

from __future__ import annotations

import pytest
from connector_oauth_audit_support import (
    OAUTH_BASE,
    SEED_CONNECTOR_TYPE,
    UNKNOWN_CONNECTOR_TYPE,
    ConnectorOAuthClient,
    bearer,
)
from helper.pipeshub_client import PipeshubClient
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/oauth/registry/:connectorType"


def test_registry_entry_for_registered_type(
    connector_oauth_client: ConnectorOAuthClient,
) -> None:
    resp = connector_oauth_client.registry_entry(SEED_CONNECTOR_TYPE)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    body = resp.json()
    assert body["success"] is True
    connector = body["connector"]
    assert connector["name"] == SEED_CONNECTOR_TYPE
    assert connector["type"] == SEED_CONNECTOR_TYPE
    assert connector["authType"] == "OAUTH"


def test_registry_entry_for_unknown_type_is_not_found(
    connector_oauth_client: ConnectorOAuthClient,
) -> None:
    resp = connector_oauth_client.registry_entry(UNKNOWN_CONNECTOR_TYPE)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_registry_entry_rejects_out_of_range_page(
    connector_oauth_client: ConnectorOAuthClient,
) -> None:
    # The route reuses the list schema, so a query the handler never reads still fails validation.
    resp = connector_oauth_client.get(f"/registry/{SEED_CONNECTOR_TYPE}", params={"page": 0})
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_registry_entry_requires_authentication(
    connector_oauth_client: ConnectorOAuthClient,
) -> None:
    resp = connector_oauth_client.registry_entry(SEED_CONNECTOR_TYPE, auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_registry_entry_requires_connector_read_scope(
    pipeshub_client: PipeshubClient,
    token_without_connector_read: str,
) -> None:
    resp = pipeshub_client.request(
        "GET",
        f"{OAUTH_BASE}/registry/{SEED_CONNECTOR_TYPE}",
        auth=False,
        headers=bearer(token_without_connector_read),
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
