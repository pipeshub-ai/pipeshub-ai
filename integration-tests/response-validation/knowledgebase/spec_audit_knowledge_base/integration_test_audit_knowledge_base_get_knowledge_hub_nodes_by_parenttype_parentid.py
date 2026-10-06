"""Strict OpenAPI audit of GET /api/v1/knowledgeBase/knowledge-hub/nodes/:parentType/:parentId.

guardPathParams(parentType, parentId) -> authenticate -> requireScopes(KB_READ) ->
getKnowledgeHubNodes -> connector service
(knowledge_hub_router.get_knowledge_hub_children_nodes). No Node validator: the type and
id checks, and every query check, are Python's.
"""

from __future__ import annotations

from typing import Any

import pytest
from helper.clients.kb_client import KBClient
from helper.second_user import SecondUser
from knowledge_base_audit_support import (
    MISSING_RECORD_ID,
    UNSAFE_ID,
    UNSAFE_ID_MESSAGE,
    SeedRecord,
    request_as,
    unique_name,
    wait_for_record,
)
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    assert_strict_openapi_response,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/knowledgeBase/knowledge-hub/nodes/:parentType/:parentId"
ALL_INCLUDES = "breadcrumbs,counts,availableFilters,permissions"
GONE_MESSAGE = "This item was removed, or you no longer have access. Refresh the page and try again."
WRONG_KIND_MESSAGE = (
    "This link points to something else now. Go back to the collection and open the item from there."
)


def _path(parent_type: str, parent_id: str) -> str:
    return f"/knowledge-hub/nodes/{parent_type}/{parent_id}"


def _ids(body: dict[str, Any]) -> list[str]:
    return [item["id"] for item in body["items"]]


def test_children_of_a_knowledge_base_with_every_include(
    kb_client: KBClient, audit_kb_id: str, seed_record: SeedRecord
) -> None:
    stem = unique_name("spec-audit-child")
    record_id = seed_record(f"{stem}.txt")

    resp = kb_client.get(
        _path("app", audit_kb_id),
        params={"include": ALL_INCLUDES, "sortBy": "name", "sortOrder": "asc", "limit": 200},
    )

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert body["success"] is True
    assert body["id"] == audit_kb_id
    assert body["currentNode"]["id"] == audit_kb_id
    assert body["currentNode"]["nodeType"] == "app"
    assert body["parentNode"] is None
    assert record_id in _ids(body)
    item = next(i for i in body["items"] if i["id"] == record_id)
    assert item["name"] == stem
    assert item["nodeType"] == "record"
    assert item["permission"]["role"] == "OWNER"
    assert [crumb["id"] for crumb in body["breadcrumbs"]] == [audit_kb_id]
    assert body["counts"] is not None
    assert body["permissions"] is not None
    assert body["filters"]["available"] is not None


def test_children_of_a_folder_name_the_knowledge_base_as_parent(
    kb_client: KBClient, audit_kb_id: str
) -> None:
    folder_id = kb_client.create_folder(audit_kb_id, unique_name("spec-audit-folder"))["id"]
    record_id = kb_client.upload_file(
        audit_kb_id, f"{unique_name()}.txt", b"in a folder", folder_id=folder_id
    )["records"][0]["recordId"]
    wait_for_record(kb_client, record_id)
    try:
        resp = kb_client.get(_path("folder", folder_id), params={"include": "breadcrumbs"})

        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
        body = resp.json()
        assert body["currentNode"]["nodeType"] == "folder"
        assert body["parentNode"]["id"] == audit_kb_id
        assert _ids(body) == [record_id]
        assert [crumb["id"] for crumb in body["breadcrumbs"]] == [audit_kb_id, folder_id]

        # A search under the knowledge base reaches into the folder.
        nested = kb_client.get(_path("app", audit_kb_id), params={"flattened": "true", "nodeTypes": "record"})
        assert nested.status_code == 200, nested.text[:500]
        assert_strict_openapi_exchange(nested, ROUTE)
        assert record_id in _ids(nested.json())
    finally:
        kb_client.delete(f"/{audit_kb_id}/folder/{folder_id}")


def test_children_of_a_file_record_are_empty(
    kb_client: KBClient, audit_kb_id: str, seed_record: SeedRecord
) -> None:
    record_id = seed_record()

    resp = kb_client.get(_path("record", record_id))

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert body["items"] == []
    assert body["currentNode"]["nodeType"] == "record"
    assert body["parentNode"]["id"] == audit_kb_id


def test_reader_browses_a_shared_knowledge_base(
    second_user: SecondUser, shared_kb_id: str, seed_shared_record: SeedRecord
) -> None:
    record_id = seed_shared_record()

    resp = request_as(second_user, "GET", _path("app", shared_kb_id), params={"include": "permissions"})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert _ids(body) == [record_id]
    assert body["items"][0]["permission"]["role"] == "READER"


@pytest.mark.parametrize("parent_type", ["app", "record"])
def test_member_without_access_still_gets_the_parents_name(
    kb_client: KBClient, second_user: SecondUser, audit_kb_id: str, seed_record: SeedRecord, parent_type: str
) -> None:
    stem = unique_name("spec-audit-private")
    record_id = seed_record(f"{stem}.txt")
    parent_id = audit_kb_id if parent_type == "app" else record_id
    as_admin = kb_client.get(_path(parent_type, parent_id))
    assert as_admin.status_code == 200, as_admin.text[:500]

    # The same record answers 404 on GET /knowledgeBase/record/:recordId for this caller.
    resp = request_as(second_user, "GET", _path(parent_type, parent_id))

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert body["items"] == []
    assert body["currentNode"] == as_admin.json()["currentNode"]
    assert body["parentNode"] == as_admin.json()["parentNode"]
    assert request_as(second_user, "GET", f"/record/{record_id}").status_code == 404


