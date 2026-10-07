"""Strict OpenAPI audit of POST /api/v1/configurationManager/connectors/googleWorkspaceCredentials.

Chain: authenticate -> requireScopes(config:write) -> userAdminCheck -> JSON file upload
processor -> createGoogleWorkspaceCredentials. No validator: the handler checks the body
itself. The test organization is a business account, so the service-account branch runs;
the individual (OAuth token) branch cannot be reached on this deployment. Real-time updates
stay off: turning them on publishes a Gmail event to the connector service. The stored key
is put back byte for byte afterwards.
"""

from __future__ import annotations

import json
from typing import Any

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
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    assert_strict_openapi_response,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/connectors/googleWorkspaceCredentials"
PATH = "/connectors/googleWorkspaceCredentials"
UPLOAD_FIELD = "googleWorkspaceCredentials"
SAVED_BODY = {"message": "Successfully updated"}
ADMIN_EMAIL = "spec-audit-admin@example.com"

# Not a real key: nothing reads it while these tests run.
KEY_FILE: dict[str, str] = {
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
STORED = {**KEY_FILE, "adminEmail": ADMIN_EMAIL, "enableRealTimeUpdates": False, "topicName": ""}

UploadFiles = dict[str, tuple[str, bytes, str]]


def _upload(content: bytes, mimetype: str = "application/json", filename: str = "credentials.json") -> UploadFiles:
    return {UPLOAD_FIELD: (filename, content, mimetype)}


@pytest.fixture
def business_key(pipeshub_client: PipeshubClient, guard_stored_value: GuardStoredValue) -> str:
    """The organization's key path; whatever is stored there is put back after the test."""
    kv_path = f"{KV_GOOGLE_WORKSPACE_BUSINESS}/{pipeshub_client.org_id}"
    guard_stored_value(kv_path)
    return kv_path


def _stored(config_client: ConfigClient) -> dict[str, Any]:
    resp = config_client.get(PATH)
    assert resp.status_code == 200, resp.text[:500]
    body: dict[str, Any] = resp.json()
    return body


def test_upload_of_a_key_file_stores_it_with_the_settings(config_client: ConfigClient, business_key: str) -> None:
    resp = config_client.post(
        PATH,
        files=_upload(json.dumps(KEY_FILE).encode()),
        data={"adminEmail": ADMIN_EMAIL, "fileChanged": "true", "enableRealTimeUpdates": "false"},
    )

    assert resp.status_code == 200, resp.text[:500]
    assert resp.json() == SAVED_BODY
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _stored(config_client) == STORED


def test_a_json_body_with_the_key_as_file_content_is_stored_too(config_client: ConfigClient, business_key: str) -> None:
    resp = config_client.post(PATH, json={"fileChanged": True, "fileContent": KEY_FILE, "adminEmail": ADMIN_EMAIL})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _stored(config_client) == STORED


def test_without_file_changed_the_stored_key_is_kept_and_only_the_settings_change(
    config_client: ConfigClient, business_key: str
) -> None:
    seeded = config_client.post(PATH, json={"fileChanged": True, "fileContent": KEY_FILE, "adminEmail": ADMIN_EMAIL})
    assert seeded.status_code == 200, seeded.text[:500]

    resp = config_client.post(PATH, json={"adminEmail": "spec-audit-other@example.com"})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _stored(config_client) == {**STORED, "adminEmail": "spec-audit-other@example.com"}


def test_without_file_changed_and_nothing_stored_is_refused(config_client: ConfigClient, business_key: str) -> None:
    forget_stored_config(business_key)

    resp = config_client.post(PATH, json={"adminEmail": ADMIN_EMAIL})

    assert resp.status_code == 400, resp.text[:500]
    error = resp.json()["error"]
    assert (error["code"], error["message"]) == ("HTTP_BAD_REQUEST", "File Not found")
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _stored(config_client) == {}


def test_fields_the_handler_does_not_read_are_not_stored(config_client: ConfigClient, business_key: str) -> None:
    with outside_request_contract("specAudit is not a field of the body or of the key file"):
        resp = config_client.post(
            PATH,
            json={
                "fileChanged": True,
                "fileContent": {**KEY_FILE, "specAudit": "x"},
                "adminEmail": ADMIN_EMAIL,
                "specAudit": "x",
            },
        )

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert _stored(config_client) == STORED


@pytest.mark.parametrize(
    ("body", "message"),
    [
        pytest.param({"fileChanged": True, "fileContent": KEY_FILE}, "Google Workspace Admin Email is required", id="missing-admin-email"),
        pytest.param(
            {"fileChanged": True, "fileContent": KEY_FILE, "adminEmail": ""},
            "Google Workspace Admin Email is required",
            id="empty-admin-email",
        ),
        pytest.param(
            {"fileChanged": True, "fileContent": KEY_FILE, "adminEmail": ADMIN_EMAIL, "enableRealTimeUpdates": True},
            "Topic name is required when real-time updates are enabled",
            id="real-time-without-topic",
        ),
        pytest.param(
            {"fileChanged": True, "adminEmail": ADMIN_EMAIL},
            "Google Workspace validation failed:\n  • Unknown field: Required  ",
            id="file-changed-without-file",
        ),
        pytest.param(
            {"fileChanged": "true", "fileContent": {k: v for k, v in KEY_FILE.items() if k != "private_key"}, "adminEmail": ADMIN_EMAIL},
            "Google Workspace validation failed:\n  • private_key: Required  ",
            id="key-without-private-key",
        ),
        pytest.param(
            {"fileChanged": True, "fileContent": {**KEY_FILE, "client_id": ""}, "adminEmail": ADMIN_EMAIL},
            "Google Workspace validation failed:\n  • client_id: Google workspace client ID is required  ",
            id="key-with-empty-client-id",
        ),
    ],
)
def test_invalid_json_body_is_refused_by_the_handler_and_nothing_is_stored(
    config_client: ConfigClient, business_key: str, body: dict[str, Any], message: str
) -> None:
    before = _stored(config_client)

    resp = config_client.post(PATH, json=body)

    assert resp.status_code == 400, resp.text[:500]
    error = resp.json()["error"]
    assert (error["code"], error["message"]) == ("HTTP_BAD_REQUEST", message)
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)
    assert _stored(config_client) == before


