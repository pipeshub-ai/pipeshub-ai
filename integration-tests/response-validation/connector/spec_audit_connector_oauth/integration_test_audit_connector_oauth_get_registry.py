"""Strict OpenAPI audit of GET /api/v1/oauth/registry."""

from __future__ import annotations

from typing import Any

import pytest
from connector_oauth_audit_support import (
    INVALID_PAGING_IDS,
    INVALID_PAGING_QUERIES,
    OAUTH_BASE,
    SEED_CONNECTOR_TYPE,
    ConnectorOAuthClient,
    bearer,
    request_as,
    validation_error_fields,
)
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/oauth/registry"


def test_admin_searches_registry(connector_oauth_client: ConnectorOAuthClient) -> None:
    resp = connector_oauth_client.registry(page=1, limit=200, search=SEED_CONNECTOR_TYPE)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    body = resp.json()
    assert body["success"] is True
    assert SEED_CONNECTOR_TYPE in [c["type"] for c in body["connectors"]]
    pagination = body["pagination"]
    assert pagination["page"] == 1
    assert pagination["limit"] == 200
    assert pagination["search"] == SEED_CONNECTOR_TYPE
    assert pagination["totalCount"] == len(body["connectors"])


def test_without_query_the_defaults_apply(connector_oauth_client: ConnectorOAuthClient) -> None:
    resp = connector_oauth_client.registry()
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    pagination = resp.json()["pagination"]
    assert pagination["page"] == 1
    assert pagination["limit"] == 20
    assert pagination["search"] is None
    assert pagination["prevPage"] is None


def test_page_past_the_end_is_an_empty_page(connector_oauth_client: ConnectorOAuthClient) -> None:
    resp = connector_oauth_client.registry(page=99, limit=200)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    body = resp.json()
    assert body["connectors"] == []
    assert body["pagination"]["hasPrev"] is True
    assert body["pagination"]["prevPage"] == 98
    assert body["pagination"]["nextPage"] is None


def test_member_can_read_registry(second_user: SecondUser) -> None:
    # Neither Node nor Python gates this route on admin.
    resp = request_as(second_user, "GET", "/registry", params={"limit": 1})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert len(resp.json()["connectors"]) <= 1


@pytest.mark.parametrize("query", INVALID_PAGING_QUERIES, ids=INVALID_PAGING_IDS)
def test_invalid_paging_query_is_rejected(
    connector_oauth_client: ConnectorOAuthClient, query: Any
) -> None:
    resp = connector_oauth_client.get("/registry", params=query)
    assert resp.status_code == 400, resp.text[:500]
    assert validation_error_fields(resp), resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unknown_query_parameter_is_ignored(connector_oauth_client: ConnectorOAuthClient) -> None:
    with outside_request_contract("the validator passes unknown query parameters through"):
        resp = connector_oauth_client.registry(limit=1, scope="org", specAudit="x")
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["pagination"]["limit"] == 1


def test_token_without_connector_read_is_forbidden(
    pipeshub_client: PipeshubClient, token_without_connector_read: str
) -> None:
    resp = pipeshub_client.request(
        "GET",
        f"{OAUTH_BASE}/registry",
        auth=False,
        headers=bearer(token_without_connector_read),
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_without_token_is_unauthorized(connector_oauth_client: ConnectorOAuthClient) -> None:
    resp = connector_oauth_client.registry(auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
