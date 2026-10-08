"""Strict OpenAPI audit of GET /api/v1/configurationManager/connectors/googleWorkspaceOauthConfig."""

from __future__ import annotations

import pytest
from configuration_manager_audit_support import (
    INVALID_BEARER_HEADERS,
    KV_GOOGLE_WORKSPACE_OAUTH,
    GuardStoredValue,
    forget_stored_config,
    request_as,
)
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/connectors/googleWorkspaceOauthConfig"
PATH = "/connectors/googleWorkspaceOauthConfig"
SAVED = {
    "clientId": "spec-audit-client-id.apps.googleusercontent.com",
    "clientSecret": "spec-audit-client-secret",
    "enableRealTimeUpdates": False,
}


def test_admin_reads_the_saved_client_with_the_secret_unmasked(
    config_client: ConfigClient, guard_stored_value: GuardStoredValue
) -> None:
    guard_stored_value(KV_GOOGLE_WORKSPACE_OAUTH)
    saved = config_client.post(PATH, json=SAVED)
    assert saved.status_code == 200, saved.text[:500]

    resp = config_client.get(PATH)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {**SAVED, "topicName": ""}


def test_admin_reads_an_empty_object_when_nothing_is_saved(
    config_client: ConfigClient, guard_stored_value: GuardStoredValue
) -> None:
    guard_stored_value(KV_GOOGLE_WORKSPACE_OAUTH)
    forget_stored_config(KV_GOOGLE_WORKSPACE_OAUTH)

    resp = config_client.get(PATH)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {}


@pytest.mark.parametrize("headers", [None, INVALID_BEARER_HEADERS], ids=["no-token", "invalid-token"])
def test_read_without_valid_token_is_unauthorized(config_client: ConfigClient, headers: dict[str, str] | None) -> None:
    resp = config_client.get(PATH, auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "GET", PATH)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
