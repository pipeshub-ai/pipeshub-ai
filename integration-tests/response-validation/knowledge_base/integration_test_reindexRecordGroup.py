"""
Knowledge Base API – Reindex Record Group Response Validation Integration Tests
===============================================================================

Tests POST /api/v1/knowledgeBase/reindex/record-group/{recordGroupId}
(``reindexRecordGroup``) against OpenAPI schemas.

**Request:** optional body with ``depth`` (int, -1 to 100) and ``force`` (bool).
``recordGroupId`` must be a KB ID — folder IDs are not recognised as record groups.

**Response:** ``{message}`` on 200; ``ErrorResponse`` on 400, 401, 404.

KBs are registered synchronously in the connector, so no polling is needed.

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


_UNIQUE_MARKER = "IT-ReindexGroup"
_REINDEX_GROUP_OP = "reindexRecordGroup"
_ERROR_REF = "#/components/schemas/ErrorResponse"
_FAKE_GROUP_ID = "nonexistent-group-000"

# Valid request bodies (body entirely optional; depth -1..100, force bool)
VALID_BODIES: list[tuple[str, dict[str, Any]]] = [
    ("empty_body", {}),
    ("depth_neg1", {"depth": -1}),
    ("depth_zero", {"depth": 0}),
    ("depth_max", {"depth": 100}),
    ("force_true", {"force": True}),
    ("depth_and_force", {"depth": 10, "force": True}),
]

# Invalid request bodies — fail OpenAPI schema (depth out of range or wrong type)
INVALID_BODIES: list[tuple[str, dict[str, Any]]] = [
    ("depth_above_max", {"depth": 101}),
    ("depth_below_min", {"depth": -2}),
    ("depth_string", {"depth": "full"}),
]


@pytest.mark.integration
class TestReindexRecordGroupOpenApiRequestSchema:
    """Static alignment: request JSON ↔ OpenAPI reindexRecordGroup request."""

    @pytest.mark.parametrize("label,body", VALID_BODIES)
    def test_valid_bodies_match_openapi(self, label: str, body: dict[str, Any]) -> None:
        assert_request_body_matches_openapi_operation(body, _REINDEX_GROUP_OP)

    @pytest.mark.parametrize("label,body", INVALID_BODIES)
    def test_invalid_bodies_fail_openapi(self, label: str, body: dict[str, Any]) -> None:
        with pytest.raises(AssertionError):
            assert_request_body_matches_openapi_operation(body, _REINDEX_GROUP_OP)


@pytest.mark.integration
class TestReindexRecordGroup:
    """POST /api/v1/knowledgeBase/reindex/record-group/{recordGroupId} — reindexRecordGroup."""

    @pytest.fixture(autouse=True)
    def _setup(
        self,
        pipeshub_client: PipeshubClient,
        create_kb,
        delete_kb,
        reindex_record_group,
    ) -> None:
        self.base_url = pipeshub_client.base_url
        self.timeout = pipeshub_client.timeout_seconds
        email, password = require_test_user_credentials()
        self.access_token, _ = login_with_user(
            self.base_url, email, password, self.timeout,
        )
        self.headers = {"Authorization": f"Bearer {self.access_token}", "Content-Type": "application/json"}
        self.create_kb = create_kb
        self.delete_kb = delete_kb
        self.reindex_record_group = reindex_record_group

    def test_reindex_kb_as_group_returns_200(self) -> None:
        """Use a KB ID as recordGroupId → 200. KBs are created synchronously so no polling needed."""
        create_resp = self.create_kb(self.base_url, self.access_token, f"{_UNIQUE_MARKER}-kb", self.timeout)
        assert create_resp.status_code == 200, f"KB setup failed: {create_resp.text}"
        kb_id = create_resp.json()["id"]

        try:
            resp = self.reindex_record_group(self.base_url, self.access_token, kb_id, timeout=self.timeout)
            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
            assert_response_matches_openapi_operation(resp.json(), _REINDEX_GROUP_OP)
        finally:
            self.delete_kb(self.base_url, self.access_token, kb_id, self.timeout)

    def test_reindex_with_depth_and_force(self) -> None:
        """Reindex KB with depth=0 and force=True → 200."""
        create_resp = self.create_kb(self.base_url, self.access_token, f"{_UNIQUE_MARKER}-force-kb", self.timeout)
        assert create_resp.status_code == 200, f"KB setup failed: {create_resp.text}"
        kb_id = create_resp.json()["id"]

        try:
            resp = self.reindex_record_group(
                self.base_url, self.access_token, kb_id, depth=0, force=True, timeout=self.timeout,
            )
            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
            assert_response_matches_openapi_operation(resp.json(), _REINDEX_GROUP_OP)
        finally:
            self.delete_kb(self.base_url, self.access_token, kb_id, self.timeout)

    def test_invalid_depth_returns_400(self) -> None:
        """depth > 100 fails Zod max validation → 400 VALIDATION_ERROR."""
        resp = requests.post(
            f"{self.base_url}/api/v1/knowledgeBase/reindex/record-group/{_FAKE_GROUP_ID}",
            headers=self.headers,
            json={"depth": 101},
            timeout=self.timeout,
        )
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
        err_body = resp.json()
        assert err_body.get("error", {}).get("code") == "VALIDATION_ERROR", err_body
        assert_response_matches_openapi_ref(err_body, _ERROR_REF)
        assert_response_matches_openapi_operation(err_body, _REINDEX_GROUP_OP, status_code="400")

    def test_no_auth_returns_401(self) -> None:
        """No bearer token → 401 with ErrorResponse body."""
        resp = requests.post(
            f"{self.base_url}/api/v1/knowledgeBase/reindex/record-group/{_FAKE_GROUP_ID}",
            json={"depth": -1},
            timeout=self.timeout,
        )
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"
        err_body = resp.json()
        assert_response_matches_openapi_ref(err_body, _ERROR_REF)
        assert_response_matches_openapi_operation(err_body, _REINDEX_GROUP_OP, status_code="401")

    def test_nonexistent_group_returns_404(self) -> None:
        """Non-existent recordGroupId → 404 from the connector backend."""
        resp = self.reindex_record_group(
            self.base_url, self.access_token, _FAKE_GROUP_ID, timeout=self.timeout,
        )
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"
        err_body = resp.json()
        assert_response_matches_openapi_ref(err_body, _ERROR_REF)
        assert_response_matches_openapi_operation(err_body, _REINDEX_GROUP_OP, status_code="404")
