"""Strict OpenAPI audit of POST /api/v1/configurationManager/connectors/atlassian/config.

Chain: authenticate -> requireScopes(config:write) -> userAdminCheck -> zod body -> setAtlassianOauthConfig.
The route only creates or replaces; ``guard_saved_config`` puts the previous state back.
"""

from __future__ import annotations

from typing import Any

import pytest
from configuration_manager_audit_support import (
    INVALID_BEARER_HEADERS,
    KV_CONNECTOR_ATLASSIAN,
    GuardSavedConfig,
    assert_validation_error,
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

ROUTE = "/api/v1/configurationManager/connectors/atlassian/config"
PATH = "/connectors/atlassian/config"
REQUIRED_FIELDS = ["body.clientId", "body.clientSecret"]
SAVED_BODY = {"message": "Atlassian config created successfully"}

VALID_BODY: dict[str, Any] = {
    "clientId": "spec-audit-client-id",
    "clientSecret": "spec-audit-client-secret",
}


def _without(*keys: str) -> dict[str, Any]:
    return {k: v for k, v in VALID_BODY.items() if k not in keys}


def test_set_atlassian_config_stores_the_body_for_the_callers_org(
    config_client: ConfigClient,
    pipeshub_client: PipeshubClient,
    guard_saved_config: GuardSavedConfig,
) -> None:
    guard_saved_config(PATH, f"{KV_CONNECTOR_ATLASSIAN}/{pipeshub_client.org_id}")

    resp = config_client.post(PATH, json=VALID_BODY)

    assert resp.status_code == 200, resp.text[:500]
    assert resp.json() == SAVED_BODY
    assert_strict_openapi_exchange(resp, ROUTE)
    stored = config_client.get(PATH)
    assert stored.status_code == 200, stored.text[:500]
    assert stored.json() == VALID_BODY


def test_set_atlassian_config_replaces_the_previous_one_and_drops_unknown_fields(
    config_client: ConfigClient,
    pipeshub_client: PipeshubClient,
    guard_saved_config: GuardSavedConfig,
) -> None:
    guard_saved_config(PATH, f"{KV_CONNECTOR_ATLASSIAN}/{pipeshub_client.org_id}")
    first = config_client.post(PATH, json=VALID_BODY)
    assert first.status_code == 200, first.text[:500]
    replacement = {**VALID_BODY, "clientId": "spec-audit-second-client-id"}

    with outside_request_contract("specAudit is not a field of the body; the validator strips it"):
        resp = config_client.post(PATH, json={**replacement, "specAudit": "not stored"})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    stored = config_client.get(PATH)
    assert stored.status_code == 200, stored.text[:500]
    assert stored.json() == replacement


@pytest.mark.parametrize(
    "headers",
    [None, INVALID_BEARER_HEADERS],
    ids=["no-token", "invalid-token"],
)
def test_set_atlassian_config_without_valid_token_is_unauthorized(
    config_client: ConfigClient, headers: dict[str, str] | None
) -> None:
    resp = config_client.post(PATH, auth=False, headers=headers, json=VALID_BODY)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_set_atlassian_config_as_member_is_forbidden(
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
        pytest.param({}, ["body.clientId", "body.clientSecret"], id="empty-object"),
        pytest.param(_without("clientSecret"), ["body.clientSecret"], id="missing-client-secret"),
        pytest.param({**VALID_BODY, "clientId": ""}, ["body.clientId"], id="empty-client-id"),
        pytest.param({**VALID_BODY, "clientSecret": ""}, ["body.clientSecret"], id="empty-client-secret"),
        pytest.param({**VALID_BODY, "clientId": 42}, ["body.clientId"], id="client-id-not-a-string"),
        pytest.param({**VALID_BODY, "clientSecret": None}, ["body.clientSecret"], id="client-secret-null"),
        pytest.param([VALID_BODY], ["body"], id="body-is-a-list"),
    ],
)
def test_set_atlassian_config_invalid_body_is_rejected_and_nothing_is_stored(
    config_client: ConfigClient, body: Any, fields: list[str]
) -> None:
    before = config_client.get(PATH)
    assert before.status_code == 200, before.text[:500]

    resp = config_client.post(PATH, json=body)

    assert_validation_error(resp, *fields)
    assert_strict_openapi_exchange(resp, ROUTE)
    assert config_client.get(PATH).json() == before.json()


def test_set_atlassian_config_without_a_body_is_rejected_like_an_empty_one(config_client: ConfigClient) -> None:
    resp = config_client.post(PATH)

    assert_validation_error(resp, *REQUIRED_FIELDS)
    assert_strict_openapi_exchange(resp, ROUTE)
