"""Strict OpenAPI audit of POST /api/v1/knowledgeBase/records/restore.

authenticate -> requireScopes(kb:delete) -> zod body (recordIds: 1..100 non-empty
strings) -> connector service. The connector answers 200 whatever the per-id
outcomes are, so a refusal for one id (missing, no access, trash turned off)
shows up inside ``results`` and never as the HTTP status.
"""

from __future__ import annotations

from typing import Any

import pytest
from helper.clients.kb_client import KBClient
from helper.second_user import SecondUser
from knowledge_base_audit_support import (
    MAX_RESTORE_RECORD_IDS,
    MISSING_RECORD_ID,
    SOFT_DELETE_OFF_MESSAGE,
    SeedRecord,
    body_spec_refusals,
    request_as,
    unique_name,
    wait_for_record,
)
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/knowledgeBase/records/restore"
PATH = "/records/restore"


def _trash(kb_client: KBClient, record_id: str) -> str:
    """Delete while the trash is on and return the delete batch id."""
    deleted = kb_client.delete(f"/record/{record_id}")
    assert deleted.status_code == 200, deleted.text[:500]
    assert deleted.json()["softDeleted"] is True, deleted.text[:500]
    return str(deleted.json()["batchId"])


def _assert_outcomes(body: dict[str, Any], record_ids: list[str]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = body["results"]
    assert [r["recordId"] for r in results] == record_ids
    failed = [r for r in results if not r["success"]]
    assert body["failedCount"] == len(failed)
    assert body["success"] is (not failed)
    for outcome in results:
        if outcome["success"]:
            assert outcome["code"] == 200 and outcome["message"]
        else:
            assert outcome["code"] != 200 and outcome["reason"]
    return results


def test_restore_answers_each_id_with_its_own_outcome(
    kb_client: KBClient, audit_kb_id: str, seed_record: SeedRecord, trash_on: None
) -> None:
    stem = unique_name("spec-audit-batch")
    trashed_id = seed_record(f"{stem}.txt")
    batch_id = _trash(kb_client, trashed_id)
    untouched_id = seed_record()
    folder_name = unique_name("spec-audit-folder")
    folder_id = kb_client.create_folder(audit_kb_id, folder_name)["id"]
    inside_id = kb_client.upload_file(
        audit_kb_id, f"{unique_name()}.txt", b"in a folder", folder_id=folder_id
    )["records"][0]["recordId"]
    wait_for_record(kb_client, inside_id)
    try:
        _trash(kb_client, inside_id)
        _trash(kb_client, folder_id)
        record_ids = [trashed_id, untouched_id, inside_id, MISSING_RECORD_ID]

        resp = kb_client.post(PATH, json={"recordIds": record_ids})

        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
        body = resp.json()
        restored, untouched, blocked, missing = _assert_outcomes(body, record_ids)
        assert restored == {
            "recordId": trashed_id,
            "success": True,
            "code": 200,
            "message": "Restored 1 item(s).",
            "batchId": batch_id,
            "restoredRecords": [{"recordId": trashed_id, "name": stem}],
        }
        # Unlike the single restore, a null batchId is kept here.
        assert untouched["restoredRecords"] == [] and untouched["batchId"] is None
        assert blocked["code"] == 409 and blocked["parentId"] == folder_id
        assert missing["code"] == 404
        assert body["restoredCount"] == 1
        assert body["failedCount"] == 2
        assert kb_client.get(f"/record/{trashed_id}").status_code == 200
    finally:
        # Out of the trash again so the module's knowledge base cleanup removes both for good.
        kb_client.post(PATH, json={"recordIds": [folder_id]})
        kb_client.post(PATH, json={"recordIds": [inside_id]})


def test_restore_reports_all_restored_as_success(
    kb_client: KBClient, seed_record: SeedRecord, trash_on: None
) -> None:
    record_ids = [seed_record(), seed_record()]
    for record_id in record_ids:
        _trash(kb_client, record_id)

    resp = kb_client.post(PATH, json={"recordIds": record_ids})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    _assert_outcomes(body, record_ids)
    assert body["success"] is True
    assert body["restoredCount"] == 2
    assert body["failedCount"] == 0


def test_restore_answers_a_repeated_id_once(
    kb_client: KBClient, seed_record: SeedRecord, trash_on: None
) -> None:
    record_id = seed_record()
    _trash(kb_client, record_id)

    resp = kb_client.post(PATH, json={"recordIds": [record_id, record_id]})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    _assert_outcomes(resp.json(), [record_id])
    assert resp.json()["restoredCount"] == 1


def test_restore_as_member_without_access_is_refused_inside_200(
    kb_client: KBClient, second_user: SecondUser, seed_record: SeedRecord, trash_on: None
) -> None:
    record_id = seed_record()
    _trash(kb_client, record_id)

    resp = request_as(second_user, "POST", PATH, json={"recordIds": [record_id]})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    (outcome,) = _assert_outcomes(resp.json(), [record_id])
    assert outcome == {
        "recordId": record_id,
        "success": False,
        "code": 404,
        "reason": "Knowledge base not found",
    }
    assert resp.json()["restoredCount"] == 0


def test_restore_as_reader_is_refused_inside_200(
    kb_client: KBClient, second_user: SecondUser, seed_shared_record: SeedRecord, trash_on: None
) -> None:
    record_id = seed_shared_record()
    _trash(kb_client, record_id)

    resp = request_as(second_user, "POST", PATH, json={"recordIds": [record_id]})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    (outcome,) = _assert_outcomes(resp.json(), [record_id])
    assert outcome["code"] == 403
    assert "You need edit access to this collection" in outcome["reason"]


def test_restore_while_the_trash_is_off_refuses_every_id_inside_200(
    kb_client: KBClient, seed_record: SeedRecord, trash_off: None
) -> None:
    record_ids = [seed_record(), MISSING_RECORD_ID]

    resp = kb_client.post(PATH, json={"recordIds": record_ids})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    results = _assert_outcomes(body, record_ids)
    assert all(r["code"] == 403 and SOFT_DELETE_OFF_MESSAGE in r["reason"] for r in results)
    assert body == {"success": False, "restoredCount": 0, "failedCount": 2, "results": results}


def test_restore_ignores_fields_other_than_record_ids(kb_client: KBClient, trash_off: None) -> None:
    with outside_request_contract("the validator drops body fields it does not know"):
        resp = kb_client.post(
            PATH, json={"recordIds": [MISSING_RECORD_ID], "force": True, "kbId": "anything"}
        )
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    _assert_outcomes(resp.json(), [MISSING_RECORD_ID])
    assert body_spec_refusals(resp, ROUTE) == []


def test_restore_accepts_the_most_ids_allowed(kb_client: KBClient, trash_off: None) -> None:
    record_ids = [f"00000000-0000-4000-8000-{n:012d}" for n in range(MAX_RESTORE_RECORD_IDS)]

    resp = kb_client.post(PATH, json={"recordIds": record_ids})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["failedCount"] == MAX_RESTORE_RECORD_IDS


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="recordIds-missing"),
        pytest.param({"recordIds": None}, id="recordIds-null"),
        pytest.param({"recordIds": MISSING_RECORD_ID}, id="recordIds-not-a-list"),
        pytest.param({"recordIds": []}, id="empty-list"),
        pytest.param({"recordIds": [MISSING_RECORD_ID] * (MAX_RESTORE_RECORD_IDS + 1)}, id="over-limit"),
        pytest.param({"recordIds": [MISSING_RECORD_ID, 7]}, id="id-not-a-string"),
        pytest.param({"recordIds": [""]}, id="id-empty"),
        pytest.param({"recordIds": [" \t "]}, id="id-only-whitespace"),
        pytest.param([MISSING_RECORD_ID], id="body-is-a-list"),
    ],
)
def test_restore_rejects_a_body_outside_the_validator(kb_client: KBClient, payload: Any) -> None:
    resp = kb_client.post(PATH, json=payload)
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_restore_without_a_body_is_rejected(kb_client: KBClient) -> None:
    resp = kb_client.post(PATH)
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="no-token"),
        pytest.param({"Authorization": "Bearer not-a-jwt"}, id="invalid-token"),
    ],
)
def test_restore_rejects_unauthenticated_calls(kb_client: KBClient, headers: dict[str, str]) -> None:
    resp = kb_client.post(PATH, json={"recordIds": [MISSING_RECORD_ID]}, auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_restore_with_a_token_lacking_kb_delete_is_forbidden(
    kb_client: KBClient, unscoped_headers: dict[str, str]
) -> None:
    resp = kb_client.post(
        PATH, json={"recordIds": [MISSING_RECORD_ID]}, auth=False, headers=unscoped_headers
    )
    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["message"] == "Insufficient scope. Required: kb:delete"
    assert_strict_openapi_exchange(resp, ROUTE)
