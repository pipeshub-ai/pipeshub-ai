"""Strict OpenAPI audit of PUT /api/v1/knowledgeBase/record/:recordId.

guardPathParams(recordId) -> authenticate -> kb:write scope -> multer (optional ``file``)
-> XSS and format-specifier check on ``recordName`` -> zod -> updateRecord. With a file,
the gateway checks the caller's role, stores the file as the next version of the record's
storage document, then calls the connector service (PUT /api/v1/kb/record/{record_id});
without one it only renames. A JSON body is read too: multer only handles multipart.
"""

from __future__ import annotations

import io
from typing import Any

import pytest
import requests
from helper.clients.kb_client import KBClient
from helper.second_user import SecondUser
from knowledge_base_audit_support import (
    HTML_REFUSED_MESSAGE,
    MISSING_RECORD_ID,
    UNSAFE_ID,
    UNSAFE_ID_MESSAGE,
    SeedRecord,
    request_as,
    unique_name,
)
from strict_openapi import (
    assert_strict_openapi_exchange,
    assert_strict_openapi_response,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/knowledgeBase/record/:recordId"
RENAMED = "Record updated successfully"
REPLACED = "Record updated with new file version"

Multipart = dict[str, tuple[str | None, Any] | tuple[str | None, Any, str]]


def rename_form(record_name: str) -> Multipart:
    """A multipart body with only the text field; the route reads fields through multer."""
    return {"recordName": (None, record_name)}


def text_file(file_name: str, content: bytes = b"replaced by the spec audit") -> tuple[str, Any, str]:
    return (file_name, io.BytesIO(content), "text/plain")


def _updated(resp: requests.Response, message: str) -> dict[str, Any]:
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body: dict[str, Any] = resp.json()
    assert body["message"] == message
    assert body["fileUploaded"] is (message == REPLACED)
    record: dict[str, Any] = body["record"]
    return record


def test_rename_with_a_multipart_form_returns_the_updated_record(
    kb_client: KBClient, seed_record: SeedRecord
) -> None:
    record_id = seed_record()
    new_name = unique_name("spec-audit-renamed")

    record = _updated(kb_client.put(f"/record/{record_id}", files=rename_form(new_name)), RENAMED)

    assert record["id"] == record_id
    assert record["recordName"] == new_name
    assert kb_client.get(f"/record/{record_id}").json()["record"]["recordName"] == new_name


def test_rename_with_a_json_body_works_the_same(kb_client: KBClient, seed_record: SeedRecord) -> None:
    record_id = seed_record()
    new_name = unique_name("spec-audit-json")

    record = _updated(kb_client.put(f"/record/{record_id}", json={"recordName": new_name}), RENAMED)

    assert record["recordName"] == new_name


def test_rename_has_no_length_limit_of_255(kb_client: KBClient, seed_record: SeedRecord) -> None:
    record_id = seed_record()
    new_name = unique_name("spec-audit-long").ljust(300, "n")

    record = _updated(kb_client.put(f"/record/{record_id}", json={"recordName": new_name}), RENAMED)

    assert record["recordName"] == new_name


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({}, id="no-body"),
        pytest.param({"json": {}}, id="empty-json-object"),
        pytest.param({"json": {"recordName": ""}}, id="empty-name-json"),
        # The JSON body is trimmed before the handler sees it; a multipart field is not.
        pytest.param({"json": {"recordName": "   "}}, id="blank-name-json"),
        pytest.param({"files": rename_form("")}, id="empty-name-multipart"),
    ],
)
def test_update_with_nothing_to_change_still_answers_200(
    kb_client: KBClient, seed_record: SeedRecord, kwargs: dict[str, Any]
) -> None:
    stem = unique_name("spec-audit-unchanged")
    record_id = seed_record(f"{stem}.txt")

    record = _updated(kb_client.put(f"/record/{record_id}", **kwargs), RENAMED)

    assert record["recordName"] == stem


def test_update_ignores_fields_it_does_not_know(kb_client: KBClient, seed_record: SeedRecord) -> None:
    stem = unique_name("spec-audit-extra")
    record_id = seed_record(f"{stem}.txt")
    with outside_request_contract("the validator drops body fields it does not know"):
        resp = kb_client.put(
            f"/record/{record_id}", json={"indexingStatus": "COMPLETED", "version": 9, "isDeleted": True}
        )
        record = _updated(resp, RENAMED)
    assert record["recordName"] == stem
    assert record["isDeleted"] is False
    assert record["version"] != 9


