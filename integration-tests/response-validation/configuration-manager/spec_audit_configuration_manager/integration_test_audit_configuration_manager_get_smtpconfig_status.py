"""Strict OpenAPI audit of GET /api/v1/configurationManager/smtpConfig/status.

Chain: authenticate -> getSmtpConfigStatus. No scope check, no admin check, no validator.
"""

from __future__ import annotations

import pytest
from configuration_manager_audit_support import (
    INVALID_BEARER_HEADERS,
    KV_SMTP,
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

ROUTE = "/api/v1/configurationManager/smtpConfig/status"
PATH = "/smtpConfig/status"


def test_admin_reads_boolean_only_status(config_client: ConfigClient) -> None:
    resp = config_client.get(PATH)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    # Nothing but the flag may leak: members who cannot read /smtpConfig call this too.
    assert list(body) == ["configured"], body
    # host, port and fromEmail are all stored on this stack.
    assert body["configured"] is True


def test_status_is_false_when_nothing_is_saved(config_client: ConfigClient) -> None:
    before = read_stored_value(KV_SMTP)
    assert before is not None, "this stack has no SMTP configuration to put back afterwards"
    # The stored bytes go back unchanged at once: the invite routes of other suites read them.
    forget_stored_config(KV_SMTP)
    try:
        resp = config_client.get(PATH)
    finally:
        write_stored_value(KV_SMTP, before)

    assert resp.status_code == 200, resp.text[:500]
    assert resp.json() == {"configured": False}
    assert_strict_openapi_exchange(resp, ROUTE)
    assert read_stored_value(KV_SMTP) == before


def test_member_reads_same_status_as_admin(
    config_client: ConfigClient, second_user: SecondUser
) -> None:
    # No userAdminCheck and no requireScopes on this route.
    resp = request_as(second_user, "GET", PATH)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    admin = config_client.get(PATH)
    assert admin.status_code == 200, admin.text[:500]
    assert resp.json() == admin.json()


def test_status_does_not_read_the_query_string(config_client: ConfigClient) -> None:
    with outside_request_contract("the handler never reads the query string"):
        resp = config_client.get(PATH, params={"specAudit": "bogus", "configured": "false"})

    assert resp.status_code == 200, resp.text[:500]
    assert resp.json() == {"configured": True}
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
