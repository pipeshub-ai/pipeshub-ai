"""Strict OpenAPI audit of POST /api/v1/knowledgeBase/:kbId/upload.

guardPathParams(kbId) -> authenticate -> requireScopes(kb:upload) -> multipart parser (field
``files``, at most 1000; per-file type and size checks record rejections instead of failing the
batch; files_metadata gives each file its path and time) -> recordName XSS/format check -> zod
(kbId UUID; folderId non-empty; recordName, recordType, origin non-empty; isVersioned boolean
or a boolean-like string; files_metadata an array of {file_path, last_modified}) ->
uploadRecords, which checks the caller's role and the folder, then answers 200 with a
text/event-stream of per-file outcomes.

The gate reads neither multipart requests nor event streams, so the tests check the stream
frames with ``upload_event_problems`` and multipart refusals with ``multipart_spec_problems``.
"""

from __future__ import annotations

import io
import json
from typing import Any

import pytest
import requests
from helper.clients.kb_client import KBClient
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser
from knowledge_base_audit_support import (
    KB_BASE,
    MALFORMED_ID,
    MISSING_RECORD_ID,
    UNSAFE_ID,
    UNSAFE_ID_MESSAGE,
    MakeKb,
    multipart_spec_problems,
    oauth_token_with_scopes,
    sse_events,
    unique_name,
    upload_event_problems,
    wait_for_record,
)
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/knowledgeBase/:kbId/upload"
MULTIPART_GAP = "the gate cannot read multipart bodies; the test checks the spec's multipart schema itself"
NO_FILES = "No files available for processing"


def _file(name: str = "spec-audit.txt", content: bytes = b"spec audit upload", mime: str = "text/plain") -> Any:
    return ("files", (name, io.BytesIO(content), mime))


def _upload(
    headers: dict[str, str],
    base_url: str,
    kb_id: str,
    files: list[Any],
    data: dict[str, str] | None = None,
    params: dict[str, str] | None = None,
) -> requests.Response:
    return requests.post(
        f"{base_url}{KB_BASE}/{kb_id}/upload", headers=headers, files=files, data=data, params=params, timeout=300
    )


@pytest.fixture
def admin(pipeshub_client: PipeshubClient) -> dict[str, str]:
    pipeshub_client._ensure_access_token()
    return {"Authorization": f"Bearer {pipeshub_client._access_token}"}


@pytest.fixture
def upload(admin: dict[str, str], pipeshub_client: PipeshubClient) -> Any:
    def _call(kb_id: str, files: list[Any], data: dict[str, str] | None = None, **kwargs: Any) -> requests.Response:
        return _upload(admin, pipeshub_client.base_url, kb_id, files, data, **kwargs)

    return _call


def _stream(resp: requests.Response) -> list[tuple[str, Any]]:
    assert resp.status_code == 200, resp.text[:500]
    assert resp.headers["Content-Type"] == "text/event-stream"
    assert resp.text.startswith(": connected\n\n")
    events = sse_events(resp.text)
    for event, data in events:
        assert upload_event_problems(event, data) == [], (event, data)
    assert events[-1][0] == "done"
    return events


def _refused(resp: requests.Response, status: int, fields: dict[str, Any] | None = None) -> dict[str, Any]:
    assert resp.status_code == status, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    if fields is not None:
        assert multipart_spec_problems("POST", ROUTE, {"files": ["<file>"], **fields}) != [], fields
    error: dict[str, Any] = resp.json()["error"]
    return error


def test_upload_one_file_streams_its_success(kb_client: KBClient, make_kb: MakeKb, upload: Any) -> None:
    kb_id = make_kb()

    events = _stream(upload(kb_id, [_file("report.txt")]))

    assert [e for e, _ in events] == ["file:succeeded", "done"]
    detail = events[0][1]
    assert set(detail) == {"recordId", "fileName", "filePath", "extension"}
    # fileName is the name without its extension.
    assert (detail["fileName"], detail["filePath"], detail["extension"]) == ("report", "report.txt", "txt")
    assert events[1][1] == {"summary": {"total": 1, "succeeded": 1, "failed": 0}}
    wait_for_record(kb_client, detail["recordId"])
    record = kb_client.get(f"/record/{detail['recordId']}").json()["record"]
    assert (record["recordName"], record["origin"], record["recordType"]) == ("report", "UPLOAD", "FILE")


