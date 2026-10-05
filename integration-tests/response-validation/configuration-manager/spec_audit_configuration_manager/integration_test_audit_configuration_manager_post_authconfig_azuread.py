"""Strict OpenAPI audit of POST /api/v1/configurationManager/authConfig/azureAd.

Negative paths only: a successful call overwrites the org-wide Azure AD sign-in config.
"""

from __future__ import annotations

from typing import Any

import pytest
from configuration_manager_audit_support import request_as
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/authConfig/azureAd"
PATH = "/authConfig/azureAd"

VALID_BODY: dict[str, Any] = {
    "clientId": "00000000-0000-4000-8000-0000000000aa",
    "tenantId": "common",
    "enableJit": True,
}


def test_set_azure_ad_config_without_token_is_unauthorized(config_client: ConfigClient) -> None:
    resp = config_client.post(PATH, auth=False, json=VALID_BODY)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_set_azure_ad_config_as_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "POST", PATH, json=VALID_BODY)

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"tenantId": "common"}, id="missing-client-id"),
        pytest.param({"clientId": "", "tenantId": "common"}, id="empty-client-id"),
        # enableJit is not in the spec's AzureAdAuthConfig, yet the validator type-checks it.
        pytest.param({**VALID_BODY, "enableJit": "yes"}, id="enable-jit-not-boolean"),
    ],
)
def test_set_azure_ad_config_rejects_invalid_body(config_client: ConfigClient, body: dict[str, Any]) -> None:
    resp = config_client.post(PATH, json=body)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
