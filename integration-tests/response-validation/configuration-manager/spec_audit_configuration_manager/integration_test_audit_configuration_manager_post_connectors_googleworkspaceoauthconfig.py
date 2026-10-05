"""Strict OpenAPI audit of POST /api/v1/configurationManager/connectors/googleWorkspaceOauthConfig.

Negative paths only: a successful call replaces the org's Google Workspace OAuth
client, may publish Gmail real-time-update events, and no route removes the
stored config again when none existed before.
"""

from __future__ import annotations

from typing import Any

import pytest
from configuration_manager_audit_support import INVALID_BEARER_HEADERS, request_as
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/connectors/googleWorkspaceOauthConfig"
PATH = "/connectors/googleWorkspaceOauthConfig"

VALID_BODY: dict[str, Any] = {
    "clientId": "spec-audit-client-id.apps.googleusercontent.com",
    "clientSecret": "spec-audit-client-secret",
    "enableRealTimeUpdates": False,
}


@pytest.mark.parametrize(
    "headers",
    [None, INVALID_BEARER_HEADERS],
    ids=["no_token", "invalid_token"],
)
def test_google_workspace_oauth_config_requires_valid_token(
    config_client: ConfigClient, headers: dict[str, str] | None
) -> None:
    resp = config_client.post(PATH, auth=False, headers=headers, json=VALID_BODY)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_google_workspace_oauth_config_member_is_forbidden(second_user: SecondUser) -> None:
    # userAdminCheck runs before zod, so even this valid body never reaches the store.
    resp = request_as(second_user, "POST", PATH, json=VALID_BODY)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    "body",
    [
        {k: v for k, v in VALID_BODY.items() if k != "clientSecret"},
        # Passes zod (topicName is optional there); the controller refuses it before reading the store.
        {**VALID_BODY, "enableRealTimeUpdates": True},
    ],
    ids=["missing_client_secret", "real_time_updates_without_topic_name"],
)
def test_google_workspace_oauth_config_invalid_body_is_rejected(
    config_client: ConfigClient, body: dict[str, Any]
) -> None:
    resp = config_client.post(PATH, json=body)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
