"""
Organization API – Response Validation Integration Tests
=========================================================

Tests every JSON-returning route under /api/v1/org against its documented
OpenAPI response schema in ``pipeshub-openapi.yaml``.  Each test validates:
  - HTTP status code
  - Required fields and types per the OpenAPI JSON Schema

Routes covered:
  GET    /api/v1/org/exists           — checkOrgExistence
  GET    /api/v1/org/health           — health check
  GET    /api/v1/org                  — getOrganizationById
  PUT    /api/v1/org                  — updateOrganizationDetails
  GET    /api/v1/org/onboarding-status — getOnboardingStatus
  PUT    /api/v1/org/onboarding-status — updateOnboardingStatus
  PUT    /api/v1/org/logo             — updateOrgLogo
  DELETE /api/v1/org/logo             — removeOrgLogo

Skipped (non-JSON responses):
  GET    /api/v1/org/logo             — returns raw image bytes / 204
  POST   /api/v1/org                  — creates org (destructive, one-shot)
  DELETE /api/v1/org                  — soft-deletes org (destructive)

Requires:
  - PIPESHUB_BASE_URL in .env / .env.local
  - Valid OAuth credentials (CLIENT_ID + CLIENT_SECRET) or test-user login
"""

from __future__ import annotations

import base64
import logging
import os
import sys
from pathlib import Path

import pytest
import requests

_ROOT = Path(__file__).resolve().parents[2]
_RV_HELPER = _ROOT / "response-validation" / "helper"
for _p in (_ROOT, _RV_HELPER):
    s = str(_p)
    if s not in sys.path:
        sys.path.insert(0, s)

from helper.pipeshub_client import PipeshubClient  # noqa: E402
from openapi_schema_validator import (  # noqa: E402
    assert_response_matches_openapi_operation,
)

logger = logging.getLogger("org-integration-test")

# Minimal valid 1×1 PNG for logo upload tests (strict decoders e.g. libspng reject
# hand-rolled chunk boundaries; this is a standard tiny PNG as base64).
_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


# ====================================================================
# GET /api/v1/org/exists
# ====================================================================
@pytest.mark.integration
class TestCheckOrgExistence:
    """GET /api/v1/org/exists — no auth required."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client
        self.url = f"{pipeshub_client.base_url}/api/v1/org/exists"

    def test_response_schema(self) -> None:
        """Response must match OrgCheckExistenceResponse schema."""
        resp = requests.get(self.url, timeout=self.client.timeout_seconds)
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "checkOrgExists")

    def test_unsupported_method_returns_4xx(self) -> None:
        """POST to /exists is not a registered method — must return 4xx."""
        resp = requests.post(self.url, timeout=self.client.timeout_seconds)
        assert resp.status_code >= 400, (
            f"Expected 4xx for unsupported POST method, got {resp.status_code}"
        )


# ====================================================================
# GET /api/v1/org/health
# ====================================================================
@pytest.mark.integration
class TestOrgHealth:
    """GET /api/v1/org/health — no auth required."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client
        self.url = f"{pipeshub_client.base_url}/api/v1/org/health"

    def test_response_schema(self) -> None:
        """Response must match OrgHealthResponse schema."""
        resp = requests.get(self.url, timeout=self.client.timeout_seconds)
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "getOrgHealth")

    def test_unsupported_method_returns_4xx(self) -> None:
        """POST to /health is not a registered method — must return 4xx."""
        resp = requests.post(self.url, timeout=self.client.timeout_seconds)
        assert resp.status_code >= 400, (
            f"Expected 4xx for unsupported POST method, got {resp.status_code}"
        )


