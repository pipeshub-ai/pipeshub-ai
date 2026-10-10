"""Strict OpenAPI audit of POST /api/v1/userAccount/initAuth."""

from __future__ import annotations

from typing import Any

import pytest
from helper.pipeshub_client import PipeshubClient
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract
from user_account_audit_support import (
    INIT_AUTH_ROUTE,
    OAUTH_SETTING,
    VALIDATION_ERROR,
    OAuthProviderStub,
    SignInPolicy,
    UserAccountAuditClient,
    configure_oauth,
    steps,
    unused_email,
    without_setting,
)

pytestmark = pytest.mark.spec_audit

ROUTE = INIT_AUTH_ROUTE

# What the public OAuth settings must never carry (stripped in initAuth).
OAUTH_SERVER_SIDE_FIELDS = {"clientSecret", "tokenEndpoint", "userInfoEndpoint"}
EVERY_METHOD = ["password", "otp", "google", "microsoft", "azureAd", "oauth", "samlSso", "github"]


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(None, id="no_body"),
        pytest.param({}, id="empty_object"),
        pytest.param({"email": "spec-audit-nobody@example.com"}, id="email_of_no_account"),
    ],
)
def test_init_auth_starts_a_session(
    user_account_audit_client: UserAccountAuditClient, body: dict[str, Any] | None
) -> None:
    resp = user_account_audit_client.init_auth(body)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    assert resp.headers.get("x-session-token")
    answer = resp.json()
    assert answer["currentStep"] == 0
    assert answer["message"] == "Authentication initialized"
    assert "password" in answer["allowedMethods"]


@pytest.mark.parametrize(
    ("body", "field"),
    [
        pytest.param({"email": "not-an-email"}, "body.email", id="malformed_email"),
        pytest.param({"email": ""}, "body.email", id="empty_email"),
        pytest.param({"email": None}, "body.email", id="null_email"),
        pytest.param({"email": 123}, "body.email", id="numeric_email"),
        pytest.param({"email": "a" * 250 + "@example.com"}, "body.email", id="email_over_254_chars"),
        pytest.param(["not", "an", "object"], "body", id="array_body"),
    ],
)
def test_init_auth_refuses_what_the_validator_refuses(
    user_account_audit_client: UserAccountAuditClient, body: Any, field: str
) -> None:
    resp = user_account_audit_client.init_auth(body)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    error = resp.json()["error"]
    assert error["code"] == VALIDATION_ERROR
    assert [e["field"] for e in error["metadata"]["errors"]] == [field]
    assert "x-session-token" not in resp.headers


def test_init_auth_ignores_unknown_fields_and_the_query_string(
    user_account_audit_client: UserAccountAuditClient,
) -> None:
    with outside_request_contract("the validator drops body fields and query keys it does not know"):
        resp = user_account_audit_client.init_auth(
            {"specAuditUnknown": True}, params={"specAuditUnknown": "1"}
        )
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.headers.get("x-session-token")


def test_init_auth_lists_every_method_whose_settings_can_be_read(
    user_account_audit_client: UserAccountAuditClient,
    sign_in_policy: SignInPolicy,
    oauth_provider: OAuthProviderStub,
) -> None:
    # A provider with nothing saved still answers its settings route, so it is listed
    # with an empty (or near-empty) entry. github has no settings route and is left out.
    with sign_in_policy.temporarily(steps(EVERY_METHOD)):
        resp = user_account_audit_client.init_auth()
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    answer = resp.json()
    assert answer["allowedMethods"] == [m for m in EVERY_METHOD if m != "github"]
    providers = answer["authProviders"]
    assert set(providers) == {"google", "microsoft", "azureAd", "oauth", "saml"}
    assert "spEntityId" in providers["saml"]

    oauth = providers["oauth"]
    assert not OAUTH_SERVER_SIDE_FIELDS & oauth.keys(), sorted(oauth)
    assert oauth == {
        key: value
        for key, value in oauth_provider.config().items()
        if key not in OAUTH_SERVER_SIDE_FIELDS
    }


def test_init_auth_lists_oauth_with_an_empty_entry_when_nothing_is_saved(
    user_account_audit_client: UserAccountAuditClient,
    sign_in_policy: SignInPolicy,
    oauth_provider: OAuthProviderStub,
) -> None:
    with without_setting(OAUTH_SETTING), sign_in_policy.temporarily(steps(["password", "oauth"])):
        resp = user_account_audit_client.init_auth()
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    answer = resp.json()
    assert answer["allowedMethods"] == ["password", "oauth"]
    assert answer["authProviders"] == {"oauth": {}}
    assert answer["jitEnabled"] is False


def test_init_auth_reports_jit_when_a_listed_provider_has_it_on(
    user_account_audit_client: UserAccountAuditClient,
    sign_in_policy: SignInPolicy,
    oauth_provider: OAuthProviderStub,
    pipeshub_client: PipeshubClient,
) -> None:
    policy = steps(["password", "oauth"])
    with sign_in_policy.temporarily(policy):
        off = user_account_audit_client.init_auth({"email": unused_email()})
    assert off.status_code == 200, off.text[:500]
    assert_strict_openapi_exchange(off, ROUTE)
    assert off.json()["jitEnabled"] is False

    # Only the fields the settings route requires: the public entry is then just these.
    configure_oauth(
        pipeshub_client,
        {"providerName": "spec-audit-oauth", "clientId": "spec-audit-client", "enableJit": True},
    )
    try:
        with sign_in_policy.temporarily(policy):
            on = user_account_audit_client.init_auth()
    finally:
        configure_oauth(pipeshub_client, oauth_provider.config())
    assert on.status_code == 200, on.text[:500]
    assert_strict_openapi_exchange(on, ROUTE)
    answer = on.json()
    assert answer["jitEnabled"] is True
    assert answer["authProviders"]["oauth"] == {
        "providerName": "spec-audit-oauth",
        "clientId": "spec-audit-client",
        "enableJit": True,
    }
