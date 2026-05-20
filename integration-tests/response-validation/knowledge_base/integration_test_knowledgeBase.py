"""
Knowledge Base API – Response Validation Integration Tests
==========================================================

Tests POST /api/v1/knowledgeBase (``createKnowledgeBase``) against OpenAPI schemas
and live Zod/controller validation.

**Request (OpenAPI + Zod):** ``CreateKnowledgeBaseRequest`` / ``createKBSchema``
(trimmed ``kbName``, min 1, max 255).

**Response:** ``KnowledgeBase`` on 200; ``ErrorResponse`` on 400/401.

**Controller guards (after Zod):** XSS/HTML, format specifiers.

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

_UNIQUE_MARKER = "IT-CreateKB"
_CREATE_OP = "createKnowledgeBase"
_ERROR_REF = "#/components/schemas/ErrorResponse"

# Zod createKBSchema → 400 VALIDATION_ERROR
ZOD_INVALID_BODIES: list[tuple[str, dict[str, Any] | None]] = [
    ("missing_kbName", {}),
    ("empty_string", {"kbName": ""}),
    ("whitespace_only", {"kbName": "   "}),
    ("tab_only", {"kbName": "\t"}),
    ("too_long", {"kbName": "a" * 256}),
    ("null_kbName", {"kbName": None}),
    ("number_kbName", {"kbName": 42}),
    ("boolean_kbName", {"kbName": True}),
    ("array_kbName", {"kbName": ["x"]}),
    ("object_kbName", {"kbName": {"name": "x"}}),
]

# Controller guards → 400 HTTP_BAD_REQUEST
CONTROLLER_GUARD_BODIES: list[tuple[str, dict[str, Any]]] = [
    ("xss_script_tag", {"kbName": "<script>alert(1)</script>"}),
    ("format_specifier", {"kbName": "My %s KB"}),
    ("javascript_protocol", {"kbName": "javascript:alert(1)"}),
]

# Valid request bodies (OpenAPI + live API)
VALID_BODIES: list[tuple[str, dict[str, Any]]] = [
    ("minimal", {"kbName": "Valid KB"}),
    ("trimmed_edges", {"kbName": "  Trimmed Name  "}),
]


@pytest.mark.integration
class TestCreateKbOpenApiRequestSchema:
    """Static alignment: request JSON ↔ OpenAPI CreateKnowledgeBaseRequest."""

    @pytest.mark.parametrize("label,body", VALID_BODIES + [("max_length", {"kbName": "x" * 255})])
    def test_valid_bodies_match_openapi(self, label: str, body: dict[str, Any]) -> None:
        assert_request_body_matches_openapi_operation(body, _CREATE_OP)

    @pytest.mark.parametrize("label,body", ZOD_INVALID_BODIES)
    def test_invalid_bodies_fail_openapi(
        self, label: str, body: dict[str, Any] | None
    ) -> None:
        payload = body if body is not None else {}
        with pytest.raises(AssertionError):
            assert_request_body_matches_openapi_operation(payload, _CREATE_OP)


@pytest.mark.integration
class TestCreateKnowledgeBase:
    """POST /api/v1/knowledgeBase — createKnowledgeBase."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient, create_kb, delete_kb) -> None:
        self.base_url = pipeshub_client.base_url
        self.timeout = pipeshub_client.timeout_seconds
        email, password = require_test_user_credentials()
        self.access_token, _ = login_with_user(
            self.base_url, email, password, self.timeout,
        )
        self.create_url = f"{self.base_url}/api/v1/knowledgeBase"
        self.headers = {"Authorization": f"Bearer {self.access_token}", "Content-Type": "application/json"}
        self.create_kb = create_kb
        self.delete_kb = delete_kb

    def test_create_kb_response_schema(self) -> None:
        """Valid kbName → 200 body matches OpenAPI KnowledgeBase schema."""
        kb_name = f"{_UNIQUE_MARKER}-schema-test"
        resp = self.create_kb(self.base_url, self.access_token, kb_name, self.timeout)
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, _CREATE_OP)
        assert body.get("id"), f"Missing id: {list(body.keys())}"
        assert body.get("name") == kb_name
        assert isinstance(body.get("createdAtTimestamp"), int)
        assert isinstance(body.get("updatedAtTimestamp"), int)
        assert body.get("userRole")
        self.delete_kb(self.base_url, self.access_token, body["id"], self.timeout)

    def test_success_body_does_not_match_list_schema(self) -> None:
        """Single KnowledgeBase must not satisfy paginated listKnowledgeBases shape."""
        kb_name = f"{_UNIQUE_MARKER}-cross-schema"
        resp = self.create_kb(self.base_url, self.access_token, kb_name, self.timeout)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert_response_matches_openapi_operation(body, _CREATE_OP)
        with pytest.raises(AssertionError):
            assert_response_matches_openapi_operation(body, "listKnowledgeBases")
        self.delete_kb(self.base_url, self.access_token, body["id"], self.timeout)

    def test_create_kb_name_at_boundary(self) -> None:
        """kbName = 255 chars (max boundary) → 200 with valid KnowledgeBase response."""
        prefix = f"{_UNIQUE_MARKER}-boundary-"
        kb_name = prefix + "a" * (255 - len(prefix))
        assert len(kb_name) == 255
        assert_request_body_matches_openapi_operation({"kbName": kb_name}, _CREATE_OP)

        resp = self.create_kb(self.base_url, self.access_token, kb_name, self.timeout)
        assert resp.status_code == 200, (
            f"Expected 200 (255-char kbName), got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, _CREATE_OP)
        self.delete_kb(self.base_url, self.access_token, body["id"], self.timeout)

    @pytest.mark.parametrize("label,body", ZOD_INVALID_BODIES)
    def test_zod_rejects_invalid_body(
        self, label: str, body: dict[str, Any] | None
    ) -> None:
        payload = body if body is not None else {}
        resp = requests.post(
            self.create_url,
            headers=self.headers,
            json=payload,
            timeout=self.timeout,
        )
        assert resp.status_code == 400, (
            f"[{label}] Expected 400, got {resp.status_code}: {resp.text}"
        )
        err_body = resp.json()
        assert err_body.get("error", {}).get("code") == "VALIDATION_ERROR", err_body
        assert err_body["error"]["message"] == "Validation failed"
        assert_response_matches_openapi_ref(err_body, _ERROR_REF)
        assert_response_matches_openapi_operation(
            err_body, _CREATE_OP, status_code="400"
        )
        with pytest.raises(AssertionError):
            assert_response_matches_openapi_operation(err_body, _CREATE_OP)

    @pytest.mark.parametrize("label,body", CONTROLLER_GUARD_BODIES)
    def test_controller_guards_reject_dangerous_kb_name(
        self, label: str, body: dict[str, Any]
    ) -> None:
        resp = requests.post(
            self.create_url,
            headers=self.headers,
            json=body,
            timeout=self.timeout,
        )
        assert resp.status_code == 400, (
            f"[{label}] Expected 400, got {resp.status_code}: {resp.text}"
        )
        err_body = resp.json()
        assert err_body.get("error", {}).get("code") == "HTTP_BAD_REQUEST", err_body
        assert_response_matches_openapi_ref(err_body, _ERROR_REF)
        assert_response_matches_openapi_operation(
            err_body, _CREATE_OP, status_code="400"
        )

    @pytest.mark.parametrize("label,body", VALID_BODIES)
    def test_valid_body_creates_kb_and_matches_openapi(
        self, label: str, body: dict[str, Any]
    ) -> None:
        assert_request_body_matches_openapi_operation(body, _CREATE_OP)

        raw_name = str(body["kbName"])
        post_json = (
            body
            if label == "trimmed_edges"
            else {"kbName": f"{_UNIQUE_MARKER}-{label}-{raw_name.strip()}"[:255]}
        )
        expected_name = "Trimmed Name" if label == "trimmed_edges" else post_json["kbName"].strip()

        resp = requests.post(
            self.create_url,
            headers=self.headers,
            json=post_json,
            timeout=self.timeout,
        )
        assert resp.status_code == 200, (
            f"[{label}] Expected 200, got {resp.status_code}: {resp.text}"
        )
        success = resp.json()
        assert_response_matches_openapi_operation(success, _CREATE_OP)
        assert success.get("name") == expected_name
        assert success.get("id")
        self.delete_kb(self.base_url, self.access_token, success["id"], self.timeout)

    def test_no_auth_returns_401_error_response(self) -> None:
        resp = requests.post(
            self.create_url,
            json={"kbName": "Test KB"},
            timeout=self.timeout,
        )
        assert resp.status_code == 401, (
            f"Expected 401, got {resp.status_code}: {resp.text}"
        )
        err_body = resp.json()
        assert_response_matches_openapi_ref(err_body, _ERROR_REF)
        assert_response_matches_openapi_operation(
            err_body, _CREATE_OP, status_code="401"
        )
        with pytest.raises(AssertionError):
            assert_response_matches_openapi_operation(err_body, _CREATE_OP)