def test_record_in_the_trash_is_still_a_valid_parent(
    kb_client: KBClient, seed_record: SeedRecord, trash_on: None
) -> None:
    stem = unique_name("spec-audit-binned")
    record_id = seed_record(f"{stem}.txt")
    assert kb_client.delete(f"/record/{record_id}").json()["softDeleted"] is True

    resp = kb_client.get(_path("record", record_id))

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["currentNode"]["name"] == stem
    assert kb_client.get(f"/record/{record_id}").status_code == 404


@pytest.mark.parametrize("parent_type", ["app", "folder", "record", "recordGroup"])
def test_unknown_parent_is_not_found(kb_client: KBClient, parent_type: str) -> None:
    resp = kb_client.get(_path(parent_type, MISSING_RECORD_ID))

    assert resp.status_code == 404, resp.text[:500]
    assert resp.json()["error"]["message"] == GONE_MESSAGE
    assert_strict_openapi_exchange(resp, ROUTE)


def test_upper_case_parent_id_passes_the_format_check_and_is_not_found(
    kb_client: KBClient, audit_kb_id: str
) -> None:
    # The UUID check ignores case; the lookup does not.
    resp = kb_client.get(_path("app", audit_kb_id.upper()))

    assert resp.status_code == 404, resp.text[:500]
    assert resp.json()["error"]["message"] == GONE_MESSAGE
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("parent_type", ["folder", "recordGroup"])
def test_parent_of_another_kind_is_bad_request(
    kb_client: KBClient, audit_kb_id: str, parent_type: str
) -> None:
    resp = kb_client.get(_path(parent_type, audit_kb_id))

    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["message"] == WRONG_KIND_MESSAGE
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("parent_type", "parent_id", "message"),
    [
        pytest.param("kb", MISSING_RECORD_ID, "Invalid parent_type. Must be one of:", id="unknown-parent-type"),
        pytest.param("APP", MISSING_RECORD_ID, "Invalid parent_type. Must be one of:", id="parent-type-upper-case"),
        pytest.param("app", "not-a-uuid", "Invalid UUID format for parent_id: not-a-uuid", id="parent-id-not-a-uuid"),
        pytest.param("app", UNSAFE_ID, UNSAFE_ID_MESSAGE, id="parent-id-not-a-url-segment"),
        pytest.param(UNSAFE_ID, MISSING_RECORD_ID, UNSAFE_ID_MESSAGE, id="parent-type-not-a-url-segment"),
    ],
)
def test_malformed_parent_is_bad_request(
    kb_client: KBClient, parent_type: str, parent_id: str, message: str
) -> None:
    resp = kb_client.get(_path(parent_type, parent_id))

    assert resp.status_code == 400, resp.text[:500]
    assert message in resp.json()["error"]["message"]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_search_text_too_short_is_bad_request(kb_client: KBClient, audit_kb_id: str) -> None:
    resp = kb_client.get(_path("app", audit_kb_id), params={"q": "a"})

    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["message"] == "Search query must be at least 2 characters"
    assert_strict_openapi_response(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"page": 0}, id="page-zero"),
        pytest.param({"limit": 201}, id="limit-over-200"),
        pytest.param({"onlyContainers": "maybe"}, id="onlyContainers-not-boolean"),
        pytest.param({"flattened": "maybe"}, id="flattened-not-boolean"),
    ],
)
def test_typed_parameter_with_a_wrong_value_is_unprocessable(
    kb_client: KBClient, audit_kb_id: str, params: dict[str, Any]
) -> None:
    resp = kb_client.get(_path("app", audit_kb_id), params=params)

    assert resp.status_code == 422, resp.text[:500]
    assert resp.json()["error"]["code"] == "HTTP_UNPROCESSABLE_ENTITY"
    assert_strict_openapi_response(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


def test_unknown_query_parameters_and_sort_values_are_tolerated(
    kb_client: KBClient, audit_kb_id: str
) -> None:
    with outside_request_contract("undeclared query keys are dropped and an unknown sortBy becomes name"):
        resp = kb_client.get(_path("app", audit_kb_id), params={"view": "grid", "sortBy": "relevance"})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["filters"]["applied"]["sortBy"] == "name"


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="no-token"),
        pytest.param({"Authorization": "Bearer not-a-jwt"}, id="invalid-token"),
    ],
)
def test_rejects_unauthenticated_calls(kb_client: KBClient, headers: dict[str, str]) -> None:
    resp = kb_client.get(_path("app", MISSING_RECORD_ID), auth=False, headers=headers)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_with_a_token_lacking_kb_read_is_forbidden(
    kb_client: KBClient, unscoped_headers: dict[str, str]
) -> None:
    resp = kb_client.get(_path("app", MISSING_RECORD_ID), auth=False, headers=unscoped_headers)

    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["message"] == "Insufficient scope. Required: kb:read"
    assert_strict_openapi_exchange(resp, ROUTE)
