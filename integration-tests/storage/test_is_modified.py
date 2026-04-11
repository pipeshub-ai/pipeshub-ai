"""
isModified tests — tests 8, 9, 29.

Test 8  : GET /isModified on a freshly uploaded doc → 200, false
Test 9  : PUT /buffer → GET /isModified → 200, true
Test 29 : GET /isModified for non-existent document → 404
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
# Test 8 — isModified false on fresh upload
# ---------------------------------------------------------------------------

def test_is_modified_false_on_fresh_upload(sc: StorageClient):
    """Test 8: isModified is false immediately after upload (no buffer update yet)."""
    resp = sc.upload(
        file_content=b"fresh content",
        file_name="fresh.txt",
        document_name="Fresh Upload",
        is_versioned=False,
    )
    assert resp.status_code == 200
    doc_id = _doc_id(resp.json())

    mod_resp = sc.is_modified(doc_id)
    assert mod_resp.status_code == 200, (
        f"isModified failed ({mod_resp.status_code}): {mod_resp.text}"
    )
    assert mod_resp.json() is False, f"Expected isModified=false, got: {mod_resp.json()}"

    sc.delete_document(doc_id)


# ---------------------------------------------------------------------------
# Test 9 — isModified true after buffer update
# ---------------------------------------------------------------------------

class TestIsModifiedAfterBufferUpdate:
    """Sequential: upload → PUT /buffer → GET /isModified → true."""

    doc_id: str = ""

    @pytest.fixture(autouse=True)
    def _bind(self, sc: StorageClient) -> None:
        self.__class__._sc = sc

    def test_01_upload(self):
        resp = self._sc.upload(
            file_content=b"original content",
            file_name="mod_test.txt",
            document_name="Mod Test",
            is_versioned=False,
        )
        assert resp.status_code == 200
        self.__class__.doc_id = _doc_id(resp.json())

    def test_02_update_buffer(self):
        assert self.doc_id
        resp = self._sc.update_buffer(self.doc_id, b"modified content", "mod_test.txt")
        assert resp.status_code == 200

    def test_03_is_modified_true(self):
        """Test 9: After PUT /buffer, isModified should be true."""
        assert self.doc_id
        resp = self._sc.is_modified(self.doc_id)
        assert resp.status_code == 200, (
            f"isModified failed ({resp.status_code}): {resp.text}"
        )
        assert resp.json() is True, f"Expected isModified=true after buffer update, got: {resp.json()}"

    def test_04_cleanup(self):
        assert self.doc_id
        self._sc.delete_document(self.doc_id)

# ---------------------------------------------------------------------------
# Test 29 — not found
# ---------------------------------------------------------------------------

def test_is_modified_nonexistent_document(sc: StorageClient):
    """Test 29: GET /isModified for non-existent document → 404."""
    resp = sc.is_modified(NONEXISTENT_ID)
    assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"
