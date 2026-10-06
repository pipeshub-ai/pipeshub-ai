"""Strict OpenAPI audit of POST /api/v1/knowledgeBase/record/:recordId/restore.

guardPathParams(recordId) -> authenticate -> requireScopes(kb:delete) -> zod (recordId
only) -> connector service, which checks the Labs flag ENABLE_SOFT_DELETE before anything
else and answers 403 while it is off (the default). The flag is org-wide and read on every
call, so each test holds it at the value it needs and puts it back.
"""

from __future__ import annotations

import pytest
from helper.clients.kb_client import KBClient
from helper.second_user import SecondUser
from knowledge_base_audit_support import (
    MISSING_RECORD_ID,
    SOFT_DELETE_OFF_MESSAGE,
    UNSAFE_ID,
    UNSAFE_ID_MESSAGE,
    SeedRecord,
    request_as,
    unique_name,
    wait_for_record,
)
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/knowledgeBase/record/:recordId/restore"


def _trash(kb_client: KBClient, record_id: str) -> str:
    """Delete while the trash is on and return the delete batch id."""
    deleted = kb_client.delete(f"/record/{record_id}")
    assert deleted.status_code == 200, deleted.text[:500]
    assert deleted.json()["softDeleted"] is True, deleted.text[:500]
    return str(deleted.json()["batchId"])


def test_restore_brings_a_trashed_record_back(
    kb_client: KBClient, seed_record: SeedRecord, trash_on: None
) -> None:
    stem = unique_name("spec-audit-restore")
    record_id = seed_record(f"{stem}.txt")
    batch_id = _trash(kb_client, record_id)

    resp = kb_client.post(f"/record/{record_id}/restore")

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {
        "success": True,
        "message": "Restored 1 item(s).",
        "batchId": batch_id,
        "restoredRecords": [{"recordId": record_id, "name": stem}],
    }
    assert kb_client.get(f"/record/{record_id}").status_code == 200


def test_restore_record_that_is_not_in_the_trash_restores_nothing(
    kb_client: KBClient, seed_record: SeedRecord, trash_on: None
) -> None:
    record_id = seed_record()

    resp = kb_client.post(f"/record/{record_id}/restore")

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    # The service sends batchId null and the route drops nulls.
    assert resp.json() == {
        "success": True,
        "message": "This item isn't in the trash, so there was nothing to restore.",
        "restoredRecords": [],
    }


def test_restore_renames_a_record_whose_name_was_taken(
    kb_client: KBClient, audit_kb_id: str, seed_record: SeedRecord, trash_on: None
) -> None:
    stem = unique_name("spec-audit-taken")
    record_id = seed_record(f"{stem}.txt")
    _trash(kb_client, record_id)
    # seed_record deletes this one too; it takes the trashed record's name.
    seed_record(f"{stem}.txt")

    resp = kb_client.post(f"/record/{record_id}/restore")

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["restoredRecords"] == [
        {"recordId": record_id, "name": f"{stem} (restored)", "renamedFrom": stem}
    ]


def test_restore_record_whose_folder_is_still_in_the_trash_is_a_conflict(
    kb_client: KBClient, audit_kb_id: str, trash_on: None
) -> None:
    folder_name = unique_name("spec-audit-folder")
    folder_id = kb_client.create_folder(audit_kb_id, folder_name)["id"]
    stem = unique_name("spec-audit-in-folder")
    uploaded = kb_client.upload_file(audit_kb_id, f"{stem}.txt", b"in a folder", folder_id=folder_id)
    record_id = uploaded["records"][0]["recordId"]
    wait_for_record(kb_client, record_id)
    try:
        _trash(kb_client, record_id)
        _trash(kb_client, folder_id)

        resp = kb_client.post(f"/record/{record_id}/restore")

        assert resp.status_code == 409, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
        assert resp.json()["error"]["message"] == (
            f"'{stem}' was in '{folder_name}', which is also in the trash. "
            f"Restore '{folder_name}' first, then restore '{stem}'."
        )
    finally:
        # Out of the trash again so the module's knowledge base cleanup removes both for good.
        kb_client.post(f"/record/{folder_id}/restore")
        kb_client.post(f"/record/{record_id}/restore")


def test_restore_unknown_record_is_not_found(kb_client: KBClient, trash_on: None) -> None:
    resp = kb_client.post(f"/record/{MISSING_RECORD_ID}/restore")
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_restore_as_member_without_access_is_not_found(
    kb_client: KBClient, second_user: SecondUser, seed_record: SeedRecord, trash_on: None
) -> None:
    record_id = seed_record()
    _trash(kb_client, record_id)

    resp = request_as(second_user, "POST", f"/record/{record_id}/restore")

    # No role on the collection is reported as 404, so its existence is not revealed.
    assert resp.status_code == 404, resp.text[:500]
    assert resp.json()["error"]["message"] == "Knowledge base not found"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_restore_as_reader_is_forbidden(
    kb_client: KBClient, second_user: SecondUser, seed_shared_record: SeedRecord, trash_on: None
) -> None:
    record_id = seed_shared_record()
    _trash(kb_client, record_id)

    resp = request_as(second_user, "POST", f"/record/{record_id}/restore")

    assert resp.status_code == 403, resp.text[:500]
    assert "You need edit access to this collection to restore this item" in resp.json()["error"]["message"]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_restore_reads_neither_query_nor_body(
    kb_client: KBClient, seed_record: SeedRecord, trash_on: None
) -> None:
    record_id = seed_record()
    _trash(kb_client, record_id)
    with outside_request_contract("the validator checks only the recordId path parameter"):
        resp = kb_client.post(
            f"/record/{record_id}/restore",
            params={"dryRun": "true"},
            json={"recordIds": [MISSING_RECORD_ID], "newName": "ignored"},
        )
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert [item["recordId"] for item in resp.json()["restoredRecords"]] == [record_id]


@pytest.mark.parametrize("existing", [True, False], ids=["existing-record", "unknown-record"])
def test_restore_while_the_trash_is_off_is_forbidden(
    kb_client: KBClient, seed_record: SeedRecord, trash_off: None, existing: bool
) -> None:
    record_id = seed_record() if existing else MISSING_RECORD_ID

    resp = kb_client.post(f"/record/{record_id}/restore")

    assert resp.status_code == 403, resp.text[:500]
    assert SOFT_DELETE_OFF_MESSAGE in resp.json()["error"]["message"]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_restore_id_that_cannot_be_a_url_segment_is_bad_request(kb_client: KBClient) -> None:
    resp = kb_client.post(f"/record/{UNSAFE_ID}/restore")
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
def test_restore_rejects_unauthenticated_calls(kb_client: KBClient, headers: dict[str, str]) -> None:
    resp = kb_client.post(f"/record/{MISSING_RECORD_ID}/restore", auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_restore_with_a_token_lacking_kb_delete_is_forbidden(
    kb_client: KBClient, unscoped_headers: dict[str, str]
) -> None:
    resp = kb_client.post(f"/record/{MISSING_RECORD_ID}/restore", auth=False, headers=unscoped_headers)
    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["message"] == "Insufficient scope. Required: kb:delete"
    assert_strict_openapi_exchange(resp, ROUTE)

