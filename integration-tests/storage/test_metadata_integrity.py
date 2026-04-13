"""
Metadata integrity tests — Groups A through F.

Group A: documentPath integrity across all operations
Group B: extension consistency across all operations
Group C: versionHistory file paths contain correct version markers
Group D: documentName consistency across all operations
Group E: orgId scoping — documentPath starts with orgId, orgId never changes
Group F: storageVendor consistency across all operations
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from storage_client import StorageClient

logger = logging.getLogger("storage-integration")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _doc_id(body: dict[str, Any]) -> str:
    return str(body.get("_id") or body.get("id") or body.get("documentId"))


def _version_count(body: dict[str, Any]) -> int:
    return len(body.get("versionHistory") or [])


def _version_path(entry: dict[str, Any], vendor: str) -> str:
    """Return the path that identifies the stored version file.

    The upload service (initial upload, local storage) writes a normalised
    HTTP download URL into ``url`` and the raw ``file://`` path into
    ``localPath``.  All other write paths — the upload service for S3/Azure,
    and all controller operations (uploadNextVersion, rollback) for every
    vendor — only set ``url`` (S3/Azure object URL or ``file://`` path).

    Prefer ``localPath`` when non-empty so that the initial-upload local-
    storage case returns the file path rather than the download URL.
    """
    data = entry.get(vendor) or {}
    return data.get("localPath") or data.get("url", "")


# ===========================================================================
# Group A — documentPath field integrity
# ===========================================================================

def test_a1_upload_without_document_path(sc: StorageClient):
    """A1: Upload without documentPath → documentPath ends with /PipesHub."""
    resp = sc.upload(b"a1 content", "a1.txt", "A1 Doc", is_versioned=False)
    assert resp.status_code == 200
    body = resp.json()
    assert body["documentPath"].endswith("/PipesHub"), (
        f"Expected /PipesHub suffix, got: {body['documentPath']}"
    )
    sc.delete_document(_doc_id(body))


def test_a2_upload_with_document_path(sc: StorageClient):
    """A2: Upload with documentPath='reports/q1' → documentPath ends with /PipesHub/reports/q1."""
    resp = sc.upload(
        b"a2 content", "a2.txt", "A2 Doc",
        is_versioned=False, document_path="reports/q1",
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["documentPath"].endswith("/PipesHub/reports/q1"), (
        f"Expected /PipesHub/reports/q1 suffix, got: {body['documentPath']}"
    )
    sc.delete_document(_doc_id(body))


class TestDocumentPathAfterVersionOps:
    """A3/A4: documentPath unchanged after uploadNextVersion and rollback."""

    doc_id: str = ""
    original_path: str = ""

    @pytest.fixture(autouse=True)
    def _bind(self, sc: StorageClient) -> None:
        self.__class__._sc = sc

    def test_01_upload(self):
        resp = self._sc.upload(b"v0", "a3.txt", "A3 Path Doc", is_versioned=True)
        assert resp.status_code == 200
        body = resp.json()
        self.__class__.doc_id = _doc_id(body)
        self.__class__.original_path = body["documentPath"]

    def test_02_upload_next_version_preserves_path(self):
        """A3: documentPath unchanged after uploadNextVersion."""
        assert self.doc_id
        resp = self._sc.upload_next_version(self.doc_id, b"v1", "a3.txt")
        assert resp.status_code == 200
        assert resp.json()["documentPath"] == self.original_path, (
            f"A3: documentPath changed after uploadNextVersion"
        )

    def test_03_rollback_preserves_path(self):
        """A4: documentPath unchanged after rollback."""
        assert self.doc_id
        resp = self._sc.rollback(self.doc_id, version=0, note="a4 rollback")
        assert resp.status_code == 200
        assert resp.json()["documentPath"] == self.original_path, (
            f"A4: documentPath changed after rollback"
        )

    def test_04_cleanup(self):
        assert self.doc_id
        self._sc.delete_document(self.doc_id)


def test_a5_document_path_unchanged_after_buffer_update(sc: StorageClient):
    """A5: documentPath unchanged after PUT /buffer."""
    resp = sc.upload(b"original", "a5.txt", "A5 Buffer Doc", is_versioned=False)
    assert resp.status_code == 200
    body = resp.json()
    doc_id = _doc_id(body)
    original_path = body["documentPath"]

    update_resp = sc.update_buffer(doc_id, b"updated content", "a5.txt")
    assert update_resp.status_code == 200

    get_resp = sc.get_document(doc_id)
    assert get_resp.status_code == 200
    assert get_resp.json()["documentPath"] == original_path, (
        f"A5: documentPath changed after PUT /buffer"
    )
    sc.delete_document(doc_id)


def test_a6_placeholder_without_document_path(sc: StorageClient):
    """A6: create_placeholder with empty documentPath → documentPath ends with /PipesHub."""
    # CreateDocumentSchema requires documentPath: z.string(), so we pass "" which is
    # falsy in JS; getFullDocumentPath(orgId, "") returns `${orgId}/PipesHub`.
    resp = sc.create_placeholder("A6 Placeholder", "txt", "")
    assert resp.status_code == 200
    body = resp.json()
    assert body["documentPath"].endswith("/PipesHub"), (
        f"A6: Expected /PipesHub suffix for empty path, got: {body['documentPath']}"
    )
    sc.delete_document(_doc_id(body))


def test_a7_placeholder_with_document_path(sc: StorageClient):
    """A7: create_placeholder with documentPath → documentPath ends with /PipesHub/<path>."""
    resp = sc.create_placeholder("A7 Placeholder", "txt", "finance/2024")
    assert resp.status_code == 200
    body = resp.json()
    assert body["documentPath"].endswith("/PipesHub/finance/2024"), (
        f"A7: Expected /PipesHub/finance/2024 suffix, got: {body['documentPath']}"
    )
    sc.delete_document(_doc_id(body))


# ===========================================================================
# Group B — extension consistency
# ===========================================================================

def test_b1_extension_txt(sc: StorageClient):
    """B1: Upload .txt file → extension is .txt."""
    resp = sc.upload(b"text content", "b1.txt", "B1 Txt Doc", is_versioned=False)
    assert resp.status_code == 200
    assert resp.json()["extension"] == ".txt", f"B1: expected .txt, got: {resp.json()['extension']}"
    sc.delete_document(_doc_id(resp.json()))


def test_b2_extension_pdf(sc: StorageClient):
    """B2: Upload .pdf file → extension is .pdf."""
    resp = sc.upload(b"%PDF-1.4 content", "b2.pdf", "B2 Pdf Doc", is_versioned=False)
    assert resp.status_code == 200
    assert resp.json()["extension"] == ".pdf", f"B2: expected .pdf, got: {resp.json()['extension']}"
    sc.delete_document(_doc_id(resp.json()))


class TestExtensionConsistencyAcrossOps:
    """B3/B4/B5/B6: extension unchanged across uploadNextVersion, rollback, PUT /buffer."""

    doc_id: str = ""
    buf_doc_id: str = ""
    original_ext: str = ""

    @pytest.fixture(autouse=True)
    def _bind(self, sc: StorageClient) -> None:
        self.__class__._sc = sc

    def test_01_upload(self):
        resp = self._sc.upload(b"v0 content", "b3.txt", "B3 Ext Doc", is_versioned=True)
        assert resp.status_code == 200
        body = resp.json()
        self.__class__.doc_id = _doc_id(body)
        self.__class__.original_ext = body["extension"]
        assert self.original_ext == ".txt"

    def test_02_upload_next_version_preserves_extension(self):
        """B3: extension unchanged after uploadNextVersion."""
        assert self.doc_id
        resp = self._sc.upload_next_version(self.doc_id, b"v1 content", "b3.txt")
        assert resp.status_code == 200
        assert resp.json()["extension"] == self.original_ext, (
            f"B3: extension changed after uploadNextVersion"
        )

    def test_03_rollback_preserves_extension(self):
        """B4: extension unchanged after rollback."""
        assert self.doc_id
        resp = self._sc.rollback(self.doc_id, version=0, note="b4 rollback")
        assert resp.status_code == 200
        assert resp.json()["extension"] == self.original_ext, (
            f"B4: extension changed after rollback"
        )

    def test_04_buffer_update_preserves_extension(self):
        """B5: extension unchanged after PUT /buffer (non-versioned doc)."""
        resp = self._sc.upload(b"buf orig", "b5.txt", "B5 Buffer Ext", is_versioned=False)
        assert resp.status_code == 200
        self.__class__.buf_doc_id = _doc_id(resp.json())
        buf_ext = resp.json()["extension"]

        self._sc.update_buffer(self.buf_doc_id, b"updated content", "b5.txt")
        get_resp = self._sc.get_document(self.buf_doc_id)
        assert get_resp.status_code == 200
        assert get_resp.json()["extension"] == buf_ext, (
            f"B5: extension changed after PUT /buffer"
        )

    def test_05_version_history_extensions_consistent(self):
        """B6: all versionHistory entries carry the same extension."""
        assert self.doc_id
        resp = self._sc.get_document(self.doc_id)
        assert resp.status_code == 200
        history = resp.json().get("versionHistory") or []
        assert len(history) > 0, "B6: no versionHistory entries to check"
        for i, entry in enumerate(history):
            assert entry.get("extension") == self.original_ext, (
                f"B6: versionHistory[{i}] extension {entry.get('extension')!r} != {self.original_ext!r}"
            )

    def test_06_cleanup(self):
        if self.doc_id:
            self._sc.delete_document(self.doc_id)
        if self.buf_doc_id:
            self._sc.delete_document(self.buf_doc_id)


# ===========================================================================
# Group C — versionHistory file paths
# ===========================================================================

class TestVersionHistoryPaths:
    """C1-C5: versionHistory entries have paths containing /versions/v{N}.

    Timeline built up step by step:
      upload          → versionHistory = [v0]
      uploadNextVersion → [v0, v1]
      uploadNextVersion → [v0, v1, v2]
      rollback to v0  → [v0, v1, v2, v3]  (v3 is the rollback clone)
    """

    doc_id: str = ""
    vendor: str = ""

    @pytest.fixture(autouse=True)
    def _bind(self, sc: StorageClient) -> None:
        self.__class__._sc = sc

    def test_01_upload(self):
        resp = self._sc.upload(b"v0 content", "c1.txt", "C1 Version Paths", is_versioned=True)
        assert resp.status_code == 200
        body = resp.json()
        self.__class__.doc_id = _doc_id(body)
        self.__class__.vendor = body.get("storageVendor", "")
        # Presigned direct upload: v0 is materialised lazily on the first
        # uploadNextVersion call, so versionHistory is empty here.
        assert _version_count(body) == 0

    def test_02_first_upload_next_version(self):
        """C1: versionHistory[0] path contains versions/v0."""
        assert self.doc_id
        resp = self._sc.upload_next_version(self.doc_id, b"v1 content", "c1.txt")
        assert resp.status_code == 200
        history = resp.json().get("versionHistory") or []
        assert len(history) == 2

        v0_path = _version_path(history[0], self.vendor)
        assert "versions/v0" in v0_path, (
            f"C1: versionHistory[0] path should contain 'versions/v0', got: {v0_path!r}"
        )

    def test_03_second_upload_next_version(self):
        """C2: versionHistory[1] path contains versions/v1."""
        assert self.doc_id
        resp = self._sc.upload_next_version(self.doc_id, b"v2 content", "c1.txt")
        assert resp.status_code == 200
        history = resp.json().get("versionHistory") or []
        assert len(history) == 3

        v1_path = _version_path(history[1], self.vendor)
        assert "versions/v1" in v1_path, (
            f"C2: versionHistory[1] path should contain 'versions/v1', got: {v1_path!r}"
        )

    def test_04_rollback_to_v0(self):
        """C3: Rollback entry path contains /versions/v (stored as a proper version file)."""
        assert self.doc_id
        resp = self._sc.rollback(self.doc_id, version=0, note="c3 rollback")
        assert resp.status_code == 200
        history = resp.json().get("versionHistory") or []
        assert len(history) == 4

        rollback_path = _version_path(history[3], self.vendor)
        assert "versions/v" in rollback_path, (
            f"C3: rollback entry path should contain 'versions/v', got: {rollback_path!r}"
        )

    def test_05_all_paths_share_document_root(self):
        """C5: All versionHistory paths contain the documentId (same root folder)."""
        assert self.doc_id
        resp = self._sc.get_document(self.doc_id)
        assert resp.status_code == 200
        history = resp.json().get("versionHistory") or []

        for i, entry in enumerate(history):
            path = _version_path(entry, self.vendor)
            if not path:
                continue
            assert self.doc_id in path, (
                f"C5: versionHistory[{i}] path {path!r} does not contain documentId {self.doc_id!r}"
            )

    def test_06_cleanup(self):
        assert self.doc_id
        self._sc.delete_document(self.doc_id)


class TestRollbackToV1Path:
    """C4: After rollback to v1, the appended entry path contains /versions/v."""

    doc_id: str = ""
    vendor: str = ""

    @pytest.fixture(autouse=True)
    def _bind(self, sc: StorageClient) -> None:
        self.__class__._sc = sc

    def test_01_upload(self):
        resp = self._sc.upload(b"v0", "c4.txt", "C4 Rollback V1 Path", is_versioned=True)
        assert resp.status_code == 200
        body = resp.json()
        self.__class__.doc_id = _doc_id(body)
        self.__class__.vendor = body.get("storageVendor", "")

    def test_02_upload_v1(self):
        assert self.doc_id
        resp = self._sc.upload_next_version(self.doc_id, b"v1", "c4.txt")
        assert resp.status_code == 200

    def test_03_upload_v2(self):
        assert self.doc_id
        resp = self._sc.upload_next_version(self.doc_id, b"v2", "c4.txt")
        assert resp.status_code == 200

    def test_04_rollback_to_v1(self):
        """C4: Rollback to v1 — appended entry path contains /versions/v."""
        assert self.doc_id
        resp = self._sc.rollback(self.doc_id, version=1, note="c4 rollback to v1")
        assert resp.status_code == 200
        history = resp.json().get("versionHistory") or []
        rollback_path = _version_path(history[-1], self.vendor)
        assert "versions/v" in rollback_path, (
            f"C4: rollback-to-v1 entry path should contain 'versions/v', got: {rollback_path!r}"
        )

    def test_05_cleanup(self):
        assert self.doc_id
        self._sc.delete_document(self.doc_id)


# ===========================================================================
# Group D — documentName consistency
# ===========================================================================

class TestDocumentNameConsistency:
    """D1/D2/D3: documentName unchanged after uploadNextVersion, rollback, PUT /buffer."""

    doc_id: str = ""
    buf_doc_id: str = ""
    original_name: str = "D Consistency Doc"

    @pytest.fixture(autouse=True)
    def _bind(self, sc: StorageClient) -> None:
        self.__class__._sc = sc

    def test_01_upload(self):
        resp = self._sc.upload(b"v0", "d1.txt", self.original_name, is_versioned=True)
        assert resp.status_code == 200
        self.__class__.doc_id = _doc_id(resp.json())
        assert resp.json()["documentName"] == self.original_name

    def test_02_upload_next_version_preserves_name(self):
        """D1: documentName unchanged after uploadNextVersion."""
        assert self.doc_id
        resp = self._sc.upload_next_version(self.doc_id, b"v1", "d1.txt")
        assert resp.status_code == 200
        assert resp.json()["documentName"] == self.original_name, (
            f"D1: documentName changed after uploadNextVersion"
        )

    def test_03_rollback_preserves_name(self):
        """D2: documentName unchanged after rollback."""
        assert self.doc_id
        resp = self._sc.rollback(self.doc_id, version=0, note="d2 rollback")
        assert resp.status_code == 200
        assert resp.json()["documentName"] == self.original_name, (
            f"D2: documentName changed after rollback"
        )

    def test_04_buffer_update_preserves_name(self):
        """D3: documentName unchanged after PUT /buffer."""
        resp = self._sc.upload(b"buf orig", "d3.txt", "D3 Buffer Name", is_versioned=False)
        assert resp.status_code == 200
        self.__class__.buf_doc_id = _doc_id(resp.json())
        buf_name = resp.json()["documentName"]

        self._sc.update_buffer(self.buf_doc_id, b"updated content", "d3.txt")
        get_resp = self._sc.get_document(self.buf_doc_id)
        assert get_resp.status_code == 200
        assert get_resp.json()["documentName"] == buf_name, (
            f"D3: documentName changed after PUT /buffer"
        )

    def test_05_cleanup(self):
        if self.doc_id:
            self._sc.delete_document(self.doc_id)
        if self.buf_doc_id:
            self._sc.delete_document(self.buf_doc_id)


# ===========================================================================
# Group E — orgId scoping
# ===========================================================================
#
# The Mongoose Document schema does not declare ``orgId``, so the field is
# not serialised onto API responses — but the authenticated client *does*
# know its own orgId (it's a claim on the access token, exactly what the
# backend's ``extractOrgId`` helper reads off every request).  We use that
# as the source of truth here, mirroring the backend.


def test_e1_document_path_starts_with_org_id(sc: StorageClient):
    """E1: documentPath starts with the authenticated client's orgId."""
    resp = sc.upload(b"e1 content", "e1.txt", "E1 OrgId Doc", is_versioned=False)
    assert resp.status_code == 200
    body = resp.json()
    doc_id = _doc_id(body)
    doc_path = body.get("documentPath", "")

    org_id = sc.org_id
    assert doc_path.startswith(f"{org_id}/"), (
        f"E1: documentPath {doc_path!r} does not start with orgId {org_id!r}"
    )
    sc.delete_document(doc_id)


class TestOrgIdNeverChanges:
    """E2: documentPath remains scoped to the same orgId across
    uploadNextVersion, rollback, and PUT /buffer."""

    doc_id: str = ""

    @pytest.fixture(autouse=True)
    def _bind(self, sc: StorageClient) -> None:
        self.__class__._sc = sc

    def _assert_path_in_org(self, doc: dict[str, Any], label: str) -> None:
        doc_path = doc.get("documentPath", "") or ""
        org_id = self._sc.org_id
        assert doc_path.startswith(f"{org_id}/"), (
            f"E2 ({label}): documentPath {doc_path!r} no longer starts with "
            f"orgId {org_id!r}"
        )

    def test_01_upload(self):
        resp = self._sc.upload(b"v0", "e2.txt", "E2 OrgId Stable", is_versioned=True)
        assert resp.status_code == 200
        self.__class__.doc_id = _doc_id(resp.json())
        self._assert_path_in_org(resp.json(), "upload")

    def test_02_upload_next_version_orgid_stable(self):
        assert self.doc_id
        resp = self._sc.upload_next_version(self.doc_id, b"v1", "e2.txt")
        assert resp.status_code == 200
        self._assert_path_in_org(resp.json(), "uploadNextVersion")

    def test_03_rollback_orgid_stable(self):
        assert self.doc_id
        resp = self._sc.rollback(self.doc_id, version=0, note="e2 rollback")
        assert resp.status_code == 200
        self._assert_path_in_org(resp.json(), "rollback")

    def test_04_buffer_update_orgid_stable(self):
        # Use a separate non-versioned doc to also cover buffer op
        resp = self._sc.upload(b"buf", "e2b.txt", "E2 Buffer OrgId", is_versioned=False)
        assert resp.status_code == 200
        buf_doc_id = _doc_id(resp.json())
        self._assert_path_in_org(resp.json(), "buffer upload")

        self._sc.update_buffer(buf_doc_id, b"updated", "e2b.txt")
        get_resp = self._sc.get_document(buf_doc_id)
        assert get_resp.status_code == 200
        self._assert_path_in_org(get_resp.json(), "PUT /buffer")
        self._sc.delete_document(buf_doc_id)

    def test_05_cleanup(self):
        assert self.doc_id
        self._sc.delete_document(self.doc_id)


# ===========================================================================
# Group F — storageVendor consistency
# ===========================================================================

class TestStorageVendorConsistency:
    """F1/F2/F3: storageVendor is valid after upload and unchanged across operations."""

    doc_id: str = ""
    original_vendor: str = ""

    @pytest.fixture(autouse=True)
    def _bind(self, sc: StorageClient) -> None:
        self.__class__._sc = sc

    def test_01_upload_sets_valid_vendor(self):
        """F1: storageVendor is set to a recognised value after upload."""
        resp = self._sc.upload(b"v0", "f1.txt", "F1 Vendor Doc", is_versioned=True)
        assert resp.status_code == 200
        body = resp.json()
        self.__class__.doc_id = _doc_id(body)
        self.__class__.original_vendor = body.get("storageVendor", "")
        assert self.original_vendor in ("local", "s3", "azureBlob"), (
            f"F1: unexpected storageVendor value: {self.original_vendor!r}"
        )

    def test_02_upload_next_version_vendor_stable(self):
        """F2: storageVendor unchanged after uploadNextVersion."""
        assert self.doc_id
        resp = self._sc.upload_next_version(self.doc_id, b"v1", "f1.txt")
        assert resp.status_code == 200
        assert resp.json().get("storageVendor") == self.original_vendor, (
            f"F2: storageVendor changed after uploadNextVersion"
        )

    def test_03_rollback_vendor_stable(self):
        """F3: storageVendor unchanged after rollback."""
        assert self.doc_id
        resp = self._sc.rollback(self.doc_id, version=0, note="f3 rollback")
        assert resp.status_code == 200
        assert resp.json().get("storageVendor") == self.original_vendor, (
            f"F3: storageVendor changed after rollback"
        )

    def test_04_cleanup(self):
        assert self.doc_id
        self._sc.delete_document(self.doc_id)
