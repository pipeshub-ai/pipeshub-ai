"""Strict OpenAPI audit of GET /api/v1/toolsets/:toolsetId/oauth/authorize.

Legacy route: Node validates and pastes the id into `/api/v1/toolsets/{id}/oauth/authorize`
on the connector service, which has no such route (only
/instances/:instanceId/oauth/authorize), so every ordinary id ends in the relayed
routing 404. For the id `oauth-configs` the URL matches Python's
`/oauth-configs/{type}/{id}`, which has no GET handler; its 405 comes back as a 500.
"""

from __future__ import annotations

import pytest
from strict_openapi import (
    assert_strict_openapi_exchange,
    assert_strict_openapi_response,
    outside_request_contract,
)
from toolsets_audit_support import (
    AGENTS_SEGMENT,
    INSTANCES_SEGMENT,
    MISSING_TOOLSET_ID,
    NO_BACKEND_ROUTE,
    OAUTH_CONFIGS_SEGMENT,
    UNSAFE_PATH_ID,
    SeedToolsetInstance,
    ToolsetsClient,
    assert_backend_failure,
    assert_not_found,
    rejected_fields,
    request_as,
)

from helper.second_user import SecondUser

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/toolsets/:toolsetId/oauth/authorize"


def _authorize_path(toolset_id: str) -> str:
    return f"/{toolset_id}/oauth/authorize"


@pytest.mark.parametrize(
    "params",
    [{}, {"base_url": "https://spec-audit.invalid"}, {"base_url": ""}],
    ids=["no-query", "base-url", "empty-base-url"],
)
def test_authorize_for_an_unknown_toolset_id_is_not_found(
    toolsets_client: ToolsetsClient, params: dict[str, str]
) -> None:
    resp = toolsets_client.get(_authorize_path(MISSING_TOOLSET_ID), params=params)
    assert_not_found(resp, NO_BACKEND_ROUTE)
    assert_strict_openapi_exchange(resp, ROUTE)


def test_authorize_is_not_found_even_for_a_real_oauth_instance(
    toolsets_client: ToolsetsClient,
    seed_toolset_instance: SeedToolsetInstance,
) -> None:
    instance = seed_toolset_instance(oauth=True)
    resp = toolsets_client.get(_authorize_path(instance["_id"]))
    assert_not_found(resp, NO_BACKEND_ROUTE)
    assert_strict_openapi_exchange(resp, ROUTE)

    # The id that is refused above builds an authorization URL on the instance route.
    live = toolsets_client.get(f"/instances/{instance['_id']}/oauth/authorize")
    assert live.status_code == 200, live.text[:500]


def test_authorize_as_member_is_not_found(second_user: SecondUser) -> None:
    resp = request_as(second_user, "GET", _authorize_path(MISSING_TOOLSET_ID))
    assert_not_found(resp, NO_BACKEND_ROUTE)
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("toolset_id", [INSTANCES_SEGMENT, AGENTS_SEGMENT])
def test_authorize_for_the_ids_instances_and_agents_is_not_found(
    toolsets_client: ToolsetsClient, toolset_id: str
) -> None:
    resp = toolsets_client.get(_authorize_path(toolset_id))
    assert_not_found(resp, NO_BACKEND_ROUTE)
    assert_strict_openapi_exchange(resp, ROUTE)


def test_authorize_for_the_id_oauth_configs_is_an_internal_error(
    toolsets_client: ToolsetsClient,
) -> None:
    # API bug: /oauth-configs/oauth/authorize matches Python's PUT/DELETE-only
    # /oauth-configs/{type}/{id}. FastAPI answers 405, and Node turns every backend
    # status it does not list into a 500.
    resp = toolsets_client.get(_authorize_path(OAUTH_CONFIGS_SEGMENT))
    assert_backend_failure(resp)
    assert_strict_openapi_exchange(resp, ROUTE)


def test_authorize_rejects_a_repeated_base_url(toolsets_client: ToolsetsClient) -> None:
    # Express parses a repeated key into an array, which the zod string refuses.
    resp = toolsets_client.get(
        _authorize_path(MISSING_TOOLSET_ID),
        params=[("base_url", "https://a.invalid"), ("base_url", "https://b.invalid")],
    )
    assert resp.status_code == 400, resp.text[:500]
    assert rejected_fields(resp) == {"query.base_url"}
    assert_strict_openapi_exchange(resp, ROUTE)


def test_authorize_does_not_refuse_unknown_query_parameters(toolsets_client: ToolsetsClient) -> None:
    with outside_request_contract("the validator drops query parameters it does not list"):
        resp = toolsets_client.get(_authorize_path(MISSING_TOOLSET_ID), params={"specAuditUnknown": "1"})
    assert_not_found(resp, NO_BACKEND_ROUTE)
    assert_strict_openapi_response(resp, ROUTE)


def test_authorize_rejects_a_call_without_a_token(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.get(_authorize_path(MISSING_TOOLSET_ID), auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_authorize_refuses_an_unsafe_toolset_id_before_auth(
    toolsets_client: ToolsetsClient,
) -> None:
    resp = toolsets_client.get(_authorize_path(UNSAFE_PATH_ID), auth=False)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