@pytest.mark.parametrize(
    ("files", "message"),
    [
        # Refused by the upload filter: only application/json is allowed.
        pytest.param(_upload(b"not json", "text/plain", "credentials.txt"), None, id="file-type-not-json"),
        pytest.param(_upload(b"{not json"), "Invalid JSON format in uploaded file", id="file-content-not-json"),
        pytest.param(
            _upload(json.dumps({"type": "service_account"}).encode()),
            None,
            id="key-file-incomplete",
        ),
    ],
)
def test_invalid_upload_is_refused_and_nothing_is_stored(
    config_client: ConfigClient, business_key: str, files: UploadFiles, message: str | None
) -> None:
    before = _stored(config_client)

    resp = config_client.post(PATH, files=files, data={"adminEmail": ADMIN_EMAIL, "fileChanged": "true"})

    assert resp.status_code == 400, resp.text[:500]
    if message:
        assert resp.json()["error"]["message"] == message
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _stored(config_client) == before


@pytest.mark.parametrize("headers", [None, INVALID_BEARER_HEADERS], ids=["no-token", "invalid-token"])
def test_upload_without_valid_token_is_unauthorized(config_client: ConfigClient, headers: dict[str, str] | None) -> None:
    resp = config_client.post(PATH, auth=False, headers=headers, files=_upload(b"{}"))

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_upload_as_member_is_forbidden(config_client: ConfigClient, second_user: SecondUser) -> None:
    before = _stored(config_client)

    resp = request_as(
        second_user, "POST", PATH, files=_upload(json.dumps(KEY_FILE).encode()), data={"adminEmail": ADMIN_EMAIL, "fileChanged": "true"}
    )

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _stored(config_client) == before


def test_an_admin_email_that_is_not_an_email_address_is_stored(config_client: ConfigClient, business_key: str) -> None:
    # API bug: adminEmail is only checked for being non-empty.
    resp = config_client.post(
        PATH,
        files=_upload(json.dumps(KEY_FILE).encode()),
        data={"adminEmail": "not-an-email", "fileChanged": "true", "enableRealTimeUpdates": "false"},
    )

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _stored(config_client) == {**STORED, "adminEmail": "not-an-email"}
