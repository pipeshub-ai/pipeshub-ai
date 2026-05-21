"""
Knowledge Base API – Move Record Response Validation Integration Tests
======================================================================

Tests PUT /api/v1/knowledgeBase/{kbId}/record/{recordId}/move (``moveRecord``)
against OpenAPI schemas.

**Request:** required ``newParentId`` (string folder ID or null for KB root);
kbId must be a valid UUID.

**Response:** ``{message}`` on 200; ``ErrorResponse`` on 400, 401, 403, 404.

Requires:
  - PIPESHUB_TEST_ENV=local → integration-tests/.env.local
  - PIPESHUB_BASE_URL, PIPESHUB_TEST_USER_EMAIL, PIPESHUB_TEST_USER_PASSWORD
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any, Callable

import pytest
import requests

_ROOT = Path(__file__).resolve().parents[2]
_RV_HELPER = _ROOT / "response-validation" / "helper"

for _p in (_ROOT, _RV_HELPER):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)

from openapi_schema_validator import (  # noqa: E402
    assert_request_body_matches_openapi_operation,
    assert_response_matches_openapi_operation,
    assert_response_matches_openapi_ref,
)
from helper.pipeshub_client import PipeshubClient  # noqa: E402

_AUTH_UTILS = _ROOT / "response-validation" / "auth" / "utils"
if str(_AUTH_UTILS) not in sys.path:
    sys.path.insert(0, str(_AUTH_UTILS))
from auth_helpers import login_with_user, require_test_user_credentials  # noqa: E402

def _retry_on_404(fn: Callable[[], requests.Response], max_wait: int = 15, interval: float = 1.5) -> requests.Response:
    """Call fn() repeatedly until it returns a non-404 or max_wait seconds elapse.

    Used to wait for the connector backend to finish registering a freshly uploaded
    record before performing operations that require it to exist there.
    """
    deadline = time.time() + max_wait
    resp = fn()
    while resp.status_code == 404 and time.time() < deadline:
        time.sleep(interval)
        resp = fn()
    return resp


_MOVE_OP = "moveRecord"
_ERROR_REF = "#/components/schemas/ErrorResponse"
_FAKE_UUID = "00000000-0000-0000-0000-000000000000"
_FAKE_RECORD_ID = "nonexistent-record-000"

# Valid bodies per OpenAPI + Zod (newParentId required, string or null)
VALID_BODIES: list[tuple[str, dict[str, Any]]] = [
    ("to_folder", {"newParentId": "some-folder-id"}),
    ("to_root", {"newParentId": None}),
]

# Invalid bodies — fail OpenAPI schema (newParentId missing or wrong type)
INVALID_BODIES: list[tuple[str, dict[str, Any] | None]] = [
    ("missing_newParentId", {}),
    ("number_newParentId", {"newParentId": 42}),
    ("array_newParentId", {"newParentId": ["folder-id"]}),
]


@pytest.mark.integration
class TestMoveRecordOpenApiRequestSchema:
    """Static alignment: request JSON ↔ OpenAPI moveRecord request."""

    @pytest.mark.parametrize("label,body", VALID_BODIES)
    def test_valid_bodies_match_openapi(self, label: str, body: dict[str, Any]) -> None:
        assert_request_body_matches_openapi_operation(body, _MOVE_OP)

    @pytest.mark.parametrize("label,body", INVALID_BODIES)
    def test_invalid_bodies_fail_openapi(self, label: str, body: dict[str, Any] | None) -> None:
        with pytest.raises(AssertionError):
            assert_request_body_matches_openapi_operation(body or {}, _MOVE_OP)


@pytest.mark.integration
class TestMoveRecord:
    """PUT /api/v1/knowledgeBase/{kbId}/record/{recordId}/move — moveRecord."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient, create_kb, delete_kb, upload_record, create_folder, move_record) -> None:
        self.base_url = pipeshub_client.base_url
        self.timeout = pipeshub_client.timeout_seconds
        email, password = require_test_user_credentials()
        self.access_token, _ = login_with_user(
            self.base_url, email, password, self.timeout,
        )
        self.headers = {"Authorization": f"Bearer {self.access_token}", "Content-Type": "application/json"}
        self.create_kb = create_kb
        self.delete_kb = delete_kb
        self.upload_record = upload_record
        self.create_folder = create_folder
        self.move_record = move_record

    def test_no_auth_returns_401(self) -> None:
        """No bearer token → 401 with ErrorResponse body."""
        resp = requests.put(
            f"{self.base_url}/api/v1/knowledgeBase/{_FAKE_UUID}/record/{_FAKE_RECORD_ID}/move",
            json={"newParentId": None},
            timeout=self.timeout,
        )
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"
        err_body = resp.json()
        assert_response_matches_openapi_ref(err_body, _ERROR_REF)
        assert_response_matches_openapi_operation(err_body, _MOVE_OP, status_code="401")

    def test_non_uuid_kbid_returns_400(self) -> None:
        """Non-UUID kbId fails Zod params validation → 400 VALIDATION_ERROR."""
        resp = requests.put(
            f"{self.base_url}/api/v1/knowledgeBase/not-a-uuid/record/{_FAKE_RECORD_ID}/move",
            headers=self.headers,
            json={"newParentId": None},
            timeout=self.timeout,
        )
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
        err_body = resp.json()
        assert err_body.get("error", {}).get("code") == "VALIDATION_ERROR", err_body
        assert_response_matches_openapi_ref(err_body, _ERROR_REF)
        assert_response_matches_openapi_operation(err_body, _MOVE_OP, status_code="400")

    def test_missing_newparentid_returns_400(self) -> None:
        """Missing required newParentId → 400 VALIDATION_ERROR."""
        resp = requests.put(
            f"{self.base_url}/api/v1/knowledgeBase/{_FAKE_UUID}/record/{_FAKE_RECORD_ID}/move",
            headers=self.headers,
            json={},
            timeout=self.timeout,
        )
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
        err_body = resp.json()
        assert err_body.get("error", {}).get("code") == "VALIDATION_ERROR", err_body
        assert_response_matches_openapi_ref(err_body, _ERROR_REF)
        assert_response_matches_openapi_operation(err_body, _MOVE_OP, status_code="400")

    def test_nonexistent_kb_returns_403(self) -> None:
        """Valid auth, non-existent UUID kbId → 403 (server does not distinguish not-found from no-access)."""
        resp = self.move_record(
            self.base_url, self.access_token, _FAKE_UUID, _FAKE_RECORD_ID, None, self.timeout,
        )
        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"
        err_body = resp.json()
        assert_response_matches_openapi_ref(err_body, _ERROR_REF)
        assert_response_matches_openapi_operation(err_body, _MOVE_OP, status_code="403")

    def test_nonexistent_record_in_existing_kb_returns_404(self) -> None:
        """Valid KB, non-existent recordId → 404 (connector distinguishes record-not-found from KB-not-found)."""
        create_resp = self.create_kb(self.base_url, self.access_token, "IT-MoveRecord-setup", self.timeout)
        assert create_resp.status_code == 200, f"Setup failed: {create_resp.text}"
        kb_id = create_resp.json()["id"]

        try:
            resp = self.move_record(
                self.base_url, self.access_token, kb_id, _FAKE_RECORD_ID, None, self.timeout,
            )
            assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"
            err_body = resp.json()
            assert_response_matches_openapi_ref(err_body, _ERROR_REF)
            assert_response_matches_openapi_operation(err_body, _MOVE_OP, status_code="404")
        finally:
            self.delete_kb(self.base_url, self.access_token, kb_id, self.timeout)

    def test_move_record_to_folder_returns_200(self) -> None:
        """Upload a file, create a folder, move the record into the folder → 200.

        Upload returns immediately with a placeholder; the connector backend registers the
        record asynchronously. _retry_on_404 polls until it is ready rather than sleeping.
        """
        create_resp = self.create_kb(self.base_url, self.access_token, "IT-MoveRecord-happy", self.timeout)
        assert create_resp.status_code == 200, f"KB setup failed: {create_resp.text}"
        kb_id = create_resp.json()["id"]

        try:
            upload_resp = self.upload_record(self.base_url, self.access_token, kb_id, self.timeout)
            assert upload_resp.status_code == 200, f"Upload failed: {upload_resp.text}"
            records = upload_resp.json().get("records", [])
            assert records, "No records returned from upload"
            record_id = records[0]["_key"]

            folder_resp = self.create_folder(self.base_url, self.access_token, kb_id, "IT-MoveRecord-dest", self.timeout)
            assert folder_resp.status_code == 200, f"Folder creation failed: {folder_resp.text}"
            folder_id = folder_resp.json()["id"]

            resp = _retry_on_404(
                lambda: self.move_record(self.base_url, self.access_token, kb_id, record_id, folder_id, self.timeout),
            )
            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
            assert_response_matches_openapi_operation(resp.json(), _MOVE_OP)
        finally:
            self.delete_kb(self.base_url, self.access_token, kb_id, self.timeout)

    def test_move_record_to_root_returns_200(self) -> None:
        """Upload a file, move it to root (newParentId=null) → 200.

        Upload returns immediately with a placeholder; the connector backend registers the
        record asynchronously. _retry_on_404 polls until it is ready rather than sleeping.
        """
        create_resp = self.create_kb(self.base_url, self.access_token, "IT-MoveRecord-toroot", self.timeout)
        assert create_resp.status_code == 200, f"KB setup failed: {create_resp.text}"
        kb_id = create_resp.json()["id"]

        try:
            upload_resp = self.upload_record(self.base_url, self.access_token, kb_id, self.timeout)
            assert upload_resp.status_code == 200, f"Upload failed: {upload_resp.text}"
            records = upload_resp.json().get("records", [])
            assert records, "No records returned from upload"
            record_id = records[0]["_key"]

            resp = _retry_on_404(
                lambda: self.move_record(self.base_url, self.access_token, kb_id, record_id, None, self.timeout),
            )
            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
            assert_response_matches_openapi_operation(resp.json(), _MOVE_OP)
        finally:
            self.delete_kb(self.base_url, self.access_token, kb_id, self.timeout)
