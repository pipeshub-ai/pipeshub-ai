"""Strict OpenAPI audit of GET /api/v1/knowledgeBase/knowledge-hub/nodes.

authenticate -> requireScopes(KB_READ) -> getKnowledgeHubNodes, which forwards the
query to the connector service (knowledge_hub_router.get_knowledge_hub_root_nodes).
Node has no validator here: every refusal below the token check is Python's.
"""

from __future__ import annotations

from typing import Any

import pytest
from helper.clients.kb_client import KBClient
from helper.second_user import SecondUser
from knowledge_base_audit_support import SeedRecord, request_as, unique_name
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    assert_strict_openapi_response,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/knowledgeBase/knowledge-hub/nodes"
PATH = "/knowledge-hub/nodes"
ALL_INCLUDES = "breadcrumbs,counts,availableFilters,permissions"
MAX_LIST_ITEMS = 100
ONE_TERABYTE = 1099511627776
MAX_TIMESTAMP_MS = 9999999999999

Query = dict[str, Any] | list[tuple[str, str]]


def _ids(body: dict[str, Any]) -> list[str]:
    return [item["id"] for item in body["items"]]


def test_root_listing_without_filters(kb_client: KBClient, audit_kb_id: str) -> None:
    resp = kb_client.get(PATH, params={"limit": 200})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert body["success"] is True
    assert body["id"] is None and body["currentNode"] is None and body["parentNode"] is None
    assert all(item["nodeType"] == "app" for item in body["items"])
    assert audit_kb_id in _ids(body)
    assert body["filters"]["applied"]["sortBy"] == "updatedAt"
    assert body["filters"]["applied"]["sortOrder"] == "desc"
    for section in ("breadcrumbs", "counts", "permissions"):
        assert body[section] is None
    assert body["filters"]["available"] is None


def test_filtered_listing_with_every_parameter_finds_the_record(
    kb_client: KBClient, audit_kb_id: str, seed_record: SeedRecord
) -> None:
    stem = unique_name("spec-audit-hub")
    record_id = seed_record(f"{stem}.txt")

    # Any filter switches the handler to the flattened search across the whole tree.
    resp = kb_client.get(
        PATH,
        params={
            "q": stem,
            "nodeTypes": "record,folder",
            "recordTypes": "FILE",
            "origins": "COLLECTION",
            "connectorIds": audit_kb_id,
            "indexingStatus": "NOT_STARTED,QUEUED,IN_PROGRESS,COMPLETED,FAILED,EMPTY",
            "createdAt": f"gte:0,lte:{MAX_TIMESTAMP_MS}",
            "updatedAt": "gte:0",
            "size": f"gte:0,lte:{ONE_TERABYTE}",
            "include": ALL_INCLUDES,
            "sortBy": "name",
            "sortOrder": "asc",
            "onlyContainers": "false",
            "flattened": "true",
            "page": 1,
            "limit": 200,
        },
    )

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert _ids(body) == [record_id]
    item = body["items"][0]
    assert item["name"] == stem
    assert item["nodeType"] == "record"
    assert item["recordType"] == "FILE"
    applied = body["filters"]["applied"]
    assert applied["q"] == stem
    assert applied["nodeTypes"] == ["record", "folder"]
    assert applied["sortBy"] == "name" and applied["sortOrder"] == "asc"
    assert body["filters"]["available"] is not None
    assert body["counts"] is not None
    assert body["permissions"] is not None
    # No parent in the path, so no trail even when asked for.
    assert body["breadcrumbs"] is None


def test_flattened_false_keeps_the_top_level_listing_despite_filters(
    kb_client: KBClient, audit_kb_id: str
) -> None:
    resp = kb_client.get(PATH, params={"flattened": "false", "nodeTypes": "record", "limit": 200})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_only_containers_lists_apps(kb_client: KBClient, audit_kb_id: str) -> None:
    resp = kb_client.get(PATH, params={"onlyContainers": "true", "limit": 200})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert all(item["nodeType"] == "app" for item in resp.json()["items"])


def test_member_does_not_see_the_admins_knowledge_base(
    second_user: SecondUser, audit_kb_id: str
) -> None:
    resp = request_as(second_user, "GET", PATH, params={"limit": 200})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert audit_kb_id not in _ids(resp.json())


@pytest.mark.parametrize(
    ("params", "applied_key", "applied_value"),
    [
        pytest.param({"sortBy": "relevance"}, "sortBy", "name", id="unknown-sortBy-becomes-name"),
        pytest.param({"sortOrder": "up"}, "sortOrder", "asc", id="unknown-sortOrder-becomes-asc"),
    ],
)
def test_unknown_sort_values_fall_back_instead_of_failing(
    kb_client: KBClient, params: dict[str, str], applied_key: str, applied_value: str
) -> None:
    with outside_request_contract("the connector service replaces an unknown sort value instead of refusing it"):
        resp = kb_client.get(PATH, params=params)
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["filters"]["applied"][applied_key] == applied_value


