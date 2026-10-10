"""Strict OpenAPI audit of POST /api/v1/configurationManager/connectors/googleWorkspaceOauthConfig.

Chain: authenticate -> requireScopes(config:write) -> userAdminCheck -> zod body -> handler.
The success cases keep real-time updates off: turning them on publishes a Gmail event to the
connector service. The stored key is put back byte for byte afterwards.
"""

from __future__ import annotations

from typing import Any

import pytest
from configuration_manager_audit_support import (
    INVALID_BEARER_HEADERS,
    KV_GOOGLE_WORKSPACE_OAUTH,
    GuardStoredValue,
    assert_validation_error,
    request_as,
)
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    assert_strict_openapi_response,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/connectors/googleWorkspaceOauthConfig"
PATH = "/connectors/googleWorkspaceOauthConfig"
SAVED_BODY = {"message": "Google Workspace credentials created successfully"}
TOPIC_REQUIRED = "Topic name is required when real-time updates are enabled"

VALID_BODY: dict[str, Any] = {
    "clientId": "spec-audit-client-id.apps.googleusercontent.com",
    "clientSecret": "spec-audit-client-secret",
    "enableRealTimeUpdates": False,
}


@pytest.mark.parametrize(
    ("body", "stored"),
    [
        pytest.param(VALID_BODY, {**VALID_BODY, "topicName": ""}, id="real-time-off"),
        pytest.param(
            {k: v for k, v in VALID_BODY.items() if k != "enableRealTimeUpdates"},
            {**VALID_BODY, "topicName": ""},
            id="real-time-left-out",
        ),
        # Any string other than "true" (in any case) means off.
        pytest.param({**VALID_BODY, "enableRealTimeUpdates": "False"}, {**VALID_BODY, "topicName": ""}, id="string-false"),
        pytest.param({**VALID_BODY, "enableRealTimeUpdates": "no"}, {**VALID_BODY, "topicName": ""}, id="string-no"),
        # The topic is only kept when real-time updates are on.
        pytest.param({**VALID_BODY, "topicName": "projects/p/topics/t"}, {**VALID_BODY, "topicName": ""}, id="topic-dropped"),
    ],
)
def test_save_stores_the_client_with_real_time_updates_off(
    config_client: ConfigClient,
    guard_stored_value: GuardStoredValue,
    body: dict[str, Any],
    stored: dict[str, Any],
) -> None:
    guard_stored_value(KV_GOOGLE_WORKSPACE_OAUTH)

    resp = config_client.post(PATH, json=body)

    assert resp.status_code == 200, resp.text[:500]
    assert resp.json() == SAVED_BODY
    assert_strict_openapi_exchange(resp, ROUTE)
    read = config_client.get(PATH)
    assert read.status_code == 200, read.text[:500]
    assert read.json() == stored


def test_save_drops_fields_the_validator_does_not_know(
    config_client: ConfigClient, guard_stored_value: GuardStoredValue
) -> None:
    guard_stored_value(KV_GOOGLE_WORKSPACE_OAUTH)

    with outside_request_contract("specAudit is not a field of the body"):
        resp = config_client.post(PATH, json={**VALID_BODY, "specAudit": "x"})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert config_client.get(PATH).json() == {**VALID_BODY, "topicName": ""}


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({**VALID_BODY, "enableRealTimeUpdates": True}, id="true-without-topic"),
        pytest.param({**VALID_BODY, "enableRealTimeUpdates": "TRUE"}, id="string-true-without-topic"),
        pytest.param({**VALID_BODY, "enableRealTimeUpdates": True, "topicName": ""}, id="true-with-empty-topic"),
    ],
)
def test_real_time_updates_without_a_topic_are_refused_by_the_handler(
    config_client: ConfigClient, guard_stored_value: GuardStoredValue, body: dict[str, Any]
) -> None:
    guard_stored_value(KV_GOOGLE_WORKSPACE_OAUTH)
    before = config_client.get(PATH).json()

    resp = config_client.post(PATH, json=body)

    assert resp.status_code == 400, resp.text[:500]
    error = resp.json()["error"]
    assert (error["code"], error["message"]) == ("HTTP_BAD_REQUEST", TOPIC_REQUIRED)
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)
    assert config_client.get(PATH).json() == before


@pytest.mark.parametrize(
    ("body", "fields"),
    [
        pytest.param({}, ["body.clientId", "body.clientSecret"], id="empty-object"),
        pytest.param({k: v for k, v in VALID_BODY.items() if k != "clientSecret"}, ["body.clientSecret"], id="missing-client-secret"),
        pytest.param({**VALID_BODY, "clientId": ""}, ["body.clientId"], id="empty-client-id"),
        pytest.param({**VALID_BODY, "clientSecret": "  "}, ["body.clientSecret"], id="whitespace-client-secret"),
        pytest.param({**VALID_BODY, "enableRealTimeUpdates": 1}, ["body.enableRealTimeUpdates"], id="real-time-a-number"),
        pytest.param({**VALID_BODY, "enableRealTimeUpdates": None}, ["body.enableRealTimeUpdates"], id="real-time-null"),
        pytest.param({**VALID_BODY, "topicName": 7}, ["body.topicName"], id="topic-not-a-string"),
    ],
)
def test_invalid_body_is_rejected_and_nothing_is_stored(
    config_client: ConfigClient, body: dict[str, Any], fields: list[str]
) -> None:
    before = config_client.get(PATH).json()

    resp = config_client.post(PATH, json=body)

    assert_validation_error(resp, *fields)
    assert_strict_openapi_exchange(resp, ROUTE)
    assert config_client.get(PATH).json() == before


@pytest.mark.parametrize("headers", [None, INVALID_BEARER_HEADERS], ids=["no-token", "invalid-token"])
def test_save_requires_valid_token(config_client: ConfigClient, headers: dict[str, str] | None) -> None:
    resp = config_client.post(PATH, auth=False, headers=headers, json=VALID_BODY)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_save_as_member_is_forbidden(config_client: ConfigClient, second_user: SecondUser) -> None:
    before = config_client.get(PATH).json()

    # userAdminCheck runs before zod, so even this valid body never reaches the store.
    resp = request_as(second_user, "POST", PATH, json=VALID_BODY)

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert config_client.get(PATH).json() == before
