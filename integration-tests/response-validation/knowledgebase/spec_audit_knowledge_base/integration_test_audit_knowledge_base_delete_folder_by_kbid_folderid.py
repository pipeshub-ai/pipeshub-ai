"""Strict OpenAPI audit of DELETE /api/v1/knowledgeBase/:kbId/folder/:folderId.

guardPathParams(kbId, folderId) -> authenticate -> requireScopes(kb:delete) -> zod (both ids
non-empty) -> deleteFolder -> connector service DELETE /api/v1/kb/{kb_id}/folder/{folder_id},
which removes the folder with everything under it, or moves it all to the trash while the
Labs flag ENABLE_SOFT_DELETE is on. OWNER and WRITER may delete; READER may not.
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
    unique_name,
    upload_text_record,
    wait_for_record,
)
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/knowledgeBase/:kbId/folder/:folderId"
DELETED = {"success": True, "message": "Folder deleted successfully"}
FOLDER_NOT_FOUND = "Folder not found in knowledge base"
KB_NOT_FOUND = "Knowledge base not found"


def _folder(kb_client: KBClient, kb_id: str, name: str = "folder", parent: str | None = None) -> str:
    params = {"folderId": parent} if parent else None
    resp = kb_client.post(f"/{kb_id}/folder", params=params, json={"folderName": name})
    assert resp.status_code == 200, resp.text[:500]
    return str(resp.json()["id"])


def _record_in(kb_client: KBClient, kb_id: str, folder_id: str) -> str:
    name = f"{unique_name()}.txt"
    uploaded = kb_client.upload_file(kb_id, name, b"spec audit folder content", folder_id=folder_id)
    record_id = str(uploaded["records"][0]["recordId"])
    wait_for_record(kb_client, record_id)
    return record_id


def _folder_ids(kb_client: KBClient, kb_id: str) -> set[str]:
    resp = kb_client.get(f"/{kb_id}")
    assert resp.status_code == 200, resp.text[:500]
    return {f["id"] for f in resp.json()["folders"]}


def test_delete_removes_the_folder_with_everything_under_it(
    kb_client: KBClient, make_kb: MakeKb, trash_off: None
) -> None:
    kb_id = make_kb()
    folder_id = _folder(kb_client, kb_id)
    child = _folder(kb_client, kb_id, "child", folder_id)
    record_id = _record_in(kb_client, kb_id, child)
    kept = _folder(kb_client, kb_id, "kept")

    resp = kb_client.delete(f"/{kb_id}/folder/{folder_id}")

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == DELETED
    assert _folder_ids(kb_client, kb_id) == {kept}
    assert kb_client.get(f"/record/{record_id}").status_code == 404

    again = kb_client.delete(f"/{kb_id}/folder/{folder_id}")
    assert again.status_code == 404, again.text[:500]
    assert again.json()["error"]["message"] == FOLDER_NOT_FOUND
    assert_strict_openapi_exchange(again, ROUTE)


def test_delete_moves_the_folder_to_the_trash_while_the_trash_is_on(
    kb_client: KBClient, make_kb: MakeKb, trash_on: None
) -> None:
    kb_id = make_kb()
    folder_id = _folder(kb_client, kb_id)
    record_id = _record_in(kb_client, kb_id, folder_id)

    resp = kb_client.delete(f"/{kb_id}/folder/{folder_id}")

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    # The same answer as a delete for good: nothing says the folder went to the trash.
    assert resp.json() == DELETED
    assert kb_client.get(f"/record/{record_id}").status_code == 404
    # GET /knowledgeBase/:kbId still lists the trashed folder.
    assert _folder_ids(kb_client, kb_id) == {folder_id}

    restored = kb_client.post(f"/record/{folder_id}/restore")
    assert restored.status_code == 200, restored.text[:500]
    assert _folder_ids(kb_client, kb_id) == {folder_id}
    assert kb_client.get(f"/record/{record_id}").status_code == 200


def test_delete_as_writer_is_allowed(kb_client: KBClient, second_user: SecondUser, make_kb: MakeKb) -> None:
    kb_id = make_kb(member_role="WRITER")
    folder_id = _folder(kb_client, kb_id)
    resp = request_as(second_user, "DELETE", f"/{kb_id}/folder/{folder_id}")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _folder_ids(kb_client, kb_id) == set()


def test_delete_ignores_query_and_body(kb_client: KBClient, make_kb: MakeKb) -> None:
    kb_id = make_kb()
    folder_id = _folder(kb_client, kb_id)
    with outside_request_contract("the validator checks only the path parameters"):
        resp = kb_client.delete(f"/{kb_id}/folder/{folder_id}", params={"recursive": "false"}, json={"keep": True})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert _folder_ids(kb_client, kb_id) == set()


def test_delete_as_reader_is_forbidden(kb_client: KBClient, second_user: SecondUser, make_kb: MakeKb) -> None:
    kb_id = make_kb(member_role="READER")
    folder_id = _folder(kb_client, kb_id)
    resp = request_as(second_user, "DELETE", f"/{kb_id}/folder/{folder_id}")
    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["message"] == "User lacks permission to delete folder"
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _folder_ids(kb_client, kb_id) == {folder_id}


def test_delete_as_member_without_a_role_is_not_found(
    kb_client: KBClient, second_user: SecondUser, make_kb: MakeKb
) -> None:
    kb_id = make_kb()
    folder_id = _folder(kb_client, kb_id)
    resp = request_as(second_user, "DELETE", f"/{kb_id}/folder/{folder_id}")
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _folder_ids(kb_client, kb_id) == {folder_id}


def test_delete_a_folder_that_is_not_there_is_not_found(kb_client: KBClient, make_kb: MakeKb) -> None:
    kb_id = make_kb()
    record_id = upload_text_record(kb_client, kb_id)
    wait_for_record(kb_client, record_id)
    for folder_id in (MISSING_RECORD_ID, record_id):
        resp = kb_client.delete(f"/{kb_id}/folder/{folder_id}")
        assert resp.status_code == 404, resp.text[:500]
        assert resp.json()["error"]["message"] == FOLDER_NOT_FOUND
        assert_strict_openapi_exchange(resp, ROUTE)
    assert kb_client.get(f"/record/{record_id}").status_code == 200


@pytest.mark.parametrize("kb_id", [MISSING_RECORD_ID, MALFORMED_ID], ids=["unknown-uuid", "not-a-uuid"])
def test_delete_in_an_unknown_knowledge_base_is_not_found(kb_client: KBClient, kb_id: str) -> None:
    resp = kb_client.delete(f"/{kb_id}/folder/{MISSING_RECORD_ID}")
    assert resp.status_code == 404, resp.text[:500]
    assert resp.json()["error"]["message"] == KB_NOT_FOUND
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "path",
    [f"/{UNSAFE_ID}/folder/{MISSING_RECORD_ID}", f"/{MISSING_RECORD_ID}/folder/{UNSAFE_ID}"],
    ids=["kbId", "folderId"],
)
def test_delete_id_that_cannot_be_a_url_segment_is_bad_request(kb_client: KBClient, path: str) -> None:
    resp = kb_client.delete(path)
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
    resp = kb_client.delete(f"/{MISSING_RECORD_ID}/folder/{MISSING_RECORD_ID}", auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_delete_with_a_token_lacking_kb_delete_is_forbidden(
    kb_client: KBClient, unscoped_headers: dict[str, str]
) -> None:
    resp = kb_client.delete(f"/{MISSING_RECORD_ID}/folder/{MISSING_RECORD_ID}", auth=False, headers=unscoped_headers)
    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["message"] == "Insufficient scope. Required: kb:delete"
    assert_strict_openapi_exchange(resp, ROUTE)
