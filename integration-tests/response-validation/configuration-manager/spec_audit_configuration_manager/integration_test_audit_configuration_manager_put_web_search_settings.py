"""Strict OpenAPI audit of PUT /api/v1/configurationManager/web-search/settings.

The PUT overwrites both settings, so the success case sends back exactly what
GET /web-search returned: the write is its own restore.
"""

from __future__ import annotations

from typing import Any

import pytest
from configuration_manager_audit_support import request_as
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/web-search/settings"

VALID_BODY: dict[str, Any] = {"includeImages": False, "maxImages": 3}


def test_update_web_search_settings_with_current_values_is_saved(config_client: ConfigClient) -> None:
    current = config_client.get("/web-search")
    assert current.status_code == 200, f"could not read web search settings: {current.text[:500]}"
    before: dict[str, Any] = current.json()["settings"]
    body = {"includeImages": before["includeImages"], "maxImages": before["maxImages"]}

    try:
        resp = config_client.put("/web-search/settings", json=body)

        assert resp.status_code == 200, resp.text[:500]
        assert resp.json() == {
            "status": "success",
            "message": "Web search settings updated successfully",
            "settings": before,
        }
        assert_strict_openapi_response(resp, ROUTE)
    finally:
        after = config_client.get("/web-search")
        if after.status_code != 200 or after.json().get("settings") != before:
            restored = config_client.put("/web-search/settings", json=body)
            assert restored.status_code == 200, f"web search settings not restored: {restored.text[:500]}"


def test_update_web_search_settings_without_token_is_unauthorized(config_client: ConfigClient) -> None:
    resp = config_client.put("/web-search/settings", auth=False, json=VALID_BODY)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_update_web_search_settings_as_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "PUT", "/web-search/settings", json=VALID_BODY)

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    "body",
    [
        # The superRefine of webSearchSettingsSchema: images on needs an explicit limit.
        pytest.param({"includeImages": True}, id="images-on-without-max"),
        pytest.param({"includeImages": False, "maxImages": 501}, id="max-images-over-500"),
    ],
)
def test_update_web_search_settings_rejects_invalid_body(config_client: ConfigClient, body: dict[str, Any]) -> None:
    resp = config_client.put("/web-search/settings", json=body)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
