"""Strict OpenAPI audit of GET /api/v1/knowledgeBase/stream/record/:recordId.

guardPathParams(recordId) -> authenticate -> requireScopes(kb:read) -> zod (recordId non-empty;
convertTo string and version digits, both optional) -> getRecordBuffer, which pipes the
connector service's GET /api/v1/stream/record/{record_id} through. The file's bytes come back
with the file's own Content-Type; a failure of the connector service comes back as
``{"error": "<its message>"}`` with its status.
"""

from __future__ import annotations

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
    request_as,
    wait_for_record,
    web_record_group,
)
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/knowledgeBase/stream/record/:recordId"
NO_ACCESS = "You do not have permission to access this record"

FILES = [
    pytest.param("note.txt", "text/plain", b"spec audit stream text", id="text"),
    pytest.param("note.md", "text/markdown", b"# spec audit stream", id="markdown"),
    pytest.param("table.csv", "text/csv", b"a,b\n1,2\n", id="csv"),
    pytest.param("page.html", "text/html", b"<p>spec audit stream</p>", id="html"),
    pytest.param("data.json", "application/json", b'{"spec": "audit", "n": [1, 2]}', id="json"),
]


def _path(record_id: str) -> str:
    return f"/stream/record/{record_id}"


@pytest.mark.parametrize(("file_name", "mime_type", "content"), FILES)
def test_stream_returns_the_file_with_its_own_content_type(
    kb_client: KBClient, audit_kb_id: str, file_name: str, mime_type: str, content: bytes
) -> None:
    uploaded = kb_client.upload_file(audit_kb_id, file_name, content, mimetype=mime_type)
    record_id = str(uploaded["records"][0]["recordId"])
    try:
        wait_for_record(kb_client, record_id)

        resp = kb_client.get(_path(record_id))

        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
        assert resp.headers["Content-Type"] == f"{mime_type}; charset=utf-8"
        assert "Content-Disposition" not in resp.headers
        assert resp.content == content
    finally:
        kb_client.delete(f"/record/{record_id}")


def test_stream_as_reader(second_user: SecondUser, seed_shared_record: SeedRecord) -> None:
    record_id = seed_shared_record(content=b"shared stream")
    resp = request_as(second_user, "GET", _path(record_id))
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.content == b"shared stream"


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"version": "0"}, id="version-0"),
        pytest.param({"version": "7"}, id="version-7"),
        pytest.param({"convertTo": "application/pdf"}, id="convert-to-pdf"),
        pytest.param({"convertTo": "spreadsheet"}, id="convert-to-anything"),
    ],
)
def test_stream_of_a_text_file_ignores_version_and_conversion(
    kb_client: KBClient, seed_record: SeedRecord, params: dict[str, str]
) -> None:
    record_id = seed_record(content=b"unchanged")
    resp = kb_client.get(_path(record_id), params=params)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.headers["Content-Type"] == "text/plain; charset=utf-8"
    assert resp.content == b"unchanged"


def test_stream_ignores_other_query_parameters(kb_client: KBClient, seed_record: SeedRecord) -> None:
    record_id = seed_record(content=b"plain")
    with outside_request_contract("the validator drops query parameters it does not know"):
        resp = kb_client.get(_path(record_id), params={"download": "true"})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.content == b"plain"


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"version": "-1"}, id="version-negative"),
        pytest.param({"version": "1.5"}, id="version-not-an-integer"),
        pytest.param({"version": "latest"}, id="version-a-word"),
        pytest.param({"version": ""}, id="version-empty"),
        pytest.param([("version", "1"), ("version", "2")], id="version-twice"),
        pytest.param([("convertTo", "a"), ("convertTo", "b")], id="convert-to-twice"),
    ],
)
def test_stream_rejects_a_query_outside_the_validator(kb_client: KBClient, params: object) -> None:
    resp = kb_client.get(_path(MISSING_RECORD_ID), params=params)
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("record_id", [MISSING_RECORD_ID, "not-a-graph-id"], ids=["unknown-uuid", "not-a-uuid"])
def test_stream_unknown_record_is_not_found(kb_client: KBClient, record_id: str) -> None:
    resp = kb_client.get(_path(record_id))
    assert resp.status_code == 404, resp.text[:500]
    assert resp.json() == {"error": "Record not found"}
    assert_strict_openapi_exchange(resp, ROUTE)


def test_stream_a_knowledge_base_id_is_not_found(kb_client: KBClient, audit_kb_id: str) -> None:
    resp = kb_client.get(_path(audit_kb_id))
    assert resp.status_code == 404, resp.text[:500]
    assert resp.json() == {"error": "Record not found"}
    assert_strict_openapi_exchange(resp, ROUTE)


def test_stream_a_folder_is_a_bad_gateway(kb_client: KBClient, make_kb: MakeKb) -> None:
    kb_id = make_kb()
    folder = kb_client.post(f"/{kb_id}/folder", json={"folderName": "spec-audit-folder"})
    assert folder.status_code == 200, folder.text[:500]
    resp = kb_client.get(_path(folder.json()["id"]))
    assert resp.status_code == 502, resp.text[:500]
    assert resp.json() == {"error": "Could not retrieve this item right now. Please try again later."}
    assert_strict_openapi_exchange(resp, ROUTE)


def test_stream_a_record_of_a_disabled_connector_is_a_conflict(
    kb_client: KBClient, pipeshub_client: PipeshubClient
) -> None:
    with web_record_group(pipeshub_client, kb_client) as group:
        pipeshub_client.toggle_sync(group.connector_id, False)
        resp = kb_client.get(_path(group.record_id))
        assert resp.status_code == 409, resp.text[:500]
        assert "error" in resp.json()
        assert_strict_openapi_exchange(resp, ROUTE)


def test_stream_as_member_without_access_is_forbidden(second_user: SecondUser, seed_record: SeedRecord) -> None:
    record_id = seed_record()
    resp = request_as(second_user, "GET", _path(record_id))
    assert resp.status_code == 403, resp.text[:500]
    assert resp.json() == {"error": NO_ACCESS}
    assert_strict_openapi_exchange(resp, ROUTE)


def test_stream_record_in_the_trash_is_forbidden(
    kb_client: KBClient, seed_record: SeedRecord, trash_on: None
) -> None:
    record_id = seed_record()
    deleted = kb_client.delete(f"/record/{record_id}")
    assert deleted.status_code == 200, deleted.text[:500]
    resp = kb_client.get(_path(record_id))
    assert resp.status_code == 403, resp.text[:500]
    assert resp.json() == {"error": NO_ACCESS}
    assert_strict_openapi_exchange(resp, ROUTE)


def test_stream_id_that_cannot_be_a_url_segment_is_bad_request(kb_client: KBClient) -> None:
    resp = kb_client.get(_path(UNSAFE_ID))
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
def test_stream_rejects_unauthenticated_calls(kb_client: KBClient, headers: dict[str, str]) -> None:
    resp = kb_client.get(_path(MISSING_RECORD_ID), auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_stream_with_a_token_lacking_kb_read_is_forbidden(
    kb_client: KBClient, unscoped_headers: dict[str, str]
) -> None:
    resp = kb_client.get(_path(MISSING_RECORD_ID), auth=False, headers=unscoped_headers)
    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["message"] == "Insufficient scope. Required: kb:read"
    assert_strict_openapi_exchange(resp, ROUTE)
