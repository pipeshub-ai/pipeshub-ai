"""
Knowledge Base API – Update Knowledge Base Response Validation Integration Tests
================================================================================

Tests PUT /api/v1/knowledgeBase/{kbId} (``updateKnowledgeBase``) against OpenAPI schemas.

**Request:** optional kbName (1–255 chars); kbId must be a valid UUID.
**Response:** ``KnowledgeBase`` on 200; ``ErrorResponse`` on 400, 401, 403.

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

_UNIQUE_MARKER = "IT-UpdateKB"
_UPDATE_OP = "updateKnowledgeBase"
_GET_OP = "getKnowledgeBase"
_ERROR_REF = "#/components/schemas/ErrorResponse"
_FAKE_UUID = "00000000-0000-0000-0000-000000000000"

# kbName provided but invalid → 400 VALIDATION_ERROR (Zod min/max; no trim in updateKBSchema)
ZOD_INVALID_BODIES: list[tuple[str, dict[str, Any]]] = [
    ("empty_string", {"kbName": ""}),
    ("too_long", {"kbName": "a" * 256}),
]

# Controller guards → 400 HTTP_BAD_REQUEST
CONTROLLER_GUARD_BODIES: list[tuple[str, dict[str, Any]]] = [
    ("xss_script_tag", {"kbName": "<script>alert(1)</script>"}),
    ("format_specifier", {"kbName": "My %s KB"}),
    ("javascript_protocol", {"kbName": "javascript:alert(1)"}),
]

# Valid bodies (OpenAPI + live API)
VALID_BODIES: list[tuple[str, dict[str, Any]]] = [
    ("with_kbName", {"kbName": "Updated KB Name"}),
    ("empty_body", {}),
    ("max_length", {"kbName": "a" * 255}),
]


@pytest.mark.integration
class TestUpdateKbOpenApiRequestSchema:
    """Static alignment: request JSON ↔ OpenAPI UpdateKnowledgeBase request."""

    @pytest.mark.parametrize("label,body", VALID_BODIES)
    def test_valid_bodies_match_openapi(self, label: str, body: dict[str, Any]) -> None:
        assert_request_body_matches_openapi_operation(body, _UPDATE_OP)

    @pytest.mark.parametrize("label,body", ZOD_INVALID_BODIES)
    def test_invalid_bodies_fail_openapi(self, label: str, body: dict[str, Any]) -> None:
        with pytest.raises(AssertionError):
            assert_request_body_matches_openapi_operation(body, _UPDATE_OP)


@pytest.mark.integration
class TestUpdateKnowledgeBase:
    """PUT /api/v1/knowledgeBase/{kbId} — updateKnowledgeBase."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient, create_kb, delete_kb, get_kb, update_kb) -> None:
        self.base_url = pipeshub_client.base_url
        self.timeout = pipeshub_client.timeout_seconds
        email, password = require_test_user_credentials()
        self.access_token, _ = login_with_user(
            self.base_url, email, password, self.timeout,
        )
        self.headers = {"Authorization": f"Bearer {self.access_token}", "Content-Type": "application/json"}
        self.create_kb = create_kb
        self.delete_kb = delete_kb
        self.get_kb = get_kb
        self.update_kb = update_kb

    def test_update_kb_response_matches_openapi(self) -> None:
        """Create KB, rename it, validate 200 body matches OpenAPI success schema."""
        create_resp = self.create_kb(self.base_url, self.access_token, f"{_UNIQUE_MARKER}-original", self.timeout)
        assert create_resp.status_code == 200, f"Setup failed: {create_resp.text}"
        kb_id = create_resp.json()["id"]

        try:
            resp = self.update_kb(self.base_url, self.access_token, kb_id, f"{_UNIQUE_MARKER}-renamed", self.timeout)
            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
            body = resp.json()
            assert_response_matches_openapi_operation(body, _UPDATE_OP)
            assert body.get("success") is True
            assert isinstance(body.get("message"), str)
        finally:
            self.delete_kb(self.base_url, self.access_token, kb_id, self.timeout)

    def test_update_name_is_reflected_in_get(self) -> None:
        """Rename is persisted: GET after PUT returns the new name."""
        create_resp = self.create_kb(self.base_url, self.access_token, f"{_UNIQUE_MARKER}-before", self.timeout)
        assert create_resp.status_code == 200, create_resp.text
        kb_id = create_resp.json()["id"]

        try:
            new_name = f"{_UNIQUE_MARKER}-after"
            update_resp = self.update_kb(self.base_url, self.access_token, kb_id, new_name, self.timeout)
            assert update_resp.status_code == 200, update_resp.text

            get_resp = self.get_kb(self.base_url, self.access_token, kb_id, self.timeout)
            assert get_resp.status_code == 200, get_resp.text
            body = get_resp.json()
            assert_response_matches_openapi_operation(body, _GET_OP)
            assert body.get("name") == new_name
        finally:
            self.delete_kb(self.base_url, self.access_token, kb_id, self.timeout)

    def test_update_name_at_boundary(self) -> None:
        """kbName = 255 chars (max boundary) → 200 success response."""
        create_resp = self.create_kb(self.base_url, self.access_token, f"{_UNIQUE_MARKER}-boundary", self.timeout)
        assert create_resp.status_code == 200, create_resp.text
        kb_id = create_resp.json()["id"]

        try:
            prefix = f"{_UNIQUE_MARKER}-boundary-"
            new_name = prefix + "a" * (255 - len(prefix))
            assert len(new_name) == 255
            resp = self.update_kb(self.base_url, self.access_token, kb_id, new_name, self.timeout)
            assert resp.status_code == 200, f"Expected 200 (255-char name), got {resp.status_code}: {resp.text}"
            assert_response_matches_openapi_operation(resp.json(), _UPDATE_OP)
        finally:
            self.delete_kb(self.base_url, self.access_token, kb_id, self.timeout)

    @pytest.mark.parametrize("label,body", ZOD_INVALID_BODIES)
    def test_zod_rejects_invalid_kbname(self, label: str, body: dict[str, Any]) -> None:
        create_resp = self.create_kb(self.base_url, self.access_token, f"{_UNIQUE_MARKER}-zod-{label}", self.timeout)
        assert create_resp.status_code == 200, create_resp.text
        kb_id = create_resp.json()["id"]

        try:
            resp = requests.put(
                f"{self.base_url}/api/v1/knowledgeBase/{kb_id}",
                headers=self.headers,
                json=body,
                timeout=self.timeout,
            )
            assert resp.status_code == 400, f"[{label}] Expected 400, got {resp.status_code}: {resp.text}"
            err_body = resp.json()
            assert err_body.get("error", {}).get("code") == "VALIDATION_ERROR", err_body
            assert_response_matches_openapi_ref(err_body, _ERROR_REF)
            assert_response_matches_openapi_operation(err_body, _UPDATE_OP, status_code="400")
        finally:
            self.delete_kb(self.base_url, self.access_token, kb_id, self.timeout)

    @pytest.mark.parametrize("label,body", CONTROLLER_GUARD_BODIES)
    def test_controller_guards_reject_dangerous_kbname(self, label: str, body: dict[str, Any]) -> None:
        create_resp = self.create_kb(self.base_url, self.access_token, f"{_UNIQUE_MARKER}-guard-{label}", self.timeout)
        assert create_resp.status_code == 200, create_resp.text
        kb_id = create_resp.json()["id"]

        try:
            resp = requests.put(
                f"{self.base_url}/api/v1/knowledgeBase/{kb_id}",
                headers=self.headers,
                json=body,
                timeout=self.timeout,
            )
            assert resp.status_code == 400, f"[{label}] Expected 400, got {resp.status_code}: {resp.text}"
            err_body = resp.json()
            assert err_body.get("error", {}).get("code") == "HTTP_BAD_REQUEST", err_body
            assert_response_matches_openapi_ref(err_body, _ERROR_REF)
            assert_response_matches_openapi_operation(err_body, _UPDATE_OP, status_code="400")
        finally:
            self.delete_kb(self.base_url, self.access_token, kb_id, self.timeout)

    def test_non_uuid_kbid_returns_400(self) -> None:
        """Non-UUID kbId fails Zod params validation → 400 VALIDATION_ERROR."""
        resp = requests.put(
            f"{self.base_url}/api/v1/knowledgeBase/not-a-valid-uuid",
            headers=self.headers,
            json={"kbName": "Test"},
            timeout=self.timeout,
        )
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
        err_body = resp.json()
        assert err_body.get("error", {}).get("code") == "VALIDATION_ERROR", err_body
        assert_response_matches_openapi_ref(err_body, _ERROR_REF)
        assert_response_matches_openapi_operation(err_body, _UPDATE_OP, status_code="400")

    def test_no_auth_returns_401(self) -> None:
        """No bearer token → 401 with ErrorResponse body."""
        resp = requests.put(
            f"{self.base_url}/api/v1/knowledgeBase/{_FAKE_UUID}",
            json={"kbName": "Test"},
            timeout=self.timeout,
        )
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"
        err_body = resp.json()
        assert_response_matches_openapi_ref(err_body, _ERROR_REF)
        assert_response_matches_openapi_operation(err_body, _UPDATE_OP, status_code="401")

    def test_nonexistent_kb_returns_403(self) -> None:
        """Valid auth, non-existent UUID kbId → 403 (server does not distinguish not-found from no-access)."""
        resp = self.update_kb(self.base_url, self.access_token, _FAKE_UUID, "New Name", self.timeout)
        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"
        err_body = resp.json()
        assert_response_matches_openapi_ref(err_body, _ERROR_REF)
        assert_response_matches_openapi_operation(err_body, _UPDATE_OP, status_code="403")
