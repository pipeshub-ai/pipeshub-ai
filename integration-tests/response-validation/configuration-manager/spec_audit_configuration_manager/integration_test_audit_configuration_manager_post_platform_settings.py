"""Strict OpenAPI audit of POST /api/v1/configurationManager/platform/settings.

The POST replaces the whole stored object, so the success case sends back exactly
what GET /platform/settings returned: the write is its own restore.
"""

from __future__ import annotations

from typing import Any

import pytest
from configuration_manager_audit_support import request_as
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/platform/settings"

VALID_BODY: dict[str, Any] = {"fileUploadMaxSizeBytes": 31457280, "featureFlags": {}}


def test_set_platform_settings_with_current_values_is_saved(config_client: ConfigClient) -> None:
    current = config_client.get("/platform/settings")
    assert current.status_code == 200, f"could not read platform settings: {current.text[:500]}"
    before: dict[str, Any] = current.json()
    body = {
        "fileUploadMaxSizeBytes": before["fileUploadMaxSizeBytes"],
        "featureFlags": before["featureFlags"],
    }

    try:
        resp = config_client.post("/platform/settings", json=body)

        assert resp.status_code == 200, resp.text[:500]
        assert resp.json() == {"message": "Platform settings saved"}
        assert_strict_openapi_response(resp, ROUTE)
    finally:
        after = config_client.get("/platform/settings")
        if after.status_code != 200 or after.json() != before:
            restored = config_client.post("/platform/settings", json=body)
            assert restored.status_code == 200, f"platform settings not restored: {restored.text[:500]}"


def test_set_platform_settings_without_token_is_unauthorized(config_client: ConfigClient) -> None:
    resp = config_client.post("/platform/settings", auth=False, json=VALID_BODY)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_set_platform_settings_as_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "POST", "/platform/settings", json=VALID_BODY)

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"featureFlags": {}}, id="missing-upload-limit"),
        # One byte over the 1 GB ceiling of platformSettingsSchema.
        pytest.param({"fileUploadMaxSizeBytes": 1024**3 + 1, "featureFlags": {}}, id="upload-limit-over-1gb"),
    ],
)
def test_set_platform_settings_rejects_invalid_body(config_client: ConfigClient, body: dict[str, Any]) -> None:
    resp = config_client.post("/platform/settings", json=body)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
