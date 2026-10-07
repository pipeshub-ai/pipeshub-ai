"""Strict OpenAPI audit of PUT /api/v1/configurationManager/web-search/settings.

The stored web search config is put back byte for byte after every test that writes.
"""

from __future__ import annotations

from typing import Any

import pytest
from configuration_manager_audit_support import (
    KV_WEB_SEARCH,
    GuardStoredValue,
    assert_validation_error,
    request_as,
)
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/web-search/settings"

VALID_BODY: dict[str, Any] = {"includeImages": False, "maxImages": 3}


def _settings(config_client: ConfigClient) -> dict[str, Any]:
    current = config_client.get("/web-search")
    assert current.status_code == 200, f"could not read web search settings: {current.text[:500]}"
    settings: dict[str, Any] = current.json()["settings"]
    return settings


def test_images_on_with_a_limit_is_saved_and_echoed(
    config_client: ConfigClient, guard_stored_value: GuardStoredValue
) -> None:
    guard_stored_value(KV_WEB_SEARCH)

    resp = config_client.put("/web-search/settings", json={"includeImages": True, "maxImages": 7})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {
        "status": "success",
        "message": "Web search settings updated successfully",
        "settings": {"includeImages": True, "maxImages": 7},
    }
    assert _settings(config_client) == {"includeImages": True, "maxImages": 7}


def test_images_off_without_a_limit_keeps_the_stored_limit(
    config_client: ConfigClient, guard_stored_value: GuardStoredValue
) -> None:
    guard_stored_value(KV_WEB_SEARCH)
    before = _settings(config_client)

    resp = config_client.put("/web-search/settings", json={"includeImages": False})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["settings"] == {"includeImages": False, "maxImages": before["maxImages"]}


def test_fields_other_than_the_settings_are_dropped(
    config_client: ConfigClient, guard_stored_value: GuardStoredValue
) -> None:
    guard_stored_value(KV_WEB_SEARCH)

    with outside_request_contract("an unknown body field, to show the validator drops it"):
        resp = config_client.put("/web-search/settings", json={**VALID_BODY, "specAudit": True})
        assert_strict_openapi_exchange(resp, ROUTE)

    assert resp.status_code == 200, resp.text[:500]
    assert resp.json()["settings"] == VALID_BODY


def test_update_web_search_settings_without_token_is_unauthorized(config_client: ConfigClient) -> None:
    resp = config_client.put("/web-search/settings", auth=False, json=VALID_BODY)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_web_search_settings_as_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "PUT", "/web-search/settings", json=VALID_BODY)

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_a_token_without_config_write_scope_still_saves_the_settings(
    config_client: ConfigClient, guard_stored_value: GuardStoredValue, narrow_scope_headers: dict[str, str]
) -> None:
    # API bug: unlike the other configurationManager writes, this route has no requireScopes.
    guard_stored_value(KV_WEB_SEARCH)
    before = _settings(config_client)

    resp = config_client.put("/web-search/settings", auth=False, headers=narrow_scope_headers, json=before)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("body", "field"),
    [
        # The superRefine of webSearchSettingsSchema: images on needs an explicit limit.
        pytest.param({"includeImages": True}, "body.maxImages", id="images-on-without-max"),
        pytest.param({"includeImages": False, "maxImages": 501}, "body.maxImages", id="max-images-over-500"),
        pytest.param({"includeImages": False, "maxImages": 0}, "body.maxImages", id="max-images-zero"),
        pytest.param({"includeImages": True, "maxImages": 2.5}, "body.maxImages", id="max-images-not-whole"),
        pytest.param({"maxImages": 3}, "body.includeImages", id="include-images-missing"),
        pytest.param({"includeImages": "true", "maxImages": 3}, "body.includeImages", id="include-images-string"),
    ],
)
def test_update_web_search_settings_rejects_invalid_body(
    config_client: ConfigClient, body: dict[str, Any], field: str
) -> None:
    resp = config_client.put("/web-search/settings", json=body)

    assert_validation_error(resp, field)
    assert_strict_openapi_exchange(resp, ROUTE)
