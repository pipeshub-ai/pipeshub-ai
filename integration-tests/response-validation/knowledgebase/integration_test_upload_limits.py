from __future__ import annotations

import sys
from pathlib import Path

import pytest
import requests

_ROOT = Path(__file__).resolve().parents[2]
_RV_HELPER = _ROOT / "response-validation" / "helper"
for _p in (_ROOT, _RV_HELPER):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from helper.pipeshub_client import PipeshubClient  # noqa: E402
from openapi_schema_validator import assert_response_matches_openapi_operation  # noqa: E402


@pytest.mark.integration
class TestUploadLimits:
    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client
        self.client._ensure_access_token()
        self.limits_url = f"{self.client.base_url}/api/v1/knowledgeBase/limits"
        self.headers = {
            "Authorization": f"Bearer {self.client._access_token}",
        }

    def test_get_upload_limits_success(self) -> None:
        resp = requests.get(
            self.limits_url,
            headers=self.headers,
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert_response_matches_openapi_operation(body, "getUploadLimits")

        assert body["maxFilesPerRequest"] == 1000
        assert body["maxFileSizeBytes"] > 0

    def test_get_upload_limits_negative(self) -> None:
        resp = requests.get(
            self.limits_url,
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "getUploadLimits", status_code="401"
        )