def test_upload_several_files_with_paths_creates_their_folders(
    kb_client: KBClient, make_kb: MakeKb, upload: Any
) -> None:
    kb_id = make_kb()
    metadata = [
        {"file_path": "docs/2026/a.txt", "last_modified": 1767225600000},
        {"file_path": "b.md", "last_modified": 1767225600000},
    ]
    events = _stream(
        upload(
            kb_id,
            [_file("a.txt"), _file("b.md", b"# b", "text/markdown")],
            {"files_metadata": json.dumps(metadata)},
        )
    )
    succeeded = {d["filePath"]: d for e, d in events if e == "file:succeeded"}
    assert set(succeeded) == {"docs/2026/a.txt", "b.md"}
    assert events[-1][1]["summary"] == {"total": 2, "succeeded": 2, "failed": 0}
    folders = {f["name"] for f in kb_client.get(f"/{kb_id}").json()["folders"]}
    assert {"docs", "2026"} <= folders
    wait_for_record(kb_client, succeeded["docs/2026/a.txt"]["recordId"])
    record = kb_client.get(f"/record/{succeeded['docs/2026/a.txt']['recordId']}").json()["record"]
    assert record["sourceLastModifiedTimestamp"] == 1767225600000


def test_upload_into_a_folder(kb_client: KBClient, make_kb: MakeKb, upload: Any) -> None:
    kb_id = make_kb()
    folder = kb_client.post(f"/{kb_id}/folder", json={"folderName": "inbox"}).json()["id"]
    events = _stream(upload(kb_id, [_file()], params={"folderId": folder}))
    record_id = events[0][1]["recordId"]
    children = kb_client.get(f"/knowledge-hub/nodes/folder/{folder}").json()["items"]
    assert [c["id"] for c in children] == [record_id]


def test_upload_reports_rejected_and_duplicate_files_inside_the_stream(make_kb: MakeKb, upload: Any) -> None:
    kb_id = make_kb()
    _stream(upload(kb_id, [_file("twin.txt")]))

    events = _stream(upload(kb_id, [_file("twin.txt"), _file("notes.qqq", b"?", "application/octet-stream")]))

    failed = {d["filePath"]: d for e, d in events if e == "file:failed"}
    # A file refused up front keeps its whole name; one refused later loses the extension.
    assert failed["notes.qqq"]["fileName"] == "notes.qqq"
    assert failed["twin.txt"]["fileName"] == "twin"
    assert failed["notes.qqq"]["reason"] == "UNSUPPORTED_TYPE"
    assert failed["notes.qqq"]["stage"] == "upload"
    assert "recordId" not in failed["notes.qqq"]
    assert failed["twin.txt"]["reason"] == "DUPLICATE_NAME"
    assert failed["twin.txt"]["stage"] == "index"
    assert failed["twin.txt"]["recordId"]
    assert failed["twin.txt"]["errors"] == ['A file named "twin" already exists in this location; it was skipped.']
    assert events[-1][1]["summary"] == {"total": 2, "succeeded": 0, "failed": 2}


def test_upload_of_only_unsupported_files_still_answers_200(make_kb: MakeKb, upload: Any) -> None:
    kb_id = make_kb()
    events = _stream(upload(kb_id, [_file(".DS_Store", b"x", "application/octet-stream")]))
    assert [e for e, _ in events] == ["file:failed", "done"]
    assert events[0][1]["reason"] == "UNSUPPORTED_TYPE"
    assert events[1][1]["summary"] == {"total": 1, "succeeded": 0, "failed": 1}


