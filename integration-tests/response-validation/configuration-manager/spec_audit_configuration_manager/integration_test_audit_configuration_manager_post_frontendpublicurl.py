"""Strict OpenAPI audit of POST /api/v1/configurationManager/frontendPublicUrl.

Negative paths only: a successful call rewrites the org-wide frontend URL (OAuth
redirects, mail links) and makes every service reload its config.
"""

from __future__ import annotations

from typing import Any

import pytest
from configuration_manager_audit_support import request_as
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/frontendPublicUrl"

VALID_BODY: dict[str, Any] = {"url": "https://spec-audit.pipeshub.invalid"}


def test_set_frontend_url_without_token_is_unauthorized(config_client: ConfigClient) -> None:
    resp = config_client.post("/frontendPublicUrl", auth=False, json=VALID_BODY)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_set_frontend_url_as_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "POST", "/frontendPublicUrl", json=VALID_BODY)

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({}, id="missing-url"),
        # z.string().url() needs a scheme; the controller's own URL check is never reached.
        pytest.param({"url": "spec-audit.pipeshub.invalid"}, id="url-without-scheme"),
        pytest.param({"url": 8080}, id="url-not-a-string"),
    ],
)
def test_set_frontend_url_rejects_invalid_body(config_client: ConfigClient, body: dict[str, Any]) -> None:
    resp = config_client.post("/frontendPublicUrl", json=body)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
