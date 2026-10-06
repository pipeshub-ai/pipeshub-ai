"""The request-wide HTML filter, as seen on /api/v1/configurationManager routes.

``xssSanitizationMiddleware`` is mounted right after the body parsers, before the router:
it refuses any request whose query string or JSON body holds a string that looks like
markup or script, and trims every string of the ones it lets through. Both happen before
the token check and before the route's own validator.
"""

from __future__ import annotations

from typing import Any

import pytest
from configuration_manager_audit_support import (
    CONFIGURATION_MANAGER_BASE,
    PRE_ROUTER_OPERATIONS,
    SSO_VALID_BODY,
    assert_validation_error,
)
from helper.clients.config_client import ConfigClient
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

HTML_REFUSED_MESSAGE = (
    "HTML tags, scripts, and XSS content are not allowed. Please remove any HTML tags and try again."
)
POST_SUB_PATHS = [sub_path for method, sub_path in PRE_ROUTER_OPERATIONS if method == "POST"]

_MICROSOFT_APP = {
    "clientId": "spec-audit-client-id",
    "clientSecret": "spec-audit-client-secret",
    "tenantId": "spec-audit-tenant-id",
    "hasAdminConsent": True,
}
# For each POST: a body its validator accepts, and the fields it requires to be non-empty text.
NON_EMPTY_TEXT: dict[str, tuple[dict[str, Any], tuple[str, ...]]] = {
    "/storageConfig": (
        {
            "storageType": "azureBlob",
            "accountName": "specauditaccount",
            "accountKey": "c3BlYy1hdWRpdA==",
            "endpointSuffix": "core.windows.net",
            "containerName": "spec-audit",
        },
        ("accountName", "accountKey", "endpointSuffix", "containerName"),
    ),
    "/smtpConfig": (
        {"host": "smtp.spec-audit.invalid", "port": 587, "fromEmail": "spec-audit@example.com"},
        ("host", "fromEmail"),
    ),
    "/connectors/atlassian/config": (
        {"clientId": "spec-audit-client-id", "clientSecret": "spec-audit-client-secret"},
        ("clientId", "clientSecret"),
    ),
    "/connectors/onedrive/config": (_MICROSOFT_APP, ("clientId", "clientSecret", "tenantId")),
    "/connectors/sharepoint/config": (
        {**_MICROSOFT_APP, "sharepointDomain": "spec-audit.sharepoint.invalid"},
        ("clientId", "clientSecret", "tenantId", "sharepointDomain"),
    ),
    "/authConfig/azureAd": ({"clientId": "spec-audit-client-id"}, ("clientId",)),
    "/authConfig/microsoft": ({"clientId": "spec-audit-client-id"}, ("clientId",)),
    "/authConfig/google": ({"clientId": "spec-audit-client-id"}, ("clientId",)),
    "/authConfig/sso": (SSO_VALID_BODY, ("entryPoint", "certificate", "emailKey")),
    "/authConfig/oauth": (
        {"providerName": "spec-audit", "clientId": "spec-audit-client-id"},
        ("providerName", "clientId"),
    ),
}
S3_BODY = {"storageType": "s3", "s3Region": "us-east-1", "s3BucketName": "spec-audit-bucket"}
BLANK_CASES = [
    *((sub_path, body, field) for sub_path, (body, fields) in NON_EMPTY_TEXT.items() for field in fields),
    ("/storageConfig", S3_BODY, "s3Region"),
    ("/storageConfig", S3_BODY, "s3BucketName"),
]


def _send(config_client: ConfigClient, method: str, sub_path: str, **kwargs: Any) -> Any:
    send = config_client.get if method == "GET" else config_client.post
    return send(sub_path, auth=False, **kwargs)


