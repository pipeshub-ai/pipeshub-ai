"""Strict OpenAPI audit of POST /api/v1/knowledgeBase/record/:recordId/restore.

The connector service checks the Labs flag ENABLE_SOFT_DELETE before anything
else and answers 403 while it is off (the default). The flag is org-wide, so
these tests read it and expect what the code answers in the state they find,
instead of switching it under the other suites.
"""

from __future__ import annotations

import pytest
from knowledge_base_audit_support import (
    MISSING_RECORD_ID,
    SeedRecord,
    request_as,
)
from helper.clients.kb_client import KBClient
from helper.second_user import SecondUser
from pipeshub_client import PipeshubClient
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/knowledgeBase/record/:recordId/restore"
EFFECTIVE_FLAGS_PATH = "/api/v1/configurationManager/platform/feature-flags/effective"
SOFT_DELETE_FLAG = "ENABLE_SOFT_DELETE"
TURNED_OFF_STATUS = 403


@pytest.fixture(scope="module")
def soft_delete_on(pipeshub_client: PipeshubClient) -> bool:
    resp = pipeshub_client.request("GET", EFFECTIVE_FLAGS_PATH)
    assert resp.status_code == 200, resp.text[:500]
    return bool((resp.json().get("featureFlags") or {}).get(SOFT_DELETE_FLAG, False))


def test_restore_without_token_is_unauthorized(kb_client: KBClient) -> None:
    resp = kb_client.post(f"/record/{MISSING_RECORD_ID}/restore", auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_restore_unknown_record(kb_client: KBClient, soft_delete_on: bool) -> None:
    resp = kb_client.post(f"/record/{MISSING_RECORD_ID}/restore")
    expected = 404 if soft_delete_on else TURNED_OFF_STATUS
    assert resp.status_code == expected, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_restore_as_member_without_access(
    second_user: SecondUser, seed_record: SeedRecord, soft_delete_on: bool
) -> None:
    record_id = seed_record()

    resp = request_as(second_user, "POST", f"/record/{record_id}/restore")
    # No role on the collection is reported as 404, so its existence is not revealed.
    expected = 404 if soft_delete_on else TURNED_OFF_STATUS
    assert resp.status_code == expected, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_restore_record_that_is_not_in_the_trash(
    kb_client: KBClient, seed_record: SeedRecord, soft_delete_on: bool
) -> None:
    record_id = seed_record()

    resp = kb_client.post(f"/record/{record_id}/restore")
    expected = 200 if soft_delete_on else TURNED_OFF_STATUS
    assert resp.status_code == expected, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    if soft_delete_on:
        body = resp.json()
        assert body["success"] is True
        assert body["restoredRecords"] == []
        # The service sends batchId null and the route drops nulls.
        assert "batchId" not in body


def test_restore_trashed_record(
    kb_client: KBClient, seed_record: SeedRecord, soft_delete_on: bool
) -> None:
    if not soft_delete_on:
        pytest.skip(
            f"{SOFT_DELETE_FLAG} is off: a delete removes the record for good, so nothing can be "
            "restored. Turn on 'Move Deleted Records to the Trash' in Labs to run this case."
        )
    record_id = seed_record()
    deleted = kb_client.delete(f"/record/{record_id}")
    assert deleted.ok, deleted.text[:500]

    resp = kb_client.post(f"/record/{record_id}/restore")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    body = resp.json()
    assert body["success"] is True
    assert body["batchId"]
    assert [item["recordId"] for item in body["restoredRecords"]] == [record_id]
