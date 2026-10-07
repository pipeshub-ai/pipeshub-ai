"""Strict OpenAPI audit of GET /api/v1/configurationManager/connectors/googleWorkspaceCredentials.

The test organization is a business account: the answer is the service-account key stored
for the organization, unmasked, or an empty object.
"""

from __future__ import annotations

import pytest
from configuration_manager_audit_support import (
    INVALID_BEARER_HEADERS,
    KV_GOOGLE_WORKSPACE_BUSINESS,
    GuardStoredValue,
    forget_stored_config,
    request_as,
)
from helper.clients.config_client import ConfigClient
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/connectors/googleWorkspaceCredentials"
PATH = "/connectors/googleWorkspaceCredentials"
KEY_FILE = {
    "type": "service_account",
    "project_id": "spec-audit-project",
    "private_key_id": "spec-audit-key-id",
    "private_key": "spec-audit-not-a-private-key",
    "client_email": "spec-audit@spec-audit-project.iam.gserviceaccount.com",
    "client_id": "000000000000000000000",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
    "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
    "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/spec-audit",
    "universe_domain": "googleapis.com",
}


@pytest.fixture
def business_key(pipeshub_client: PipeshubClient, guard_stored_value: GuardStoredValue) -> str:
    kv_path = f"{KV_GOOGLE_WORKSPACE_BUSINESS}/{pipeshub_client.org_id}"
    guard_stored_value(kv_path)
    return kv_path


def test_admin_reads_the_stored_key_unmasked(config_client: ConfigClient, business_key: str) -> None:
    saved = config_client.post(
        PATH, json={"fileChanged": True, "fileContent": KEY_FILE, "adminEmail": "spec-audit-admin@example.com"}
    )
    assert saved.status_code == 200, saved.text[:500]

    resp = config_client.get(PATH)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {
        **KEY_FILE,
        "adminEmail": "spec-audit-admin@example.com",
        "enableRealTimeUpdates": False,
        "topicName": "",
    }


def test_admin_reads_an_empty_object_when_nothing_is_stored(config_client: ConfigClient, business_key: str) -> None:
    forget_stored_config(business_key)

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
