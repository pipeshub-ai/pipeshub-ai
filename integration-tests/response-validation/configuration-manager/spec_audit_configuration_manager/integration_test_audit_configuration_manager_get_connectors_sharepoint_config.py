"""Strict OpenAPI audit of GET /api/v1/configurationManager/connectors/sharepoint/config.

Chain: authenticate -> requireScopes(config:read) -> userAdminCheck -> getSharePointCredentials.
No validator: the handler reads nothing from the request but the caller's org id.
"""

from __future__ import annotations

from typing import Any

import pytest
from configuration_manager_audit_support import (
    INVALID_BEARER_HEADERS,
    KV_CONNECTOR_SHAREPOINT,
    GuardSavedConfig,
    forget_stored_config,
    request_as,
)
from helper.clients.config_client import ConfigClient
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser
from strict_openapi import (
    assert_strict_openapi_exchange,
    assert_strict_openapi_response,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/connectors/sharepoint/config"
PATH = "/connectors/sharepoint/config"

SAVED: dict[str, Any] = {
    "clientId": "spec-audit-client-id",
    "clientSecret": "spec-audit-client-secret",
    "tenantId": "spec-audit-tenant-id",
    "sharepointDomain": "spec-audit.sharepoint.invalid",
    "hasAdminConsent": True,
}


def test_admin_reads_an_empty_object_when_nothing_is_saved(
    config_client: ConfigClient,
    pipeshub_client: PipeshubClient,
    guard_saved_config: GuardSavedConfig,
) -> None:
    kv_path = f"{KV_CONNECTOR_SHAREPOINT}/{pipeshub_client.org_id}"
    guard_saved_config(PATH, kv_path)
    forget_stored_config(kv_path)

    resp = config_client.get(PATH)

    assert resp.status_code == 200, resp.text[:500]
    assert resp.json() == {}
    assert_strict_openapi_exchange(resp, ROUTE)


def test_admin_reads_the_saved_credentials_with_the_secret_unmasked(
    config_client: ConfigClient,
    pipeshub_client: PipeshubClient,
    guard_saved_config: GuardSavedConfig,
) -> None:
    guard_saved_config(PATH, f"{KV_CONNECTOR_SHAREPOINT}/{pipeshub_client.org_id}")
    saved = config_client.post(PATH, json=SAVED)
    assert saved.status_code == 200, saved.text[:500]

    resp = config_client.get(PATH)

    assert resp.status_code == 200, resp.text[:500]
    assert resp.json() == SAVED
    assert_strict_openapi_exchange(resp, ROUTE)


def test_admin_read_does_not_read_the_query_string(config_client: ConfigClient) -> None:
    plain = config_client.get(PATH)
    assert plain.status_code == 200, plain.text[:500]

    with outside_request_contract("the handler never reads the query string"):
        resp = config_client.get(PATH, params={"specAudit": "bogus", "orgId": "000000000000000000000000"})

    assert resp.status_code == 200, resp.text[:500]
    assert resp.json() == plain.json()
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    "headers",
    [None, INVALID_BEARER_HEADERS],
    ids=["no-token", "invalid-token"],
)
def test_unauthenticated_is_unauthorized(
    config_client: ConfigClient, headers: dict[str, str] | None
) -> None:
    resp = config_client.get(PATH, auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "GET", PATH)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
