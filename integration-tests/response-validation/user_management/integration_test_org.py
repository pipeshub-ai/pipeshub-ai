"""
Organization API – Response Validation Integration Tests
=========================================================

Tests every JSON-returning route under /api/v1/org against its documented
OpenAPI response schema in ``pipeshub-openapi.yaml``.  Each test validates:
  - HTTP status code
  - Required fields and types per the OpenAPI JSON Schema

Routes covered:
  GET    /api/v1/org/exists            — checkOrgExistence
  GET    /api/v1/org/health            — health check
  GET    /api/v1/org                   — getOrganizationById
  PUT    /api/v1/org                   — updateOrganizationDetails (body validation)
  GET    /api/v1/org/onboarding-status — getOnboardingStatus
  PUT    /api/v1/org/onboarding-status — updateOnboardingStatus
  PUT    /api/v1/org/logo              — updateOrgLogo
  DELETE /api/v1/org/logo              — removeOrgLogo
  GET    /api/v1/org/logo              — getOrgLogo (binary response + 204/401)
  POST   /api/v1/org                   — createOrganization (validation + already-exists)

Skipped:
  DELETE /api/v1/org                   — soft-deletes org (destructive, irreversible)

Requires:
  - PIPESHUB_BASE_URL in .env / .env.local
  - Valid OAuth credentials (CLIENT_ID + CLIENT_SECRET) or test-user login
"""

from __future__ import annotations

import base64
import logging
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

from helper.clients.org_client import OrgClient  # noqa: E402
from openapi_schema_validator import (  # noqa: E402
    assert_response_matches_openapi_operation,
)

logger = logging.getLogger("org-integration-test")

# Minimal valid 1×1 PNG for logo upload tests (strict decoders e.g. libspng reject
# hand-rolled chunk boundaries; this is a standard tiny PNG as base64).
_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


# ------------------------------------------------------------------ #
# Base test class
# ------------------------------------------------------------------ #
class OrgTestBase:
    """Base class with shared org_client fixture for org integration tests."""

    @pytest.fixture(autouse=True)
    def _setup(self, org_client: OrgClient) -> None:
        self.org = org_client


def _upload_org_logo(
    org: OrgClient,
    file_bytes: bytes,
    filename: str,
    content_type: str,
) -> requests.Response:
    """Upload org logo via multipart (shared by logo test classes)."""
    return org.upload_logo(file_bytes, filename=filename, content_type=content_type)


# ====================================================================
# GET /api/v1/org/exists
# ====================================================================
@pytest.mark.integration
class TestCheckOrgExistence(OrgTestBase):
    """GET /api/v1/org/exists — no auth required."""

    def test_check_org_existence_response_schema(self) -> None:
        """GET /api/v1/org/exists — response must match OrgCheckExistenceResponse schema."""
        resp = self.org.check_exists()
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "checkOrgExists")

    def test_check_org_existence_negative_tests(self) -> None:
        """Unsupported methods and other negative paths for GET /api/v1/org/exists."""
        resp = self.org.post("/exists", auth=False)
        assert resp.status_code >= 400, (
            f"[unsupported POST] Expected 4xx, got {resp.status_code}"
        )


# ====================================================================
# GET /api/v1/org/health
# ====================================================================
@pytest.mark.integration
class TestOrgHealth(OrgTestBase):
    """GET /api/v1/org/health — no auth required."""

    def test_org_health_response_schema(self) -> None:
        """GET /api/v1/org/health — response must match OrgHealthResponse schema."""
        resp = self.org.health()
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "getOrgHealth")

    def test_org_health_negative_tests(self) -> None:
        """Unsupported methods and other negative paths for GET /api/v1/org/health."""
        resp = self.org.post("/health", auth=False)
        assert resp.status_code >= 400, (
            f"[unsupported POST] Expected 4xx, got {resp.status_code}"
        )