def test_upload_reports_a_file_over_the_size_limit_inside_the_stream(
    kb_client: KBClient, make_kb: MakeKb, upload: Any
) -> None:
    kb_id = make_kb()
    limit = int(kb_client.get("/limits").json()["maxFileSizeBytes"])
    # Held in memory only: a rejected file is never stored.
    events = _stream(upload(kb_id, [_file("big.txt", b"x" * (limit + 1)), _file("small.txt")]))
    failed = [d for e, d in events if e == "file:failed"]
    assert len(failed) == 1 and failed[0]["reason"] == "EXCEEDS_SIZE_LIMIT"
    assert failed[0]["filePath"] == "big.txt"
    assert events[-1][1]["summary"] == {"total": 2, "succeeded": 1, "failed": 1}


@pytest.mark.parametrize("value", ["true", "false", "1", "0", ""])
def test_upload_reads_boolean_like_is_versioned(make_kb: MakeKb, upload: Any, value: str) -> None:
    kb_id = make_kb()
    assert multipart_spec_problems("POST", ROUTE, {"files": ["<file>"], "isVersioned": value}) == []
    events = _stream(upload(kb_id, [_file()], {"isVersioned": value}))
    assert events[-1][1]["summary"]["succeeded"] == 1


def test_upload_ignores_record_name_origin_and_record_type(
    kb_client: KBClient, make_kb: MakeKb, upload: Any
) -> None:
    kb_id = make_kb()
    fields = {"recordName": "renamed", "origin": "CONNECTOR", "recordType": "MAIL"}
    assert multipart_spec_problems("POST", ROUTE, {"files": ["<file>"], **fields}) == []
    events = _stream(upload(kb_id, [_file("kept.txt")], fields))
    record_id = events[0][1]["recordId"]
    wait_for_record(kb_client, record_id)
    record = kb_client.get(f"/record/{record_id}").json()["record"]
    assert (record["recordName"], record["origin"], record["recordType"]) == ("kept", "UPLOAD", "FILE")


def test_upload_ignores_unknown_fields_and_query_parameters(make_kb: MakeKb, upload: Any) -> None:
    kb_id = make_kb()
    # rejectedFiles and fileBuffers are set by the gateway itself; a client's value is replaced.
    with outside_request_contract("fields and query parameters the upload does not document"):
        resp = upload(kb_id, [_file()], {"rejectedFiles": "[]", "fileBuffers": "x"}, params={"notify": "1"})
        events = _stream(resp)
    assert events[-1][1]["summary"] == {"total": 1, "succeeded": 1, "failed": 0}


@pytest.mark.parametrize(
    ("fields", "field"),
    [
        pytest.param({"recordName": ""}, "body.recordName", id="record-name-empty"),
        pytest.param({"origin": ""}, "body.origin", id="origin-empty"),
        pytest.param({"recordType": ""}, "body.recordType", id="record-type-empty"),
        pytest.param(
            {"files_metadata": json.dumps([{"file_path": "a.txt", "last_modified": "2026-01-01"}])},
            "body.files_metadata",
            id="metadata-time-not-a-number",
        ),
        pytest.param(
            {"files_metadata": json.dumps([{"last_modified": 1}])}, "body.files_metadata", id="metadata-without-path"
        ),
    ],
)
def test_upload_rejects_fields_outside_the_validator(
    make_kb: MakeKb, upload: Any, fields: dict[str, str], field: str
) -> None:
    kb_id = make_kb()
    with outside_request_contract(MULTIPART_GAP):
        error = _refused(upload(kb_id, [_file()], fields), 400, fields)
    assert error["code"] == "VALIDATION_ERROR"
    assert error["metadata"]["errors"][0]["field"] == field


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        pytest.param({"files_metadata": "{not json"}, "Invalid files_metadata format. Expected JSON array.", id="metadata-not-json"),
        pytest.param({"files_metadata": "[]"}, "Metadata count mismatch: expected 1 entries but got 0", id="metadata-count"),
        pytest.param({"files_metadata": "{}"}, "Metadata count mismatch: expected 1 entries but got undefined", id="metadata-object"),
        pytest.param(
            {"recordName": "x %s"},
            "Record name contains potentially dangerous format specifiers. Format specifiers like %s, %x, %n are not allowed.",
            id="record-name-format-specifier",
        ),
    ],
)
def test_upload_refuses_fields_the_gateway_checks_by_hand(
    make_kb: MakeKb, upload: Any, fields: dict[str, str], message: str
) -> None:
    kb_id = make_kb()
    with outside_request_contract(MULTIPART_GAP):
        error = _refused(upload(kb_id, [_file()], fields), 400, fields)
    assert error == {"code": "HTTP_BAD_REQUEST", "message": message, "requestId": error["requestId"]}


