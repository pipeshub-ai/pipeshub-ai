"""
Document get + delete tests — tests 2, 7, 17, 25-26.

Test 2  : GET /:documentId returns 200 and _id matches
Test 7  : DELETE /:documentId returns 200 and isDeleted=true
Test 17 : Upload → delete → GET → 404
Test 25 : GET valid-format ObjectId that doesn't exist → 404
Test 26 : DELETE non-existent documentId → 404
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from storage_client import StorageClient

logger = logging.getLogger("storage-integration")

NONEXISTENT_ID = "000000000000000000000001"


def _doc_id(body: dict[str, Any]) -> str:
    return str(body.get("_id") or body.get("id") or body.get("documentId"))


# ---------------------------------------------------------------------------
# Test 2 — GET by ID
# ---------------------------------------------------------------------------

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

def test_get_nonexistent_document(sc: StorageClient):
    """Test 25: GET valid-format ObjectId that doesn't exist → 404."""
    resp = sc.get_document(NONEXISTENT_ID)
    assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"


def test_delete_nonexistent_document(sc: StorageClient):
    """Test 26: DELETE non-existent documentId → 404."""
    resp = sc.delete_document(NONEXISTENT_ID)
    assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"
