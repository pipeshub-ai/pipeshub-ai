"""Strict OpenAPI audit of DELETE /api/v1/toolsets/:toolsetId/config."""

from __future__ import annotations

import pytest
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response
from toolsets_audit_support import (
    MISSING_TOOLSET_ID,
    UNSAFE_PATH_ID,
    ToolsetsClient,
    request_as,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/toolsets/:toolsetId/config"


def test_delete_config_without_token_is_unauthorized(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.delete(f"/{MISSING_TOOLSET_ID}/config", auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_delete_config_with_unsafe_toolset_id_is_refused_before_auth(
    toolsets_client: ToolsetsClient,
) -> None:
    # guardPathParams is a router.param hook, so it answers before authenticate runs.
    resp = toolsets_client.delete(f"/{UNSAFE_PATH_ID}/config", auth=False)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_delete_config_has_no_backend_handler(toolsets_client: ToolsetsClient) -> None:
    # Python registers no DELETE /{toolsetId}/config, so even the admin only ever
    # reaches FastAPI's routing 404, relayed by mapBackendError.
    resp = toolsets_client.delete(f"/{MISSING_TOOLSET_ID}/config")

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.xfail(
    strict=True,
    reason="API bug: DELETE /toolsets/oauth-configs/config answers 500 "
    "(the id is pasted into a GET-only Python path; its 405 is mapped to a 500)",
)
def test_delete_config_on_a_get_only_backend_path_is_not_found(
    toolsets_client: ToolsetsClient,
) -> None:
    # The id is pasted into the Python URL: /oauth-configs/config matches the GET-only
    # /oauth-configs/{toolset_type}, FastAPI answers 405, and mapBackendError turns
    # every status it does not list into a 500. No config exists under any id, so the
    # answer every other id gets is a 404.
    resp = toolsets_client.delete("/oauth-configs/config")

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.xfail(
    strict=True,
    reason="API bug: DELETE /toolsets/instances/config is served by Python's "
    "DELETE /instances/{instance_id} with instance_id='config' (403 for a member)",
)
def test_delete_config_with_a_reserved_segment_as_id_is_not_found_for_member(
    second_user: SecondUser,
) -> None:
    # /instances/config is Python's DELETE /instances/{instance_id}, whose admin check
    # runs before the lookup, so the member is refused and nothing is read or deleted.
    # The legacy config route has no handler of its own, so its answer should be the 404.
    resp = request_as(second_user, "DELETE", "/instances/config")

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