@pytest.mark.parametrize(
    ("params", "reason"),
    [
        pytest.param(
            {"view": "grid", "parentId": "anything", "kbIds": "anything", "foo": "bar"},
            "query keys the connector service does not declare are dropped",
            id="unknown-parameters",
        ),
        pytest.param(
            {"nodeTypes": "app,planet", "recordTypes": "NOVEL", "origins": "MARS", "include": "everything"},
            "list items outside the known values are dropped",
            id="unknown-list-items",
        ),
        pytest.param({"createdAt": "last-week"}, "a range with no gte:/lte: part is ignored", id="range-without-bounds"),
        pytest.param({"onlyContainers": "yes"}, "FastAPI reads yes/no, on/off and 1/0 as booleans", id="boolean-words"),
        pytest.param({"limit": ""}, "the gateway does not forward an empty value", id="empty-limit"),
    ],
)
def test_root_listing_tolerates_what_it_does_not_understand(
    kb_client: KBClient, audit_kb_id: str, params: dict[str, str], reason: str
) -> None:
    with outside_request_contract(reason):
        resp = kb_client.get(PATH, params=params)
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["success"] is True


@pytest.mark.parametrize(
    ("params", "message"),
    [
        pytest.param({"q": "a"}, "Search query must be at least 2 characters", id="q-one-character"),
        pytest.param({"q": "q" * 501}, "Search query too long (max: 500 characters)", id="q-over-500"),
    ],
)
def test_search_text_outside_the_length_limits_is_bad_request(
    kb_client: KBClient, params: dict[str, str], message: str
) -> None:
    resp = kb_client.get(PATH, params=params)

    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["message"] == message
    assert_strict_openapi_response(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


@pytest.mark.parametrize(
    ("params", "message"),
    [
        # Trimmed before it is measured, which a length keyword cannot say.
        pytest.param({"q": " a "}, "Search query must be at least 2 characters", id="q-one-character-padded"),
        pytest.param(
            {"nodeTypes": ",".join(["app"] * (MAX_LIST_ITEMS + 1))},
            "Too many items in comma-separated list (max: 100, got: 101)",
            id="list-over-100-items",
        ),
        pytest.param({"createdAt": "gte:5,lte:1"}, "Date range invalid: gte must be <= lte", id="createdAt-inverted"),
        pytest.param({"createdAt": "gte:soon"}, "Invalid timestamp value: soon", id="createdAt-not-a-number"),
        pytest.param(
            {"updatedAt": f"lte:{MAX_TIMESTAMP_MS + 1}"},
            f"Timestamp out of valid range: {MAX_TIMESTAMP_MS + 1}",
            id="updatedAt-out-of-range",
        ),
        pytest.param({"size": "gte:5,lte:1"}, "Size range invalid: gte must be <= lte", id="size-inverted"),
        pytest.param({"size": "gte:-1"}, "Size must be non-negative, got: -1", id="size-negative"),
        pytest.param(
            {"size": f"lte:{ONE_TERABYTE + 1}"},
            f"Size exceeds maximum (1TB): {ONE_TERABYTE + 1}",
            id="size-over-1tb",
        ),
        pytest.param({"size": "gte:big"}, "Invalid size value: big", id="size-not-a-number"),
    ],
)
def test_filter_value_the_connector_service_refuses_is_bad_request(
    kb_client: KBClient, params: dict[str, str], message: str
) -> None:
    # Rules on the inside of a string value: described on each parameter, not expressible as a schema.
    resp = kb_client.get(PATH, params=params)

    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "HTTP_BAD_REQUEST"
    assert resp.json()["error"]["message"] == message
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"page": 0}, id="page-zero"),
        pytest.param({"page": "two"}, id="page-not-a-number"),
        pytest.param({"limit": 0}, id="limit-zero"),
        pytest.param({"limit": 201}, id="limit-over-200"),
        pytest.param({"onlyContainers": "maybe"}, id="onlyContainers-not-boolean"),
        pytest.param({"flattened": "maybe"}, id="flattened-not-boolean"),
        # The gateway joins repeats with a comma, and "1,2" is not an integer.
        pytest.param([("page", "1"), ("page", "2")], id="page-repeated"),
    ],
)
def test_typed_parameter_with_a_wrong_value_is_unprocessable(kb_client: KBClient, params: Query) -> None:
    # FastAPI refuses these with 422 and handleBackendError keeps the status.
    resp = kb_client.get(PATH, params=params)

    assert resp.status_code == 422, resp.text[:500]
    assert resp.json()["error"]["code"] == "HTTP_UNPROCESSABLE_ENTITY"
    assert_strict_openapi_response(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="no-token"),
        pytest.param({"Authorization": "Bearer not-a-jwt"}, id="invalid-token"),
    ],
)
def test_rejects_unauthenticated_calls(kb_client: KBClient, headers: dict[str, str]) -> None:
    resp = kb_client.get(PATH, auth=False, headers=headers)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_with_a_token_lacking_kb_read_is_forbidden(
    kb_client: KBClient, unscoped_headers: dict[str, str]
) -> None:
    resp = kb_client.get(PATH, auth=False, headers=unscoped_headers)

    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["message"] == "Insufficient scope. Required: kb:read"
    assert_strict_openapi_exchange(resp, ROUTE)