def test_upload_with_is_versioned_that_is_not_boolean_like_is_an_internal_error(make_kb: MakeKb, upload: Any) -> None:
    kb_id = make_kb()
    with outside_request_contract(MULTIPART_GAP):
        error = _refused(upload(kb_id, [_file()], {"isVersioned": "maybe"}), 500, {"isVersioned": "maybe"})
    assert error["code"] == "INTERNAL_ERROR"


@pytest.mark.parametrize(
    ("files", "message"),
    [
        pytest.param([], NO_FILES, id="no-files"),
        pytest.param(
            [("file", ("a.txt", io.BytesIO(b"x"), "text/plain"))],
            "The files weren't sent the way this page expects. Refresh the page and try again.",
            id="other-field-name",
        ),
    ],
)
def test_upload_without_files_is_bad_request(make_kb: MakeKb, upload: Any, files: list[Any], message: str) -> None:
    kb_id = make_kb()
    resp = upload(kb_id, files, {"recordName": "x"})
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["message"] == message
    assert_strict_openapi_exchange(resp, ROUTE)


def test_upload_as_json_is_bad_request(
    make_kb: MakeKb, admin: dict[str, str], pipeshub_client: PipeshubClient
) -> None:
    kb_id = make_kb()
    # Only multipart is parsed: a JSON fileBuffers list cannot stand in for files.
    with outside_request_contract("a JSON body where the operation documents only multipart"):
        resp = requests.post(
            f"{pipeshub_client.base_url}{KB_BASE}/{kb_id}/upload",
            headers=admin,
            json={"fileBuffers": [{"originalname": "a.txt", "buffer": "eA==", "mimetype": "text/plain"}]},
            timeout=60,
        )
        assert resp.status_code == 400, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["message"] == NO_FILES


def test_upload_with_an_empty_folder_id_is_rejected(make_kb: MakeKb, upload: Any) -> None:
    resp = upload(make_kb(), [_file()], params={"folderId": ""})
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["metadata"]["errors"][0]["field"] == "query.folderId"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_upload_kb_id_that_is_not_a_uuid_is_rejected(upload: Any) -> None:
    resp = upload(MALFORMED_ID, [_file()])
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["metadata"]["errors"][0]["field"] == "params.kbId"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_upload_kb_id_that_cannot_be_a_url_segment_is_bad_request(upload: Any) -> None:
    resp = upload(UNSAFE_ID, [_file()])
    assert resp.status_code == 400, resp.text[:500]
    assert UNSAFE_ID_MESSAGE in resp.json()["error"]["message"]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_upload_to_an_unknown_knowledge_base_is_not_found(upload: Any) -> None:
    resp = upload(MISSING_RECORD_ID, [_file()])
    assert resp.status_code == 404, resp.text[:500]
    assert resp.json()["error"]["message"] == f"Knowledge base {MISSING_RECORD_ID} not found"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_upload_to_a_folder_that_is_not_there_is_not_found(make_kb: MakeKb, upload: Any) -> None:
    kb_id = make_kb()
    resp = upload(kb_id, [_file()], params={"folderId": MISSING_RECORD_ID})
    assert resp.status_code == 404, resp.text[:500]
    assert resp.json()["error"]["message"].startswith(f"Folder {MISSING_RECORD_ID} was not found in knowledge base")
    assert_strict_openapi_exchange(resp, ROUTE)


