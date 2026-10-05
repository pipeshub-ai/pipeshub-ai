"""Strict OpenAPI audit of POST /api/v1/knowledgeBase/records/restore.

authenticate -> requireScopes(kb:delete) -> zod body (recordIds: 1..100 non-empty
strings) -> connector service. The connector answers 200 whatever the per-id
outcomes are, so a refusal for one id (missing, no access, trash turned off)
shows up inside ``results`` and never as the HTTP status.
"""

from __future__ import annotations

from typing import Any

import pytest
from knowledge_base_audit_support import (
    MAX_RESTORE_RECORD_IDS,
    MISSING_RECORD_ID,
    SeedRecord,
    request_as,
)
from helper.clients.kb_client import KBClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/knowledgeBase/records/restore"
PATH = "/records/restore"


def _assert_outcomes(body: dict[str, Any], record_ids: list[str]) -> list[dict[str, Any]]:
    """Checks that hold whether or not the org has the trash (soft delete) turned on."""
    results = body["results"]
    assert [r["recordId"] for r in results] == record_ids
    failed = [r for r in results if not r["success"]]
    assert body["failedCount"] == len(failed)
    assert body["success"] is (not failed)
    for outcome in failed:
        assert outcome["code"] != 200 and outcome["reason"]
    return results


def test_restore_deleted_and_missing_record_answers_per_id(
    kb_client: KBClient, seed_record: SeedRecord
) -> None:
    record_id = seed_record()
    deleted = kb_client.delete(f"/record/{record_id}")
    assert deleted.status_code == 200, deleted.text[:500]
    record_ids = [record_id, MISSING_RECORD_ID]

    resp = kb_client.post(PATH, json={"recordIds": record_ids})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    body = resp.json()
    restored, missing = _assert_outcomes(body, record_ids)
    assert missing["success"] is False
    assert body["failedCount"] >= 1
    assert body["success"] is False
    if restored["success"]:
        assert body["restoredCount"] >= 1
        assert kb_client.get(f"/record/{record_id}").status_code == 200


def test_member_without_access_gets_refusal_inside_200(
    kb_client: KBClient, second_user: SecondUser, seed_record: SeedRecord
) -> None:
    record_id = seed_record()
    deleted = kb_client.delete(f"/record/{record_id}")
    assert deleted.status_code == 200, deleted.text[:500]

    resp = request_as(second_user, "POST", PATH, json={"recordIds": [record_id]})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    body = resp.json()
    (outcome,) = _assert_outcomes(body, [record_id])
    assert outcome["success"] is False
    assert body["restoredCount"] == 0


@pytest.mark.parametrize(
    "record_ids",
    [
        pytest.param([], id="empty-list"),
        pytest.param([MISSING_RECORD_ID] * (MAX_RESTORE_RECORD_IDS + 1), id="over-limit"),
    ],
)
def test_restore_rejects_record_ids_outside_limits(
    kb_client: KBClient, record_ids: list[str]
) -> None:
    resp = kb_client.post(PATH, json={"recordIds": record_ids})
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_restore_without_token_is_unauthorized(kb_client: KBClient) -> None:
    resp = kb_client.post(PATH, json={"recordIds": [MISSING_RECORD_ID]}, auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
