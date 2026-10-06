"""Strict OpenAPI audit of POST /api/v1/configurationManager/authConfig/sso.

Chain: authenticate -> requireScopes(config:write) -> userAdminCheck -> zod body -> setSsoAuthConfig.
The route only creates or replaces; ``guard_saved_config`` puts the stored state back. The
SAML strategy the Node process rebuilds from each saved config has no "unset": after these
tests it holds the last config posted (the restored one when there was one to restore).
"""

from __future__ import annotations

from typing import Any

import pytest
from configuration_manager_audit_support import (
    INVALID_BEARER_HEADERS,
    KV_AUTH_SSO,
    SSO_CERTIFICATE_BODY,
    SSO_CERTIFICATE_PEM,
    SSO_DERIVED_FIELDS,
    SSO_VALID_BODY,
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

ROUTE = "/api/v1/configurationManager/authConfig/sso"
PATH = "/authConfig/sso"
SAVED_BODY = {"message": "Sso config created successfully"}

REQUIRED_ONLY: dict[str, Any] = {
    key: SSO_VALID_BODY[key] for key in ("entryPoint", "certificate", "emailKey")
}


def _stored(config_client: ConfigClient) -> dict[str, Any]:
    """The saved config without the field GET adds on every read."""
    resp = config_client.get(PATH)
    assert resp.status_code == 200, resp.text[:500]
    body: dict[str, Any] = resp.json()
    assert isinstance(body.pop("spEntityId"), str)
    return body


def test_set_sso_config_stores_the_certificate_without_its_pem_wrapping(
    config_client: ConfigClient, guard_saved_config: GuardSavedConfig
) -> None:
    guard_saved_config(PATH, KV_AUTH_SSO, SSO_DERIVED_FIELDS)
    assert SSO_VALID_BODY["certificate"] == SSO_CERTIFICATE_PEM

    resp = config_client.post(PATH, json=SSO_VALID_BODY)

    assert resp.status_code == 200, resp.text[:500]
    assert resp.json() == SAVED_BODY
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _stored(config_client) == {**SSO_VALID_BODY, "certificate": SSO_CERTIFICATE_BODY}


def test_set_sso_config_with_only_the_required_fields_enables_jit(
    config_client: ConfigClient, guard_saved_config: GuardSavedConfig
) -> None:
    guard_saved_config(PATH, KV_AUTH_SSO, SSO_DERIVED_FIELDS)

    resp = config_client.post(PATH, json=REQUIRED_ONLY)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    # No samlPlatform key at all when the request left it out.
    assert _stored(config_client) == {
        **REQUIRED_ONLY,
        "certificate": SSO_CERTIFICATE_BODY,
        "enableJit": True,
    }


def test_set_sso_config_drops_what_the_validator_does_not_know(
    config_client: ConfigClient, guard_saved_config: GuardSavedConfig
) -> None:
    guard_saved_config(PATH, KV_AUTH_SSO, SSO_DERIVED_FIELDS)
    sent = {**SSO_VALID_BODY, "spEntityId": "https://spec-audit.invalid/sp", "specAudit": "x"}

    with outside_request_contract("spEntityId and specAudit are not fields of the body"):
        resp = config_client.post(PATH, json=sent)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    read = config_client.get(PATH)
    assert read.status_code == 200, read.text[:500]
    assert "specAudit" not in read.json()
    # spEntityId comes from the server's environment, never from the caller.
    assert read.json()["spEntityId"] != sent["spEntityId"]


def test_set_sso_config_accepts_a_certificate_that_is_empty_once_cleaned(
    config_client: ConfigClient, guard_saved_config: GuardSavedConfig
) -> None:
    # API bug: "certificate is required" is checked on the text as sent, and the PEM
    # markers are removed after it, so an empty certificate is stored.
    guard_saved_config(PATH, KV_AUTH_SSO, SSO_DERIVED_FIELDS)
    markers_only = "-----BEGIN CERTIFICATE-----\n-----END CERTIFICATE-----\n"

    resp = config_client.post(PATH, json={**REQUIRED_ONLY, "certificate": markers_only})

    assert resp.status_code == 200, resp.text[:500]
    assert resp.json() == SAVED_BODY
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _stored(config_client)["certificate"] == ""


@pytest.mark.parametrize(
    "headers",
    [None, INVALID_BEARER_HEADERS],
    ids=["no-token", "invalid-token"],
)
def test_set_sso_config_without_valid_token_is_unauthorized(
    config_client: ConfigClient, headers: dict[str, str] | None
) -> None:
    resp = config_client.post(PATH, auth=False, headers=headers, json=SSO_VALID_BODY)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_set_sso_config_as_member_is_forbidden(
    config_client: ConfigClient, second_user: SecondUser
) -> None:
    before = config_client.get(PATH)
    assert before.status_code == 200, before.text[:500]

    # A valid body, so the 403 can only come from userAdminCheck, which runs before zod.
    resp = request_as(second_user, "POST", PATH, json=SSO_VALID_BODY)

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert config_client.get(PATH).json() == before.json()


@pytest.mark.parametrize(
    ("body", "fields"),
    [
        pytest.param({}, ["body.entryPoint", "body.certificate", "body.emailKey"], id="empty-object"),
        *(
            pytest.param(
                {k: v for k, v in SSO_VALID_BODY.items() if k != field},
                [f"body.{field}"],
                id=f"missing-{field}",
            )
            for field in ("entryPoint", "certificate", "emailKey")
        ),
        pytest.param(
            {**SSO_VALID_BODY, "entryPoint": "", "certificate": "", "emailKey": ""},
            ["body.entryPoint", "body.certificate", "body.emailKey"],
            id="empty-required-strings",
        ),
        pytest.param({**SSO_VALID_BODY, "certificate": 42}, ["body.certificate"], id="certificate-not-a-string"),
        # Every string is trimmed before the validator sees it.
        pytest.param({**SSO_VALID_BODY, "certificate": " \n "}, ["body.certificate"], id="certificate-only-whitespace"),
        pytest.param({**SSO_VALID_BODY, "enableJit": "true"}, ["body.enableJit"], id="enable-jit-as-string"),
        pytest.param({**SSO_VALID_BODY, "samlPlatform": 42}, ["body.samlPlatform"], id="saml-platform-not-a-string"),
        pytest.param([SSO_VALID_BODY], ["body"], id="body-is-a-list"),
    ],
)
def test_set_sso_config_invalid_body_is_rejected_and_nothing_is_stored(
    config_client: ConfigClient, body: Any, fields: list[str]
) -> None:
    before = config_client.get(PATH)
    assert before.status_code == 200, before.text[:500]

    resp = config_client.post(PATH, json=body)

    assert_validation_error(resp, *fields)
    assert_strict_openapi_exchange(resp, ROUTE)
    assert config_client.get(PATH).json() == before.json()


def test_set_sso_config_without_a_body_is_rejected_like_an_empty_one(
    config_client: ConfigClient,
) -> None:
    resp = config_client.post(PATH)

    assert_validation_error(resp, "body.entryPoint", "body.certificate", "body.emailKey")
    assert_strict_openapi_exchange(resp, ROUTE)