def test_replace_file_stores_a_new_version_named_after_the_file(
    kb_client: KBClient, seed_record: SeedRecord
) -> None:
    record_id = seed_record()
    before = kb_client.get(f"/record/{record_id}").json()["record"]
    new_stem = unique_name("spec-audit-v2")
    content = b"second version from the spec audit"

    resp = kb_client.put(
        f"/record/{record_id}",
        # recordName is overridden by the file name whenever a file is sent.
        files={"recordName": (None, "ignored-when-a-file-is-sent"), "file": text_file(f"{new_stem}.txt", content)},
    )
    record = _updated(resp, REPLACED)

    assert record["recordName"] == new_stem
    assert record["externalRecordId"] == before["externalRecordId"]
    assert record["externalRevisionId"] != before.get("externalRevisionId")
    # Neither is brought up to date for the new file.
    assert record["version"] == before["version"]
    assert record["sizeInBytes"] == before["sizeInBytes"] != len(content)


def test_replace_file_with_another_extension_is_an_internal_error(
    kb_client: KBClient, seed_record: SeedRecord
) -> None:
    stem = unique_name("spec-audit-ext")
    record_id = seed_record(f"{stem}.txt")

    # Storage refuses the extension change with 400; the gateway reports that as its own failure.
    resp = kb_client.put(
        f"/record/{record_id}", files={"file": (f"{stem}.md", io.BytesIO(b"# markdown"), "text/markdown")}
    )

    assert resp.status_code == 500, resp.text[:500]
    assert resp.json()["error"]["code"] == "HTTP_INTERNAL_SERVER_ERROR"
    assert "tried to save this file" in resp.json()["error"]["message"]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert kb_client.get(f"/record/{record_id}").json()["record"]["recordName"] == stem


def test_json_file_buffer_is_taken_for_an_uploaded_file(kb_client: KBClient, seed_record: SeedRecord) -> None:
    # fileBuffer is where the upload middleware puts the parsed file. Nothing stops a JSON
    # body from supplying it, and the handler then stores the JSON string as the new version.
    record_id = seed_record()
    stem = unique_name("spec-audit-injected")
    with outside_request_contract("fileBuffer is an internal field, not part of the request contract"):
        resp = kb_client.put(
            f"/record/{record_id}",
            json={
                "fileBuffer": {
                    "buffer": "text sent as JSON",
                    "originalname": f"{stem}.txt",
                    "mimetype": "text/plain",
                    "size": 17,
                    "lastModified": 1,
                }
            },
        )
        record = _updated(resp, REPLACED)
    assert record["recordName"] == stem
    assert record["sourceLastModifiedTimestamp"] == 1


def test_rename_to_a_name_taken_in_the_same_place_is_a_conflict(
    kb_client: KBClient, seed_record: SeedRecord
) -> None:
    taken = unique_name("spec-audit-taken")
    seed_record(f"{taken}.txt")
    record_id = seed_record()

    resp = kb_client.put(f"/record/{record_id}", files=rename_form(taken))

    assert resp.status_code == 409, resp.text[:500]
    assert resp.json()["error"]["message"] == f"A file named '{taken}' already exists in this location"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_rename_a_record_in_the_trash_answers_200_and_changes_nothing(
    kb_client: KBClient, seed_record: SeedRecord, trash_on: None
) -> None:
    stem = unique_name("spec-audit-in-trash")
    record_id = seed_record(f"{stem}.txt")
    deleted = kb_client.delete(f"/record/{record_id}")
    assert deleted.status_code == 200 and deleted.json()["softDeleted"] is True, deleted.text[:500]
    new_name = unique_name("spec-audit-trashed")

    # Not refused: the answer is the success message with the record as it lies in the trash.
    record = _updated(kb_client.put(f"/record/{record_id}", json={"recordName": new_name}), RENAMED)

    assert record["recordName"] == stem
    assert record["isDeleted"] is True
    assert record["deleteSource"] == "USER"
    assert record["deleteBatchId"] == deleted.json()["batchId"]
    assert isinstance(record["deletedAtTimestamp"], int)

    restored = kb_client.post(f"/record/{record_id}/restore")
    assert restored.status_code == 200, restored.text[:500]
    record = _updated(kb_client.put(f"/record/{record_id}", json={"recordName": new_name}), RENAMED)
    assert record["recordName"] == new_name
    assert record["isDeleted"] is False
    assert isinstance(record["restoredAtTimestamp"], int)


