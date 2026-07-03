"""
Web Search Providers API – Response Validation Integration Tests
================================================================

GET /api/v1/configurationManager/web-search — getWebSearchProviders

Validates live gateway responses against ``pipeshub-openapi.yaml`` for
``operationId: getWebSearchProviders``.

Requires:
  - PIPESHUB_BASE_URL and OAuth credentials (see integration-tests/README.md)
  - Running Node.js API
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_RV_HELPER = _ROOT / "response-validation" / "helper"
for _p in (_ROOT, _RV_HELPER):
    s = str(_p)
    if s not in sys.path:
        sys.path.insert(0, s)

from helper.clients.config_client import ConfigClient  # noqa: E402
from openapi_schema_validator import (  # noqa: E402
    assert_response_matches_openapi_operation,
    assert_response_matches_openapi_ref,
)

logger = logging.getLogger("web-search-integration-test")


# ------------------------------------------------------------------ #
# Base test class
# ------------------------------------------------------------------ #
class WebSearchTestBase:
    """Base class with shared config_client fixture."""

    @pytest.fixture(autouse=True)
    def _setup(self, config_client: ConfigClient) -> None:
        self.config = config_client


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #
def _assert_error_envelope_matches_spec(body: dict) -> None:
    assert_response_matches_openapi_ref(body, "#/components/schemas/ErrorResponse")


def _assert_web_search_success_shape(body: dict) -> None:
    assert body.get("status") == "success", (
        f"Expected status 'success', got {body.get('status')!r}"
    )
    providers = body.get("providers")
    assert isinstance(providers, list), (
        f"Expected providers list, got {type(providers).__name__}"
    )
    settings = body.get("settings")
    assert isinstance(settings, dict), (
        f"Expected settings object, got {type(settings).__name__}"
    )
    assert isinstance(settings.get("includeImages"), bool), (
        "settings.includeImages must be boolean"
    )
    assert isinstance(settings.get("maxImages"), int), (
        "settings.maxImages must be integer"
    )


# ==================================================================== #
# GET /api/v1/configurationManager/web-search
# ==================================================================== #
@pytest.mark.integration
class TestGetWebSearchProviders(WebSearchTestBase):
    """GET /configurationManager/web-search — getWebSearchProviders."""

    def test_get_web_search_providers_200_matches_openapi(self) -> None:
        resp = self.config.get_web_search_providers()
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text[:500]}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(
            body, "getWebSearchProviders", status_code="200"
        )
        _assert_web_search_success_shape(body)

    def test_get_web_search_missing_authorization(self) -> None:
        resp = self.config.get("/web-search", auth=False)
        assert resp.status_code in (400, 401), (
            f"Expected 400/401 without Authorization, got {resp.status_code}: "
            f"{resp.text[:500]}"
        )
        _assert_error_envelope_matches_spec(resp.json())

    def test_get_web_search_invalid_bearer_token(self) -> None:
        resp = self.config.get(
            "/web-search",
            auth=False,
            headers={"Authorization": "Bearer invalid-token"},
        )
        assert resp.status_code in (400, 401), (
            f"Expected 400/401 for invalid Bearer, got {resp.status_code}: "
            f"{resp.text[:500]}"
        )
        _assert_error_envelope_matches_spec(resp.json())
