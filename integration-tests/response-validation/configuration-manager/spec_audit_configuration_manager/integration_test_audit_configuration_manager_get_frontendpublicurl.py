"""Strict OpenAPI audit of GET /api/v1/configurationManager/frontendPublicUrl."""

from __future__ import annotations

import pytest
from configuration_manager_audit_support import INVALID_BEARER_HEADERS, request_as
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/frontendPublicUrl"
PATH = "/frontendPublicUrl"


def test_admin_reads_url_or_empty_object(config_client: ConfigClient) -> None:
    resp = config_client.get(PATH)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    body = resp.json()
    # The controller answers {} when no frontend endpoint is stored.
    assert body == {} or list(body) == ["url"], body
    if body:
        assert isinstance(body["url"], str) and body["url"], body


def test_member_reads_same_url_as_admin(
    config_client: ConfigClient, second_user: SecondUser
) -> None:
    # Unlike POST on this path, the GET has no userAdminCheck.
    resp = request_as(second_user, "GET", PATH)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    admin = config_client.get(PATH)
    assert admin.status_code == 200, admin.text[:500]
    assert_strict_openapi_response(admin, ROUTE)
    assert resp.json() == admin.json()


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
    assert_strict_openapi_response(resp, ROUTE)
