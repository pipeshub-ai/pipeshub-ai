"""Strict OpenAPI audit of POST /api/v1/configurationManager/connectors/atlassian/config.

Chain: authenticate -> requireScopes(config:write) -> userAdminCheck -> zod body -> setAtlassianOauthConfig.
Negative paths only: a successful call overwrites the org's Atlassian OAuth client.
"""

from __future__ import annotations

from typing import Any

import pytest
from configuration_manager_audit_support import INVALID_BEARER_HEADERS, request_as
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/connectors/atlassian/config"
PATH = "/connectors/atlassian/config"
VALID_BODY = {"clientId": "spec-audit-client-id", "clientSecret": "spec-audit-client-secret"}


@pytest.mark.parametrize(
    "headers",
    [None, INVALID_BEARER_HEADERS],
    ids=["no-token", "invalid-token"],
)
def test_set_atlassian_config_without_valid_token_is_unauthorized(
    config_client: ConfigClient, headers: dict[str, str] | None
) -> None:
    resp = config_client.post(PATH, auth=False, headers=headers, json=VALID_BODY)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_set_atlassian_config_as_member_is_forbidden(second_user: SecondUser) -> None:
    # A valid body, so the 403 can only come from userAdminCheck and nothing is stored.
    resp = request_as(second_user, "POST", PATH, json=VALID_BODY)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    "body",
    [{}, {"clientId": "", "clientSecret": "spec-audit-client-secret"}],
    ids=["missing-fields", "empty-client-id"],
)
def test_set_atlassian_config_invalid_body_is_rejected(
    config_client: ConfigClient, body: dict[str, Any]
) -> None:
    resp = config_client.post(PATH, json=body)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
