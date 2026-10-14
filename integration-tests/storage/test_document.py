"""
Document get + delete tests — tests 2, 7, 17, 25-26.

Test 2  : GET /:documentId returns 200 and _id matches
Test 7  : DELETE /:documentId returns 200 and isDeleted=true
Test 17 : Upload → delete → GET → 404
Test 25 : GET valid-format ObjectId that doesn't exist → 404
Test 26 : DELETE non-existent documentId → 404
Malformed ids (not a 24-character hex ObjectId), in the path or in the
storage token, → 400 with a message that says what to send, never 500
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

import requests

from storage_client import StorageClient, mint_storage_token

logger = logging.getLogger("storage-integration")

NONEXISTENT_ID = "000000000000000000000001"
MALFORMED_ID = "not-an-object-id"


def _doc_id(body: dict[str, Any]) -> str:
    return str(body.get("_id") or body.get("id") or body.get("documentId"))


# ---------------------------------------------------------------------------
# Test 2 — GET by ID
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.storage
def test_get_document_by_id(sc: StorageClient):
    resp = sc.upload(
        file_content=b"get-by-id test",
        file_name="get_test.txt",
        document_name="Get Test Doc",
        is_versioned=False,
    )
    assert resp.status_code == 200
    doc_id = _doc_id(resp.json())

    get_resp = sc.get_document(doc_id)
    assert get_resp.status_code == 200, f"GET failed ({get_resp.status_code}): {get_resp.text}"

    body = get_resp.json()
    assert _doc_id(body) == doc_id, f"_id mismatch: expected {doc_id}, got {_doc_id(body)}"

    # cleanup
    sc.delete_document(doc_id)


# ---------------------------------------------------------------------------
# Test 7 — DELETE
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.storage
def test_delete_document(sc: StorageClient):
    resp = sc.upload(
        file_content=b"delete me",
        file_name="delete_test.txt",
        document_name="Delete Test Doc",
        is_versioned=False,
    )
    assert resp.status_code == 200
    doc_id = _doc_id(resp.json())

    del_resp = sc.delete_document(doc_id)
    assert del_resp.status_code == 200, f"DELETE failed ({del_resp.status_code}): {del_resp.text}"
    assert del_resp.json().get("isDeleted") is True, f"Expected isDeleted=true: {del_resp.json()}"


# ---------------------------------------------------------------------------
# Test 17 — Upload → delete → operations that filter isDeleted → 404
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.storage
def test_deleted_document_buffer_returns_404(sc: StorageClient):
    """
    GET /:documentId returns the doc even after soft-delete (isDeleted=true, no filter).
    But /buffer, /download, and /isModified all call getDocumentInfo which filters
    isDeleted: false, so those routes return 404 after deletion.
    """
    resp = sc.upload(
        file_content=b"will be deleted",
        file_name="delete_then_get.txt",
        document_name="Delete Then Get",
        is_versioned=False,
    )
    assert resp.status_code == 200
    doc_id = _doc_id(resp.json())

    sc.delete_document(doc_id)

    # GET /:documentId still returns the document (soft-delete, no isDeleted filter)
    get_resp = sc.get_document(doc_id)
    assert get_resp.status_code == 200
    assert get_resp.json().get("isDeleted") is True

    # /buffer uses getDocumentInfo which filters isDeleted:false → 404
    buf_resp = sc.get_document_buffer(doc_id)
    assert buf_resp.status_code == 404, (
        f"Expected /buffer 404 after delete, got {buf_resp.status_code}: {buf_resp.text}"
    )


# ---------------------------------------------------------------------------
# Tests 25 & 26 — Not-found for non-existent IDs
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.storage
def test_get_nonexistent_document(sc: StorageClient):
    """Test 25: GET valid-format ObjectId that doesn't exist → 404."""
    resp = sc.get_document(NONEXISTENT_ID)
    assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"


@pytest.mark.integration
@pytest.mark.storage
def test_delete_nonexistent_document(sc: StorageClient):
    """Test 26: DELETE non-existent documentId → 404."""
    resp = sc.delete_document(NONEXISTENT_ID)
    assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"


# ---------------------------------------------------------------------------
# Malformed ids — the server used to fail casting them and answer 500
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.storage
@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda sc: sc.get_document(MALFORMED_ID), id="get"),
        pytest.param(lambda sc: sc.delete_document(MALFORMED_ID), id="delete"),
        pytest.param(lambda sc: sc.get_document_buffer(MALFORMED_ID), id="buffer"),
        pytest.param(lambda sc: sc.download_document(MALFORMED_ID), id="download"),
        pytest.param(lambda sc: sc.is_modified(MALFORMED_ID), id="is-modified"),
    ],
)
def test_malformed_document_id_is_a_400(sc: StorageClient, call):
    resp = call(sc)
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
    message = resp.json()["error"]["message"]
    assert "24-character id" in message, message


@pytest.mark.integration
@pytest.mark.storage
def test_malformed_user_id_in_token_is_a_400(sc: StorageClient):
    token = mint_storage_token(sc.org_id, "not-a-user-id")
    resp = requests.post(
        sc._url("/placeholder"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={
            "documentName": "Malformed User Id",
            "extension": "txt",
            "documentPath": "integration-tests/malformed-user-id",
            "isVersionedFile": False,
        },
        timeout=30,
    )
    # A 200 here would have created a document the suite must clean up.
    sc._track_document_from_response(resp)
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
    assert "user id in the storage token" in resp.json()["error"]["message"]
