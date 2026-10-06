"""Strict OpenAPI audit of POST /api/v1/configurationManager/smtpConfig.

Chain: authenticate -> requireScopes(config:write) -> userAdminCheck -> zod body -> createSmtpConfig,
which stores the config and then has the mail service reload it.
Every body that is accepted here keeps the host, port and sender this stack already
uses, so mail keeps flowing for the other suites while these tests run.
"""

from __future__ import annotations

from typing import Any

import pytest
from configuration_manager_audit_support import (
    INVALID_BEARER_HEADERS,
    KV_SMTP,
    SECRET_PLACEHOLDER,
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

ROUTE = "/api/v1/configurationManager/smtpConfig"
PATH = "/smtpConfig"
SAVED_BODY = {"message": "SMTP config created successfully"}
REQUIRED = ("host", "port", "fromEmail")

# Never stored: only bodies the validator or an earlier middleware refuses are built from it.
UNUSED_BODY: dict[str, Any] = {
    "host": "smtp.spec-audit.invalid",
    "port": 587,
    "fromEmail": "spec-audit@example.com",
}


@pytest.fixture
def current(config_client: ConfigClient, guard_saved_config: GuardSavedConfig) -> dict[str, Any]:
    """The working SMTP config of this stack; whatever a test stores is replaced by it afterwards."""
    before = guard_saved_config(PATH, KV_SMTP)
    if not all(before.get(key) for key in REQUIRED):
        pytest.fail(
            "this stack has no SMTP configuration to keep working: save one that points at "
            "Mailpit before running the SMTP success cases"
        )
    return before


def _stored(config_client: ConfigClient) -> dict[str, Any]:
    resp = config_client.get(PATH)
    assert resp.status_code == 200, resp.text[:500]
    body: dict[str, Any] = resp.json()
    return body


def _assert_mail_can_still_be_sent(config_client: ConfigClient) -> None:
    status = config_client.get("/smtpConfig/status")
    assert status.status_code == 200, status.text[:500]
    assert status.json() == {"configured": True}


def test_set_smtp_config_stores_the_body_and_reloads_the_mail_service(
    config_client: ConfigClient, current: dict[str, Any]
) -> None:
    resp = config_client.post(PATH, json=current)

    assert resp.status_code == 200, resp.text[:500]
    assert resp.json() == SAVED_BODY
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _stored(config_client) == current
    _assert_mail_can_still_be_sent(config_client)


def test_set_smtp_config_without_credentials_stores_only_the_three_required_fields(
    config_client: ConfigClient, current: dict[str, Any]
) -> None:
    body = {key: current[key] for key in REQUIRED}

    resp = config_client.post(PATH, json=body)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    # A whole replace: username and password of the previous config are gone, not kept.
    assert _stored(config_client) == body
    _assert_mail_can_still_be_sent(config_client)


def test_set_smtp_config_keeps_the_stored_password_when_sent_the_placeholder(
    config_client: ConfigClient, current: dict[str, Any]
) -> None:
    first = config_client.post(PATH, json={**current, "password": current.get("password", "")})
    assert first.status_code == 200, first.text[:500]
    kept = _stored(config_client)["password"]

    resp = config_client.post(PATH, json={**current, "password": SECRET_PLACEHOLDER})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _stored(config_client)["password"] == kept
    assert kept != SECRET_PLACEHOLDER


def test_set_smtp_config_drops_what_the_validator_does_not_know(
    config_client: ConfigClient, current: dict[str, Any]
) -> None:
    with outside_request_contract("secure and specAudit are not fields of the body"):
        resp = config_client.post(PATH, json={**current, "secure": True, "specAudit": "x"})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert _stored(config_client) == current


@pytest.mark.parametrize(
    "headers",
    [None, INVALID_BEARER_HEADERS],
    ids=["no-token", "invalid-token"],
)
def test_set_smtp_config_without_valid_token_is_unauthorized(
    config_client: ConfigClient, headers: dict[str, str] | None
) -> None:
    resp = config_client.post(PATH, auth=False, headers=headers, json=UNUSED_BODY)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_set_smtp_config_as_member_is_forbidden(
    config_client: ConfigClient, second_user: SecondUser
) -> None:
    before = _stored(config_client)

    # A valid body, so the 403 can only come from userAdminCheck, which runs before zod.
    resp = request_as(second_user, "POST", PATH, json=UNUSED_BODY)

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _stored(config_client) == before


@pytest.mark.parametrize(
    ("body", "fields"),
    [
        pytest.param({}, ["body.host", "body.port", "body.fromEmail"], id="empty-object"),
        *(
            pytest.param(
                {k: v for k, v in UNUSED_BODY.items() if k != field},
                [f"body.{field}"],
                id=f"missing-{field}",
            )
            for field in REQUIRED
        ),
        pytest.param(
            {**UNUSED_BODY, "host": "", "fromEmail": ""},
            ["body.host", "body.fromEmail"],
            id="empty-host-and-sender",
        ),
        pytest.param({**UNUSED_BODY, "port": "587"}, ["body.port"], id="port-as-string"),
        pytest.param({**UNUSED_BODY, "port": 0}, ["body.port"], id="port-zero"),
        pytest.param({**UNUSED_BODY, "port": -25}, ["body.port"], id="port-negative"),
        pytest.param(
            {**UNUSED_BODY, "username": 42, "password": 42},
            ["body.username", "body.password"],
            id="credentials-not-strings",
        ),
        # Optional means "may be left out": an explicit null is refused.
        pytest.param({**UNUSED_BODY, "password": None}, ["body.password"], id="password-null"),
        pytest.param([UNUSED_BODY], ["body"], id="body-is-a-list"),
    ],
)
def test_set_smtp_config_invalid_body_is_rejected_and_nothing_is_stored(
    config_client: ConfigClient, body: Any, fields: list[str]
) -> None:
    before = _stored(config_client)

    resp = config_client.post(PATH, json=body)

    assert_validation_error(resp, *fields)
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _stored(config_client) == before


def test_set_smtp_config_without_a_body_is_rejected_like_an_empty_one(
    config_client: ConfigClient,
) -> None:
    resp = config_client.post(PATH)

    assert_validation_error(resp, "body.host", "body.port", "body.fromEmail")
    assert_strict_openapi_exchange(resp, ROUTE)
