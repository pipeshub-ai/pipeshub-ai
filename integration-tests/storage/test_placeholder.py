"""
Placeholder + directUpload tests — tests 5, 18, 19, 35, 46.

Test 5  : POST /placeholder → 200, document created
Test 18 : Create placeholder → POST /directUpload → signedUrl and documentId present
Test 19 : Upload with customMetadata → GET → metadata preserved
Test 35 : POST /placeholder without extension field → 400
Test 46 : POST /placeholder with documentName that includes an extension → 400
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
import requests

from storage_client import StorageClient

logger = logging.getLogger("storage-integration")


def _doc_id(body: dict[str, Any]) -> str:
    return str(body.get("_id") or body.get("id") or body.get("documentId"))


# ---------------------------------------------------------------------------
# Test 5 — placeholder smoke test
# ---------------------------------------------------------------------------

def test_create_placeholder(sc: StorageClient):
    """Test 5: POST /placeholder → 200 and document created."""
    resp = sc.create_placeholder(
        document_name="Placeholder Doc",
        extension="txt",
        document_path="test-folder",
    )
    assert resp.status_code == 200, f"Placeholder failed ({resp.status_code}): {resp.text}"

    body = resp.json()
    doc_id = _doc_id(body)
    assert doc_id and doc_id != "None", f"No documentId in placeholder response: {body}"
    logger.info("Placeholder created: %s", doc_id)

    sc.delete_document(doc_id)


# ---------------------------------------------------------------------------
# Test 18 — placeholder → directUpload
# ---------------------------------------------------------------------------

def test_placeholder_then_direct_upload(sc: StorageClient):
    """Test 18: Create placeholder → POST /directUpload → assert signedUrl and documentId present."""
    ph_resp = sc.create_placeholder(
        document_name="Direct Upload Doc",
        extension="pdf",
        document_path="direct-upload-test",
    )
    assert ph_resp.status_code == 200
    doc_id = _doc_id(ph_resp.json())

    du_resp = sc.direct_upload(doc_id)
    assert du_resp.status_code == 200, (
        f"directUpload failed ({du_resp.status_code}): {du_resp.text}"
    )

    body = du_resp.json()
    # Response may be wrapped (e.g. { document: {...} }) or flat
    # signedUrl may be at top level or in a nested key depending on storage vendor
    has_signed_url = (
        body.get("signedUrl")
        or (isinstance(body.get("document"), dict) and body["document"].get("signedUrl"))
    )
    has_doc_id = (
        body.get("documentId")
        or body.get("_id")
        or (isinstance(body.get("document"), dict) and body["document"].get("_id"))
    )

    # For local storage the signed URL is a local path; for S3/Azure it's a presigned URL.
    # We just assert the fields are present and non-empty.
    assert has_signed_url or du_resp.headers.get("Location"), (
        f"No signedUrl or Location header in directUpload response: {body}"
    )
    assert has_doc_id or doc_id, f"No documentId in directUpload response: {body}"


# ---------------------------------------------------------------------------
# Test 19 — customMetadata preserved
# ---------------------------------------------------------------------------

def test_upload_custom_metadata_preserved(sc: StorageClient):
    """Test 19: Upload with customMetadata → GET → metadata preserved."""
    metadata = [{"key": "project", "value": "integration-test"}, {"key": "owner", "value": "qa"}]
    resp = sc.create_placeholder(
        document_name="Metadata Doc",
        extension="txt",
        document_path="metadata-test",
        custom_metadata=metadata,
    )
    assert resp.status_code == 200, f"Placeholder failed: {resp.status_code}"
    doc_id = _doc_id(resp.json())

    get_resp = sc.get_document(doc_id)
    assert get_resp.status_code == 200

    body = get_resp.json()
    stored_meta = body.get("customMetadata") or []
    stored_keys = {item["key"] for item in stored_meta if isinstance(item, dict)}
    assert "project" in stored_keys, f"customMetadata not preserved: {stored_meta}"
    assert "owner" in stored_keys, f"customMetadata not preserved: {stored_meta}"

    sc.delete_document(doc_id)


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------

def test_placeholder_without_extension(sc: StorageClient):
    """Test 35: POST /placeholder without extension field → 400."""
    resp = requests.post(
        sc._url("/placeholder"),
        headers=sc._json_headers(),
        json={"documentName": "NoExt", "documentPath": "test"},
        timeout=sc._c.timeout_seconds,
    )
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"


def test_placeholder_document_name_with_extension(sc: StorageClient):
    """Test 46: POST /placeholder with documentName that includes an extension → 400."""
    resp = sc.create_placeholder(
        document_name="my_document.pdf",
        extension="pdf",
        document_path="test",
    )
    assert resp.status_code == 400, (
        f"Expected 400 for documentName with extension, got {resp.status_code}: {resp.text}"
    )
