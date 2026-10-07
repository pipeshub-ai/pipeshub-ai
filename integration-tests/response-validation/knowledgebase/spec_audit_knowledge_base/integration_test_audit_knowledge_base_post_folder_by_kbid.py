"""Strict OpenAPI audit of POST /api/v1/knowledgeBase/:kbId/folder.

body parser -> XSS check (refuses HTML, trims every string) -> authenticate ->
requireScopes(kb:write) -> zod (kbId UUID, folderName 1..255, query folderId non-empty
optional) -> createFolder, which refuses format specifiers and calls the connector service:
POST /api/v1/kb/{kb_id}/folder, or .../folder/{folderId}/subfolder when folderId is sent.
Folder names are unique per parent, ignoring case.
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
    path_parameter_spec_problems,
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

ROUTE = "/api/v1/knowledgeBase/:kbId/folder"
MAX_NAME_LENGTH = 255
MAX_FOLDER_DEPTH = 20


def _folders(kb_client: KBClient, kb_id: str) -> dict[str, str]:
    resp = kb_client.get(f"/{kb_id}")
    assert resp.status_code == 200, resp.text[:500]
    return {f["id"]: f["name"] for f in resp.json()["folders"]}


def _create(kb_client: KBClient, kb_id: str, name: str, parent: str | None = None) -> str:
    params = {"folderId": parent} if parent else None
    resp = kb_client.post(f"/{kb_id}/folder", params=params, json={"folderName": name})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert set(body) == {"id", "name"}, body
    return str(body["id"])


def test_create_at_the_root_returns_the_trimmed_folder(kb_client: KBClient, make_kb: MakeKb) -> None:
    kb_id = make_kb()
    name = unique_name("spec-audit-folder")

    resp = kb_client.post(f"/{kb_id}/folder", json={"folderName": f"  {name}\t"})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert body == {"id": body["id"], "name": name}
    assert _folders(kb_client, kb_id) == {body["id"]: name}


def test_create_inside_a_folder_makes_a_subfolder(kb_client: KBClient, make_kb: MakeKb) -> None:
    kb_id = make_kb()
    parent = _create(kb_client, kb_id, "parent")
    child = _create(kb_client, kb_id, "child", parent)
    assert child != parent
    # GET /knowledgeBase/:kbId lists nested folders along with root ones.
    assert set(_folders(kb_client, kb_id)) == {parent, child}


def test_create_accepts_a_name_at_the_length_limit(kb_client: KBClient, make_kb: MakeKb) -> None:
    kb_id = make_kb()
    name = unique_name("f").ljust(MAX_NAME_LENGTH, "f")
    folder_id = _create(kb_client, kb_id, name)
    assert _folders(kb_client, kb_id)[folder_id] == name


def test_create_as_writer_is_allowed(second_user: SecondUser, make_kb: MakeKb) -> None:
    kb_id = make_kb(member_role="WRITER")
    resp = request_as(second_user, "POST", f"/{kb_id}/folder", json={"folderName": unique_name()})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_create_allows_the_name_of_a_file_or_of_a_folder_elsewhere(kb_client: KBClient, make_kb: MakeKb) -> None:
    kb_id = make_kb()
    record_id = upload_text_record(kb_client, kb_id, "spec-audit-same-name.txt")
    wait_for_record(kb_client, record_id)
    parent = _create(kb_client, kb_id, "parent")
    _create(kb_client, kb_id, "twin", parent)
    _create(kb_client, kb_id, "twin")
    _create(kb_client, kb_id, "spec-audit-same-name.txt")


def test_create_ignores_other_fields_and_query_parameters(kb_client: KBClient, make_kb: MakeKb) -> None:
    kb_id = make_kb()
    name = unique_name("spec-audit-extra")
    with outside_request_contract("the validator drops body fields and query parameters it does not know"):
        resp = kb_client.post(
            f"/{kb_id}/folder", params={"parentId": MISSING_RECORD_ID}, json={"folderName": name, "name": "other"}
        )
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["name"] == name
    assert resp.json()["id"] in _folders(kb_client, kb_id)


@pytest.mark.parametrize(
    ("first", "second"),
    [pytest.param("Reports", "Reports", id="same-name"), pytest.param("Reports", "rEPORTS", id="other-case")],
)
def test_create_a_name_already_used_in_the_parent_is_a_conflict(
    kb_client: KBClient, make_kb: MakeKb, first: str, second: str
) -> None:
    kb_id = make_kb()
    _create(kb_client, kb_id, first)
    resp = kb_client.post(f"/{kb_id}/folder", json={"folderName": second})
    assert resp.status_code == 409, resp.text[:500]
    assert resp.json()["error"]["message"] == f"Folder '{second}' already exists in KB root"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_create_a_name_already_used_in_a_subfolder_is_a_conflict(kb_client: KBClient, make_kb: MakeKb) -> None:
    kb_id = make_kb()
    parent = _create(kb_client, kb_id, "parent")
    _create(kb_client, kb_id, "child", parent)
    resp = kb_client.post(f"/{kb_id}/folder", params={"folderId": parent}, json={"folderName": "CHILD"})
    assert resp.status_code == 409, resp.text[:500]
    assert resp.json()["error"]["message"] == "Folder 'CHILD' already exists in this folder"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_create_below_the_deepest_level_is_bad_request(kb_client: KBClient, make_kb: MakeKb) -> None:
    kb_id = make_kb()
    deepest = None
    for level in range(MAX_FOLDER_DEPTH):
        deepest = _create(kb_client, kb_id, f"level-{level}", deepest)
    resp = kb_client.post(f"/{kb_id}/folder", params={"folderId": deepest}, json={"folderName": "too-deep"})
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["message"].startswith(f"Folders can be nested at most {MAX_FOLDER_DEPTH} levels deep.")
    assert_strict_openapi_exchange(resp, ROUTE)


def test_create_in_a_parent_that_is_not_a_folder_is_not_found(kb_client: KBClient, make_kb: MakeKb) -> None:
    kb_id = make_kb()
    record_id = upload_text_record(kb_client, kb_id)
    wait_for_record(kb_client, record_id)
    for parent in (MISSING_RECORD_ID, record_id):
        resp = kb_client.post(f"/{kb_id}/folder", params={"folderId": parent}, json={"folderName": "child"})
        assert resp.status_code == 404, resp.text[:500]
        assert resp.json()["error"]["message"] == f"Parent folder {parent} not found in KB {kb_id}"
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
def test_create_rejects_a_body_outside_the_validator(kb_client: KBClient, payload: Any) -> None:
    resp = kb_client.post(f"/{MISSING_RECORD_ID}/folder", json=payload)
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_create_without_a_body_is_rejected(kb_client: KBClient) -> None:
    resp = kb_client.post(f"/{MISSING_RECORD_ID}/folder")
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_create_with_an_empty_parent_folder_id_is_rejected(kb_client: KBClient) -> None:
    resp = kb_client.post(f"/{MISSING_RECORD_ID}/folder", params={"folderId": ""}, json={"folderName": "x"})
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["metadata"]["errors"][0]["field"] == "query.folderId"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_create_kb_id_that_is_not_a_uuid_is_rejected(kb_client: KBClient) -> None:
    resp = kb_client.post(f"/{MALFORMED_ID}/folder", json={"folderName": "x"})
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["metadata"]["errors"][0]["field"] == "params.kbId"
    assert_strict_openapi_exchange(resp, ROUTE)
    assert path_parameter_spec_problems("POST", ROUTE, "kbId", MALFORMED_ID)


@pytest.mark.parametrize("name", ["spec-audit %s", "spec-audit %x"])
def test_create_refuses_format_specifiers_in_the_name(kb_client: KBClient, make_kb: MakeKb, name: str) -> None:
    kb_id = make_kb()
    resp = kb_client.post(f"/{kb_id}/folder", json={"folderName": name})
    assert resp.status_code == 400, resp.text[:500]
    error = resp.json()["error"]
    assert error["code"] == "HTTP_BAD_REQUEST"
    assert "format specifiers" in error["message"]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)
    assert _folders(kb_client, kb_id) == {}


def test_create_refuses_html_in_the_name_before_the_token_check(kb_client: KBClient) -> None:
    resp = kb_client.post(f"/{MISSING_RECORD_ID}/folder", auth=False, json={"folderName": "<i>x</i>"})
    assert resp.status_code == 400, resp.text[:500]
    assert HTML_REFUSED_MESSAGE in resp.json()["error"]["message"]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_create_in_an_unknown_knowledge_base_is_not_found(kb_client: KBClient) -> None:
    resp = kb_client.post(f"/{MISSING_RECORD_ID}/folder", json={"folderName": "x"})
    assert resp.status_code == 404, resp.text[:500]
    assert resp.json()["error"]["message"] == f"Knowledge base {MISSING_RECORD_ID} not found"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_create_as_member_without_a_role_is_not_found(second_user: SecondUser, make_kb: MakeKb) -> None:
    kb_id = make_kb()
    resp = request_as(second_user, "POST", f"/{kb_id}/folder", json={"folderName": "x"})
    assert resp.status_code == 404, resp.text[:500]
    assert resp.json()["error"]["message"] == f"Knowledge base {kb_id} not found"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_create_as_reader_is_forbidden(second_user: SecondUser, make_kb: MakeKb) -> None:
    kb_id = make_kb(member_role="READER")
    resp = request_as(second_user, "POST", f"/{kb_id}/folder", json={"folderName": "x"})
    assert resp.status_code == 403, resp.text[:500]
    assert "OWNER or WRITER role required, but your role is: READER" in resp.json()["error"]["message"]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_create_kb_id_that_cannot_be_a_url_segment_is_bad_request(kb_client: KBClient) -> None:
    resp = kb_client.post(f"/{UNSAFE_ID}/folder", json={"folderName": "x"})
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
def test_create_rejects_unauthenticated_calls(kb_client: KBClient, headers: dict[str, str]) -> None:
    resp = kb_client.post(f"/{MISSING_RECORD_ID}/folder", auth=False, headers=headers, json={"folderName": "x"})
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_create_with_a_token_lacking_kb_write_is_forbidden(
    kb_client: KBClient, unscoped_headers: dict[str, str]
) -> None:
    resp = kb_client.post(
        f"/{MISSING_RECORD_ID}/folder", auth=False, headers=unscoped_headers, json={"folderName": "x"}
    )
    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["message"] == "Insufficient scope. Required: kb:write"
    assert_strict_openapi_exchange(resp, ROUTE)
