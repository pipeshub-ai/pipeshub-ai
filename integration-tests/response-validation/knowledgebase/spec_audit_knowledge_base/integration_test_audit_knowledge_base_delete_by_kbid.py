"""Strict OpenAPI audit of DELETE /api/v1/knowledgeBase/:kbId.

guardPathParams(kbId) -> authenticate -> requireScopes(kb:delete) -> zod (kbId non-empty) ->
deleteKnowledgeBase -> connector service DELETE /api/v1/kb/{kb_id}, which removes the knowledge
base with its folders, records and permissions. Only an OWNER may do it.
"""

from __future__ import annotations

import pytest
from helper.clients.kb_client import KBClient
from helper.second_user import SecondUser
from knowledge_base_audit_support import (
    MALFORMED_ID,
    MISSING_RECORD_ID,
    UNSAFE_ID,
    UNSAFE_ID_MESSAGE,
    MakeKb,
    SeedRecord,
    request_as,
    upload_text_record,
    wait_for_record,
)
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/knowledgeBase/:kbId"
DELETED = {"success": True, "message": "Knowledge base deleted successfully"}
NOT_FOUND_MESSAGE = "Knowledge base not found"


def test_delete_removes_the_knowledge_base_with_its_records(kb_client: KBClient, make_kb: MakeKb) -> None:
    kb_id = make_kb()
    folder = kb_client.post(f"/{kb_id}/folder", json={"folderName": "spec-audit-folder"})
    assert folder.status_code == 200, folder.text[:500]
    record_id = upload_text_record(kb_client, kb_id)
    wait_for_record(kb_client, record_id)

    resp = kb_client.delete(f"/{kb_id}")

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == DELETED
    assert kb_client.get(f"/{kb_id}").status_code == 404
    assert kb_client.get(f"/record/{record_id}").status_code == 404

    again = kb_client.delete(f"/{kb_id}")
    assert again.status_code == 404, again.text[:500]
    assert_strict_openapi_exchange(again, ROUTE)


def test_delete_as_member_owner_is_allowed(second_user: SecondUser, make_kb: MakeKb) -> None:
    kb_id = make_kb(member_role="OWNER")
    resp = request_as(second_user, "DELETE", f"/{kb_id}")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == DELETED


def test_delete_ignores_query_and_body(kb_client: KBClient, make_kb: MakeKb) -> None:
    kb_id = make_kb()
    with outside_request_contract("the validator checks only the kbId path parameter"):
        resp = kb_client.delete(f"/{kb_id}", params={"force": "false"}, json={"keepRecords": True})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert kb_client.get(f"/{kb_id}").status_code == 404


@pytest.mark.parametrize("role", ["READER", "WRITER"])
def test_delete_as_non_owner_is_forbidden(
    kb_client: KBClient, second_user: SecondUser, make_kb: MakeKb, role: str
) -> None:
    kb_id = make_kb(member_role=role)
    resp = request_as(second_user, "DELETE", f"/{kb_id}")
    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["message"] == "Only KB owners can delete knowledge bases"
    assert_strict_openapi_exchange(resp, ROUTE)
    assert kb_client.get(f"/{kb_id}").status_code == 200


def test_delete_as_member_without_a_role_is_not_found(
    kb_client: KBClient, second_user: SecondUser, make_kb: MakeKb
) -> None:
    kb_id = make_kb()
    resp = request_as(second_user, "DELETE", f"/{kb_id}")
    assert resp.status_code == 404, resp.text[:500]
    assert resp.json()["error"]["message"] == NOT_FOUND_MESSAGE
    assert_strict_openapi_exchange(resp, ROUTE)
    assert kb_client.get(f"/{kb_id}").status_code == 200


@pytest.mark.parametrize("kb_id", [MISSING_RECORD_ID, MALFORMED_ID], ids=["unknown-uuid", "not-a-uuid"])
def test_delete_unknown_knowledge_base_is_not_found(kb_client: KBClient, kb_id: str) -> None:
    resp = kb_client.delete(f"/{kb_id}")
    assert resp.status_code == 404, resp.text[:500]
    assert resp.json()["error"]["message"] == NOT_FOUND_MESSAGE
    assert_strict_openapi_exchange(resp, ROUTE)


def test_delete_record_id_is_not_a_knowledge_base(kb_client: KBClient, seed_record: SeedRecord) -> None:
    record_id = seed_record()
    resp = kb_client.delete(f"/{record_id}")
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert kb_client.get(f"/record/{record_id}").status_code == 200


def test_delete_id_that_cannot_be_a_url_segment_is_bad_request(kb_client: KBClient) -> None:
    resp = kb_client.delete(f"/{UNSAFE_ID}")
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
    resp = kb_client.delete(f"/{MISSING_RECORD_ID}", auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_delete_with_a_token_lacking_kb_delete_is_forbidden(
    kb_client: KBClient, unscoped_headers: dict[str, str]
) -> None:
    resp = kb_client.delete(f"/{MISSING_RECORD_ID}", auth=False, headers=unscoped_headers)
    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["message"] == "Insufficient scope. Required: kb:delete"
    assert_strict_openapi_exchange(resp, ROUTE)
