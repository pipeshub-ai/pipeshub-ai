"""Strict OpenAPI audit of GET /api/v1/oauth/registry/:connectorType."""

from __future__ import annotations

from typing import Any

import pytest
from connector_oauth_audit_support import (
    INVALID_PAGING_IDS,
    INVALID_PAGING_QUERIES,
    OAUTH_BASE,
    SEED_CONNECTOR_TYPE,
    UNKNOWN_CONNECTOR_TYPE,
    UNSAFE_PATH_SEGMENT,
    ConnectorOAuthClient,
    bearer,
    error_code,
    request_as,
    validation_error_fields,
)
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/oauth/registry/:connectorType"


def test_registry_entry_for_registered_type(
    connector_oauth_client: ConnectorOAuthClient,
) -> None:
    resp = connector_oauth_client.registry_entry(SEED_CONNECTOR_TYPE)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    body = resp.json()
    assert body["success"] is True
    connector = body["connector"]
    assert connector["name"] == SEED_CONNECTOR_TYPE
    assert connector["type"] == SEED_CONNECTOR_TYPE
    assert connector["authType"] == "OAUTH"


def test_valid_paging_query_is_accepted_and_changes_nothing(
    connector_oauth_client: ConnectorOAuthClient,
) -> None:
    # The route reuses the list validator; the handler reads none of the three.
    plain = connector_oauth_client.registry_entry(SEED_CONNECTOR_TYPE)
    resp = connector_oauth_client.get(
        f"/registry/{SEED_CONNECTOR_TYPE}",
        params={"page": 7, "limit": 1, "search": "spec-audit-matches-nothing"},
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == plain.json()


def test_member_can_read_registry_entry(second_user: SecondUser) -> None:
    resp = request_as(second_user, "GET", f"/registry/{SEED_CONNECTOR_TYPE}")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["connector"]["type"] == SEED_CONNECTOR_TYPE


@pytest.mark.parametrize(
    "connector_type",
    [UNKNOWN_CONNECTOR_TYPE, SEED_CONNECTOR_TYPE.lower()],
    ids=["unregistered", "wrong-case"],
)
def test_registry_entry_for_unknown_type_is_not_found(
    connector_oauth_client: ConnectorOAuthClient, connector_type: str
) -> None:
    resp = connector_oauth_client.registry_entry(connector_type)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("query", INVALID_PAGING_QUERIES, ids=INVALID_PAGING_IDS)
def test_registry_entry_rejects_invalid_paging_query(
    connector_oauth_client: ConnectorOAuthClient, query: Any
) -> None:
    # Validated although the handler never reads them.
    resp = connector_oauth_client.get(f"/registry/{SEED_CONNECTOR_TYPE}", params=query)
    assert resp.status_code == 400, resp.text[:500]
    assert validation_error_fields(resp), resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_registry_entry_rejects_unsafe_connector_type(
    connector_oauth_client: ConnectorOAuthClient,
) -> None:
    resp = connector_oauth_client.registry_entry(UNSAFE_PATH_SEGMENT)
    assert resp.status_code == 400, resp.text[:500]
    assert error_code(resp) == "HTTP_BAD_REQUEST"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_registry_entry_requires_authentication(
    connector_oauth_client: ConnectorOAuthClient,
) -> None:
    resp = connector_oauth_client.registry_entry(SEED_CONNECTOR_TYPE, auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


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
    assert_strict_openapi_exchange(resp, ROUTE)
