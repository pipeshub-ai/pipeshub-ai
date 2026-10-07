"""Strict OpenAPI audit of PUT /api/v1/knowledgeBase/:kbId/record/:recordId/move.

guardPathParams(kbId, recordId) -> authenticate -> requireScopes(kb:write) -> zod (kbId UUID,
recordId non-empty, newParentId string or null) -> moveRecord -> connector service
PUT /api/v1/kb/{kb_id}/record/{record_id}/move. An empty newParentId means the root, like null.
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
    MakeKb,
    body_spec_refusals,
    request_as,
    unique_name,
    wait_for_record,
)
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/knowledgeBase/:kbId/record/:recordId/move"
MOVED = {"success": True, "message": "Record moved successfully"}
KB_NOT_FOUND = "Knowledge base not found"
MAX_FOLDER_DEPTH = 20


def _folder(kb_client: KBClient, kb_id: str, name: str = "folder", parent: str | None = None) -> str:
    params = {"folderId": parent} if parent else None
    resp = kb_client.post(f"/{kb_id}/folder", params=params, json={"folderName": name})
    assert resp.status_code == 200, resp.text[:500]
    return str(resp.json()["id"])


def _file(kb_client: KBClient, kb_id: str, name: str | None = None, folder_id: str | None = None) -> str:
    uploaded = kb_client.upload_file(kb_id, name or f"{unique_name()}.txt", b"spec audit move", folder_id=folder_id)
    record_id = str(uploaded["records"][0]["recordId"])
    wait_for_record(kb_client, record_id)
    return record_id


def _children(kb_client: KBClient, parent_type: str, parent_id: str) -> set[str]:
    resp = kb_client.get(f"/knowledge-hub/nodes/{parent_type}/{parent_id}")
    assert resp.status_code == 200, resp.text[:500]
    return {item["id"] for item in resp.json()["items"]}


def _move(kb_client: KBClient, kb_id: str, record_id: str, payload: Any) -> Any:
    return kb_client.put(f"/{kb_id}/record/{record_id}/move", json=payload)


def test_move_a_file_into_a_folder_and_back_to_the_root(kb_client: KBClient, make_kb: MakeKb) -> None:
    kb_id = make_kb()
    folder_id = _folder(kb_client, kb_id)
    record_id = _file(kb_client, kb_id)

    into = _move(kb_client, kb_id, record_id, {"newParentId": folder_id})
    assert into.status_code == 200, into.text[:500]
    assert_strict_openapi_exchange(into, ROUTE)
    assert into.json() == MOVED
    assert _children(kb_client, "folder", folder_id) == {record_id}

    back = _move(kb_client, kb_id, record_id, {"newParentId": None})
    assert back.status_code == 200, back.text[:500]
    assert_strict_openapi_exchange(back, ROUTE)
    assert back.json() == MOVED
    assert _children(kb_client, "folder", folder_id) == set()
    assert record_id in _children(kb_client, "app", kb_id)


def test_move_to_where_the_record_already_is_answers_the_same(kb_client: KBClient, make_kb: MakeKb) -> None:
    kb_id = make_kb()
    record_id = _file(kb_client, kb_id)
    resp = _move(kb_client, kb_id, record_id, {"newParentId": None})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == MOVED


def test_move_with_an_empty_parent_id_goes_to_the_root(kb_client: KBClient, make_kb: MakeKb) -> None:
    kb_id = make_kb()
    folder_id = _folder(kb_client, kb_id)
    record_id = _file(kb_client, kb_id, folder_id=folder_id)
    resp = _move(kb_client, kb_id, record_id, {"newParentId": ""})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _children(kb_client, "folder", folder_id) == set()


def test_move_a_folder_with_its_content(kb_client: KBClient, make_kb: MakeKb) -> None:
    kb_id = make_kb()
    target = _folder(kb_client, kb_id, "target")
    moving = _folder(kb_client, kb_id, "moving")
    record_id = _file(kb_client, kb_id, folder_id=moving)
    resp = _move(kb_client, kb_id, moving, {"newParentId": target})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _children(kb_client, "folder", target) == {moving}
    assert _children(kb_client, "folder", moving) == {record_id}


def test_move_as_writer_is_allowed(kb_client: KBClient, second_user: SecondUser, make_kb: MakeKb) -> None:
    kb_id = make_kb(member_role="WRITER")
    folder_id = _folder(kb_client, kb_id)
    record_id = _file(kb_client, kb_id)
    resp = request_as(second_user, "PUT", f"/{kb_id}/record/{record_id}/move", json={"newParentId": folder_id})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_move_ignores_other_fields_and_the_query(kb_client: KBClient, make_kb: MakeKb) -> None:
    kb_id = make_kb()
    folder_id = _folder(kb_client, kb_id)
    record_id = _file(kb_client, kb_id)
    with outside_request_contract("the validator drops body fields and query parameters it does not know"):
        resp = kb_client.put(
            f"/{kb_id}/record/{record_id}/move",
            params={"newParentId": "ignored"},
            json={"newParentId": folder_id, "newKbId": MISSING_RECORD_ID},
        )
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert _children(kb_client, "folder", folder_id) == {record_id}
    assert body_spec_refusals(resp, ROUTE) == []


def test_move_into_a_folder_holding_the_same_name_is_a_conflict(kb_client: KBClient, make_kb: MakeKb) -> None:
    kb_id = make_kb()
    target = _folder(kb_client, kb_id, "target")
    _folder(kb_client, kb_id, "twin", target)
    twin = _folder(kb_client, kb_id, "twin")
    resp = _move(kb_client, kb_id, twin, {"newParentId": target})
    assert resp.status_code == 409, resp.text[:500]
    assert resp.json()["error"]["message"] == "Folder 'twin' already exists in this folder"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_move_a_file_next_to_a_file_of_the_same_name_is_a_conflict(kb_client: KBClient, make_kb: MakeKb) -> None:
    kb_id = make_kb()
    target = _folder(kb_client, kb_id, "target")
    name = f"{unique_name()}.txt"
    _file(kb_client, kb_id, name, folder_id=target)
    record_id = _file(kb_client, kb_id, name)
    resp = _move(kb_client, kb_id, record_id, {"newParentId": target})
    assert resp.status_code == 409, resp.text[:500]
    assert resp.json()["error"]["message"] == f"A file named '{name.removesuffix('.txt')}' already exists in this folder"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_move_a_folder_into_itself_or_below_itself_is_bad_request(kb_client: KBClient, make_kb: MakeKb) -> None:
    kb_id = make_kb()
    parent = _folder(kb_client, kb_id, "parent")
    child = _folder(kb_client, kb_id, "child", parent)
    cases = [
        (parent, "Cannot move a folder into itself"),
        (child, "Cannot move a folder into one of its own sub-folders (circular reference)"),
    ]
    for target, message in cases:
        resp = _move(kb_client, kb_id, parent, {"newParentId": target})
        assert resp.status_code == 400, resp.text[:500]
        assert resp.json()["error"]["message"] == message
        assert_strict_openapi_exchange(resp, ROUTE)


def test_move_that_would_nest_folders_too_deep_is_bad_request(kb_client: KBClient, make_kb: MakeKb) -> None:
    kb_id = make_kb()
    deepest = None
    for level in range(MAX_FOLDER_DEPTH):
        deepest = _folder(kb_client, kb_id, f"level-{level}", deepest)
    moving = _folder(kb_client, kb_id, "moving")
    resp = _move(kb_client, kb_id, moving, {"newParentId": deepest})
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["message"].startswith(f"Folders can be nested at most {MAX_FOLDER_DEPTH} levels deep.")
    assert_strict_openapi_exchange(resp, ROUTE)


def test_move_to_a_target_that_is_not_a_folder_is_not_found(kb_client: KBClient, make_kb: MakeKb) -> None:
    kb_id = make_kb()
    record_id = _file(kb_client, kb_id)
    other_file = _file(kb_client, kb_id)
    for target in (MISSING_RECORD_ID, other_file):
        resp = _move(kb_client, kb_id, record_id, {"newParentId": target})
        assert resp.status_code == 404, resp.text[:500]
        assert resp.json()["error"]["message"] == f"Target folder {target} not found in KB {kb_id}"
        assert_strict_openapi_exchange(resp, ROUTE)


def test_move_a_record_of_another_knowledge_base_is_not_found(kb_client: KBClient, make_kb: MakeKb) -> None:
    kb_id = make_kb()
    elsewhere = _file(kb_client, make_kb())
    for record_id in (MISSING_RECORD_ID, elsewhere):
        resp = _move(kb_client, kb_id, record_id, {"newParentId": None})
        assert resp.status_code == 404, resp.text[:500]
        assert resp.json()["error"]["message"] == f"Record {record_id} not found in KB {kb_id}"
        assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="parent-missing"),
        pytest.param({"newParentId": 7}, id="parent-not-a-string"),
        pytest.param({"parentId": None}, id="other-field-name"),
    ],
)
def test_move_rejects_a_body_outside_the_validator(kb_client: KBClient, payload: Any) -> None:
    resp = _move(kb_client, MISSING_RECORD_ID, MISSING_RECORD_ID, payload)
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_move_without_a_body_is_rejected(kb_client: KBClient) -> None:
    resp = kb_client.put(f"/{MISSING_RECORD_ID}/record/{MISSING_RECORD_ID}/move")
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_move_kb_id_that_is_not_a_uuid_is_rejected(kb_client: KBClient) -> None:
    resp = _move(kb_client, MALFORMED_ID, MISSING_RECORD_ID, {"newParentId": None})
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["metadata"]["errors"][0]["field"] == "params.kbId"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_move_in_an_unknown_knowledge_base_is_not_found(kb_client: KBClient) -> None:
    resp = _move(kb_client, MISSING_RECORD_ID, MISSING_RECORD_ID, {"newParentId": None})
    assert resp.status_code == 404, resp.text[:500]
    assert resp.json()["error"]["message"] == KB_NOT_FOUND
    assert_strict_openapi_exchange(resp, ROUTE)


def test_move_as_member_without_a_role_is_not_found(
    kb_client: KBClient, second_user: SecondUser, make_kb: MakeKb
) -> None:
    kb_id = make_kb()
    record_id = _file(kb_client, kb_id)
    resp = request_as(second_user, "PUT", f"/{kb_id}/record/{record_id}/move", json={"newParentId": None})
    assert resp.status_code == 404, resp.text[:500]
    assert resp.json()["error"]["message"] == KB_NOT_FOUND
    assert_strict_openapi_exchange(resp, ROUTE)


def test_move_as_reader_is_forbidden(kb_client: KBClient, second_user: SecondUser, make_kb: MakeKb) -> None:
    kb_id = make_kb(member_role="READER")
    record_id = _file(kb_client, kb_id)
    resp = request_as(second_user, "PUT", f"/{kb_id}/record/{record_id}/move", json={"newParentId": None})
    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["message"] == (
        "You do not have permission to perform this action on this knowledge base"
    )
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "path",
    [f"/{UNSAFE_ID}/record/{MISSING_RECORD_ID}/move", f"/{MISSING_RECORD_ID}/record/{UNSAFE_ID}/move"],
    ids=["kbId", "recordId"],
)
def test_move_id_that_cannot_be_a_url_segment_is_bad_request(kb_client: KBClient, path: str) -> None:
    resp = kb_client.put(path, json={"newParentId": None})
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
def test_move_rejects_unauthenticated_calls(kb_client: KBClient, headers: dict[str, str]) -> None:
    resp = kb_client.put(
        f"/{MISSING_RECORD_ID}/record/{MISSING_RECORD_ID}/move", auth=False, headers=headers, json={"newParentId": None}
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_move_with_a_token_lacking_kb_write_is_forbidden(
    kb_client: KBClient, unscoped_headers: dict[str, str]
) -> None:
    resp = kb_client.put(
        f"/{MISSING_RECORD_ID}/record/{MISSING_RECORD_ID}/move",
        auth=False,
        headers=unscoped_headers,
        json={"newParentId": None},
    )
    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["message"] == "Insufficient scope. Required: kb:write"
    assert_strict_openapi_exchange(resp, ROUTE)
