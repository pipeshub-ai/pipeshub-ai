"""Strict OpenAPI audit of POST /api/v1/knowledgeBase/reindex/record/:recordId.

guardPathParams(recordId) -> authenticate -> requireScopes(kb:write) -> zod (depth int -1..100,
statusFilters string[], both optional; other fields, force included, dropped) -> reindexRecord
-> connector service POST /api/v1/records/{record_id}/reindex. Any role on the record's
knowledge base may reindex it, READER included.
"""

from __future__ import annotations

from typing import Any

import pytest
from helper.clients.kb_client import KBClient
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser
from knowledge_base_audit_support import (
    MISSING_RECORD_ID,
    UNSAFE_ID,
    UNSAFE_ID_MESSAGE,
    MakeKb,
    SeedRecord,
    WebRecordGroup,
    request_as,
    upload_text_record,
    wait_for_record,
    web_record_group,
)
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/knowledgeBase/reindex/record/:recordId"
RESPONSE_FIELDS = {"success", "message", "recordId", "recordName", "connector", "eventPublished", "userRole", "depth"}


def _path(record_id: str) -> str:
    return f"/reindex/record/{record_id}"


def test_reindex_a_knowledge_base_file(kb_client: KBClient, make_kb: MakeKb) -> None:
    kb_id = make_kb()
    record_id = upload_text_record(kb_client, kb_id, "spec-audit-reindex.txt")
    wait_for_record(kb_client, record_id)

    resp = kb_client.post(_path(record_id), json={})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {
        "success": True,
        "message": f"Reindex initiated for record {record_id}",
        "recordId": record_id,
        "recordName": "spec-audit-reindex",
        "connector": "KB",
        "eventPublished": True,
        "userRole": "OWNER",
        "depth": 0,
    }


@pytest.mark.parametrize("depth", [-1, 1, 100])
def test_reindex_with_a_depth_names_it_in_the_message(
    kb_client: KBClient, seed_record: SeedRecord, depth: int
) -> None:
    record_id = seed_record()
    resp = kb_client.post(_path(record_id), json={"depth": depth})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["message"] == f"Reindex initiated for record {record_id} with depth {depth}"
    assert resp.json()["depth"] == depth


def test_reindex_a_folder_with_its_content(kb_client: KBClient, make_kb: MakeKb) -> None:
    kb_id = make_kb()
    folder = kb_client.post(f"/{kb_id}/folder", json={"folderName": "spec-audit-folder"})
    assert folder.status_code == 200, folder.text[:500]
    resp = kb_client.post(_path(folder.json()["id"]), json={"depth": 100})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["recordName"] == "spec-audit-folder"


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(None, id="no-body"),
        pytest.param({"statusFilters": ["FAILED", "COMPLETED"]}, id="known-statuses"),
        pytest.param({"statusFilters": ["NOT_A_STATUS"]}, id="any-string-status"),
        pytest.param({"statusFilters": []}, id="no-statuses"),
    ],
)
def test_reindex_with_defaults_and_filters(kb_client: KBClient, seed_record: SeedRecord, payload: Any) -> None:
    record_id = seed_record()
    resp = kb_client.post(_path(record_id), json=payload) if payload is not None else kb_client.post(_path(record_id))
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert set(resp.json()) == RESPONSE_FIELDS
    assert resp.json()["depth"] == 0


def test_reindex_drops_force_and_other_fields(kb_client: KBClient, seed_record: SeedRecord) -> None:
    record_id = seed_record()
    with outside_request_contract("the validator drops force and any field it does not know"):
        resp = kb_client.post(_path(record_id), params={"wait": "true"}, json={"force": True})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["message"] == f"Reindex initiated for record {record_id}"


@pytest.mark.parametrize("role", ["READER", "WRITER"])
def test_reindex_by_any_role_on_the_knowledge_base(
    kb_client: KBClient, second_user: SecondUser, make_kb: MakeKb, role: str
) -> None:
    kb_id = make_kb(member_role=role)
    record_id = upload_text_record(kb_client, kb_id)
    wait_for_record(kb_client, record_id)
    resp = request_as(second_user, "POST", _path(record_id), json={})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["userRole"] == role


def test_reindex_a_connector_record(kb_client: KBClient, synced_record_group: WebRecordGroup) -> None:
    resp = kb_client.post(_path(synced_record_group.record_id), json={})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert set(resp.json()) == RESPONSE_FIELDS


def test_reindex_a_record_of_a_disabled_connector_is_a_conflict(
    kb_client: KBClient, pipeshub_client: PipeshubClient
) -> None:
    with web_record_group(pipeshub_client, kb_client) as group:
        pipeshub_client.toggle_sync(group.connector_id, False)
        resp = kb_client.post(_path(group.record_id), json={})
        assert resp.status_code == 409, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)


def test_reindex_as_member_without_access_is_forbidden(second_user: SecondUser, seed_record: SeedRecord) -> None:
    record_id = seed_record()
    resp = request_as(second_user, "POST", _path(record_id), json={})
    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["message"] == "Insufficient KB permissions. Required: OWNER, WRITER, READER"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_reindex_record_in_the_trash_is_bad_request(
    kb_client: KBClient, seed_record: SeedRecord, trash_on: None
) -> None:
    record_id = seed_record()
    deleted = kb_client.delete(f"/record/{record_id}")
    assert deleted.status_code == 200, deleted.text[:500]
    resp = kb_client.post(_path(record_id), json={})
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["message"] == "Cannot reindex deleted record"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_reindex_unknown_record_is_not_found(kb_client: KBClient, audit_kb_id: str) -> None:
    # A knowledge base id is not a record id.
    for record_id in (MISSING_RECORD_ID, audit_kb_id):
        resp = kb_client.post(_path(record_id), json={})
        assert resp.status_code == 404, resp.text[:500]
        assert resp.json()["error"]["message"] == f"Record not found: {record_id}"
        assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"depth": 101}, id="depth-above-100"),
        pytest.param({"depth": -2}, id="depth-below-minus-1"),
        pytest.param({"depth": 1.5}, id="depth-not-whole"),
        pytest.param({"depth": "1"}, id="depth-a-string"),
        pytest.param({"statusFilters": "FAILED"}, id="filters-not-a-list"),
        pytest.param({"statusFilters": [1]}, id="filter-not-a-string"),
        pytest.param([{"depth": 0}], id="body-a-list"),
    ],
)
def test_reindex_rejects_a_body_outside_the_validator(kb_client: KBClient, payload: Any) -> None:
    resp = kb_client.post(_path(MISSING_RECORD_ID), json=payload)
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_reindex_id_that_cannot_be_a_url_segment_is_bad_request(kb_client: KBClient) -> None:
    resp = kb_client.post(_path(UNSAFE_ID), json={})
    assert resp.status_code == 400, resp.text[:500]
    assert UNSAFE_ID_MESSAGE in resp.json()["error"]["message"]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="no-token"),
        pytest.param({"Authorization": "Bearer not-a-jwt"}, id="invalid-token"),
    ],
)
def test_reindex_rejects_unauthenticated_calls(kb_client: KBClient, headers: dict[str, str]) -> None:
    resp = kb_client.post(_path(MISSING_RECORD_ID), auth=False, headers=headers, json={})
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_reindex_with_a_token_lacking_kb_write_is_forbidden(
    kb_client: KBClient, unscoped_headers: dict[str, str]
) -> None:
    resp = kb_client.post(_path(MISSING_RECORD_ID), auth=False, headers=unscoped_headers, json={})
    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["message"] == "Insufficient scope. Required: kb:write"
    assert_strict_openapi_exchange(resp, ROUTE)
