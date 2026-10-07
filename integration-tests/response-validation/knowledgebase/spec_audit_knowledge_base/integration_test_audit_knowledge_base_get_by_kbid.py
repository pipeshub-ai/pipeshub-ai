"""Strict OpenAPI audit of GET /api/v1/knowledgeBase/:kbId.

guardPathParams(kbId) -> authenticate -> requireScopes(kb:read) -> zod (kbId non-empty) ->
getKnowledgeBase -> connector service GET /api/v1/kb/{kb_id}. The connector answers 404
both for an unknown id and for a knowledge base the caller holds no role on.
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
    request_as,
)
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/knowledgeBase/:kbId"
NOT_FOUND_MESSAGE = "Knowledge base not found"
KB_FIELDS = {"id", "name", "connectorId", "createdAtTimestamp", "updatedAtTimestamp", "createdBy", "userRole", "folders"}


def test_get_returns_the_knowledge_base_with_all_its_folders(kb_client: KBClient, make_kb: MakeKb) -> None:
    kb_id = make_kb()
    folder = kb_client.post(f"/{kb_id}/folder", json={"folderName": "spec-audit-folder"})
    assert folder.status_code == 200, folder.text[:500]
    nested = kb_client.post(f"/{kb_id}/folder", params={"folderId": folder.json()["id"]}, json={"folderName": "nested"})
    assert nested.status_code == 200, nested.text[:500]

    resp = kb_client.get(f"/{kb_id}")

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert set(body) == KB_FIELDS, body
    assert body["id"] == kb_id
    assert body["userRole"] == "OWNER"
    assert body["connectorId"] is None
    # Not only the root: a nested folder is listed too, with nothing that tells it apart.
    assert {f["id"] for f in body["folders"]} == {folder.json()["id"], nested.json()["id"]}
    assert all(set(f) == {"id", "name", "createdAtTimestamp"} for f in body["folders"]), body["folders"]


def test_get_as_reader_reports_the_readers_role(second_user: SecondUser, make_kb: MakeKb) -> None:
    kb_id = make_kb(member_role="READER")
    resp = request_as(second_user, "GET", f"/{kb_id}")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["userRole"] == "READER"


def test_get_ignores_the_query(kb_client: KBClient, make_kb: MakeKb) -> None:
    kb_id = make_kb()
    with outside_request_contract("the validator checks only the kbId path parameter"):
        resp = kb_client.get(f"/{kb_id}", params={"include": "records"})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["id"] == kb_id


@pytest.mark.parametrize("kb_id", [MISSING_RECORD_ID, MALFORMED_ID], ids=["unknown-uuid", "not-a-uuid"])
def test_get_unknown_knowledge_base_is_not_found(kb_client: KBClient, kb_id: str) -> None:
    resp = kb_client.get(f"/{kb_id}")
    assert resp.status_code == 404, resp.text[:500]
    assert resp.json()["error"]["message"] == NOT_FOUND_MESSAGE
    assert_strict_openapi_exchange(resp, ROUTE)


def test_get_as_member_without_a_role_is_not_found(second_user: SecondUser, make_kb: MakeKb) -> None:
    kb_id = make_kb()
    resp = request_as(second_user, "GET", f"/{kb_id}")
    assert resp.status_code == 404, resp.text[:500]
    assert resp.json()["error"]["message"] == NOT_FOUND_MESSAGE
    assert_strict_openapi_exchange(resp, ROUTE)


def test_get_id_that_cannot_be_a_url_segment_is_bad_request(kb_client: KBClient) -> None:
    resp = kb_client.get(f"/{UNSAFE_ID}")
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
def test_get_rejects_unauthenticated_calls(kb_client: KBClient, headers: dict[str, str]) -> None:
    resp = kb_client.get(f"/{MISSING_RECORD_ID}", auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_get_with_a_token_lacking_kb_read_is_forbidden(
    kb_client: KBClient, unscoped_headers: dict[str, str]
) -> None:
    resp = kb_client.get(f"/{MISSING_RECORD_ID}", auth=False, headers=unscoped_headers)
    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["message"] == "Insufficient scope. Required: kb:read"
    assert_strict_openapi_exchange(resp, ROUTE)
