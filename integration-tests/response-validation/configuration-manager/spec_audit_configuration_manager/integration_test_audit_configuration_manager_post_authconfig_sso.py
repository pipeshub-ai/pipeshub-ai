"""Strict OpenAPI audit of POST /api/v1/configurationManager/authConfig/sso.

Negative paths only: a successful call replaces the org-wide SAML sign-in config
and rebuilds the SAML strategies.
"""

from __future__ import annotations

from typing import Any

import pytest
from configuration_manager_audit_support import INVALID_BEARER_HEADERS, request_as
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/authConfig/sso"
PATH = "/authConfig/sso"

VALID_BODY: dict[str, Any] = {
    "entryPoint": "https://idp.spec-audit.invalid/sso/saml",
    "certificate": "MIIC-spec-audit-not-a-real-certificate",
    "emailKey": "email",
}


@pytest.mark.parametrize(
    "headers",
    [None, INVALID_BEARER_HEADERS],
    ids=["no_token", "invalid_token"],
)
def test_sso_auth_config_requires_valid_token(
    config_client: ConfigClient, headers: dict[str, str] | None
) -> None:
    resp = config_client.post(PATH, auth=False, headers=headers, json=VALID_BODY)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_sso_auth_config_member_is_forbidden(second_user: SecondUser) -> None:
    # userAdminCheck runs before zod, so even this valid body never reaches the store.
    resp = request_as(second_user, "POST", PATH, json=VALID_BODY)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    "body",
    [
        {"certificate": VALID_BODY["certificate"], "emailKey": "email"},
        {**VALID_BODY, "enableJit": "true"},
    ],
    ids=["missing_entry_point", "enable_jit_as_string"],
)
def test_sso_auth_config_invalid_body_is_rejected(
    config_client: ConfigClient, body: dict[str, Any]
) -> None:
    resp = config_client.post(PATH, json=body)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
