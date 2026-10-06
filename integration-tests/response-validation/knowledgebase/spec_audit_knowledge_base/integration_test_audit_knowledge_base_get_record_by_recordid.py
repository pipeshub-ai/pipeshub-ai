"""Strict OpenAPI audit of GET /api/v1/knowledgeBase/record/:recordId.

guardPathParams(recordId) -> authenticate -> requireScopes(kb:read) -> zod (query:
convertTo, version) -> getRecordById, which calls the connector service
(GET /api/v1/records/{record_id}) without the query: both parameters are validated
here and used only by GET /knowledgeBase/stream/record/:recordId.
"""

from __future__ import annotations

from typing import Any

import pytest
from helper.clients.kb_client import KBClient
from helper.second_user import SecondUser
from knowledge_base_audit_support import (
    MALFORMED_ID,
    MISSING_RECORD_ID,
    UNSAFE_ID,
    UNSAFE_ID_MESSAGE,
    SeedRecord,
    request_as,
    unique_name,
)
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/knowledgeBase/record/:recordId"

Query = dict[str, str] | list[tuple[str, str]]


def test_get_returns_the_record_with_its_knowledge_base(
    kb_client: KBClient, audit_kb_id: str, seed_record: SeedRecord
) -> None:
    stem = unique_name("spec-audit-get")
    record_id = seed_record(f"{stem}.txt")

    resp = kb_client.get(f"/record/{record_id}")

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    record = body["record"]
    assert record["id"] == record_id
    assert record["recordName"] == stem
    assert record["recordType"] == "FILE"
    assert record["origin"] == "UPLOAD"
    assert record["fileRecord"]["extension"] == "txt"
    # Graph bookkeeping keys are removed by the gateway.
    assert not {"_id", "_rev"} & record.keys()
    assert not {"_id", "_rev", "_key"} & record["fileRecord"].keys()
    assert body["knowledgeBase"]["id"] == audit_kb_id
    assert body["folder"] is None
    assert body["permissions"]


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"convertTo": "pdf"}, id="convertTo"),
        pytest.param({"version": "0"}, id="version-zero"),
        pytest.param({"version": "42"}, id="version-that-does-not-exist"),
        pytest.param({"convertTo": "no-such-format", "version": "7"}, id="both"),
    ],
)
def test_get_validates_but_does_not_use_convert_to_and_version(
    kb_client: KBClient, seed_record: SeedRecord, params: dict[str, str]
) -> None:
    record_id = seed_record()
    plain = kb_client.get(f"/record/{record_id}")
    assert plain.status_code == 200, plain.text[:500]

    resp = kb_client.get(f"/record/{record_id}", params=params)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["record"]["id"] == record_id
    assert resp.json()["record"]["version"] == plain.json()["record"]["version"]
    assert resp.json()["record"]["mimeType"] == plain.json()["record"]["mimeType"]


def test_get_ignores_unknown_query_parameters(kb_client: KBClient, seed_record: SeedRecord) -> None:
    record_id = seed_record()
    with outside_request_contract("the validator drops query keys it does not know"):
        resp = kb_client.get(f"/record/{record_id}", params={"include": "content", "orgId": "another-org"})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["record"]["id"] == record_id


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"version": "latest"}, id="version-not-digits"),
        pytest.param({"version": "-1"}, id="version-negative"),
        pytest.param({"version": "1.5"}, id="version-decimal"),
        pytest.param({"version": ""}, id="version-empty"),
        pytest.param([("convertTo", "pdf"), ("convertTo", "txt")], id="convertTo-repeated"),
    ],
)
def test_get_rejects_a_query_outside_the_validator(kb_client: KBClient, params: Query) -> None:
    # Validated before the record is looked up, so the id does not have to exist.
    resp = kb_client.get(f"/record/{MISSING_RECORD_ID}", params=params)
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("record_id", [MISSING_RECORD_ID, MALFORMED_ID], ids=["unknown-id", "not-a-uuid"])
def test_get_unknown_record_is_not_found(kb_client: KBClient, record_id: str) -> None:
    resp = kb_client.get(f"/record/{record_id}")
    assert resp.status_code == 404, resp.text[:500]
    assert resp.json()["error"]["message"] == f"Record {record_id} not found"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_get_as_member_without_access_is_not_found(
    second_user: SecondUser, seed_record: SeedRecord
) -> None:
    record_id = seed_record()
    # Same answer as for an id that does not exist, so a record's existence is not revealed.
    resp = request_as(second_user, "GET", f"/record/{record_id}")
    assert resp.status_code == 404, resp.text[:500]
    assert resp.json()["error"]["message"] == f"Record {record_id} not found"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_get_as_reader_reports_the_callers_role(
    second_user: SecondUser, shared_kb_id: str, seed_shared_record: SeedRecord
) -> None:
    record_id = seed_shared_record()

    resp = request_as(second_user, "GET", f"/record/{record_id}")

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert body["knowledgeBase"]["id"] == shared_kb_id
    assert [p["relationship"] for p in body["permissions"]] == ["READER"]


def test_get_restored_record_says_when_it_came_back(
    kb_client: KBClient, seed_record: SeedRecord, trash_on: None
) -> None:
    record_id = seed_record()
    assert kb_client.delete(f"/record/{record_id}").json()["softDeleted"] is True
    restored = kb_client.post(f"/record/{record_id}/restore")
    assert restored.status_code == 200, restored.text[:500]

    resp = kb_client.get(f"/record/{record_id}")

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    record = resp.json()["record"]
    assert record["isDeleted"] is False
    assert isinstance(record["restoredAtTimestamp"], int)


def test_get_record_in_the_trash_is_not_found(
    kb_client: KBClient, seed_record: SeedRecord, trash_on: None
) -> None:
    record_id = seed_record()
    deleted = kb_client.delete(f"/record/{record_id}")
    assert deleted.status_code == 200, deleted.text[:500]
    assert deleted.json()["softDeleted"] is True

    resp = kb_client.get(f"/record/{record_id}")
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_get_id_that_cannot_be_a_url_segment_is_bad_request(kb_client: KBClient) -> None:
    resp = kb_client.get(f"/record/{UNSAFE_ID}")
    assert resp.status_code == 400, resp.text[:500]
    error: dict[str, Any] = resp.json()["error"]
    assert error["code"] == "HTTP_BAD_REQUEST"
    assert UNSAFE_ID_MESSAGE in error["message"]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="no-token"),
        pytest.param({"Authorization": "Bearer not-a-jwt"}, id="invalid-token"),
    ],
)
def test_get_rejects_unauthenticated_calls(kb_client: KBClient, headers: dict[str, str]) -> None:
    resp = kb_client.get(f"/record/{MISSING_RECORD_ID}", auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_get_with_a_token_lacking_kb_read_is_forbidden(
    kb_client: KBClient, unscoped_headers: dict[str, str]
) -> None:
    resp = kb_client.get(f"/record/{MISSING_RECORD_ID}", auth=False, headers=unscoped_headers)
    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["message"] == "Insufficient scope. Required: kb:read"
    assert_strict_openapi_exchange(resp, ROUTE)
