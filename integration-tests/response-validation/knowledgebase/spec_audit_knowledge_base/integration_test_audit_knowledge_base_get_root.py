"""Strict OpenAPI audit of GET /api/v1/knowledgeBase.

XSS check on the query -> authenticate -> requireScopes(kb:read) -> strict zod query ->
listKnowledgeBases (its own checks on page, limit, search, permissions, sortBy and
sortOrder) -> connector service (GET /api/v1/kb/).
"""

from __future__ import annotations

from typing import Any

import pytest
from helper.clients.kb_client import KBClient
from helper.second_user import SecondUser
from knowledge_base_audit_support import HTML_REFUSED_MESSAGE, request_as, unique_name
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    assert_strict_openapi_response,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/knowledgeBase"
SORT_FIELDS = ("name", "createdAtTimestamp", "updatedAtTimestamp", "userRole")
# Number.MAX_SAFE_INTEGER // 100 in safeParsePagination.
MAX_PAGE = 90071992547409
MAX_SEARCH_LENGTH = 1000

Query = dict[str, Any] | list[tuple[str, str]]


def test_list_without_parameters_uses_the_defaults(kb_client: KBClient, audit_kb_id: str) -> None:
    resp = kb_client.get("")

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert set(body) == {"knowledgeBases", "pagination", "filters"}
    assert body["pagination"]["page"] == 1
    assert body["pagination"]["limit"] == 20
    assert body["filters"]["applied"] == {}
    assert all(kb["userRole"] in ("OWNER", "WRITER", "READER") for kb in body["knowledgeBases"])


def test_list_with_every_parameter_finds_the_knowledge_base(kb_client: KBClient) -> None:
    name = unique_name("spec-audit-list")
    kb_id = kb_client.create_kb(name)["id"]
    try:
        resp = kb_client.get(
            "",
            params={
                "page": 1,
                "limit": 100,
                "search": name,
                "permissions": "OWNER,WRITER",
                "sortBy": "updatedAtTimestamp",
                "sortOrder": "desc",
            },
        )
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
        body = resp.json()
        assert [kb["id"] for kb in body["knowledgeBases"]] == [kb_id]
        assert body["knowledgeBases"][0]["userRole"] == "OWNER"
        assert body["pagination"]["totalCount"] == 1
        assert body["filters"]["applied"] == {
            "search": name,
            "permissions": ["OWNER", "WRITER"],
            "sort_by": "updatedAtTimestamp",
            "sort_order": "desc",
        }
    finally:
        kb_client.delete(f"/{kb_id}")


@pytest.mark.parametrize("sort_by", SORT_FIELDS)
@pytest.mark.parametrize("sort_order", ["asc", "desc"])
def test_list_accepts_every_sort_field_and_order(
    kb_client: KBClient, audit_kb_id: str, sort_by: str, sort_order: str
) -> None:
    resp = kb_client.get("", params={"sortBy": sort_by, "sortOrder": sort_order, "limit": 5})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_list_as_member_does_not_show_the_admins_knowledge_base(
    kb_client: KBClient, second_user: SecondUser
) -> None:
    name = unique_name("spec-audit-private")
    kb_id = kb_client.create_kb(name)["id"]
    try:
        resp = request_as(second_user, "GET", params={"search": name})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
        assert resp.json()["knowledgeBases"] == []
        assert resp.json()["pagination"]["totalCount"] == 0
    finally:
        kb_client.delete(f"/{kb_id}")


def test_list_page_past_the_end_is_empty(kb_client: KBClient, audit_kb_id: str) -> None:
    resp = kb_client.get("", params={"page": MAX_PAGE})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert body["knowledgeBases"] == []
    assert body["pagination"]["page"] == MAX_PAGE
    assert body["pagination"]["hasNext"] is False


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"page": ""}, id="page"),
        pytest.param({"limit": ""}, id="limit"),
        pytest.param({"search": ""}, id="search"),
        pytest.param({"permissions": ""}, id="permissions"),
        pytest.param({"sortBy": ""}, id="sortBy"),
    ],
)
def test_list_reads_an_empty_value_as_the_default(
    kb_client: KBClient, audit_kb_id: str, params: dict[str, str]
) -> None:
    resp = kb_client.get("", params=params)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert body["pagination"]["page"] == 1
    assert body["pagination"]["limit"] == 20
    assert body["filters"]["applied"] == {}


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"page": "0"}, id="page-zero"),
        pytest.param({"page": "abc"}, id="page-not-a-number"),
        pytest.param({"limit": "0"}, id="limit-zero"),
        pytest.param({"limit": "101"}, id="limit-over-100"),
        pytest.param({"sortBy": "bogus"}, id="sortBy-unknown"),
        pytest.param({"sortOrder": "up"}, id="sortOrder-unknown"),
        pytest.param({"sortOrder": ""}, id="sortOrder-empty"),
        pytest.param({"sortOrder": "ASC"}, id="sortOrder-upper-case"),
        pytest.param({"search": "50% discount"}, id="search-format-specifier"),
        pytest.param([("page", "1"), ("page", "2")], id="page-repeated"),
    ],
)
def test_list_rejects_a_query_outside_the_validator(kb_client: KBClient, params: Query) -> None:
    resp = kb_client.get("", params=params)
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_list_rejects_an_unknown_query_parameter(kb_client: KBClient) -> None:
    # OpenAPI has no keyword that forbids undeclared query parameters; the operation says so in words.
    with outside_request_contract("the query validator is strict and OpenAPI cannot express that"):
        resp = kb_client.get("", params={"page": 1, "kbId": "anything"})
        assert resp.status_code == 400, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    error = resp.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["message"] == "These fields aren't accepted here: kbId."


