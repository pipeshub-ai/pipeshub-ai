"""
Upload smoke tests + validation edge cases — tests 1, 33-36, 45.

Test 1  : Upload non-versioned document → 200, isVersionedFile=false
Test 33 : POST /upload without a file attached → 400
Test 34 : POST /upload without documentName → 400
Test 36 : POST /upload with unsupported file extension → 400
Test 45 : POST /upload with documentName containing '/' → 400
"""

from __future__ import annotations

import io
import logging

import pytest
import requests

from storage_client import StorageClient

logger = logging.getLogger("storage-integration")


# ---------------------------------------------------------------------------
# Test 1 — non-versioned upload smoke test
# ---------------------------------------------------------------------------

def test_upload_non_versioned(sc: StorageClient):
    """Upload a non-versioned .txt file; verify isVersionedFile=false and versionHistory empty."""
    resp = sc.upload(
        file_content=b"non-versioned content",
        file_name="smoke_test.txt",
        document_name="Smoke Test Non-Versioned",
        is_versioned=False,
    )
    assert resp.status_code == 200, f"Upload failed ({resp.status_code}): {resp.text}"

    body = resp.json()
    doc_id = str(body.get("_id") or body.get("id") or body.get("documentId"))
    assert doc_id and doc_id != "None", f"No documentId in response: {body}"
    assert body.get("isVersionedFile") is False, f"Expected isVersionedFile=false: {body}"
    assert len(body.get("versionHistory") or []) == 0, (
        f"Expected empty versionHistory for non-versioned doc: {body}"
    )
    logger.info("Non-versioned doc created: %s", doc_id)

    # cleanup
    sc.delete_document(doc_id)


# ---------------------------------------------------------------------------
# Validation tests — independent, no shared state needed
# ---------------------------------------------------------------------------

def test_upload_without_file(sc: StorageClient):
    """Test 33: POST /upload without attaching a file → 400."""
    url = sc._url("/upload")
    resp = requests.post(
        url,
        headers=sc._auth_headers(),
        data={"documentName": "NoFile", "isVersionedFile": "false"},
        timeout=sc._c.timeout_seconds,
    )
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"


def test_upload_without_document_name(sc: StorageClient):
    """Test 34: POST /upload without documentName → 400."""
    files = [("file", ("test.txt", io.BytesIO(b"data"), "text/plain"))]
    resp = requests.post(
        sc._url("/upload"),
        headers=sc._auth_headers(),
        data={"isVersionedFile": "false"},
        files=files,
        timeout=sc._c.timeout_seconds,
    )
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"


def test_upload_unsupported_mime_type(sc: StorageClient):
    """Test 36: Upload a file whose extension has no known MIME type → 400."""
    files = [("file", ("payload.unknownxyz", io.BytesIO(b"data"), "application/octet-stream"))]
    resp = requests.post(
        sc._url("/upload"),
        headers=sc._auth_headers(),
        data={"documentName": "BadMime", "isVersionedFile": "false"},
        files=files,
        timeout=sc._c.timeout_seconds,
    )
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"


def test_upload_document_name_with_slash(sc: StorageClient):
    """Test 45: POST /upload with documentName containing '/' → 400."""
    files = [("file", ("test.txt", io.BytesIO(b"data"), "text/plain"))]
    resp = requests.post(
        sc._url("/upload"),
        headers=sc._auth_headers(),
        data={"documentName": "path/traversal", "isVersionedFile": "false"},
        files=files,
        timeout=sc._c.timeout_seconds,
    )
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
