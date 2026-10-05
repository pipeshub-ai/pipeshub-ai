"""Strict OpenAPI audit of GET /api/v1/toolsets/:toolsetId/status.

Node forwards to Python `/api/v1/toolsets/{id}/status`, which has no handler (the live
route is `/instances/{id}/status`), so every authenticated call ends in the proxied 404.
"""

from __future__ import annotations

import pytest
from toolsets_audit_support import (
    MISSING_TOOLSET_ID,
    UNSAFE_PATH_ID,
    SeedToolsetInstance,
    ToolsetsClient,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/toolsets/:toolsetId/status"


def test_status_of_existing_instance_is_not_found(
    toolsets_client: ToolsetsClient,
    seed_toolset_instance: SeedToolsetInstance,
) -> None:
    instance_id = seed_toolset_instance()["_id"]

    resp = toolsets_client.get(f"/{instance_id}/status")
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    # The same id answers on the instance route, so the 404 above is the route's, not the id's.
    live = toolsets_client.get(f"/instances/{instance_id}/status")
    assert live.status_code == 200, live.text[:500]


def test_status_of_unknown_toolset_is_not_found(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.get(f"/{MISSING_TOOLSET_ID}/status")
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_status_as_member_is_not_found(second_user: SecondUser) -> None:
    resp = request_as(second_user, "GET", f"/{MISSING_TOOLSET_ID}/status")
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_status_without_token_is_unauthorized(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.get(f"/{MISSING_TOOLSET_ID}/status", auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_status_with_unsafe_toolset_id_is_rejected_before_auth(
    toolsets_client: ToolsetsClient,
) -> None:
    resp = toolsets_client.get(f"/{UNSAFE_PATH_ID}/status", auth=False)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
