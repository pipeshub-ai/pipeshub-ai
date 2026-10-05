"""Strict OpenAPI audit of GET /api/v1/oauth/:connectorType."""

from __future__ import annotations

from typing import Any

import pytest
from connector_oauth_audit_support import (
    OAUTH_BASE,
    SEED_CONNECTOR_TYPE,
    ConnectorOAuthClient,
    SeedOAuthConfig,
    bearer,
    request_as,
)
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/oauth/:connectorType"


def _listed(body: dict[str, Any], config_id: str) -> dict[str, Any]:
    matches = [c for c in body["oauthConfigs"] if c.get("_id") == config_id]
    assert len(matches) == 1, f"seeded config {config_id} not listed once: {body}"
    return matches[0]


def test_admin_lists_configs_with_credentials(
    connector_oauth_client: ConnectorOAuthClient,
    seed_oauth_config: SeedOAuthConfig,
) -> None:
    cfg = seed_oauth_config()

    resp = connector_oauth_client.list_for_type(
        cfg["connector_type"], search=cfg["name"], page=1, limit=200
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    body = resp.json()
    assert body["success"] is True
    listed = _listed(body, cfg["id"])
    assert listed["oauthInstanceName"] == cfg["name"]
    # Python hands an admin the stored record as is, credentials included.
    assert "config" in listed
    assert body["pagination"]["page"] == 1
    assert body["pagination"]["limit"] == 200


def test_member_lists_configs_without_credentials(
    second_user: SecondUser,
    seed_oauth_config: SeedOAuthConfig,
) -> None:
    cfg = seed_oauth_config()

    # No admin gate on this route: Python answers a member with a reduced record.
    resp = request_as(
        second_user,
        "GET",
        f"/{cfg['connector_type']}",
        params={"search": cfg["name"], "limit": 200},
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    listed = _listed(resp.json(), cfg["id"])
    assert "config" not in listed
    assert "orgId" not in listed


def test_list_without_token_is_unauthorized(
    connector_oauth_client: ConnectorOAuthClient,
) -> None:
    resp = connector_oauth_client.list_for_type(SEED_CONNECTOR_TYPE, auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_list_without_connector_read_scope_is_forbidden(
    pipeshub_client: PipeshubClient,
    token_without_connector_read: str,
) -> None:
    resp = pipeshub_client.request(
        "GET",
        f"{OAUTH_BASE}/{SEED_CONNECTOR_TYPE}",
        auth=False,
        headers=bearer(token_without_connector_read),
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_list_with_scope_outside_enum_is_bad_request(
    connector_oauth_client: ConnectorOAuthClient,
) -> None:
    # `scope` is accepted by the Node validator (personal | team) only.
    resp = connector_oauth_client.list_for_type(SEED_CONNECTOR_TYPE, scope="org")
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
