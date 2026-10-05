"""Strict OpenAPI audit of GET /api/v1/configurationManager/connectors/onedrive/config."""

from __future__ import annotations

from typing import Any

import pytest
from configuration_manager_audit_support import INVALID_BEARER_HEADERS, request_as
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/connectors/onedrive/config"
PATH = "/connectors/onedrive/config"


def test_admin_reads_onedrive_config(config_client: ConfigClient) -> None:
    resp = config_client.get(PATH)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    # The decrypted stored object as is, or {} when the org has none.
    assert isinstance(resp.json(), dict)


@pytest.mark.parametrize(
    "headers",
    [None, INVALID_BEARER_HEADERS],
    ids=["no-token", "invalid-token"],
)
def test_read_without_valid_token_is_unauthorized(
    config_client: ConfigClient, headers: dict[str, str] | None
) -> None:
    kwargs: dict[str, Any] = {"headers": headers} if headers else {}
    resp = config_client.get(PATH, auth=False, **kwargs)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "GET", PATH)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
