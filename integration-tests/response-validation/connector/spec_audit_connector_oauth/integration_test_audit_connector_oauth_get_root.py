"""Strict OpenAPI audit of GET /api/v1/oauth.

Node: authenticate -> requireScopes(connector:read) -> zod query -> proxy to Python
get_all_oauth_configs, which has no admin gate and returns the same stripped rows to everyone.
"""

from __future__ import annotations

from typing import Any

import pytest
from connector_oauth_audit_support import (
    INVALID_PAGING_IDS,
    INVALID_PAGING_QUERIES,
    OAUTH_BASE,
    UNKNOWN_CONNECTOR_TYPE,
    ConnectorOAuthClient,
    SeedOAuthConfig,
    bearer,
    request_as,
    validation_error_fields,
)
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

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
    assert_strict_openapi_exchange(resp, ROUTE)


def test_config_of_an_unregistered_type_is_not_listed(
    connector_oauth_client: ConnectorOAuthClient, seed_oauth_config: SeedOAuthConfig
) -> None:
    # The listing walks the registry's types, so a config stored under any other name is skipped.
    cfg = seed_oauth_config(UNKNOWN_CONNECTOR_TYPE)

    resp = connector_oauth_client.list_all(search=cfg["name"], limit=200)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["oauthConfigs"] == []
    assert resp.json()["pagination"]["totalItems"] == 0


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
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_sees_the_same_credential_free_rows(
    second_user: SecondUser, seed_oauth_config: SeedOAuthConfig
) -> None:
    cfg = seed_oauth_config()

    resp = request_as(second_user, "GET", params={"search": cfg["name"], "limit": 200})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    rows = [row for row in resp.json()["oauthConfigs"] if row["_id"] == cfg["id"]]
    assert len(rows) == 1, resp.text[:500]
    assert set(rows[0]) == LISTED_FIELDS, rows[0].keys()


def test_list_without_token_is_unauthorized(
    connector_oauth_client: ConnectorOAuthClient,
) -> None:
    resp = connector_oauth_client.list_all(auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_list_without_connector_read_scope_is_forbidden(
    pipeshub_client: PipeshubClient, token_without_connector_read: str
) -> None:
    resp = pipeshub_client.request(
        "GET", OAUTH_BASE, auth=False, headers=bearer(token_without_connector_read)
    )

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("query", INVALID_PAGING_QUERIES, ids=INVALID_PAGING_IDS)
def test_list_rejects_invalid_paging_query(
    connector_oauth_client: ConnectorOAuthClient, query: Any
) -> None:
    resp = connector_oauth_client.get("", params=query)

    assert resp.status_code == 400, resp.text[:500]
    assert validation_error_fields(resp), resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unknown_query_parameter_is_ignored(connector_oauth_client: ConnectorOAuthClient) -> None:
    # `scope` belongs to GET /oauth/{connectorType}; here it is not even validated.
    with outside_request_contract("the validator passes unknown query parameters through"):
        resp = connector_oauth_client.list_all(limit=1, scope="org")
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["pagination"]["limit"] == 1
