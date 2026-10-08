"""Strict OpenAPI audit of POST /api/v1/configurationManager/authConfig/oauth.

Chain: authenticate -> requireScopes(config:write) -> userAdminCheck -> zod body -> setOAuthConfig.
The route only creates or replaces; ``guard_saved_config`` puts the previous state back.
"""

from __future__ import annotations

from typing import Any

import pytest
from configuration_manager_audit_support import (
    INVALID_BEARER_HEADERS,
    KV_AUTH_OAUTH,
    GuardSavedConfig,
    assert_validation_error,
    request_as,
)
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import (
    assert_strict_openapi_exchange,
    assert_strict_openapi_response,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/authConfig/oauth"
PATH = "/authConfig/oauth"
SAVED_BODY = {"message": "OAuth config created successfully"}

REQUIRED_ONLY: dict[str, Any] = {"providerName": "spec-audit", "clientId": "spec-audit-client-id"}
URL_FIELDS = ("authorizationUrl", "tokenEndpoint", "userInfoEndpoint", "redirectUri")
FULL_BODY: dict[str, Any] = {
    **REQUIRED_ONLY,
    "clientSecret": "spec-audit-client-secret",
    "authorizationUrl": "https://idp.spec-audit.invalid/authorize",
    "tokenEndpoint": "https://idp.spec-audit.invalid/token",
    "userInfoEndpoint": "https://idp.spec-audit.invalid/userinfo",
    "scope": "openid profile email",
    "redirectUri": "https://app.spec-audit.invalid/auth/oauth/callback",
    "enableJit": False,
}


def test_set_oauth_config_stores_every_field_secret_included(
    config_client: ConfigClient, guard_saved_config: GuardSavedConfig
) -> None:
    guard_saved_config(PATH, KV_AUTH_OAUTH)

    resp = config_client.post(PATH, json=FULL_BODY)

    assert resp.status_code == 200, resp.text[:500]
    assert resp.json() == SAVED_BODY
    assert_strict_openapi_exchange(resp, ROUTE)
    stored = config_client.get(PATH)
    assert stored.status_code == 200, stored.text[:500]
    assert stored.json() == FULL_BODY


def test_set_oauth_config_with_only_the_required_fields_enables_jit(
    config_client: ConfigClient, guard_saved_config: GuardSavedConfig
) -> None:
    guard_saved_config(PATH, KV_AUTH_OAUTH)

    resp = config_client.post(PATH, json=REQUIRED_ONLY)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    stored = config_client.get(PATH)
    assert stored.status_code == 200, stored.text[:500]
    assert stored.json() == {**REQUIRED_ONLY, "enableJit": True}


def test_set_oauth_config_treats_empty_optional_strings_as_not_set(
    config_client: ConfigClient, guard_saved_config: GuardSavedConfig
) -> None:
    guard_saved_config(PATH, KV_AUTH_OAUTH)
    # The replace is whole: nothing of this first config may survive the second POST.
    first = config_client.post(PATH, json=FULL_BODY)
    assert first.status_code == 200, first.text[:500]
    blanks = dict.fromkeys(("clientSecret", "scope", *URL_FIELDS), "")

    resp = config_client.post(PATH, json={**REQUIRED_ONLY, **blanks})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    stored = config_client.get(PATH)
    assert stored.status_code == 200, stored.text[:500]
    assert stored.json() == {**REQUIRED_ONLY, "enableJit": True}


def test_set_oauth_config_drops_what_the_validator_does_not_know(
    config_client: ConfigClient, guard_saved_config: GuardSavedConfig
) -> None:
    guard_saved_config(PATH, KV_AUTH_OAUTH)

    with outside_request_contract("specAudit is not a field of the body; the validator strips it"):
        resp = config_client.post(PATH, json={**REQUIRED_ONLY, "specAudit": "x"})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    stored = config_client.get(PATH)
    assert stored.status_code == 200, stored.text[:500]
    assert stored.json() == {**REQUIRED_ONLY, "enableJit": True}


@pytest.mark.parametrize(
    "headers",
    [None, INVALID_BEARER_HEADERS],
    ids=["no-token", "invalid-token"],
)
def test_set_oauth_config_without_valid_token_is_unauthorized(
    config_client: ConfigClient, headers: dict[str, str] | None
) -> None:
    resp = config_client.post(PATH, auth=False, headers=headers, json=FULL_BODY)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_set_oauth_config_as_member_is_forbidden(
    config_client: ConfigClient, second_user: SecondUser
) -> None:
    before = config_client.get(PATH)
    assert before.status_code == 200, before.text[:500]

    # A valid body, so the 403 can only come from userAdminCheck, which runs before zod.
    resp = request_as(second_user, "POST", PATH, json=FULL_BODY)

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert config_client.get(PATH).json() == before.json()


@pytest.mark.parametrize(
    ("body", "fields"),
    [
        pytest.param({}, ["body.providerName", "body.clientId"], id="empty-object"),
        pytest.param({"providerName": "spec-audit"}, ["body.clientId"], id="missing-client-id"),
        pytest.param({"clientId": "spec-audit-client-id"}, ["body.providerName"], id="missing-provider-name"),
        pytest.param(
            {"providerName": "", "clientId": ""},
            ["body.providerName", "body.clientId"],
            id="empty-required-strings",
        ),
        *(
            pytest.param({**FULL_BODY, field: "not-a-url"}, [f"body.{field}"], id=f"{field}-not-a-url")
            for field in URL_FIELDS
        ),
        pytest.param({**FULL_BODY, "scope": 42}, ["body.scope"], id="scope-not-a-string"),
        pytest.param({**FULL_BODY, "clientSecret": 42}, ["body.clientSecret"], id="client-secret-not-a-string"),
        pytest.param({**FULL_BODY, "enableJit": "true"}, ["body.enableJit"], id="enable-jit-as-string"),
        pytest.param([FULL_BODY], ["body"], id="body-is-a-list"),
    ],
)
def test_set_oauth_config_invalid_body_is_rejected_and_nothing_is_stored(
    config_client: ConfigClient, body: Any, fields: list[str]
) -> None:
    before = config_client.get(PATH)
    assert before.status_code == 200, before.text[:500]

    resp = config_client.post(PATH, json=body)

    assert_validation_error(resp, *fields)
    assert_strict_openapi_exchange(resp, ROUTE)
    assert config_client.get(PATH).json() == before.json()


def test_set_oauth_config_without_a_body_is_rejected_like_an_empty_one(
    config_client: ConfigClient,
) -> None:
    resp = config_client.post(PATH)

    assert_validation_error(resp, "body.providerName", "body.clientId")
    assert_strict_openapi_exchange(resp, ROUTE)
