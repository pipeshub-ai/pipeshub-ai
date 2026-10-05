"""Strict OpenAPI audit of GET /api/v1/oauth.

Node: authenticate -> requireScopes(connector:read) -> zod query -> proxy to Python
get_all_oauth_configs, which has no admin gate and returns the same stripped rows to everyone.
"""

from __future__ import annotations

import pytest
from connector_oauth_audit_support import (
    OAUTH_BASE,
    ConnectorOAuthClient,
    SeedOAuthConfig,
    bearer,
    request_as,
)
from strict_openapi import assert_strict_openapi_response

from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/oauth"
LISTED_FIELDS = {
    "_id",
    "oauthInstanceName",
    "iconPath",
    "appGroup",
    "appDescription",
    "appCategories",
    "connectorType",
    "createdAtTimestamp",
    "updatedAtTimestamp",
}


def test_admin_search_lists_seeded_config_without_secrets(
    connector_oauth_client: ConnectorOAuthClient, seed_oauth_config: SeedOAuthConfig
) -> None:
    cfg = seed_oauth_config()

    resp = connector_oauth_client.list_all(search=cfg["name"], page=1, limit=200)

    assert resp.status_code == 200, resp.text[:500]
    body = resp.json()
    assert body["success"] is True
    rows = [row for row in body["oauthConfigs"] if row["_id"] == cfg["id"]]
    assert len(rows) == 1, body["oauthConfigs"]
    assert set(rows[0]) == LISTED_FIELDS, rows[0].keys()
    assert rows[0]["connectorType"] == cfg["connector_type"]
    pagination = body["pagination"]
    assert pagination["page"] == 1
    assert pagination["limit"] == 200
    assert pagination["search"] == cfg["name"]
    assert pagination["totalItems"] >= 1
    assert_strict_openapi_response(resp, ROUTE)


def test_member_lists_with_default_pagination(second_user: SecondUser) -> None:
    resp = request_as(second_user, "GET")

    assert resp.status_code == 200, resp.text[:500]
    body = resp.json()
    assert body["success"] is True
    assert isinstance(body["oauthConfigs"], list)
    # Without ?search Python echoes search: null in the pagination block.
    assert body["pagination"]["page"] == 1
    assert body["pagination"]["limit"] == 20
    assert body["pagination"]["search"] is None
    assert_strict_openapi_response(resp, ROUTE)


def test_list_without_token_is_unauthorized(
    connector_oauth_client: ConnectorOAuthClient,
) -> None:
    resp = connector_oauth_client.list_all(auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_list_without_connector_read_scope_is_forbidden(
    pipeshub_client: PipeshubClient, token_without_connector_read: str
) -> None:
    resp = pipeshub_client.request(
        "GET", OAUTH_BASE, auth=False, headers=bearer(token_without_connector_read)
    )

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_list_limit_above_validator_max_is_rejected(
    connector_oauth_client: ConnectorOAuthClient,
) -> None:
    resp = connector_oauth_client.list_all(limit=201)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
