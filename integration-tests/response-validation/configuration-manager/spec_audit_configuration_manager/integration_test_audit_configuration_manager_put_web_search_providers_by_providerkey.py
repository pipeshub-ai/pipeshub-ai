"""Strict OpenAPI audit of PUT /api/v1/configurationManager/web-search/providers/:providerKey.

No success case: updating a stored provider runs a health check against the
external search service before anything is saved.
"""

from __future__ import annotations

from typing import Any

import pytest
from configuration_manager_audit_support import (
    BUILTIN_WEB_SEARCH_PROVIDER_KEY,
    MISSING_WEB_SEARCH_PROVIDER_KEY,
    request_as,
)
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/web-search/providers/:providerKey"

VALID_BODY: dict[str, Any] = {
    "provider": "serper",
    "configuration": {"apiKey": "spec-audit-not-a-real-key"},
}


@pytest.mark.parametrize(
    "provider_key",
    [
        pytest.param(MISSING_WEB_SEARCH_PROVIDER_KEY, id="unknown-key"),
        # duckduckgo is listed by GET /web-search but never stored, so it cannot be edited.
        pytest.param(BUILTIN_WEB_SEARCH_PROVIDER_KEY, id="builtin-duckduckgo"),
    ],
)
def test_update_unstored_provider_is_not_found(
    config_client: ConfigClient, provider_key: str
) -> None:
    # The lookup precedes the health check, so nothing external is called.
    resp = config_client.put(f"/web-search/providers/{provider_key}", json=VALID_BODY)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json()["status"] == "error"


def test_update_with_unsupported_provider_type_is_rejected(
    config_client: ConfigClient,
) -> None:
    resp = config_client.put(
        f"/web-search/providers/{MISSING_WEB_SEARCH_PROVIDER_KEY}",
        json={**VALID_BODY, "provider": "spec-audit-no-such-engine"},
    )
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_update_as_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(
        second_user,
        "PUT",
        f"/web-search/providers/{MISSING_WEB_SEARCH_PROVIDER_KEY}",
        json=VALID_BODY,
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_update_without_token_is_unauthorized(config_client: ConfigClient) -> None:
    resp = config_client.put(
        f"/web-search/providers/{MISSING_WEB_SEARCH_PROVIDER_KEY}",
        auth=False,
        json=VALID_BODY,
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
