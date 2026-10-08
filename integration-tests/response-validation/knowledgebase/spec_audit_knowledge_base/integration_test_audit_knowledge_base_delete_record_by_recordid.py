"""Strict OpenAPI audit of DELETE /api/v1/knowledgeBase/record/:recordId.

guardPathParams(recordId) -> authenticate -> requireScopes(kb:delete) -> zod (recordId
only) -> deleteRecord -> connector service (DELETE /api/v1/records/{record_id}), which
removes the record for good, or moves it to the trash while the Labs flag
ENABLE_SOFT_DELETE is on.
"""

from __future__ import annotations

import pytest
from helper.clients.kb_client import KBClient
from helper.second_user import SecondUser
from knowledge_base_audit_support import (
    MISSING_RECORD_ID,
    UNSAFE_ID,
    UNSAFE_ID_MESSAGE,
    SeedRecord,
    request_as,
)
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/knowledgeBase/record/:recordId"
NO_ACCESS_MESSAGE = "You do not have access to this record"


def test_delete_removes_the_record_for_good_while_the_trash_is_off(
    kb_client: KBClient, seed_record: SeedRecord, trash_off: None
) -> None:
    record_id = seed_record()

    resp = kb_client.delete(f"/record/{record_id}")

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {
        "success": True,
        "message": f"Record {record_id} deleted successfully",
        "recordId": record_id,
        "connector": None,
        "timestamp": None,
    }
    assert kb_client.get(f"/record/{record_id}").status_code == 404

    again = kb_client.delete(f"/record/{record_id}")
    assert again.status_code == 404, again.text[:500]
    assert_strict_openapi_exchange(again, ROUTE)


def test_delete_moves_the_record_to_the_trash_while_the_trash_is_on(
    kb_client: KBClient, seed_record: SeedRecord, trash_on: None
) -> None:
    record_id = seed_record()

    resp = kb_client.delete(f"/record/{record_id}")

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert body["success"] is True
    assert body["softDeleted"] is True
    assert body["batchId"]
    assert body["recordId"] == record_id
    assert body["message"] == f"Record {record_id} moved to the trash"
    assert "timestamp" not in body
    assert kb_client.get(f"/record/{record_id}").status_code == 404

    # A record already in the trash is reported like one that does not exist.
    again = kb_client.delete(f"/record/{record_id}")
    assert again.status_code == 404, again.text[:500]
    assert again.json()["error"]["message"] == NO_ACCESS_MESSAGE
    assert_strict_openapi_exchange(again, ROUTE)


def test_delete_reads_neither_query_nor_body(kb_client: KBClient, seed_record: SeedRecord) -> None:
    record_id = seed_record()
    with outside_request_contract("the validator checks only the recordId path parameter"):
        resp = kb_client.delete(
            f"/record/{record_id}", params={"softDelete": "true"}, json={"recordIds": [MISSING_RECORD_ID]}
        )
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["recordId"] == record_id


def test_delete_unknown_record_is_not_found(kb_client: KBClient) -> None:
    resp = kb_client.delete(f"/record/{MISSING_RECORD_ID}")
    assert resp.status_code == 404, resp.text[:500]
    assert resp.json()["error"]["message"] == NO_ACCESS_MESSAGE
    assert_strict_openapi_exchange(resp, ROUTE)


def test_delete_as_member_without_access_is_not_found(
    kb_client: KBClient, second_user: SecondUser, seed_record: SeedRecord
) -> None:
    record_id = seed_record()

    resp = request_as(second_user, "DELETE", f"/record/{record_id}")

    assert resp.status_code == 404, resp.text[:500]
    assert resp.json()["error"]["message"] == NO_ACCESS_MESSAGE
    assert_strict_openapi_exchange(resp, ROUTE)
    assert kb_client.get(f"/record/{record_id}").status_code == 200


def test_delete_as_reader_is_forbidden(
    kb_client: KBClient, second_user: SecondUser, seed_shared_record: SeedRecord
) -> None:
    record_id = seed_shared_record()

    resp = request_as(second_user, "DELETE", f"/record/{record_id}")

    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["message"] == "User lacks permission to delete records"
    assert_strict_openapi_exchange(resp, ROUTE)
    assert kb_client.get(f"/record/{record_id}").status_code == 200


def test_delete_id_that_cannot_be_a_url_segment_is_bad_request(kb_client: KBClient) -> None:
    resp = kb_client.delete(f"/record/{UNSAFE_ID}")
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
def test_delete_rejects_unauthenticated_calls(kb_client: KBClient, headers: dict[str, str]) -> None:
    resp = kb_client.delete(f"/record/{MISSING_RECORD_ID}", auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_delete_with_a_token_lacking_kb_delete_is_forbidden(
    kb_client: KBClient, unscoped_headers: dict[str, str]
) -> None:
    resp = kb_client.delete(f"/record/{MISSING_RECORD_ID}", auth=False, headers=unscoped_headers)
    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["message"] == "Insufficient scope. Required: kb:delete"
    assert_strict_openapi_exchange(resp, ROUTE)
