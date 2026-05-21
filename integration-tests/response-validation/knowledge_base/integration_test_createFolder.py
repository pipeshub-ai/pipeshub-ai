"""
Knowledge Base API – Create Folder Response Validation Integration Tests
========================================================================

Tests POST /api/v1/knowledgeBase/{kbId}/folder (``createRootFolder``)
against OpenAPI schemas.

**Request:** required ``folderName`` (1–255 chars); kbId must be a valid UUID.

**Response:** ``Folder`` on 200; ``ErrorResponse`` on 400, 401, 403.

Requires:
  - PIPESHUB_TEST_ENV=local → integration-tests/.env.local
  - PIPESHUB_BASE_URL, PIPESHUB_TEST_USER_EMAIL, PIPESHUB_TEST_USER_PASSWORD
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

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

_UNIQUE_MARKER = "IT-CreateFolder"
_CREATE_FOLDER_OP = "createRootFolder"
_ERROR_REF = "#/components/schemas/ErrorResponse"
_FAKE_UUID = "00000000-0000-0000-0000-000000000000"

# Valid request bodies per OpenAPI (folderName required, 1–255 chars)
VALID_BODIES: list[tuple[str, dict[str, Any]]] = [
    ("simple_name", {"folderName": "My Folder"}),
    ("with_spaces", {"folderName": "Project Documents 2024"}),
    ("max_length", {"folderName": "a" * 255}),
    ("single_char", {"folderName": "x"}),
]

# Invalid request bodies — fail OpenAPI schema
INVALID_BODIES: list[tuple[str, dict[str, Any]]] = [
    ("missing_folderName", {}),
    ("empty_string", {"folderName": ""}),
    ("too_long", {"folderName": "a" * 256}),
    ("number_type", {"folderName": 42}),
]

# Bodies that pass Zod but fail controller guards → 400 HTTP_BAD_REQUEST
CONTROLLER_GUARD_BODIES: list[tuple[str, dict[str, Any]]] = [
    ("xss_script_tag", {"folderName": "<script>alert(1)</script>"}),
    ("format_specifier", {"folderName": "My %s Folder"}),
    ("javascript_protocol", {"folderName": "javascript:alert(1)"}),
]


@pytest.mark.integration
class TestCreateFolderOpenApiRequestSchema:
    """Static alignment: request JSON ↔ OpenAPI createRootFolder request."""

    @pytest.mark.parametrize("label,body", VALID_BODIES)
    def test_valid_bodies_match_openapi(self, label: str, body: dict[str, Any]) -> None:
        assert_request_body_matches_openapi_operation(body, _CREATE_FOLDER_OP)

    @pytest.mark.parametrize("label,body", INVALID_BODIES)
    def test_invalid_bodies_fail_openapi(self, label: str, body: dict[str, Any]) -> None:
        with pytest.raises(AssertionError):
            assert_request_body_matches_openapi_operation(body, _CREATE_FOLDER_OP)


@pytest.mark.integration
class TestCreateFolder:
    """POST /api/v1/knowledgeBase/{kbId}/folder — createRootFolder."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient, create_kb, delete_kb, create_folder) -> None:
        self.base_url = pipeshub_client.base_url
        self.timeout = pipeshub_client.timeout_seconds
        email, password = require_test_user_credentials()
        self.access_token, _ = login_with_user(
            self.base_url, email, password, self.timeout,
        )
        self.headers = {"Authorization": f"Bearer {self.access_token}", "Content-Type": "application/json"}
        self.create_kb = create_kb
        self.delete_kb = delete_kb
        self.create_folder = create_folder

    def test_create_folder_response_matches_openapi(self) -> None:
        """Create KB, create folder, validate 200 body matches OpenAPI Folder schema."""
        create_resp = self.create_kb(self.base_url, self.access_token, f"{_UNIQUE_MARKER}-kb", self.timeout)
        assert create_resp.status_code == 200, f"KB setup failed: {create_resp.text}"
        kb_id = create_resp.json()["id"]

        try:
            resp = self.create_folder(self.base_url, self.access_token, kb_id, f"{_UNIQUE_MARKER}-folder", self.timeout)
            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
            body = resp.json()
            assert_response_matches_openapi_operation(body, _CREATE_FOLDER_OP)
            assert body.get("name") == f"{_UNIQUE_MARKER}-folder"
            assert isinstance(body.get("id"), str)
        finally:
            self.delete_kb(self.base_url, self.access_token, kb_id, self.timeout)

    def test_create_folder_name_boundary(self) -> None:
        """folderName = 255 chars (max boundary) → 200 success."""
        create_resp = self.create_kb(self.base_url, self.access_token, f"{_UNIQUE_MARKER}-boundary-kb", self.timeout)
        assert create_resp.status_code == 200, create_resp.text
        kb_id = create_resp.json()["id"]

        try:
            prefix = f"{_UNIQUE_MARKER}-"
            folder_name = prefix + "a" * (255 - len(prefix))
            assert len(folder_name) == 255
            resp = self.create_folder(self.base_url, self.access_token, kb_id, folder_name, self.timeout)
            assert resp.status_code == 200, f"Expected 200 (255-char name), got {resp.status_code}: {resp.text}"
            assert_response_matches_openapi_operation(resp.json(), _CREATE_FOLDER_OP)
        finally:
            self.delete_kb(self.base_url, self.access_token, kb_id, self.timeout)

    @pytest.mark.parametrize("label,body", CONTROLLER_GUARD_BODIES)
    def test_controller_guards_reject_dangerous_foldername(self, label: str, body: dict[str, Any]) -> None:
        create_resp = self.create_kb(self.base_url, self.access_token, f"{_UNIQUE_MARKER}-guard-{label}", self.timeout)
        assert create_resp.status_code == 200, create_resp.text
        kb_id = create_resp.json()["id"]

        try:
            resp = requests.post(
                f"{self.base_url}/api/v1/knowledgeBase/{kb_id}/folder",
                headers=self.headers,
                json=body,
                timeout=self.timeout,
            )
            assert resp.status_code == 400, f"[{label}] Expected 400, got {resp.status_code}: {resp.text}"
            err_body = resp.json()
            assert err_body.get("error", {}).get("code") == "HTTP_BAD_REQUEST", err_body
            assert_response_matches_openapi_ref(err_body, _ERROR_REF)
            assert_response_matches_openapi_operation(err_body, _CREATE_FOLDER_OP, status_code="400")
        finally:
            self.delete_kb(self.base_url, self.access_token, kb_id, self.timeout)

    def test_empty_foldername_returns_400(self) -> None:
        """Empty folderName → 400 VALIDATION_ERROR (Zod min(1))."""
        create_resp = self.create_kb(self.base_url, self.access_token, f"{_UNIQUE_MARKER}-empty-kb", self.timeout)
        assert create_resp.status_code == 200, create_resp.text
        kb_id = create_resp.json()["id"]

        try:
            resp = requests.post(
                f"{self.base_url}/api/v1/knowledgeBase/{kb_id}/folder",
                headers=self.headers,
                json={"folderName": ""},
                timeout=self.timeout,
            )
            assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
            err_body = resp.json()
            assert err_body.get("error", {}).get("code") == "VALIDATION_ERROR", err_body
            assert_response_matches_openapi_ref(err_body, _ERROR_REF)
            assert_response_matches_openapi_operation(err_body, _CREATE_FOLDER_OP, status_code="400")
        finally:
            self.delete_kb(self.base_url, self.access_token, kb_id, self.timeout)

    def test_too_long_foldername_returns_400(self) -> None:
        """folderName > 255 chars → 400 VALIDATION_ERROR (Zod max(255))."""
        create_resp = self.create_kb(self.base_url, self.access_token, f"{_UNIQUE_MARKER}-long-kb", self.timeout)
        assert create_resp.status_code == 200, create_resp.text
        kb_id = create_resp.json()["id"]

        try:
            resp = requests.post(
                f"{self.base_url}/api/v1/knowledgeBase/{kb_id}/folder",
                headers=self.headers,
                json={"folderName": "a" * 256},
                timeout=self.timeout,
            )
            assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
            err_body = resp.json()
            assert err_body.get("error", {}).get("code") == "VALIDATION_ERROR", err_body
            assert_response_matches_openapi_ref(err_body, _ERROR_REF)
            assert_response_matches_openapi_operation(err_body, _CREATE_FOLDER_OP, status_code="400")
        finally:
            self.delete_kb(self.base_url, self.access_token, kb_id, self.timeout)

    def test_no_auth_returns_401(self) -> None:
        """No bearer token → 401 with ErrorResponse body."""
        resp = requests.post(
            f"{self.base_url}/api/v1/knowledgeBase/{_FAKE_UUID}/folder",
            json={"folderName": "Test"},
            timeout=self.timeout,
        )
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"
        err_body = resp.json()
        assert_response_matches_openapi_ref(err_body, _ERROR_REF)
        assert_response_matches_openapi_operation(err_body, _CREATE_FOLDER_OP, status_code="401")

    def test_nonexistent_kb_returns_403(self) -> None:
        """Valid auth, non-existent UUID kbId → 403 (server does not distinguish not-found from no-access)."""
        resp = self.create_folder(self.base_url, self.access_token, _FAKE_UUID, "Test Folder", self.timeout)
        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"
        err_body = resp.json()
        assert_response_matches_openapi_ref(err_body, _ERROR_REF)
        assert_response_matches_openapi_operation(err_body, _CREATE_FOLDER_OP, status_code="403")
