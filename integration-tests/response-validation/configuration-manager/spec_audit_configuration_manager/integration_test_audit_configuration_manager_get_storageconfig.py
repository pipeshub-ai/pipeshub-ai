"""Strict OpenAPI audit of GET /api/v1/configurationManager/storageConfig."""

from __future__ import annotations

import pytest
from configuration_manager_audit_support import INVALID_BEARER_HEADERS, request_as
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/storageConfig"


def test_admin_gets_empty_object(config_client: ConfigClient) -> None:
    resp = config_client.get("/storageConfig")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    # The controller only returns the stored fields to the scoped-token caller of
    # /internal/storageConfig; any caller with a userId gets {} for every storage type.
    assert resp.json() == {}


@pytest.mark.parametrize(
    "headers",
    [None, INVALID_BEARER_HEADERS],
    ids=["no_token", "invalid_token"],
)
def test_unauthenticated_is_rejected(
    config_client: ConfigClient, headers: dict[str, str] | None
) -> None:
    resp = config_client.get("/storageConfig", auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "GET", "/storageConfig")
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