@pytest.mark.parametrize(
    ("params", "message"),
    [
        pytest.param({"permissions": "ADMIN"}, "Invalid permissions: ADMIN", id="permissions-unknown-role"),
        pytest.param({"permissions": "owner"}, "Invalid permissions: owner", id="permissions-lower-case"),
        pytest.param(
            {"permissions": "OWNER, WRITER"}, "Invalid permissions:  WRITER", id="permissions-space-after-comma"
        ),
        pytest.param({"permissions": ","}, "Invalid permissions: ", id="permissions-only-a-comma"),
        pytest.param(
            {"page": str(MAX_PAGE + 1)},
            f"Number must be at most {MAX_PAGE}: {MAX_PAGE + 1}",
            id="page-over-the-safe-maximum",
        ),
    ],
)
def test_list_rejects_what_only_the_handler_checks(
    kb_client: KBClient, params: dict[str, str], message: str
) -> None:
    # Past the validator; listKnowledgeBases refuses these itself with a plain 400.
    resp = kb_client.get("", params=params)
    assert resp.status_code == 400, resp.text[:500]
    error = resp.json()["error"]
    assert error["code"] == "HTTP_BAD_REQUEST"
    assert error["message"] == message
    assert_strict_openapi_response(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


def test_list_refuses_html_in_the_search(kb_client: KBClient) -> None:
    resp = kb_client.get("", params={"search": "<script>alert(1)</script>"})
    assert resp.status_code == 400, resp.text[:500]
    error = resp.json()["error"]
    assert error["code"] == "HTTP_BAD_REQUEST"
    assert HTML_REFUSED_MESSAGE in error["message"]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_list_search_at_the_length_limit_is_accepted(kb_client: KBClient) -> None:
    resp = kb_client.get("", params={"search": "s" * MAX_SEARCH_LENGTH})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["knowledgeBases"] == []


def test_list_search_over_the_length_limit_is_an_internal_error(kb_client: KBClient) -> None:
    # The length check throws a plain Error inside the zod transform, which the validation
    # middleware does not recognise as a validation failure.
    resp = kb_client.get("", params={"search": "s" * (MAX_SEARCH_LENGTH + 1)})
    assert resp.status_code == 500, resp.text[:500]
    assert resp.json()["error"]["code"] == "INTERNAL_ERROR"
    assert_strict_openapi_response(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


@pytest.mark.parametrize(
    ("params", "reason"),
    [
        pytest.param({"page": "1abc"}, "page is read with parseInt, which stops at the first non-digit", id="page"),
        pytest.param({"limit": "5 per page"}, "limit is read with parseInt, which stops at the first non-digit", id="limit"),
        pytest.param(
            {"permissions": "OWNER,,WRITER,"}, "empty items of the permissions list are dropped", id="permissions"
        ),
    ],
)
def test_list_tolerates_loosely_written_values(
    kb_client: KBClient, audit_kb_id: str, params: dict[str, str], reason: str
) -> None:
    with outside_request_contract(reason):
        resp = kb_client.get("", params=params)
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    pagination = resp.json()["pagination"]
    assert pagination["page"] == 1
    assert pagination["limit"] == (5 if "limit" in params else 20)


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="no-token"),
        pytest.param({"Authorization": "Bearer not-a-jwt"}, id="invalid-token"),
    ],
)
def test_list_rejects_unauthenticated_calls(kb_client: KBClient, headers: dict[str, str]) -> None:
    resp = kb_client.get("", auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_list_with_a_token_lacking_kb_read_is_forbidden(
    kb_client: KBClient, unscoped_headers: dict[str, str]
) -> None:
    resp = kb_client.get("", auth=False, headers=unscoped_headers)
    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["message"] == "Insufficient scope. Required: kb:read"
    assert_strict_openapi_exchange(resp, ROUTE)
