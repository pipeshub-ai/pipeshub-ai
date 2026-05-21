"""
Knowledge Base API – Upload Records Response Validation Integration Tests
=========================================================================

Tests POST /api/v1/knowledgeBase/{kbId}/upload (``uploadRecordsToKB``)
against OpenAPI schemas.

**Request:** multipart/form-data with required ``files`` field; kbId must be a valid UUID.

**Response:** ``UploadResult`` on 200; ``ErrorResponse`` on 400, 401, 403.

The endpoint returns immediately with ``status: "processing"`` — indexing is asynchronous.

Requires:
  - PIPESHUB_TEST_ENV=local → integration-tests/.env.local
  - PIPESHUB_BASE_URL, PIPESHUB_TEST_USER_EMAIL, PIPESHUB_TEST_USER_PASSWORD
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import requests

_ROOT = Path(__file__).resolve().parents[2]
_RV_HELPER = _ROOT / "response-validation" / "helper"

for _p in (_ROOT, _RV_HELPER):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)

from openapi_schema_validator import (  # noqa: E402
    assert_response_matches_openapi_operation,
    assert_response_matches_openapi_ref,
)
from helper.pipeshub_client import PipeshubClient  # noqa: E402

_AUTH_UTILS = _ROOT / "response-validation" / "auth" / "utils"
if str(_AUTH_UTILS) not in sys.path:
    sys.path.insert(0, str(_AUTH_UTILS))
from auth_helpers import login_with_user, require_test_user_credentials  # noqa: E402

_UNIQUE_MARKER = "IT-UploadRecord"
_UPLOAD_OP = "uploadRecordsToKB"
_ERROR_REF = "#/components/schemas/ErrorResponse"
_FAKE_UUID = "00000000-0000-0000-0000-000000000a00"


@pytest.mark.integration
class TestUploadRecord:
    """POST /api/v1/knowledgeBase/{kbId}/upload — uploadRecordsToKB."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient, create_kb, delete_kb, upload_record) -> None:
        self.base_url = pipeshub_client.base_url
        self.timeout = pipeshub_client.timeout_seconds
        email, password = require_test_user_credentials()
        self.access_token, _ = login_with_user(
            self.base_url, email, password, self.timeout,
        )
        self.headers = {"Authorization": f"Bearer {self.access_token}"}
        self.create_kb = create_kb
        self.delete_kb = delete_kb
        self.upload_record = upload_record

    def test_upload_response_matches_openapi(self) -> None:
        """Create KB, upload a file, validate 200 body matches OpenAPI UploadResult schema."""
        create_resp = self.create_kb(self.base_url, self.access_token, f"{_UNIQUE_MARKER}-kb", self.timeout)
        assert create_resp.status_code == 200, f"KB setup failed: {create_resp.text}"
        kb_id = create_resp.json()["id"]

        try:
            resp = self.upload_record(self.base_url, self.access_token, kb_id, self.timeout)
            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
            body = resp.json()
            assert_response_matches_openapi_operation(body, _UPLOAD_OP)
            assert body.get("status") == "processing"
            assert isinstance(body.get("records"), list)
            assert len(body["records"]) >= 1
            assert body["records"][0].get("_key"), "record _key must be present"
        finally:
            self.delete_kb(self.base_url, self.access_token, kb_id, self.timeout)

    def test_upload_multiple_files(self) -> None:
        """Upload two files in one request — both appear as records."""
        create_resp = self.create_kb(self.base_url, self.access_token, f"{_UNIQUE_MARKER}-multi-kb", self.timeout)
        assert create_resp.status_code == 200, f"KB setup failed: {create_resp.text}"
        kb_id = create_resp.json()["id"]

        try:
            resp = requests.post(
                f"{self.base_url}/api/v1/knowledgeBase/{kb_id}/upload",
                headers=self.headers,
                files=[
                    ("files", ("file1.txt", b"content one", "text/plain")),
                    ("files", ("file2.txt", b"content two", "text/plain")),
                ],
                timeout=self.timeout,
            )
            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
            body = resp.json()
            assert_response_matches_openapi_operation(body, _UPLOAD_OP)
            assert body.get("totalFiles") == 2
            assert len(body.get("records", [])) == 2
        finally:
            self.delete_kb(self.base_url, self.access_token, kb_id, self.timeout)

    def test_non_uuid_kbid_returns_400(self) -> None:
        """Non-UUID kbId fails Zod params validation → 400 VALIDATION_ERROR."""
        resp = requests.post(
            f"{self.base_url}/api/v1/knowledgeBase/not-a-uuid/upload",
            headers=self.headers,
            files={"files": ("test.txt", b"content", "text/plain")},
            timeout=self.timeout,
        )
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
        err_body = resp.json()
        assert err_body.get("error", {}).get("code") == "VALIDATION_ERROR", err_body
        assert_response_matches_openapi_ref(err_body, _ERROR_REF)
        assert_response_matches_openapi_operation(err_body, _UPLOAD_OP, status_code="400")

    def test_no_auth_returns_401(self) -> None:
        """No bearer token → 401 with ErrorResponse body."""
        resp = requests.post(
            f"{self.base_url}/api/v1/knowledgeBase/{_FAKE_UUID}/upload",
            files={"files": ("test.txt", b"content", "text/plain")},
            timeout=self.timeout,
        )
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"
        err_body = resp.json()
        assert_response_matches_openapi_ref(err_body, _ERROR_REF)
        assert_response_matches_openapi_operation(err_body, _UPLOAD_OP, status_code="401")

    def test_nonexistent_kb_returns_403(self) -> None:
        """Valid auth, non-existent UUID kbId → 403 (server does not distinguish not-found from no-access)."""
        resp = self.upload_record(self.base_url, self.access_token, _FAKE_UUID, self.timeout)
        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"
        err_body = resp.json()
        assert_response_matches_openapi_ref(err_body, _ERROR_REF)
        assert_response_matches_openapi_operation(err_body, _UPLOAD_OP, status_code="403")
