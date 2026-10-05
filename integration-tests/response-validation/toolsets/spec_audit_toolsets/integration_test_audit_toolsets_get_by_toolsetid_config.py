"""Strict OpenAPI audit of GET /api/v1/toolsets/:toolsetId/config.

Legacy route: Node still registers it and proxies to Python, which has no
``/{toolset_id}/config`` handler, so no request can succeed.
"""

from __future__ import annotations

import pytest
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response
from toolsets_audit_support import (
    MISSING_TOOLSET_ID,
    TOOLSET_TYPE,
    UNSAFE_PATH_ID,
    ToolsetsClient,
    request_as,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/toolsets/:toolsetId/config"


# A real toolset type is no more routable in Python than an unknown id.
@pytest.mark.parametrize("toolset_id", [MISSING_TOOLSET_ID, TOOLSET_TYPE])
def test_get_config_has_no_backend_handler(
    toolsets_client: ToolsetsClient, toolset_id: str
) -> None:
    resp = toolsets_client.get(f"/{toolset_id}/config")
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_get_config_as_member_is_not_found(second_user: SecondUser) -> None:
    resp = request_as(second_user, "GET", f"/{MISSING_TOOLSET_ID}/config")
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_get_config_without_token_is_unauthorized(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.get(f"/{MISSING_TOOLSET_ID}/config", auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_get_config_unsafe_toolset_id_is_rejected_before_auth(
    toolsets_client: ToolsetsClient,
) -> None:
    resp = toolsets_client.get(f"/{UNSAFE_PATH_ID}/config", auth=False)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