@pytest.mark.parametrize(
    ("record_name", "message"),
    [
        pytest.param(
            "<script>alert(1)</script>",
            "Record name contains potentially dangerous content",
            id="html-in-name",
        ),
        pytest.param("%s", "Record name contains potentially dangerous format specifiers", id="format-specifier"),
        # Passes the gateway (non-empty string); the connector service strips it and refuses.
        pytest.param("   ", "Record name cannot be empty", id="blank-name"),
    ],
)
def test_multipart_record_name_that_is_refused_is_bad_request(
    kb_client: KBClient, seed_record: SeedRecord, record_name: str, message: str
) -> None:
    record_id = seed_record()

    resp = kb_client.put(f"/record/{record_id}", files=rename_form(record_name))

    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "HTTP_BAD_REQUEST"
    assert message in resp.json()["error"]["message"]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_json_record_name_with_html_is_refused_before_the_token_check(kb_client: KBClient) -> None:
    resp = kb_client.put(f"/record/{MISSING_RECORD_ID}", auth=False, json={"recordName": "<b>bold</b>"})
    assert resp.status_code == 400, resp.text[:500]
    assert HTML_REFUSED_MESSAGE in resp.json()["error"]["message"]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"recordName": 20261006}, id="name-not-a-string"),
        pytest.param({"recordName": None}, id="name-null"),
        pytest.param(["recordName"], id="body-is-a-list"),
    ],
)
def test_json_body_outside_the_validator_is_rejected(kb_client: KBClient, payload: Any) -> None:
    resp = kb_client.put(f"/record/{MISSING_RECORD_ID}", json=payload)
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("files", "message"),
    [
        pytest.param(
            [("file", ("a.txt", b"one", "text/plain")), ("file", ("b.txt", b"two", "text/plain"))],
            "You can upload up to 1 files at a time",
            id="two-files",
        ),
        pytest.param(
            [("files", ("a.txt", b"one", "text/plain"))],
            "The files weren't sent the way this page expects",
            id="file-under-another-field-name",
        ),
    ],
)
def test_file_part_the_upload_parser_refuses_is_bad_request(
    kb_client: KBClient, seed_record: SeedRecord, files: list[tuple[str, Any]], message: str
) -> None:
    record_id = seed_record()

    resp = kb_client.put(f"/record/{record_id}", files=files)

    assert resp.status_code == 400, resp.text[:500]
    assert message in resp.json()["error"]["message"]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_rename_unknown_record_is_not_found(kb_client: KBClient) -> None:
    resp = kb_client.put(f"/record/{MISSING_RECORD_ID}", files=rename_form(unique_name()))
    assert resp.status_code == 404, resp.text[:500]
    assert resp.json()["error"]["message"] == "Knowledge base context not found for record"
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("with_file", "message"),
    [
        pytest.param(False, "User lacks permission to edit records", id="rename"),
        pytest.param(True, "You do not have permission to upload to this knowledge base", id="replace-file"),
    ],
)
def test_reader_is_forbidden(
    kb_client: KBClient,
    second_user: SecondUser,
    seed_shared_record: SeedRecord,
    with_file: bool,
    message: str,
) -> None:
    stem = unique_name("spec-audit-read-only")
    record_id = seed_shared_record(f"{stem}.txt")
    files: Multipart = rename_form(unique_name("spec-audit-denied"))
    if with_file:
        files = {"file": text_file("spec-audit-denied.txt")}

    resp = request_as(second_user, "PUT", f"/record/{record_id}", files=files)

    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["message"] == message
    assert_strict_openapi_response(resp, ROUTE)
    assert kb_client.get(f"/record/{record_id}").json()["record"]["recordName"] == stem


@pytest.mark.parametrize("with_file", [False, True], ids=["rename", "replace-file"])
def test_member_without_kb_role_gets_not_found(
    kb_client: KBClient, second_user: SecondUser, seed_record: SeedRecord, with_file: bool
) -> None:
    stem = unique_name("spec-audit-untouched")
    record_id = seed_record(f"{stem}.txt")
    files: Multipart = rename_form(unique_name("spec-audit-denied"))
    if with_file:
        files = {"file": text_file("spec-audit-denied.txt")}

    # No role on the knowledge base hides the record instead of answering 403.
    resp = request_as(second_user, "PUT", f"/record/{record_id}", files=files)

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert kb_client.get(f"/record/{record_id}").json()["record"]["recordName"] == stem


def test_put_id_that_cannot_be_a_url_segment_is_bad_request(kb_client: KBClient) -> None:
    resp = kb_client.put(f"/record/{UNSAFE_ID}", files=rename_form(unique_name()))
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
def test_put_rejects_unauthenticated_calls(kb_client: KBClient, headers: dict[str, str]) -> None:
    resp = kb_client.put(
        f"/record/{MISSING_RECORD_ID}", auth=False, headers=headers, json={"recordName": unique_name()}
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_put_with_a_token_lacking_kb_write_is_forbidden(
    kb_client: KBClient, unscoped_headers: dict[str, str]
) -> None:
    resp = kb_client.put(
        f"/record/{MISSING_RECORD_ID}", auth=False, headers=unscoped_headers, json={"recordName": unique_name()}
    )
    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["message"] == "Insufficient scope. Required: kb:write"
    assert_strict_openapi_exchange(resp, ROUTE)
