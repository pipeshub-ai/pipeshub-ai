"""Strict OpenAPI audit of POST /api/v1/configurationManager/authConfig/microsoft.

Chain: authenticate -> requireScopes(config:write) -> userAdminCheck -> zod body -> setMicrosoftAuthConfig (validated with azureAdConfigSchema).
The route only creates or replaces; ``guard_saved_config`` puts the previous state back.
"""

from __future__ import annotations

from typing import Any

import pytest
from configuration_manager_audit_support import (
    INVALID_BEARER_HEADERS,
    KV_AUTH_MICROSOFT,
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

ROUTE = "/api/v1/configurationManager/authConfig/microsoft"
PATH = "/authConfig/microsoft"
SAVED_BODY = {"message": "Microsoft Auth config created successfully"}
# GET adds nothing, but ``authority`` is computed by the POST handler from tenantId.
DERIVED = ("authority",)

CLIENT_ID = "00000000-0000-4000-8000-0000000000aa"
VALID_BODY: dict[str, Any] = {
    "clientId": CLIENT_ID,
    "tenantId": "spec-audit-tenant",
    "enableJit": False,
}


def test_set_microsoft_auth_config_stores_client_tenant_and_the_authority_built_from_it(
    config_client: ConfigClient, guard_saved_config: GuardSavedConfig
) -> None:
    guard_saved_config(PATH, KV_AUTH_MICROSOFT, DERIVED)

    resp = config_client.post(PATH, json=VALID_BODY)

    assert resp.status_code == 200, resp.text[:500]
    assert resp.json() == SAVED_BODY
    assert_strict_openapi_exchange(resp, ROUTE)
    stored = config_client.get(PATH)
    assert stored.status_code == 200, stored.text[:500]
    assert stored.json() == {
        **VALID_BODY,
        "authority": "https://login.microsoftonline.com/spec-audit-tenant",
    }


def test_set_microsoft_auth_config_with_only_a_client_id_gets_the_defaults(
    config_client: ConfigClient, guard_saved_config: GuardSavedConfig
) -> None:
    guard_saved_config(PATH, KV_AUTH_MICROSOFT, DERIVED)

    resp = config_client.post(PATH, json={"clientId": CLIENT_ID})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    stored = config_client.get(PATH)
    assert stored.status_code == 200, stored.text[:500]
    assert stored.json() == {
        "clientId": CLIENT_ID,
        "tenantId": "common",
        "authority": "https://login.microsoftonline.com/common",
        "enableJit": True,
    }


def test_set_microsoft_auth_config_drops_what_the_validator_does_not_know(
    config_client: ConfigClient, guard_saved_config: GuardSavedConfig
) -> None:
    guard_saved_config(PATH, KV_AUTH_MICROSOFT, DERIVED)
    sent = {**VALID_BODY, "authority": "https://spec-audit.invalid/authority", "specAudit": "x"}

    with outside_request_contract("authority and specAudit are not fields of the body"):
        resp = config_client.post(PATH, json=sent)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    stored = config_client.get(PATH)
    assert stored.status_code == 200, stored.text[:500]
    # The caller's authority is ignored: it is always rebuilt from tenantId.
    assert stored.json() == {
        **VALID_BODY,
        "authority": "https://login.microsoftonline.com/spec-audit-tenant",
    }


@pytest.mark.parametrize(
    "headers",
    [None, INVALID_BEARER_HEADERS],
    ids=["no-token", "invalid-token"],
)
def test_set_microsoft_auth_config_without_valid_token_is_unauthorized(
    config_client: ConfigClient, headers: dict[str, str] | None
) -> None:
    resp = config_client.post(PATH, auth=False, headers=headers, json=VALID_BODY)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_set_microsoft_auth_config_as_member_is_forbidden(
    config_client: ConfigClient, second_user: SecondUser
) -> None:
    before = config_client.get(PATH)
    assert before.status_code == 200, before.text[:500]

    # A valid body, so the 403 can only come from userAdminCheck, which runs before zod.
    resp = request_as(second_user, "POST", PATH, json=VALID_BODY)

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert config_client.get(PATH).json() == before.json()


@pytest.mark.parametrize(
    ("body", "fields"),
    [
        pytest.param({}, ["body.clientId"], id="empty-object"),
        pytest.param({"tenantId": "common"}, ["body.clientId"], id="missing-client-id"),
        pytest.param({**VALID_BODY, "clientId": ""}, ["body.clientId"], id="empty-client-id"),
        pytest.param({**VALID_BODY, "clientId": 42}, ["body.clientId"], id="client-id-not-a-string"),
        pytest.param({**VALID_BODY, "tenantId": 42}, ["body.tenantId"], id="tenant-id-not-a-string"),
        # Optional means "may be left out": an explicit null is refused.
        pytest.param({**VALID_BODY, "tenantId": None}, ["body.tenantId"], id="tenant-id-null"),
        pytest.param({**VALID_BODY, "enableJit": "true"}, ["body.enableJit"], id="enable-jit-as-string"),
        pytest.param([VALID_BODY], ["body"], id="body-is-a-list"),
    ],
)
def test_set_microsoft_auth_config_invalid_body_is_rejected_and_nothing_is_stored(
    config_client: ConfigClient, body: Any, fields: list[str]
) -> None:
    before = config_client.get(PATH)
    assert before.status_code == 200, before.text[:500]

    resp = config_client.post(PATH, json=body)

    assert_validation_error(resp, *fields)
    assert_strict_openapi_exchange(resp, ROUTE)
    assert config_client.get(PATH).json() == before.json()


def test_set_microsoft_auth_config_without_a_body_is_rejected_like_an_empty_one(config_client: ConfigClient) -> None:
    resp = config_client.post(PATH)

    assert_validation_error(resp, "body.clientId")
    assert_strict_openapi_exchange(resp, ROUTE)
