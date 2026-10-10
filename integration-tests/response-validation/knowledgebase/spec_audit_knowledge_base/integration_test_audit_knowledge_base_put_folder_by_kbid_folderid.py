"""Strict OpenAPI audit of PUT /api/v1/knowledgeBase/:kbId/folder/:folderId.

body parser -> XSS check (refuses HTML, trims every string) -> authenticate ->
requireScopes(kb:write) -> zod (kbId and folderId non-empty, folderName 1..255) -> updateFolder,
which refuses format specifiers and sends only the name to the connector service
(PUT /api/v1/kb/{kb_id}/folder/{folder_id}).
"""

from __future__ import annotations

from typing import Any

import pytest
from helper.clients.kb_client import KBClient
from helper.second_user import SecondUser
from knowledge_base_audit_support import (
    HTML_REFUSED_MESSAGE,
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
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/knowledgeBase/:kbId/folder/:folderId"
UPDATED = {"success": True, "message": "Folder updated successfully"}
FOLDER_NOT_FOUND = "Folder not found in knowledge base"
KB_NOT_FOUND = "Knowledge base not found"
MAX_NAME_LENGTH = 255


def _folder(kb_client: KBClient, kb_id: str, name: str, parent: str | None = None) -> str:
    params = {"folderId": parent} if parent else None
    resp = kb_client.post(f"/{kb_id}/folder", params=params, json={"folderName": name})
    assert resp.status_code == 200, resp.text[:500]
    return str(resp.json()["id"])


def _name_of(kb_client: KBClient, kb_id: str, folder_id: str) -> str:
    resp = kb_client.get(f"/{kb_id}")
    assert resp.status_code == 200, resp.text[:500]
    return next(f["name"] for f in resp.json()["folders"] if f["id"] == folder_id)


def test_rename_stores_the_trimmed_name(kb_client: KBClient, make_kb: MakeKb) -> None:
    kb_id = make_kb()
    folder_id = _folder(kb_client, kb_id, "before")
    name = unique_name("spec-audit-after")

    resp = kb_client.put(f"/{kb_id}/folder/{folder_id}", json={"folderName": f"  {name} "})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == UPDATED
    assert _name_of(kb_client, kb_id, folder_id) == name


def test_rename_a_nested_folder_and_to_its_own_name(kb_client: KBClient, make_kb: MakeKb) -> None:
    kb_id = make_kb()
    parent = _folder(kb_client, kb_id, "parent")
    child = _folder(kb_client, kb_id, "child", parent)
    for name in ("renamed", "renamed", "f" * MAX_NAME_LENGTH):
        resp = kb_client.put(f"/{kb_id}/folder/{child}", json={"folderName": name})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
        assert _name_of(kb_client, kb_id, child) == name


def test_rename_as_writer_is_allowed(kb_client: KBClient, second_user: SecondUser, make_kb: MakeKb) -> None:
    kb_id = make_kb(member_role="WRITER")
    folder_id = _folder(kb_client, kb_id, "before")
    resp = request_as(second_user, "PUT", f"/{kb_id}/folder/{folder_id}", json={"folderName": "after"})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _name_of(kb_client, kb_id, folder_id) == "after"


def test_rename_ignores_other_fields_and_the_query(kb_client: KBClient, make_kb: MakeKb) -> None:
    kb_id = make_kb()
    folder_id = _folder(kb_client, kb_id, "before")
    with outside_request_contract("the validator drops body fields and query parameters it does not know"):
        resp = kb_client.put(
            f"/{kb_id}/folder/{folder_id}", params={"folderId": MISSING_RECORD_ID}, json={"folderName": "after", "name": "x"}
        )
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert _name_of(kb_client, kb_id, folder_id) == "after"


def test_rename_to_the_name_of_a_sibling_folder_is_a_conflict(kb_client: KBClient, make_kb: MakeKb) -> None:
    kb_id = make_kb()
    folder_id = _folder(kb_client, kb_id, "first")
    _folder(kb_client, kb_id, "second")
    parent = _folder(kb_client, kb_id, "parent")
    child = _folder(kb_client, kb_id, "child", parent)
    _folder(kb_client, kb_id, "sibling", parent)
    cases = [
        (folder_id, "second", "Folder 'second' already exists in collection root"),
        (folder_id, "SECOND", "Folder 'SECOND' already exists in collection root"),
        (child, "Sibling", "Folder 'Sibling' already exists in this folder"),
    ]
    for target, name, message in cases:
        resp = kb_client.put(f"/{kb_id}/folder/{target}", json={"folderName": name})
        assert resp.status_code == 409, resp.text[:500]
        assert resp.json()["error"]["message"] == message
        assert_strict_openapi_exchange(resp, ROUTE)


def test_rename_to_the_name_of_a_file_is_allowed(kb_client: KBClient, make_kb: MakeKb) -> None:
    kb_id = make_kb()
    record_id = upload_text_record(kb_client, kb_id, "spec-audit-file.txt")
    wait_for_record(kb_client, record_id)
    folder_id = _folder(kb_client, kb_id, "folder")
    resp = kb_client.put(f"/{kb_id}/folder/{folder_id}", json={"folderName": "spec-audit-file.txt"})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="name-missing"),
        pytest.param({"folderName": None}, id="name-null"),
        pytest.param({"folderName": ""}, id="name-empty"),
        pytest.param({"folderName": " \t "}, id="name-only-whitespace"),
        pytest.param({"folderName": "f" * (MAX_NAME_LENGTH + 1)}, id="name-too-long"),
        pytest.param({"folderName": 20261006}, id="name-not-a-string"),
        pytest.param({"name": "the connector service's own field name"}, id="python-field-name"),
    ],
)
def test_rename_rejects_a_body_outside_the_validator(kb_client: KBClient, payload: Any) -> None:
    resp = kb_client.put(f"/{MISSING_RECORD_ID}/folder/{MISSING_RECORD_ID}", json=payload)
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_rename_without_a_body_is_rejected(kb_client: KBClient) -> None:
    resp = kb_client.put(f"/{MISSING_RECORD_ID}/folder/{MISSING_RECORD_ID}")
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_rename_refuses_format_specifiers_in_the_name(kb_client: KBClient, make_kb: MakeKb) -> None:
    kb_id = make_kb()
    folder_id = _folder(kb_client, kb_id, "before")
    resp = kb_client.put(f"/{kb_id}/folder/{folder_id}", json={"folderName": "after %n"})
    assert resp.status_code == 400, resp.text[:500]
    assert "format specifiers" in resp.json()["error"]["message"]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)
    assert _name_of(kb_client, kb_id, folder_id) == "before"


