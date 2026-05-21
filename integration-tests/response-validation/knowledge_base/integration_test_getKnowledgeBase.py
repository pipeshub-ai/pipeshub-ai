"""
Knowledge Base API – Get Knowledge Base Response Validation Integration Tests
=============================================================================

Tests GET /api/v1/knowledgeBase/{kbId} (``getKnowledgeBase``) against OpenAPI schemas.

**Response:** ``KnowledgeBase`` on 200; ``ErrorResponse`` on 401, 403, 404.

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

_UNIQUE_MARKER = "IT-GetKB"
_GET_OP = "getKnowledgeBase"
_CREATE_OP = "createKnowledgeBase"
_ERROR_REF = "#/components/schemas/ErrorResponse"


@pytest.mark.integration
class TestGetKnowledgeBase:
    """GET /api/v1/knowledgeBase/{kbId} — getKnowledgeBase."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient, create_kb, delete_kb, get_kb) -> None:
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

    def test_get_kb_response_matches_openapi(self) -> None:
        """Create KB, GET it, validate 200 body matches OpenAPI KnowledgeBase schema."""
        kb_name = f"{_UNIQUE_MARKER}-schema-test"
        create_resp = self.create_kb(self.base_url, self.access_token, kb_name, self.timeout)
        assert create_resp.status_code == 200, f"Setup failed: {create_resp.text}"
        kb_id = create_resp.json()["id"]

        try:
            resp = self.get_kb(self.base_url, self.access_token, kb_id, self.timeout)
            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
            body = resp.json()
            assert_response_matches_openapi_operation(body, _GET_OP)
            assert body.get("id") == kb_id
            assert body.get("name") == kb_name
            assert isinstance(body.get("createdAtTimestamp"), int)
            assert isinstance(body.get("updatedAtTimestamp"), int)
            assert body.get("userRole")
        finally:
            self.delete_kb(self.base_url, self.access_token, kb_id, self.timeout)

    def test_get_and_create_return_same_shape(self) -> None:
        """GET and POST /knowledgeBase must return the same KnowledgeBase shape."""
        kb_name = f"{_UNIQUE_MARKER}-cross-op"
        create_resp = self.create_kb(self.base_url, self.access_token, kb_name, self.timeout)
        assert create_resp.status_code == 200, create_resp.text
        kb_id = create_resp.json()["id"]

        try:
            resp = self.get_kb(self.base_url, self.access_token, kb_id, self.timeout)
            assert resp.status_code == 200
            body = resp.json()
            assert_response_matches_openapi_operation(body, _GET_OP)
            assert_response_matches_openapi_operation(body, _CREATE_OP)
        finally:
            self.delete_kb(self.base_url, self.access_token, kb_id, self.timeout)

    def test_get_kb_does_not_match_list_schema(self) -> None:
        """Single KnowledgeBase must not satisfy paginated listKnowledgeBases shape."""
        kb_name = f"{_UNIQUE_MARKER}-list-cross"
        create_resp = self.create_kb(self.base_url, self.access_token, kb_name, self.timeout)
        assert create_resp.status_code == 200, create_resp.text
        kb_id = create_resp.json()["id"]

        try:
            resp = self.get_kb(self.base_url, self.access_token, kb_id, self.timeout)
            assert resp.status_code == 200
            body = resp.json()
            with pytest.raises(AssertionError):
                assert_response_matches_openapi_operation(body, "listKnowledgeBases")
        finally:
            self.delete_kb(self.base_url, self.access_token, kb_id, self.timeout)

    def test_no_auth_returns_401(self) -> None:
        """No bearer token → 401 with ErrorResponse body."""
        resp = requests.get(
            f"{self.base_url}/api/v1/knowledgeBase/any-id",
            timeout=self.timeout,
        )
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"
        err_body = resp.json()
        assert_response_matches_openapi_ref(err_body, _ERROR_REF)
        assert_response_matches_openapi_operation(err_body, _GET_OP, status_code="401")

    def test_nonexistent_kb_returns_403(self) -> None:
        """Valid auth, non-existent kbId → 403 (server does not distinguish not-found
        from no-access to avoid leaking resource existence)."""
        resp = self.get_kb(
            self.base_url, self.access_token, "nonexistent-kb-id-000000", self.timeout,
        )
        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"
        err_body = resp.json()
        assert_response_matches_openapi_ref(err_body, _ERROR_REF)
        assert_response_matches_openapi_operation(err_body, _GET_OP, status_code="403")
