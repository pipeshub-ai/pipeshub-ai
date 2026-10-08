"""Strict OpenAPI audit of POST /api/v1/configurationManager/frontendPublicUrl.

The success case saves the frontend URL already in force (the stored one, else the
fallback the services resolve to), so OAuth redirects and mail links do not change;
the stored endpoints are put back byte for byte afterwards.
"""

from __future__ import annotations

from typing import Any

import pytest
from configuration_manager_audit_support import (
    DESKTOP_CLIENT_HEADERS,
    KV_ENDPOINTS,
    GuardStoredValue,
    assert_validation_error,
    request_as,
)
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/frontendPublicUrl"

VALID_BODY: dict[str, Any] = {"url": "https://spec-audit.pipeshub.invalid"}


def _url_in_force(config_client: ConfigClient) -> str:
    stored = config_client.get("/frontendPublicUrl")
    assert stored.status_code == 200, stored.text[:500]
    if stored.json().get("url"):
        return str(stored.json()["url"])
    resolved = config_client.get("/public/desktopFrontendUrl", auth=False, headers=DESKTOP_CLIENT_HEADERS)
    assert resolved.status_code == 200, resolved.text[:500]
    return str(resolved.json()["frontendUrl"])


def test_set_frontend_url_saves_it_without_the_trailing_slash(
    config_client: ConfigClient, guard_stored_value: GuardStoredValue
) -> None:
    guard_stored_value(KV_ENDPOINTS)
    url = _url_in_force(config_client).rstrip("/")

    resp = config_client.post("/frontendPublicUrl", json={"url": f"{url}/"})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"message": "Frontend Url saved successfully"}
    stored = config_client.get("/frontendPublicUrl")
    assert stored.status_code == 200, stored.text[:500]
    assert stored.json() == {"url": url}


def test_set_frontend_url_without_token_is_unauthorized(config_client: ConfigClient) -> None:
    resp = config_client.post("/frontendPublicUrl", auth=False, json=VALID_BODY)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_set_frontend_url_as_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "POST", "/frontendPublicUrl", json=VALID_BODY)

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({}, id="missing-url"),
        # z.string().url() needs a scheme; the controller's own URL check is never reached.
        pytest.param({"url": "spec-audit.pipeshub.invalid"}, id="url-without-scheme"),
        pytest.param({"url": ""}, id="empty-url"),
        pytest.param({"url": 8080}, id="url-not-a-string"),
    ],
)
def test_set_frontend_url_rejects_invalid_body(config_client: ConfigClient, body: dict[str, Any]) -> None:
    resp = config_client.post("/frontendPublicUrl", json=body)

    assert_validation_error(resp, "body.url")
    assert_strict_openapi_exchange(resp, ROUTE)


def test_token_without_config_write_scope_is_forbidden(
    config_client: ConfigClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = config_client.post("/frontendPublicUrl", auth=False, headers=narrow_scope_headers, json={"url": "https://spec-audit.pipeshub.invalid"})
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