# ====================================================================
# GET /api/v1/org
# ====================================================================
@pytest.mark.integration
class TestGetOrganizationById:
    """GET /api/v1/org — retrieve the authenticated user's organization."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client
        self.url = f"{pipeshub_client.base_url}/api/v1/org"

    def test_response_schema(self) -> None:
        """Response must conform to OrgDocumentResponse YAML schema."""
        resp = requests.get(
            self.url,
            headers=self.client._headers(),
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "getCurrentOrganization")

    def test_no_auth_returns_401(self) -> None:
        """GET without Authorization header must return 401."""
        resp = requests.get(self.url, timeout=self.client.timeout_seconds)
        assert resp.status_code == 401, (
            f"Expected 401, got {resp.status_code}: {resp.text}"
        )

    def test_invalid_token_returns_401(self) -> None:
        """GET with a bogus Bearer token must return 401."""
        resp = requests.get(
            self.url,
            headers={"Authorization": "Bearer this-is-not-a-valid-token"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, (
            f"Expected 401, got {resp.status_code}: {resp.text}"
        )


# ====================================================================
# PUT /api/v1/org
# ====================================================================
@pytest.mark.integration
class TestUpdateOrganizationDetails:
    """PUT /api/v1/org — update org details (admin only)."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client
        self.url = f"{pipeshub_client.base_url}/api/v1/org"

    def _get_current_org(self) -> dict[str, object]:
        resp = requests.get(
            self.url,
            headers=self.client._headers(),
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200
        return resp.json()

    def _put_org(self, body: dict[str, object]) -> requests.Response:
        return requests.put(
            self.url,
            headers=self.client._headers(),
            json=body,
            timeout=self.client.timeout_seconds,
        )

    def test_update_registered_name_response_schema(self) -> None:
        """Update only registeredName — response must match schema."""
        original = self._get_current_org()
        original_name = original.get("registeredName")

        resp = self._put_org({"registeredName": "Integration Test Org"})
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "updateOrganization")

        # Restore original name
        self._put_org({"registeredName": original_name})

    def test_update_short_name_response_schema(self) -> None:
        """Update only shortName — response must match schema."""
        original = self._get_current_org()
        original_short = original.get("shortName")

        resp = self._put_org({"shortName": "IT-ORG"})
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "updateOrganization")

        # Restore
        restore_body: dict[str, object] = {}
        if original_short is not None:
            restore_body["shortName"] = original_short
        else:
            restore_body["shortName"] = ""
        self._put_org(restore_body)

    def test_update_contact_email_response_schema(self) -> None:
        """Update only contactEmail — response must match schema."""
        original = self._get_current_org()
        original_email = original.get("contactEmail")

        resp = self._put_org({"contactEmail": str(original_email)})
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "updateOrganization")

    def test_update_permanent_address_response_schema(self) -> None:
        """Update permanentAddress — response must match schema."""
        original = self._get_current_org()
        original_addr = original.get("permanentAddress")

        resp = self._put_org({
            "permanentAddress": {
                "addressLine1": "123 Test St",
                "city": "Testville",
                "state": "TS",
                "country": "US",
                "postCode": "00000",
            }
        })
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "updateOrganization")

        # Restore
        if original_addr is not None:
            self._put_org({"permanentAddress": original_addr})

    def test_update_multiple_fields_response_schema(self) -> None:
        """Update multiple fields at once — response must match schema."""
        original = self._get_current_org()

        resp = self._put_org({
            "registeredName": "Multi-field Test Org",
            "shortName": "MFT",
        })
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "updateOrganization")

        # Verify the response data reflects the update
        body = resp.json()
        assert body["data"]["registeredName"] == "Multi-field Test Org"
        assert body["data"]["shortName"] == "MFT"

        # Restore
        self._put_org({
            "registeredName": original.get("registeredName"),
            "shortName": original.get("shortName", ""),
        })

    def test_update_empty_body_response_schema(self) -> None:
        """Empty body (no-op update) — response must still match schema."""
        resp = self._put_org({})
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "updateOrganization")

    def test_no_auth_returns_401(self) -> None:
        """PUT without Authorization header must return 401."""
        resp = requests.put(
            self.url,
            json={"registeredName": "Should Not Update"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, (
            f"Expected 401, got {resp.status_code}: {resp.text}"
        )

    def test_invalid_token_returns_401(self) -> None:
        """PUT with a bogus Bearer token must return 401."""
        resp = requests.put(
            self.url,
            headers={"Authorization": "Bearer not-a-real-token"},
            json={"registeredName": "Should Not Update"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, (
            f"Expected 401, got {resp.status_code}: {resp.text}"
        )


# ====================================================================
# GET /api/v1/org/onboarding-status
# ====================================================================
@pytest.mark.integration
class TestGetOnboardingStatus:
    """GET /api/v1/org/onboarding-status"""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client
        self.url = f"{pipeshub_client.base_url}/api/v1/org/onboarding-status"

    def test_response_schema(self) -> None:
        """Response must match OrgGetOnboardingStatusResponse schema."""
        resp = requests.get(
            self.url,
            headers=self.client._headers(),
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "getOnboardingStatus")

    def test_no_auth_returns_401(self) -> None:
        """GET without Authorization header must return 401."""
        resp = requests.get(self.url, timeout=self.client.timeout_seconds)
        assert resp.status_code == 401, (
            f"Expected 401, got {resp.status_code}: {resp.text}"
        )


# ====================================================================
# PUT /api/v1/org/onboarding-status
# ====================================================================
@pytest.mark.integration
class TestUpdateOnboardingStatus:
    """PUT /api/v1/org/onboarding-status"""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client
        self.url = f"{pipeshub_client.base_url}/api/v1/org/onboarding-status"

    def _get_current_status(self) -> str:
        resp = requests.get(
            self.url,
            headers=self.client._headers(),
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200
        return resp.json()["status"]

    def _put_status(self, status: str) -> requests.Response:
        return requests.put(
            self.url,
            headers=self.client._headers(),
            json={"status": status},
            timeout=self.client.timeout_seconds,
        )

    def test_update_to_configured_response_schema(self) -> None:
        """Set status to 'configured' — response must match schema."""
        original = self._get_current_status()

        resp = self._put_status("configured")
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "updateOnboardingStatus")
        assert body["status"] == "configured"

        # Restore
        self._put_status(original)

    def test_update_to_not_configured_response_schema(self) -> None:
        """Set status to 'notConfigured' — response must match schema."""
        original = self._get_current_status()

        resp = self._put_status("notConfigured")
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "updateOnboardingStatus")
        assert body["status"] == "notConfigured"

        # Restore
        self._put_status(original)

    def test_update_to_skipped_response_schema(self) -> None:
        """Set status to 'skipped' — response must match schema."""
        original = self._get_current_status()

        resp = self._put_status("skipped")
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "updateOnboardingStatus")
        assert body["status"] == "skipped"

        # Restore
        self._put_status(original)

    def test_invalid_status_returns_400(self) -> None:
        """An invalid status value should be rejected."""
        resp = self._put_status("invalidStatus")
        assert resp.status_code == 400

    def test_empty_body_returns_400(self) -> None:
        """Missing status field should be rejected."""
        resp = requests.put(
            self.url,
            headers=self.client._headers(),
            json={},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400

    def test_null_status_returns_400(self) -> None:
        """Sending status=null must be rejected by schema validation."""
        resp = requests.put(
            self.url,
            headers=self.client._headers(),
            json={"status": None},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, (
            f"Expected 400 for null status, got {resp.status_code}: {resp.text}"
        )

    def test_no_auth_returns_401(self) -> None:
        """PUT without Authorization header must return 401."""
        resp = requests.put(
            self.url,
            json={"status": "configured"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, (
            f"Expected 401, got {resp.status_code}: {resp.text}"
        )


# ====================================================================
# PUT /api/v1/org/logo
# ====================================================================
@pytest.mark.integration
class TestUpdateOrgLogo:
    """PUT /api/v1/org/logo — upload org logo (admin only)."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client
        self.url = f"{pipeshub_client.base_url}/api/v1/org/logo"

    def _upload_logo(
        self,
        file_bytes: bytes,
        filename: str,
        content_type: str,
    ) -> requests.Response:
        headers = self.client._headers()
        # Remove Content-Type — requests sets it with the multipart boundary
        headers.pop("Content-Type", None)
        return requests.put(
            self.url,
            headers=headers,
            files={"file": (filename, file_bytes, content_type)},
            timeout=self.client.timeout_seconds,
        )

    def test_upload_png_response_schema(self) -> None:
        """Upload a minimal PNG — response must match schema."""
        resp = self._upload_logo(_TINY_PNG, "logo.png", "image/png")
        assert resp.status_code == 201, (
            f"Expected 201, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(
            body, "uploadOrganizationLogo", status_code="201"
        )
        assert body["mimeType"] == "image/jpeg"  # PNG is converted to JPEG

    def test_upload_svg_response_schema(self) -> None:
        """Upload a minimal SVG — response must match schema."""
        svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"><rect width="1" height="1" fill="red"/></svg>'
        resp = self._upload_logo(svg, "logo.svg", "image/svg+xml")
        assert resp.status_code == 201, (
            f"Expected 201, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(
            body, "uploadOrganizationLogo", status_code="201"
        )
        assert body["mimeType"] == "image/svg+xml"

    def test_no_auth_returns_401(self) -> None:
        """PUT to /logo without Authorization header must return 401."""
        resp = requests.put(
            self.url,
            files={"file": ("logo.png", _TINY_PNG, "image/png")},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, (
            f"Expected 401, got {resp.status_code}: {resp.text}"
        )

    def test_no_file_returns_400(self) -> None:
        """PUT to /logo with auth but without a file part must return 400."""
        resp = requests.put(
            self.url,
            headers=self.client._headers(),
            json={},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, (
            f"Expected 400 when no file is uploaded, got {resp.status_code}: {resp.text}"
        )

    def test_unsupported_mime_type_returns_400(self) -> None:
        """Uploading a text/plain file must be rejected with 400."""
        resp = self._upload_logo(b"not an image", "file.txt", "text/plain")
        assert resp.status_code == 400, (
            f"Expected 400 for unsupported MIME type, got {resp.status_code}: {resp.text}"
        )

# ====================================================================
# DELETE /api/v1/org/logo
# ====================================================================
@pytest.mark.integration
class TestRemoveOrgLogo:
    """DELETE /api/v1/org/logo — remove org logo (admin only)."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client
        self.logo_url = f"{pipeshub_client.base_url}/api/v1/org/logo"

    def _ensure_logo_exists(self) -> None:
        """Upload a logo so that DELETE has something to remove."""
        headers = self.client._headers()
        headers.pop("Content-Type", None)
        resp = requests.put(
            self.logo_url,
            headers=headers,
            files={"file": ("logo.png", _TINY_PNG, "image/png")},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 201, (
            f"Logo setup failed: {resp.status_code}: {resp.text}"
        )

    def test_remove_logo_response_schema(self) -> None:
        """Upload then delete logo — DELETE response must match schema."""
        self._ensure_logo_exists()

        resp = requests.delete(
            self.logo_url,
            headers=self.client._headers(),
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "deleteOrganizationLogo")

    def test_no_auth_returns_401(self) -> None:
        """DELETE to /logo without Authorization header must return 401."""
        resp = requests.delete(
            self.logo_url,
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, (
            f"Expected 401, got {resp.status_code}: {resp.text}"
        )
