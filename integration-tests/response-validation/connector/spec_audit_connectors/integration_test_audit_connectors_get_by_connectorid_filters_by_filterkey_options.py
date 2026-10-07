"""Strict OpenAPI audit of GET /api/v1/connectors/:connectorId/filters/:filterKey/options.

The success cases use a BookStack instance whose credentials point at a local
stand-in that answers the book search, so no third party is contacted.
"""

from __future__ import annotations

from typing import Any

import pytest
from connectors_audit_support import (
    MISSING_CONNECTOR_ID,
    UNSAFE_CONNECTOR_ID,
    ConnectorsAuditClient,
    StubSource,
    bearer,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/connectors/:connectorId/filters/:filterKey/options"
DYNAMIC_KEY = "book_ids"


def _path(connector_id: str, filter_key: str = DYNAMIC_KEY) -> str:
    return f"/{connector_id}/filters/{filter_key}/options"


def test_admin_lists_the_options_of_a_dynamic_filter(
    connectors_client: ConnectorsAuditClient, bookstack_connector: str, bookstack_source: StubSource
) -> None:
    resp = connectors_client.get(
        _path(bookstack_connector), params={"page": 1, "limit": 5, "search": "spec"}
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    # Paging is reported at the top level, not under a pagination object.
    assert resp.json() == {
        "success": True,
        "options": [{"id": "7", "label": "Spec Audit Book (spec-audit-book)"}],
        "page": 1,
        "limit": 5,
        "hasMore": False,
    }
    assert any("count=5" in path for _, path in bookstack_source.requests)


def test_group_paths_and_cursor_are_accepted_by_every_connector(
    connectors_client: ConnectorsAuditClient, bookstack_connector: str
) -> None:
    # Only GitLab reads the group paths; a cursor is passed on to connectors that page by cursor.
    resp = connectors_client.get(
        _path(bookstack_connector),
        params=[
            ("contextGroupPath", "spec/audit"),
            ("contextGroupPath", "spec/other"),
            ("excludeContextGroupPath", "spec/excluded"),
            ("cursor", "spec-audit-cursor"),
        ],
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["page"] == 1 and resp.json()["limit"] == 20


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"page": 0}, id="page-zero"),
        pytest.param({"page": "abc"}, id="page-not-a-number"),
        pytest.param({"page": "1.5"}, id="page-fraction"),
        pytest.param({"page": ""}, id="page-empty"),
        pytest.param({"limit": 0}, id="limit-zero"),
        pytest.param({"limit": 101}, id="limit-above-100"),
        pytest.param({"limit": 200}, id="limit-200"),
        pytest.param({"limit": 201}, id="limit-above-200"),
        pytest.param({"limit": ""}, id="limit-empty"),
        pytest.param([("page", 1), ("page", 2)], id="page-repeated"),
        pytest.param([("search", "a"), ("search", "b")], id="search-repeated"),
    ],
)
def test_options_query_refused_by_the_validator(
    connectors_client: ConnectorsAuditClient, bookstack_connector: str, params: Any
) -> None:
    resp = connectors_client.get(_path(bookstack_connector), params=params)
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_options_of_a_filter_without_dynamic_options_is_400(
    connectors_client: ConnectorsAuditClient, bookstack_connector: str
) -> None:
    resp = connectors_client.get(_path(bookstack_connector, "modified"))
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert "does not support dynamic options" in resp.json()["error"]["message"]


def test_options_of_a_connector_that_cannot_reach_its_source_is_400(
    second_user: SecondUser, member_configured_connector: str
) -> None:
    # The connector is started on demand; with credentials it cannot use, starting it fails.
    resp = request_as(second_user, "GET", _path(member_configured_connector, "project_keys"))
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("connector", "filter_key"),
    [
        pytest.param("bookstack", "spec_audit_no_such_filter", id="unknown-filter-key"),
        pytest.param(MISSING_CONNECTOR_ID, DYNAMIC_KEY, id="unknown-connector"),
        pytest.param("bad.id", DYNAMIC_KEY, id="id-the-instance-routes-reject"),
    ],
)
def test_options_not_found(
    connectors_client: ConnectorsAuditClient, bookstack_connector: str, connector: str, filter_key: str
) -> None:
    requested = bookstack_connector if connector == "bookstack" else connector
    resp = connectors_client.get(_path(requested, filter_key))
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("connector", "filter_key"),
    [
        pytest.param(UNSAFE_CONNECTOR_ID, DYNAMIC_KEY, id="unsafe-connector-id"),
        pytest.param("bookstack", "bad%2Fkey", id="unsafe-filter-key"),
    ],
)
def test_options_with_an_unsafe_path_segment_is_400(
    connectors_client: ConnectorsAuditClient, bookstack_connector: str, connector: str, filter_key: str
) -> None:
    requested = bookstack_connector if connector == "bookstack" else connector
    resp = connectors_client.get(_path(requested, filter_key))
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_is_told_an_admin_team_connector_does_not_exist(
    second_user: SecondUser, bookstack_connector: str
) -> None:
    resp = request_as(second_user, "GET", _path(bookstack_connector))
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_options_without_a_token_is_401(
    connectors_client: ConnectorsAuditClient, bookstack_connector: str
) -> None:
    resp = connectors_client.get(_path(bookstack_connector), auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_options_without_the_read_scope_is_403(
    connectors_client: ConnectorsAuditClient,
    bookstack_connector: str,
    token_without_connector_scopes: str,
) -> None:
    resp = connectors_client.get(
        _path(bookstack_connector), auth=False, headers=bearer(token_without_connector_scopes)
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