# ====================================================================
# GET /api/v1/org
# ====================================================================
@pytest.mark.integration
class TestGetOrganizationById(OrgTestBase):
    """GET /api/v1/org — retrieve the authenticated user's organization."""

    def test_get_current_organization_response_schema(self) -> None:
        """GET /api/v1/org — response must conform to OrgDocumentResponse (getCurrentOrganization)."""
        resp = self.org.get_organization()
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "getCurrentOrganization")

    def test_get_current_organization_negative_tests(self) -> None:
        """Unauthenticated and invalid-token paths for GET /api/v1/org."""
        resp = self.org.get("/", auth=False)
        assert resp.status_code == 401, (
            f"[no auth] Expected 401, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(
            body, "getCurrentOrganization", status_code="401"
        )
        assert body["error"]["code"] == "HTTP_UNAUTHORIZED", (
            f"[no auth] Expected error code 'HTTP_UNAUTHORIZED', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "No token provided", (
            f"[no auth] Expected message 'No token provided', got {body['error']['message']!r}"
        )

        resp = self.org.get(
            "/",
            auth=False,
            headers={"Authorization": "Bearer this-is-not-a-valid-token"},
        )
        assert resp.status_code == 401, (
            f"[invalid token] Expected 401, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(
            body, "getCurrentOrganization", status_code="401"
        )
        assert body["error"]["code"] == "HTTP_UNAUTHORIZED", (
            f"[invalid token] Expected error code 'HTTP_UNAUTHORIZED', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "Invalid token", (
            f"[invalid token] Expected message 'Invalid token', got {body['error']['message']!r}"
        )


