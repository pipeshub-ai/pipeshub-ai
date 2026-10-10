"""Strict OpenAPI audit of GET /api/v1/knowledgeBase/:kbId/trash.

authenticate -> requireScopes(kb:delete) -> zod (kbId non-empty; page and limit optional
strings of digits, page >= 1, limit 1..100) -> listTrash -> connector service
GET /api/v1/kb/{kb_id}/trash, which checks the Labs flag ENABLE_SOFT_DELETE, then the
caller's role. The flag is org-wide, so each test holds it at the value it needs.
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
    bearer,
    oauth_token_with_scopes,
    request_as,
    unique_name,
    wait_for_record,
)
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/knowledgeBase/:kbId/trash"
TRASH_OFF_MESSAGE = (
    'The trash is turned off in this workspace. Ask an admin to turn on "Move Deleted Records to the Trash" '
    "in Labs, then try again."
)
EDIT_ACCESS_MESSAGE = (
    "You need edit access to this collection to see and restore its deleted items. "
    "Ask the collection's owner for edit access."
)


def _trash(kb_client: KBClient, record_id: str) -> None:
    deleted = kb_client.delete(f"/record/{record_id}")
    assert deleted.status_code == 200, deleted.text[:500]
    assert deleted.json()["softDeleted"] is True, deleted.text[:500]


def _list(kb_client: KBClient, kb_id: str, **params: Any) -> Any:
    resp = kb_client.get(f"/{kb_id}/trash", params=params or None)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    return resp.json()


def test_trash_of_a_collection_with_nothing_deleted_is_empty(
    kb_client: KBClient, make_kb: MakeKb, trash_on: None
) -> None:
    body = _list(kb_client, make_kb())
    assert body["items"] == []
    assert body["pagination"] == {"page": 1, "limit": 25, "totalCount": 0, "totalPages": 0}
    assert body["retention"]["minAgeMs"] > 0


def test_trash_lists_a_deleted_file(
    kb_client: KBClient, audit_kb_id: str, seed_record: SeedRecord, trash_on: None
) -> None:
    stem = unique_name("spec-audit-trash")
    record_id = seed_record(f"{stem}.txt")
    _trash(kb_client, record_id)

    body = _list(kb_client, audit_kb_id)

    item = next(i for i in body["items"] if i["id"] == record_id)
    assert {k: item[k] for k in ("name", "recordType", "isFolder", "mimeType", "parentId", "parentName")} == {
        "name": stem,
        "recordType": "FILE",
        "isFolder": False,
        "mimeType": "text/plain",
        "parentId": None,
        "parentName": None,
    }
    assert (item["parentInTrash"], item["itemCount"], item["rootCount"], item["otherRootNames"]) == (False, 1, 1, [])
    assert item["deletedBy"]["email"]
    assert item["removableAfterTimestamp"] == item["deletedAtTimestamp"] + body["retention"]["minAgeMs"]


def _folder_with_a_file(kb_client: KBClient, kb_id: str) -> tuple[str, str, str]:
    folder_name = unique_name("spec-audit-folder")
    folder_id = kb_client.create_folder(kb_id, folder_name)["id"]
    uploaded = kb_client.upload_file(kb_id, f"{unique_name()}.txt", b"in a folder", folder_id=folder_id)
    record_id = uploaded["records"][0]["recordId"]
    wait_for_record(kb_client, record_id)
    return folder_id, folder_name, record_id


def test_trash_lists_a_folder_deleted_with_its_contents_as_one_item(
    kb_client: KBClient, make_kb: MakeKb, trash_on: None
) -> None:
    kb_id = make_kb()
    folder_id, folder_name, record_id = _folder_with_a_file(kb_client, kb_id)
    deleted = kb_client.delete(f"/{kb_id}/folder/{folder_id}")
    assert deleted.status_code == 200, deleted.text[:500]

    body = _list(kb_client, kb_id)

    assert [(i["id"], i["isFolder"], i["name"], i["itemCount"]) for i in body["items"]] == [
        (folder_id, True, folder_name, 2)
    ]
    assert kb_client.get(f"/record/{record_id}").status_code == 404


def test_trash_holds_only_the_folder_when_it_is_deleted_as_a_record(
    kb_client: KBClient, make_kb: MakeKb, trash_on: None
) -> None:
    kb_id = make_kb()
    folder_id, _, record_id = _folder_with_a_file(kb_client, kb_id)
    # DELETE /record/{id} on a folder trashes the folder alone; its file stays live (an API defect).
    _trash(kb_client, folder_id)

    body = _list(kb_client, kb_id)

    assert [(i["id"], i["itemCount"]) for i in body["items"]] == [(folder_id, 1)]
    assert kb_client.get(f"/record/{record_id}").status_code == 200


def test_trash_marks_an_item_whose_folder_is_also_deleted(
    kb_client: KBClient, make_kb: MakeKb, trash_on: None
) -> None:
    kb_id = make_kb()
    folder_id, folder_name, record_id = _folder_with_a_file(kb_client, kb_id)
    _trash(kb_client, record_id)
    _trash(kb_client, folder_id)

    items = {i["id"]: i for i in _list(kb_client, kb_id)["items"]}

    assert list(items) == [folder_id, record_id], "newest first"
    assert (items[record_id]["parentId"], items[record_id]["parentName"]) == (folder_id, folder_name)
    assert items[record_id]["parentInTrash"] is True
    assert items[folder_id]["parentInTrash"] is False


def test_trash_pages_through_the_items(kb_client: KBClient, make_kb: MakeKb, trash_on: None) -> None:
    kb_id = make_kb()
    for _ in range(2):
        _trash(kb_client, kb_client.create_folder(kb_id, unique_name("spec-audit-folder"))["id"])

    first = _list(kb_client, kb_id, page="1", limit="1")
    second = _list(kb_client, kb_id, page="2", limit="1")
    past_the_end = _list(kb_client, kb_id, page="3", limit="1")

    assert first["pagination"] == {"page": 1, "limit": 1, "totalCount": 2, "totalPages": 2}
    assert second["pagination"] == {"page": 2, "limit": 1, "totalCount": 2, "totalPages": 2}
    assert len(first["items"]) == len(second["items"]) == 1
    assert first["items"][0]["id"] != second["items"][0]["id"]
    assert past_the_end["items"] == []


def test_trash_ignores_unknown_query_parameters(kb_client: KBClient, make_kb: MakeKb, trash_on: None) -> None:
    kb_id = make_kb()
    with outside_request_contract("the validator lists only page and limit and lets other parameters through"):
        resp = kb_client.get(f"/{kb_id}/trash", params={"sort": "oldest", "page": "1"})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["pagination"]["page"] == 1


@pytest.mark.parametrize(
    ("params", "message"),
    [
        pytest.param({"page": "0"}, "Page must be 1 or more.", id="page-zero"),
        pytest.param({"page": "1.5"}, "Page must be a whole number.", id="page-fraction"),
        pytest.param({"page": ["1", "2"]}, "Page must be text.", id="page-twice"),
        pytest.param({"limit": "0"}, "Limit must be between 1 and 100.", id="limit-zero"),
        pytest.param({"limit": "101"}, "Limit must be between 1 and 100.", id="limit-over-100"),
        pytest.param({"limit": "abc"}, "Limit must be a whole number.", id="limit-not-a-number"),
    ],
)
def test_trash_refuses_a_page_or_limit_out_of_range(
    kb_client: KBClient, params: dict[str, Any], message: str
) -> None:
    resp = kb_client.get(f"/{MISSING_RECORD_ID}/trash", params=params)
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert message in resp.json()["error"]["message"]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_trash_as_writer_lists_the_items(
    kb_client: KBClient, second_user: SecondUser, make_kb: MakeKb, trash_on: None
) -> None:
    kb_id = make_kb("WRITER")
    folder_id = kb_client.create_folder(kb_id, unique_name("spec-audit-folder"))["id"]
    _trash(kb_client, folder_id)

    resp = request_as(second_user, "GET", f"/{kb_id}/trash")

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert [i["id"] for i in resp.json()["items"]] == [folder_id]


def test_trash_as_reader_is_forbidden(
    second_user: SecondUser, make_kb: MakeKb, trash_on: None
) -> None:
    resp = request_as(second_user, "GET", f"/{make_kb('READER')}/trash")
    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["message"] == EDIT_ACCESS_MESSAGE
    assert_strict_openapi_exchange(resp, ROUTE)


def test_trash_as_member_without_a_role_is_not_found(
    second_user: SecondUser, make_kb: MakeKb, trash_on: None
) -> None:
    resp = request_as(second_user, "GET", f"/{make_kb()}/trash")
    assert resp.status_code == 404, resp.text[:500]
    assert resp.json()["error"]["message"] == "Knowledge base not found"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_trash_of_an_unknown_collection_is_not_found(kb_client: KBClient, trash_on: None) -> None:
    resp = kb_client.get(f"/{MISSING_RECORD_ID}/trash")
    assert resp.status_code == 404, resp.text[:500]
    assert resp.json()["error"]["message"] == "Knowledge base not found"
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("existing", [True, False], ids=["existing-collection", "unknown-collection"])
def test_trash_while_the_trash_is_off_is_forbidden(
    kb_client: KBClient, make_kb: MakeKb, trash_off: None, existing: bool
) -> None:
    kb_id = make_kb() if existing else MISSING_RECORD_ID
    resp = kb_client.get(f"/{kb_id}/trash")
    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["message"] == TRASH_OFF_MESSAGE
    assert_strict_openapi_exchange(resp, ROUTE)


def test_trash_id_that_cannot_be_a_url_segment_is_bad_request(kb_client: KBClient) -> None:
    resp = kb_client.get(f"/{UNSAFE_ID}/trash")
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
def test_trash_rejects_unauthenticated_calls(kb_client: KBClient, headers: dict[str, str]) -> None:
    resp = kb_client.get(f"/{MISSING_RECORD_ID}/trash", auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_trash_with_a_token_lacking_kb_delete_is_forbidden(
    kb_client: KBClient, pipeshub_client: PipeshubClient
) -> None:
    with oauth_token_with_scopes(pipeshub_client.base_url, ["kb:read"], pipeshub_client.timeout_seconds) as token:
        resp = kb_client.get(f"/{MISSING_RECORD_ID}/trash", auth=False, headers=bearer(token))
    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["message"] == "Insufficient scope. Required: kb:delete"
    assert_strict_openapi_exchange(resp, ROUTE)