def test_rename_refuses_html_in_the_name_before_the_token_check(kb_client: KBClient) -> None:
    resp = kb_client.put(
        f"/{MISSING_RECORD_ID}/folder/{MISSING_RECORD_ID}", auth=False, json={"folderName": "x onclick=alert(1)"}
    )
    assert resp.status_code == 400, resp.text[:500]
    assert HTML_REFUSED_MESSAGE in resp.json()["error"]["message"]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_rename_a_folder_that_is_not_there_is_not_found(kb_client: KBClient, make_kb: MakeKb) -> None:
    kb_id = make_kb()
    record_id = upload_text_record(kb_client, kb_id)
    wait_for_record(kb_client, record_id)
    for folder_id in (MISSING_RECORD_ID, record_id):
        resp = kb_client.put(f"/{kb_id}/folder/{folder_id}", json={"folderName": "x"})
        assert resp.status_code == 404, resp.text[:500]
        assert resp.json()["error"]["message"] == FOLDER_NOT_FOUND
        assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("kb_id", [MISSING_RECORD_ID, MALFORMED_ID], ids=["unknown-uuid", "not-a-uuid"])
def test_rename_in_an_unknown_knowledge_base_is_not_found(kb_client: KBClient, kb_id: str) -> None:
    resp = kb_client.put(f"/{kb_id}/folder/{MISSING_RECORD_ID}", json={"folderName": "x"})
    assert resp.status_code == 404, resp.text[:500]
    assert resp.json()["error"]["message"] == KB_NOT_FOUND
    assert_strict_openapi_exchange(resp, ROUTE)


def test_rename_as_member_without_a_role_is_not_found(
    kb_client: KBClient, second_user: SecondUser, make_kb: MakeKb
) -> None:
    kb_id = make_kb()
    folder_id = _folder(kb_client, kb_id, "before")
    resp = request_as(second_user, "PUT", f"/{kb_id}/folder/{folder_id}", json={"folderName": "x"})
    assert resp.status_code == 404, resp.text[:500]
    assert resp.json()["error"]["message"] == KB_NOT_FOUND
    assert_strict_openapi_exchange(resp, ROUTE)


def test_rename_as_reader_is_forbidden(kb_client: KBClient, second_user: SecondUser, make_kb: MakeKb) -> None:
    kb_id = make_kb(member_role="READER")
    folder_id = _folder(kb_client, kb_id, "before")
    resp = request_as(second_user, "PUT", f"/{kb_id}/folder/{folder_id}", json={"folderName": "x"})
    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["message"] == (
        "You do not have permission to perform this action on this knowledge base"
    )
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _name_of(kb_client, kb_id, folder_id) == "before"


@pytest.mark.parametrize(
    "path",
    [f"/{UNSAFE_ID}/folder/{MISSING_RECORD_ID}", f"/{MISSING_RECORD_ID}/folder/{UNSAFE_ID}"],
    ids=["kbId", "folderId"],
)
def test_rename_id_that_cannot_be_a_url_segment_is_bad_request(kb_client: KBClient, path: str) -> None:
    resp = kb_client.put(path, json={"folderName": "x"})
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
def test_rename_rejects_unauthenticated_calls(kb_client: KBClient, headers: dict[str, str]) -> None:
    resp = kb_client.put(
        f"/{MISSING_RECORD_ID}/folder/{MISSING_RECORD_ID}", auth=False, headers=headers, json={"folderName": "x"}
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_rename_with_a_token_lacking_kb_write_is_forbidden(
    kb_client: KBClient, unscoped_headers: dict[str, str]
) -> None:
    resp = kb_client.put(
        f"/{MISSING_RECORD_ID}/folder/{MISSING_RECORD_ID}", auth=False, headers=unscoped_headers, json={"folderName": "x"}
    )
    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["message"] == "Insufficient scope. Required: kb:write"
    assert_strict_openapi_exchange(resp, ROUTE)