# ====================================================================
# PUT /api/v1/org
# ====================================================================
@pytest.mark.integration
class TestUpdateOrganizationDetails(OrgTestBase):
    """PUT /api/v1/org — update org details (admin only)."""

    def _get_current_org(self) -> dict[str, object]:
        resp = self.org.get_organization()
        assert resp.status_code == 200
        return resp.json()

    def _put_org(self, body: dict[str, object]) -> requests.Response:
        return self.org.put("/", json=body)

    def test_update_org_single_field_response_schemas(self) -> None:
        """Partial PUTs — one logical field per step; response must match updateOrganization."""
        baseline = self._get_current_org()

        original_name = baseline.get("registeredName")
        resp = self._put_org({"registeredName": "Integration Test Org"})
        assert resp.status_code == 200, (
            f"[registeredName] Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "updateOrganization")
        self._put_org({"registeredName": original_name})

        current = self._get_current_org()
        original_short = current.get("shortName")
        resp = self._put_org({"shortName": "IT-ORG"})
        assert resp.status_code == 200, (
            f"[shortName] Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "updateOrganization")
        self._put_org({
            "shortName": original_short if original_short is not None else "",
        })

        current = self._get_current_org()
        resp = self._put_org({"contactEmail": str(current.get("contactEmail"))})
        assert resp.status_code == 200, (
            f"[contactEmail] Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "updateOrganization")

        current = self._get_current_org()
        original_addr = current.get("permanentAddress")
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
            f"[permanentAddress] Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "updateOrganization")
        if original_addr is not None:
            self._put_org({"permanentAddress": original_addr})

        resp = self._put_org({
            "permanentAddress": {
                "city": "Sparse City",
                "country": "SC",
            }
        })
        assert resp.status_code == 200, (
            f"[permanentAddress partial city+country] Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "updateOrganization")
        if original_addr is not None:
            self._put_org({"permanentAddress": original_addr})

        resp = self._put_org({
            "permanentAddress": {
                "postCode": "12345",
            }
        })
        assert resp.status_code == 200, (
            f"[permanentAddress postCode only] Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "updateOrganization")
        if original_addr is not None:
            self._put_org({"permanentAddress": original_addr})

    def test_update_org_multiple_fields_response_schemas(self) -> None:
        """PUT /api/v1/org — update multiple fields at once; response must match schema."""
        original = self._get_current_org()

        resp = self._put_org({
            "registeredName": "Multi-field Test Org",
            "shortName": "MFT",
        })
        assert resp.status_code == 200, (
            f"[registeredName+shortName] Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "updateOrganization")
        body = resp.json()
        assert body["data"]["registeredName"] == "Multi-field Test Org"
        assert body["data"]["shortName"] == "MFT"
        self._put_org({
            "registeredName": original.get("registeredName"),
            "shortName": original.get("shortName", ""),
        })

        current = self._get_current_org()
        resp = self._put_org({
            "contactEmail": str(current.get("contactEmail")),
            "registeredName": "Email+Name Test Org",
        })
        assert resp.status_code == 200, (
            f"[contactEmail+registeredName] Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "updateOrganization")
        self._put_org({"registeredName": original.get("registeredName")})

        current = self._get_current_org()
        original_addr = current.get("permanentAddress")
        resp = self._put_org({
            "shortName": "SPA",
            "permanentAddress": {
                "addressLine1": "456 Combo Ave",
                "city": "Comboville",
                "country": "US",
            },
        })
        assert resp.status_code == 200, (
            f"[shortName+permanentAddress] Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "updateOrganization")
        restore: dict[str, object] = {"shortName": original.get("shortName", "")}
        if original_addr is not None:
            restore["permanentAddress"] = original_addr
        self._put_org(restore)

        current = self._get_current_org()
        original_addr = current.get("permanentAddress")
        resp = self._put_org({
            "contactEmail": str(current.get("contactEmail")),
            "permanentAddress": {
                "city": "EmailAddr City",
                "state": "EA",
                "postCode": "99999",
            },
        })
        assert resp.status_code == 200, (
            f"[contactEmail+permanentAddress] Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "updateOrganization")
        if original_addr is not None:
            self._put_org({"permanentAddress": original_addr})

        current = self._get_current_org()
        original_addr = current.get("permanentAddress")
        resp = self._put_org({
            "registeredName": "All-fields Test Org",
            "shortName": "AFTO",
            "contactEmail": str(current.get("contactEmail")),
            "permanentAddress": {
                "addressLine1": "789 Full St",
                "city": "Fullton",
                "state": "FL",
                "country": "US",
                "postCode": "11111",
            },
        })
        assert resp.status_code == 200, (
            f"[all four fields] Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "updateOrganization")
        body = resp.json()
        assert body["data"]["registeredName"] == "All-fields Test Org"
        assert body["data"]["shortName"] == "AFTO"
        restore = {
            "registeredName": original.get("registeredName"),
            "shortName": original.get("shortName", ""),
        }
        if original_addr is not None:
            restore["permanentAddress"] = original_addr
        self._put_org(restore)

    def test_update_org_empty_body_response_schemas(self) -> None:
        """PUT /api/v1/org — empty body (no-op update); response must still match schema."""
        resp = self._put_org({})
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "updateOrganization")

    def test_update_organization_negative_tests(self) -> None:
        """Validation, unauthenticated, and invalid-token paths for PUT /api/v1/org."""
        invalid_body_cases = [
            ("invalid contactEmail", {"contactEmail": "not-an-email"}),
            ("contactEmail wrong type", {"contactEmail": 12345}),
            ("permanentAddress wrong type", {"permanentAddress": "123 Main St"}),
            (
                "permanentAddress nested wrong type",
                {"permanentAddress": {"city": 123, "country": True}},
            ),
        ]

        for label, body in invalid_body_cases:
            resp = self._put_org(body)
            assert resp.status_code == 400, (
                f"[{label}] Expected 400, got {resp.status_code}: {resp.text}"
            )
            payload = resp.json()
            assert_response_matches_openapi_operation(
                payload, "updateOrganization", status_code="400"
            )
            assert payload["error"]["code"] == "VALIDATION_ERROR", (
                f"[{label}] Expected 'VALIDATION_ERROR', got {payload['error']['code']!r}"
            )

        resp = self.org.put(
            "/",
            json={"registeredName": "Should Not Update"},
            auth=False,
        )
        assert resp.status_code == 401, (
            f"[no auth] Expected 401, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(
            body, "updateOrganization", status_code="401"
        )
        assert body["error"]["code"] == "HTTP_UNAUTHORIZED", (
            f"[no auth] Expected error code 'HTTP_UNAUTHORIZED', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "No token provided", (
            f"[no auth] Expected message 'No token provided', got {body['error']['message']!r}"
        )

        resp = self.org.put(
            "/",
            auth=False,
            headers={"Authorization": "Bearer not-a-real-token"},
            json={"registeredName": "Should Not Update"},
        )
        assert resp.status_code == 401, (
            f"[invalid token] Expected 401, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(
            body, "updateOrganization", status_code="401"
        )
        assert body["error"]["code"] == "HTTP_UNAUTHORIZED", (
            f"[invalid token] Expected error code 'HTTP_UNAUTHORIZED', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "Invalid token", (
            f"[invalid token] Expected message 'Invalid token', got {body['error']['message']!r}"
        )


# ====================================================================
# GET /api/v1/org/onboarding-status
# ====================================================================
@pytest.mark.integration
class TestGetOnboardingStatus(OrgTestBase):
    """GET /api/v1/org/onboarding-status"""

    def test_get_onboarding_status_response_schema(self) -> None:
        """GET /api/v1/org/onboarding-status — response must match OrgGetOnboardingStatusResponse."""
        resp = self.org.get_onboarding_status()
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "getOnboardingStatus")

    def test_get_onboarding_status_negative_tests(self) -> None:
        """Unauthenticated paths for GET /api/v1/org/onboarding-status."""
        resp = self.org.get("/onboarding-status", auth=False)
        assert resp.status_code == 401, (
            f"[no auth] Expected 401, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(
            body, "getOnboardingStatus", status_code="401"
        )
        assert body["error"]["code"] == "HTTP_UNAUTHORIZED", (
            f"[no auth] Expected error code 'HTTP_UNAUTHORIZED', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "No token provided", (
            f"[no auth] Expected message 'No token provided', got {body['error']['message']!r}"
        )


# ====================================================================
# PUT /api/v1/org/onboarding-status
# ====================================================================
@pytest.mark.integration
class TestUpdateOnboardingStatus(OrgTestBase):
    """PUT /api/v1/org/onboarding-status"""

    def _get_current_status(self) -> str:
        resp = self.org.get_onboarding_status()
        assert resp.status_code == 200
        return resp.json()["status"]

    def _put_status(self, status: str) -> requests.Response:
        return self.org.put("/onboarding-status", json={"status": status})

    def test_update_onboarding_status_allowed_values_response_schema(self) -> None:
        """Each allowed onboarding status updates cleanly — 200, OpenAPI, echo; restore after each."""
        original = self._get_current_status()

        resp = self._put_status("configured")
        assert resp.status_code == 200, (
            f"[configured] Expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "updateOnboardingStatus")
        assert body["status"] == "configured"
        self._put_status(original)

        resp = self._put_status("notConfigured")
        assert resp.status_code == 200, (
            f"[notConfigured] Expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "updateOnboardingStatus")
        assert body["status"] == "notConfigured"
        self._put_status(original)

        resp = self._put_status("skipped")
        assert resp.status_code == 200, (
            f"[skipped] Expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "updateOnboardingStatus")
        assert body["status"] == "skipped"

        self._put_status(original)

    def test_update_onboarding_status_negative_tests(self) -> None:
        """Validation and auth failures for PUT /api/v1/org/onboarding-status."""
        resp = self._put_status("invalidStatus")
        assert resp.status_code == 400, (
            f"[invalid status] Expected 400, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(
            body, "updateOnboardingStatus", status_code="400"
        )
        assert body["error"]["code"] == "VALIDATION_ERROR", (
            f"[invalid status] Expected error code 'VALIDATION_ERROR', got {body['error']['code']!r}"
        )

        resp = self.org.put("/onboarding-status", json={})
        assert resp.status_code == 400, (
            f"[empty body] Expected 400, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(
            body, "updateOnboardingStatus", status_code="400"
        )
        assert body["error"]["code"] == "VALIDATION_ERROR", (
            f"[empty body] Expected error code 'VALIDATION_ERROR', got {body['error']['code']!r}"
        )

        resp = self.org.put("/onboarding-status", json={"status": None})
        assert resp.status_code == 400, (
            f"[null status] Expected 400, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(
            body, "updateOnboardingStatus", status_code="400"
        )
        assert body["error"]["code"] == "VALIDATION_ERROR", (
            f"[null status] Expected error code 'VALIDATION_ERROR', got {body['error']['code']!r}"
        )

        resp = self.org.put(
            "/onboarding-status",
            json={"status": "configured"},
            auth=False,
        )
        assert resp.status_code == 401, (
            f"[no auth] Expected 401, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(
            body, "updateOnboardingStatus", status_code="401"
        )
        assert body["error"]["code"] == "HTTP_UNAUTHORIZED", (
            f"[no auth] Expected error code 'HTTP_UNAUTHORIZED', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "No token provided", (
            f"[no auth] Expected message 'No token provided', got {body['error']['message']!r}"
        )


# ====================================================================
# PUT /api/v1/org/logo
# ====================================================================
@pytest.mark.integration
class TestUpdateOrgLogo(OrgTestBase):
    """PUT /api/v1/org/logo — upload org logo (admin only)."""

    def test_upload_org_logo_png_response_schemas(self) -> None:
        """PUT /api/v1/org/logo — upload minimal PNG; response must match uploadOrganizationLogo (201)."""
        resp = _upload_org_logo(self.org, _TINY_PNG, "logo.png", "image/png")
        assert resp.status_code == 201, (
            f"Expected 201, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(
            body, "uploadOrganizationLogo", status_code="201"
        )
        assert body["mimeType"] == "image/jpeg"

    def test_upload_org_logo_svg_response_schemas(self) -> None:
        """PUT /api/v1/org/logo — upload minimal SVG; response must match uploadOrganizationLogo (201)."""
        svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"><rect width="1" height="1" fill="red"/></svg>'
        resp = _upload_org_logo(self.org, svg, "logo.svg", "image/svg+xml")
        assert resp.status_code == 201, (
            f"Expected 201, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(
            body, "uploadOrganizationLogo", status_code="201"
        )
        assert body["mimeType"] == "image/svg+xml"

    def test_upload_org_logo_negative_tests(self) -> None:
        """Auth and upload validation failures for PUT /api/v1/org/logo."""
        resp = self.org.put(
            "/logo",
            files={"file": ("logo.png", _TINY_PNG, "image/png")},
            auth=False,
        )
        assert resp.status_code == 401, (
            f"[no auth] Expected 401, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(
            body, "uploadOrganizationLogo", status_code="401"
        )
        assert body["error"]["code"] == "HTTP_UNAUTHORIZED", (
            f"[no auth] Expected error code 'HTTP_UNAUTHORIZED', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "No token provided", (
            f"[no auth] Expected message 'No token provided', got {body['error']['message']!r}"
        )

        resp = self.org.put("/logo", json={})
        assert resp.status_code == 400, (
            f"[no file] Expected 400, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(
            body, "uploadOrganizationLogo", status_code="400"
        )
        assert body["error"]["code"] == "HTTP_BAD_REQUEST", (
            f"[no file] Expected error code 'HTTP_BAD_REQUEST', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "No files available for processing", (
            f"[no file] Expected message 'No files available for processing', got {body['error']['message']!r}"
        )

        resp = _upload_org_logo(self.org, b"not an image", "file.txt", "text/plain")
        assert resp.status_code == 400, (
            f"[unsupported MIME] Expected 400, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(
            body, "uploadOrganizationLogo", status_code="400"
        )
        assert body["error"]["code"] == "HTTP_BAD_REQUEST", (
            f"[unsupported MIME] Expected error code 'HTTP_BAD_REQUEST', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == (
            "File upload failed: Invalid file type. Allowed types: "
            "image/png, image/jpeg, image/jpg, image/webp, image/gif, image/svg+xml"
        ), (
            f"[unsupported MIME] Unexpected message: {body['error']['message']!r}"
        )


# ====================================================================
# DELETE /api/v1/org/logo
# ====================================================================
@pytest.mark.integration
class TestRemoveOrgLogo(OrgTestBase):
    """DELETE /api/v1/org/logo — remove org logo (admin only)."""

    def _ensure_logo_exists(self) -> None:
        """Upload a logo so that DELETE has something to remove."""
        resp = _upload_org_logo(self.org, _TINY_PNG, "logo.png", "image/png")
        assert resp.status_code == 201, (
            f"Logo setup failed: {resp.status_code}: {resp.text}"
        )

    def test_delete_org_logo_response_schemas(self) -> None:
        """DELETE /api/v1/org/logo — upload setup then delete; response must match deleteOrganizationLogo."""
        self._ensure_logo_exists()

        resp = self.org.remove_logo()
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "deleteOrganizationLogo")

    def test_delete_org_logo_negative_tests(self) -> None:
        """Unauthenticated paths for DELETE /api/v1/org/logo."""
        resp = self.org.delete("/logo", auth=False)
        assert resp.status_code == 401, (
            f"[no auth] Expected 401, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(
            body, "deleteOrganizationLogo", status_code="401"
        )
        assert body["error"]["code"] == "HTTP_UNAUTHORIZED", (
            f"[no auth] Expected error code 'HTTP_UNAUTHORIZED', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "No token provided", (
            f"[no auth] Expected message 'No token provided', got {body['error']['message']!r}"
        )


# ====================================================================
# GET /api/v1/org/logo
# ====================================================================
@pytest.mark.integration
class TestGetOrgLogo(OrgTestBase):
    """GET /api/v1/org/logo — returns raw binary image or 204; 401 on missing auth."""

    def _upload_logo(self) -> None:
        resp = _upload_org_logo(self.org, _TINY_PNG, "logo.png", "image/png")
        assert resp.status_code == 201, (
            f"Logo upload setup failed: {resp.status_code}: {resp.text}"
        )

    def _delete_logo(self) -> None:
        """Remove the logo if one exists; silently tolerates 404 (no logo record)."""
        self.org.remove_logo()

    def test_get_org_logo_unauthenticated(self) -> None:
        """GET /api/v1/org/logo without auth → 401 with ErrorResponse schema."""
        resp = self.org.get("/logo", auth=False)
        assert resp.status_code == 401, (
            f"[no auth] Expected 401, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(
            body, "getOrganizationLogo", status_code="401"
        )
        assert body["error"]["code"] == "HTTP_UNAUTHORIZED", (
            f"[no auth] Expected error code 'HTTP_UNAUTHORIZED', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "No token provided", (
            f"[no auth] Expected message 'No token provided', got {body['error']['message']!r}"
        )

    def test_get_org_logo_with_logo_returns_binary(self) -> None:
        """GET /api/v1/org/logo after upload → 200 with binary content and correct Content-Type."""
        self._upload_logo()
        resp = self.org.get_logo()
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        content_type = resp.headers.get("Content-Type", "")
        assert content_type in ("image/jpeg", "image/svg+xml"), (
            f"Expected image/jpeg or image/svg+xml Content-Type, got {content_type!r}"
        )
        assert len(resp.content) > 0, "Expected non-empty image bytes in response body"

    def test_get_org_logo_no_logo_returns_204(self) -> None:
        """GET /api/v1/org/logo when no logo exists → 204 No Content with empty body."""
        self._delete_logo()
        resp = self.org.get_logo()
        assert resp.status_code == 204, (
            f"Expected 204, got {resp.status_code}: {resp.text}"
        )
        assert len(resp.content) == 0, (
            "204 response must have no body"
        )


# ====================================================================
# POST /api/v1/org
# ====================================================================
@pytest.mark.integration
class TestCreateOrganization(OrgTestBase):
    """POST /api/v1/org — org already exists in the test environment.

    Valid request bodies exercise the controller path and always return 500
    because the ``BadRequestError('There is already an organization')`` thrown
    inside ``createOrg`` is caught by its own ``catch`` block and re-thrown as
    ``InternalServerError``, which the error middleware serialises as
    ``HTTP_INTERNAL_SERVER_ERROR`` / 500.

    Invalid bodies are rejected by ``ValidationMiddleware`` before the
    controller runs, so they return 400 ``VALIDATION_ERROR``.
    """

    def _post_org(self, body: dict[str, object]) -> requests.Response:
        return self.org.post("/", json=body, auth=False)

    def test_create_org_individual_account_already_exists(self) -> None:
        """Valid individual-account body → 500 because org already exists."""
        resp = self._post_org({
            "accountType": "individual",
            "contactEmail": "test@example.com",
            "adminFullName": "Test Admin",
            "password": "TestPass123!",
        })
        assert resp.status_code == 500, (
            f"Expected 500 (org exists), got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(
            body, "createOrganization", status_code="500"
        )
        assert body["error"]["code"] == "HTTP_INTERNAL_SERVER_ERROR", (
            f"Expected 'HTTP_INTERNAL_SERVER_ERROR', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "There is already an organization", (
            f"Unexpected message: {body['error']['message']!r}"
        )

    def test_create_org_business_account_already_exists(self) -> None:
        """Valid business-account body (with registeredName) → 500 because org already exists."""
        resp = self._post_org({
            "accountType": "business",
            "registeredName": "Test Corp Inc.",
            "shortName": "TC",
            "contactEmail": "admin@testcorp.com",
            "adminFullName": "Corp Admin",
            "password": "CorpPass123!",
        })
        assert resp.status_code == 500, (
            f"Expected 500 (org exists), got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(
            body, "createOrganization", status_code="500"
        )
        assert body["error"]["code"] == "HTTP_INTERNAL_SERVER_ERROR", (
            f"Expected 'HTTP_INTERNAL_SERVER_ERROR', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "There is already an organization", (
            f"Unexpected message: {body['error']['message']!r}"
        )

    def test_create_org_validation_errors(self) -> None:
        """Invalid request bodies must be rejected by ValidationMiddleware with 400 VALIDATION_ERROR."""
        cases: list[tuple[str, dict[str, object]]] = [
            ("empty body", {}),
            ("missing contactEmail", {
                "accountType": "individual",
                "adminFullName": "Test Admin",
                "password": "TestPass123!",
            }),
            ("invalid accountType", {
                "accountType": "enterprise",
                "contactEmail": "test@example.com",
                "adminFullName": "Test Admin",
                "password": "TestPass123!",
            }),
            ("invalid email", {
                "accountType": "individual",
                "contactEmail": "not-an-email",
                "adminFullName": "Test Admin",
                "password": "TestPass123!",
            }),
            ("short password", {
                "accountType": "individual",
                "contactEmail": "test@example.com",
                "adminFullName": "Test Admin",
                "password": "short",
            }),
            ("business without registeredName", {
                "accountType": "business",
                "contactEmail": "admin@corp.com",
                "adminFullName": "Corp Admin",
                "password": "CorpPass123!",
            }),
        ]

        for label, payload in cases:
            resp = self._post_org(payload)
            assert resp.status_code == 400, (
                f"[{label}] Expected 400, got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert_response_matches_openapi_operation(
                body, "createOrganization", status_code="400"
            )
            assert body["error"]["code"] == "VALIDATION_ERROR", (
                f"[{label}] Expected 'VALIDATION_ERROR', got {body['error']['code']!r}"
            )
