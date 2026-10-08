"""Strict OpenAPI audit of GET /api/v1/configurationManager/smtpConfig.

Chain: authenticate -> requireScopes(config:read) -> userAdminCheck -> getSmtpConfig.
No validator: the handler reads nothing from the request.
"""

from __future__ import annotations

import pytest
from configuration_manager_audit_support import (
    INVALID_BEARER_HEADERS,
    KV_SMTP,
    SECRET_PLACEHOLDER,
    forget_stored_config,
    read_stored_value,
    request_as,
    write_stored_value,
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

STORED_KEYS = {"host", "port", "username", "password", "fromEmail"}


def test_admin_reads_the_stored_config_password_included(config_client: ConfigClient) -> None:
    resp = config_client.get_smtp_config()

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    # This stack has SMTP configured (the mail suites depend on it), so the object is not empty.
    assert {"host", "port", "fromEmail"} <= set(body) <= STORED_KEYS, sorted(body)
    assert isinstance(body["host"], str) and body["host"]
    assert isinstance(body["port"], int)
    # HIDE_SECRET_CONFIG is off here: a stored password comes back as stored, not masked.
    assert body.get("password") != SECRET_PLACEHOLDER


def test_admin_reads_an_empty_object_when_nothing_is_saved(config_client: ConfigClient) -> None:
    before = read_stored_value(KV_SMTP)
    assert before is not None, "this stack has no SMTP configuration to put back afterwards"
    # The stored bytes go back unchanged at once: the invite routes of other suites read them.
    forget_stored_config(KV_SMTP)
    try:
        resp = config_client.get(PATH)
    finally:
        write_stored_value(KV_SMTP, before)

    assert resp.status_code == 200, resp.text[:500]
    assert resp.json() == {}
    assert_strict_openapi_exchange(resp, ROUTE)
    assert read_stored_value(KV_SMTP) == before


def test_admin_read_does_not_read_the_query_string(config_client: ConfigClient) -> None:
    plain = config_client.get(PATH)
    assert plain.status_code == 200, plain.text[:500]

    with outside_request_contract("the handler never reads the query string"):
        resp = config_client.get(PATH, params={"specAudit": "bogus"})

    assert resp.status_code == 200, resp.text[:500]
    assert resp.json() == plain.json()
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    "headers",
    [None, INVALID_BEARER_HEADERS],
    ids=["no-token", "invalid-token"],
)
def test_unauthenticated_is_unauthorized(
    config_client: ConfigClient, headers: dict[str, str] | None
) -> None:
    resp = config_client.get(PATH, auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "GET", PATH)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