@pytest.mark.parametrize(
    ("method", "sub_path"),
    PRE_ROUTER_OPERATIONS,
    ids=[f"{method} {sub_path}" for method, sub_path in PRE_ROUTER_OPERATIONS],
)
@pytest.mark.parametrize(
    "value",
    [
        pytest.param("<b>spec audit</b>", id="html-tag"),
        pytest.param("javascript:alert(1)", id="javascript-url"),
    ],
)
def test_markup_in_the_query_string_is_refused_before_the_token_check(
    config_client: ConfigClient, method: str, sub_path: str, value: str
) -> None:
    resp = _send(config_client, method, sub_path, params={"specAudit": value})

    assert resp.status_code == 400, resp.text[:500]
    error = resp.json()["error"]
    assert (error["code"], error["message"]) == ("HTTP_BAD_REQUEST", HTML_REFUSED_MESSAGE)
    assert_strict_openapi_exchange(resp, f"{CONFIGURATION_MANAGER_BASE}{sub_path}")


@pytest.mark.parametrize("sub_path", POST_SUB_PATHS)
def test_markup_in_the_body_is_refused_before_the_token_check(
    config_client: ConfigClient, sub_path: str
) -> None:
    # Any string anywhere in the body counts, in a field the route knows or not.
    resp = _send(config_client, "POST", sub_path, json={"nested": {"specAudit": ["<i>spec audit</i>"]}})

    assert resp.status_code == 400, resp.text[:500]
    error = resp.json()["error"]
    assert (error["code"], error["message"]) == ("HTTP_BAD_REQUEST", HTML_REFUSED_MESSAGE)
    assert_strict_openapi_exchange(resp, f"{CONFIGURATION_MANAGER_BASE}{sub_path}")


def test_a_certificate_whose_last_line_reads_like_an_event_handler_is_refused(
    config_client: ConfigClient,
) -> None:
    # API bug: base64 that starts a line with "on" and ends in "=" matches the filter's
    # on<name>= pattern, so a valid PEM certificate can be turned away as script.
    certificate = (
        "-----BEGIN CERTIFICATE-----\nMIIBspecAuditNotARealCertificate\nonSpecAuditPadding0=\n-----END CERTIFICATE-----"
    )

    resp = config_client.post("/authConfig/sso", json={**SSO_VALID_BODY, "certificate": certificate})

    assert resp.status_code == 400, resp.text[:500]
    error = resp.json()["error"]
    assert (error["code"], error["message"]) == ("HTTP_BAD_REQUEST", HTML_REFUSED_MESSAGE)
    assert_strict_openapi_exchange(resp, f"{CONFIGURATION_MANAGER_BASE}/authConfig/sso")


@pytest.mark.parametrize(
    ("sub_path", "body", "field"),
    BLANK_CASES,
    ids=[f"{sub_path} {field}" for sub_path, _, field in BLANK_CASES],
)
def test_text_of_only_whitespace_is_trimmed_to_nothing_before_validation(
    config_client: ConfigClient, sub_path: str, body: dict[str, Any], field: str
) -> None:
    # Nothing is stored: the trimmed value fails the route's "must not be empty" check.
    resp = config_client.post(sub_path, json={**body, field: " \t\n "})

    assert_validation_error(resp, f"body.{field}")
    assert_strict_openapi_exchange(resp, f"{CONFIGURATION_MANAGER_BASE}{sub_path}")


def test_surrounding_whitespace_is_removed_from_what_is_stored(
    config_client: ConfigClient, guard_saved_config: Any
) -> None:
    from configuration_manager_audit_support import KV_AUTH_GOOGLE  # noqa: PLC0415

    guard_saved_config("/authConfig/google", KV_AUTH_GOOGLE)

    resp = config_client.post("/authConfig/google", json={"clientId": "  spec-audit-client-id \n"})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, f"{CONFIGURATION_MANAGER_BASE}/authConfig/google")
    stored = config_client.get("/authConfig/google")
    assert stored.status_code == 200, stored.text[:500]
    assert stored.json()["clientId"] == "spec-audit-client-id"
