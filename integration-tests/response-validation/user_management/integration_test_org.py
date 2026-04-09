"""
Organization API – Integration Tests
=====================================

Tests the GET /api/v1/org endpoint (getOrganizationById) against a live
Pipeshub backend.  Response shape is validated against the YAML schema
in ``response-validation/schemas/user_management/org_document_response.yaml``.

Requires:
  - PIPESHUB_BASE_URL set in .env / .env.local
  - Valid OAuth credentials (CLIENT_ID + CLIENT_SECRET) or test-user login
"""

import logging
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from helper.pipeshub_client import PipeshubClient  # noqa: E402
from helper.response_validator import (  # noqa: E402
    assert_response_matches_schema,
    load_yaml_schema,
    validate_response,
)

logger = logging.getLogger("org-integration-test")

# Load the YAML schema once for the module
_ORG_DOCUMENT_SCHEMA = load_yaml_schema(
    "response-validation/schemas/user_management/org_document_response.yaml"
)


@pytest.mark.integration
class TestGetOrganizationById:
    """GET /api/v1/org — retrieve the authenticated user's organization."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client
        self.base_url = pipeshub_client.base_url

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _get_org(self) -> dict[str, object]:
        """Call GET /api/v1/org and return the parsed JSON body."""
        import requests

        resp = requests.get(
            f"{self.base_url}/api/v1/org",
            headers=self.client._headers(),
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, (
            f"Expected 200 from GET /api/v1/org, got {resp.status_code}: {resp.text}"
        )
        
        return resp.json()

    # ------------------------------------------------------------------ #
    # Tests
    # ------------------------------------------------------------------ #
    def test_returns_200_with_valid_schema(self) -> None:
        """Response body must conform to OrgDocumentResponse YAML schema."""
        body = self._get_org()
        assert_response_matches_schema(body, _ORG_DOCUMENT_SCHEMA)

    def test_required_fields_present(self) -> None:
        """All required fields defined in the schema must be present."""
        body = self._get_org()
        required_fields = [
            "_id",
            "registeredName",
            "domain",
            "contactEmail",
            "accountType",
            "onBoardingStatus",
            "isDeleted",
            "createdAt",
            "updatedAt",
            "slug",
            "__v",
        ]
        for field_name in required_fields:
            assert field_name in body, f"Required field '{field_name}' missing from response"

    def test_id_is_valid_objectid(self) -> None:
        """The _id field must be a 24-char hex string (MongoDB ObjectId)."""
        body = self._get_org()
        _id = body.get("_id")
        assert isinstance(_id, str), f"_id should be a string, got {type(_id)}"
        assert len(_id) == 24, f"_id should be 24 chars, got {len(_id)}"
        assert all(c in "0123456789abcdef" for c in _id.lower()), "_id is not valid hex"

    def test_account_type_is_business(self) -> None:
        """accountType must be 'business' (only allowed enum value)."""
        body = self._get_org()
        assert body.get("accountType") == "business"

    def test_onboarding_status_is_valid_enum(self) -> None:
        """onBoardingStatus must be one of the allowed enum values."""
        body = self._get_org()
        allowed = {"configured", "notConfigured", "skipped"}
        status = body.get("onBoardingStatus")
        assert status in allowed, f"onBoardingStatus '{status}' not in {allowed}"

    def test_contact_email_is_valid(self) -> None:
        """contactEmail must look like a valid email address."""
        body = self._get_org()
        email = body.get("contactEmail")
        assert isinstance(email, str), "contactEmail should be a string"
        assert "@" in email, f"contactEmail '{email}' missing @"
        assert "." in email.split("@")[-1], f"contactEmail '{email}' domain has no dot"

    def test_is_deleted_is_boolean(self) -> None:
        """isDeleted must be a boolean."""
        body = self._get_org()
        assert isinstance(body.get("isDeleted"), bool)

    def test_timestamps_are_datetime_strings(self) -> None:
        """createdAt and updatedAt must be ISO datetime strings."""
        body = self._get_org()
        for field_name in ("createdAt", "updatedAt"):
            value = body.get(field_name)
            assert isinstance(value, str), f"{field_name} should be a string, got {type(value)}"
            assert "T" in value, f"{field_name} '{value}' is not ISO datetime format"

    def test_version_is_integer(self) -> None:
        """__v (Mongoose version key) must be an integer."""
        body = self._get_org()
        v = body.get("__v")
        assert isinstance(v, int), f"__v should be int, got {type(v)}"

    def test_slug_is_non_empty_string(self) -> None:
        """slug must be a non-empty string."""
        body = self._get_org()
        slug = body.get("slug")
        assert isinstance(slug, str), f"slug should be a string, got {type(slug)}"
        assert len(slug) > 0, "slug should not be empty"

    def test_optional_permanent_address_schema(self) -> None:
        """If permanentAddress is present, it must match the nested schema."""
        body = self._get_org()
        addr = body.get("permanentAddress")
        if addr is None:
            pytest.skip("permanentAddress not present in this org")
        assert isinstance(addr, dict), f"permanentAddress should be dict, got {type(addr)}"
        # Validate the full response still passes (covers nested fields)
        assert_response_matches_schema(body, _ORG_DOCUMENT_SCHEMA)

    def test_optional_short_name(self) -> None:
        """If shortName is present, it must be a string."""
        body = self._get_org()
        short_name = body.get("shortName")
        if short_name is None:
            pytest.skip("shortName not present in this org")
        assert isinstance(short_name, str), f"shortName should be string, got {type(short_name)}"

    def test_no_unexpected_validation_errors(self) -> None:
        """validate_response should return zero errors for the live response."""
        body = self._get_org()
        errors = validate_response(body, _ORG_DOCUMENT_SCHEMA)
        assert errors == [], (
            f"Schema validation returned {len(errors)} error(s):\n"
            + "\n".join(f"  - {e}" for e in errors)
        )
