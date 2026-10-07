"""Strict OpenAPI audit of POST /api/v1/search."""

from __future__ import annotations

from typing import Any

import pytest
from helper.second_user import SecondUser
from search_audit_support import (
    ROOT_TEMPLATE,
    SEARCH_QUERY,
    UNKNOWN_KB_ID,
    SearchAuditClient,
    error_of,
    request_as,
    validation_fields,
)
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = ROOT_TEMPLATE


@pytest.fixture
def kb_id(session_kb: dict[str, str]) -> str:
    return session_kb["kb_id"]


def _persisted(search_audit_client: SearchAuditClient, search_id: str) -> dict[str, Any]:
    resp = search_audit_client.get(f"/{search_id}")
    assert resp.status_code == 200, resp.text[:500]
    [row] = resp.json()
    return row


def test_search_returns_hits_and_persists_the_search(
    search_audit_client: SearchAuditClient, kb_id: str
) -> None:
    resp = search_audit_client.post("/", json={"query": SEARCH_QUERY, "filters": {"kb": [kb_id]}, "limit": 2})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    search_id = body["searchId"]
    try:
        hits = body["searchResponse"]["searchResults"]
        assert hits and all(h["metadata"]["connectorId"] == kb_id for h in hits)
        row = _persisted(search_audit_client, search_id)
        assert (row["query"], row["limit"]) == (SEARCH_QUERY, 2)
    finally:
        search_audit_client.delete(f"/{search_id}")


def test_search_with_an_apps_filter_succeeds(
    search_audit_client: SearchAuditClient, kb_id: str
) -> None:
    resp = search_audit_client.post("/", json={"query": SEARCH_QUERY, "filters": {"apps": [kb_id]}, "limit": 1})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    search_audit_client.delete(f"/{resp.json()['searchId']}")


def test_numeric_string_limit_is_coerced(
    search_audit_client: SearchAuditClient, kb_id: str
) -> None:
    with outside_request_contract("the validator turns a numeric string limit into a number"):
        resp = search_audit_client.post("/", json={"query": SEARCH_QUERY, "filters": {"kb": [kb_id]}, "limit": "2"})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    search_id = resp.json()["searchId"]
    try:
        assert _persisted(search_audit_client, search_id)["limit"] == 2
    finally:
        search_audit_client.delete(f"/{search_id}")


@pytest.mark.parametrize(
    "extra",
    [
        pytest.param({"foo": 1}, id="unknown-top-level-field"),
        pytest.param({"filters": {"x": 1}}, id="unknown-filters-field"),
    ],
)
def test_unknown_fields_are_dropped(
    search_audit_client: SearchAuditClient, kb_id: str, extra: dict[str, Any]
) -> None:
    body: dict[str, Any] = {"query": SEARCH_QUERY, "filters": {"kb": [kb_id]}, "limit": 1, **extra}
    if "filters" in extra:
        body["filters"] = {"kb": [kb_id], **extra["filters"]}
    with outside_request_contract("unknown body fields are stripped by the validator, not refused"):
        resp = search_audit_client.post("/", json=body)
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    search_audit_client.delete(f"/{resp.json()['searchId']}")


def test_search_without_limit_fails_after_searching(
    search_audit_client: SearchAuditClient, kb_id: str
) -> None:
    # The validator lets limit be omitted, but the search document cannot be saved without one.
    resp = search_audit_client.post("/", json={"query": SEARCH_QUERY, "filters": {"kb": [kb_id]}})

    assert error_of(resp, 500)["code"] == "INTERNAL_ERROR"
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


def test_fractional_limit_is_refused_by_the_search_service(
    search_audit_client: SearchAuditClient, kb_id: str
) -> None:
    resp = search_audit_client.post("/", json={"query": SEARCH_QUERY, "filters": {"kb": [kb_id]}, "limit": 2.5})

    error = error_of(resp, 422)
    assert error["code"] == "HTTP_UNPROCESSABLE_ENTITY"
    assert error["message"] == "Limit must be a whole number."
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


@pytest.mark.parametrize(
    ("body", "field"),
    [
        pytest.param(None, "body.query", id="no-body"),
        pytest.param({"limit": 1}, "body.query", id="query-missing"),
        pytest.param({"query": "", "limit": 1}, "body.query", id="query-empty"),
        pytest.param({"query": 5, "limit": 1}, "body.query", id="query-not-text"),
        pytest.param({"query": SEARCH_QUERY, "limit": 0}, "body.limit", id="limit-zero"),
        pytest.param({"query": SEARCH_QUERY, "limit": 101}, "body.limit", id="limit-over-100"),
        pytest.param({"query": SEARCH_QUERY, "limit": "many"}, "body.limit", id="limit-not-a-number"),
        pytest.param({"query": SEARCH_QUERY, "limit": 1, "filters": {"kb": ["x"]}}, "body.filters.kb.0", id="kb-not-uuid"),
        pytest.param(
            {"query": SEARCH_QUERY, "limit": 1, "filters": {"apps": ["x"]}}, "body.filters.apps.0", id="apps-not-uuid"
        ),
        pytest.param({"query": SEARCH_QUERY, "limit": 1, "filters": []}, "body.filters", id="filters-not-object"),
    ],
)
def test_invalid_body_is_rejected(
    search_audit_client: SearchAuditClient, body: dict[str, Any] | None, field: str
) -> None:
    resp = search_audit_client.post("/", json=body)

    assert validation_fields(resp) == {field}
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "query",
    [
        pytest.param("<script>alert(1)</script>", id="markup"),
        pytest.param("show %s of the files", id="format-specifier"),
    ],
)
def test_markup_or_format_specifiers_in_the_query_are_rejected(
    search_audit_client: SearchAuditClient, query: str
) -> None:
    resp = search_audit_client.post("/", json={"query": query, "limit": 1})

    assert error_of(resp, 400)["code"] == "HTTP_BAD_REQUEST"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_nothing_searchable_in_scope_is_not_found(search_audit_client: SearchAuditClient) -> None:
    resp = search_audit_client.post("/", json={"query": SEARCH_QUERY, "filters": {"kb": [UNKNOWN_KB_ID]}, "limit": 1})

    assert error_of(resp, 404)["code"] == "HTTP_NOT_FOUND"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_user_without_any_indexed_document_is_not_found(second_user: SecondUser) -> None:
    resp = request_as(second_user, "POST", json={"query": SEARCH_QUERY, "limit": 1})

    assert error_of(resp, 404)["code"] == "HTTP_NOT_FOUND"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_search_without_token_is_unauthorized(search_audit_client: SearchAuditClient) -> None:
    resp = search_audit_client.post("/", auth=False, json={"query": SEARCH_QUERY, "limit": 1})

    assert error_of(resp, 401)["code"] == "HTTP_UNAUTHORIZED"
    assert_strict_openapi_exchange(resp, ROUTE)
