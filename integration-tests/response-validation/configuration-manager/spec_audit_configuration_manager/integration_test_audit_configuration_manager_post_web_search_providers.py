"""Strict OpenAPI audit of POST /api/v1/configurationManager/web-search/providers.

Negative paths only: a body that passes zod makes Node call the Python web-search
health check, which queries an external search service before anything is stored.
"""

from __future__ import annotations

from typing import Any

import pytest
from configuration_manager_audit_support import INVALID_BEARER_HEADERS, request_as
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/web-search/providers"
PATH = "/web-search/providers"

VALID_BODY: dict[str, Any] = {
    "provider": "serper",
    "configuration": {"apiKey": "spec-audit-not-a-real-key"},
}


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(
            {"provider": "spec-audit-no-such-engine", "configuration": {"apiKey": "x"}},
            id="provider-outside-enum",
        ),
        pytest.param({"provider": "serper"}, id="configuration-missing"),
        pytest.param(
            {"provider": "serper", "configuration": {"apiKey": "x"}, "isDefault": "yes"},
            id="isDefault-not-boolean",
        ),
    ],
)
def test_add_web_search_provider_invalid_body_is_rejected(
    config_client: ConfigClient, body: dict[str, Any]
) -> None:
    resp = config_client.post(PATH, json=body)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_add_web_search_provider_requires_valid_token(config_client: ConfigClient) -> None:
    resp = config_client.post(PATH, auth=False, headers=INVALID_BEARER_HEADERS, json=VALID_BODY)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_add_web_search_provider_member_is_forbidden(second_user: SecondUser) -> None:
    # userAdminCheck runs before zod and the health check, so this valid body goes nowhere.
    resp = request_as(second_user, "POST", PATH, json=VALID_BODY)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