def test_upload_as_member_without_a_role_is_not_found(
    make_kb: MakeKb, second_user: SecondUser, pipeshub_client: PipeshubClient
) -> None:
    kb_id = make_kb()
    resp = _upload({"Authorization": second_user.headers["Authorization"]}, pipeshub_client.base_url, kb_id, [_file()])
    assert resp.status_code == 404, resp.text[:500]
    assert resp.json()["error"]["message"] == f"Knowledge base {kb_id} not found"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_upload_as_reader_is_forbidden(
    make_kb: MakeKb, second_user: SecondUser, pipeshub_client: PipeshubClient
) -> None:
    kb_id = make_kb(member_role="READER")
    resp = _upload({"Authorization": second_user.headers["Authorization"]}, pipeshub_client.base_url, kb_id, [_file()])
    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["message"] == "You do not have permission to upload to this knowledge base"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_upload_as_writer_is_allowed(make_kb: MakeKb, second_user: SecondUser, pipeshub_client: PipeshubClient) -> None:
    kb_id = make_kb(member_role="WRITER")
    resp = _upload({"Authorization": second_user.headers["Authorization"]}, pipeshub_client.base_url, kb_id, [_file()])
    assert _stream(resp)[-1][1]["summary"]["succeeded"] == 1


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="no-token"),
        pytest.param({"Authorization": "Bearer not-a-jwt"}, id="invalid-token"),
    ],
)
def test_upload_rejects_unauthenticated_calls(pipeshub_client: PipeshubClient, headers: dict[str, str]) -> None:
    resp = _upload(headers, pipeshub_client.base_url, MISSING_RECORD_ID, [_file()])
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_upload_with_a_token_lacking_kb_upload_is_forbidden(
    pipeshub_client: PipeshubClient, unscoped_headers: dict[str, str]
) -> None:
    headers = {"Authorization": unscoped_headers["Authorization"]}
    resp = _upload(headers, pipeshub_client.base_url, MISSING_RECORD_ID, [_file()])
    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["message"] == "Insufficient scope. Required: kb:upload"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_upload_with_kb_upload_but_not_kb_read_is_forbidden(
    pipeshub_client: PipeshubClient, make_kb: MakeKb
) -> None:
    kb_id = make_kb()
    # requireScopes passes on kb:upload; the knowledge base lookup before the upload needs kb:read.
    with oauth_token_with_scopes(pipeshub_client.base_url, ["kb:upload"], pipeshub_client.timeout_seconds) as token:
        resp = _upload({"Authorization": f"Bearer {token}"}, pipeshub_client.base_url, kb_id, [_file()])
    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["message"] == "You do not have permission to upload to this knowledge base"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_upload_with_kb_upload_and_kb_read_succeeds(pipeshub_client: PipeshubClient, make_kb: MakeKb) -> None:
    kb_id = make_kb()
    with oauth_token_with_scopes(
        pipeshub_client.base_url, ["kb:upload", "kb:read"], pipeshub_client.timeout_seconds
    ) as token:
        resp = _upload({"Authorization": f"Bearer {token}"}, pipeshub_client.base_url, kb_id, [_file()])
        events = sse_events(resp.text)
    assert resp.status_code == 200, resp.text[:500]
    assert [event for event, _ in events][-1] == "done", events
    assert any(event == "file:succeeded" for event, _ in events), events
    assert [problem for event, data in events for problem in upload_event_problems(event, data)] == []


def test_upload_into_a_folder_in_the_trash_is_a_conflict(
    upload: Any, kb_client: KBClient, make_kb: MakeKb, trash_on: None
) -> None:
    kb_id = make_kb()
    name = unique_name("trashed")
    folder_id = kb_client.create_folder(kb_id, name)["id"]
    deleted = kb_client.delete(f"/record/{folder_id}")
    assert deleted.status_code == 200 and deleted.json()["softDeleted"] is True, deleted.text[:500]
    resp = upload(kb_id, [_file()], params={"folderId": folder_id})
    assert resp.status_code == 409, resp.text[:500]
    assert resp.json()["error"]["message"] == (
        f"'{name}' is in Recently deleted, so you can't upload files to it. "
        "Restore it first, or choose another folder."
    )
    assert_strict_openapi_exchange(resp, ROUTE)
