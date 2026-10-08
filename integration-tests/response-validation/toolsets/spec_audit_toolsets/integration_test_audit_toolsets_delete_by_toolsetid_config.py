"""Strict OpenAPI audit of DELETE /api/v1/toolsets/:toolsetId/config.

Legacy route: Node pastes the id into DELETE `/api/v1/toolsets/{id}/config` on the
connector service, which has no such route, so an ordinary id ends in the relayed
routing 404. The id `instances` makes it Python's instance delete for an instance
called `config`; for `oauth-configs` and `agents` the URL matches a route without a
DELETE handler, whose 405 comes back as a 500.
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
    INSTANCE_NOT_FOUND,
    INSTANCES_SEGMENT,
    MISSING_TOOLSET_ID,
    NO_BACKEND_ROUTE,
    OAUTH_CONFIGS_SEGMENT,
    UNSAFE_PATH_ID,
    SeedToolsetInstance,
    ToolsetsClient,
    assert_backend_failure,
    assert_not_found,
    error_of,
    request_as,
)

from helper.second_user import SecondUser

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/toolsets/:toolsetId/config"


def _path(toolset_id: str) -> str:
    return f"/{toolset_id}/config"


def test_delete_config_has_no_backend_handler(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.delete(_path(MISSING_TOOLSET_ID))
    assert_not_found(resp, NO_BACKEND_ROUTE)
    assert_strict_openapi_exchange(resp, ROUTE)


def test_delete_config_of_an_existing_instance_deletes_nothing(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    instance_id = seed_toolset_instance()["_id"]

    resp = toolsets_client.delete(_path(instance_id))
    assert_not_found(resp, NO_BACKEND_ROUTE)
    assert_strict_openapi_exchange(resp, ROUTE)

    still_there = toolsets_client.get_instance(instance_id)
    assert still_there.status_code == 200, still_there.text[:500]


def test_delete_config_as_member_is_not_found(second_user: SecondUser) -> None:
    resp = request_as(second_user, "DELETE", _path(MISSING_TOOLSET_ID))
    assert_not_found(resp, NO_BACKEND_ROUTE)
    assert_strict_openapi_exchange(resp, ROUTE)


def test_delete_config_for_the_id_instances_is_served_as_an_instance_delete(
    toolsets_client: ToolsetsClient, second_user: SecondUser
) -> None:
    # API bug: DELETE /toolsets/instances/config is Python's "delete the instance with
    # id 'config'". No instance has that id, so the admin gets that handler's 404 and a
    # member its admin check, which runs before the lookup.
    resp = toolsets_client.delete(_path(INSTANCES_SEGMENT))
    assert_not_found(resp, INSTANCE_NOT_FOUND)
    assert_strict_openapi_exchange(resp, ROUTE)

    as_member = request_as(second_user, "DELETE", _path(INSTANCES_SEGMENT))
    assert as_member.status_code == 403, as_member.text[:500]
    assert error_of(as_member)["message"] == "Only administrators can delete toolset instances."
    assert_strict_openapi_exchange(as_member, ROUTE)


@pytest.mark.parametrize("toolset_id", [OAUTH_CONFIGS_SEGMENT, AGENTS_SEGMENT])
def test_delete_config_for_a_reserved_id_is_an_internal_error(
    toolsets_client: ToolsetsClient, toolset_id: str
) -> None:
    # API bug: the pasted URL matches a Python route that has no DELETE handler. FastAPI
    # answers 405, and Node turns every backend status it does not list into a 500.
    resp = toolsets_client.delete(_path(toolset_id))
    assert_backend_failure(resp)
    assert_strict_openapi_exchange(resp, ROUTE)


def test_delete_config_reads_neither_query_nor_body(toolsets_client: ToolsetsClient) -> None:
    with outside_request_contract("the route reads no query parameter and no body"):
        resp = toolsets_client.delete(
            _path(MISSING_TOOLSET_ID), params={"force": "true"}, json={"force": True}
        )
    assert_not_found(resp, NO_BACKEND_ROUTE)
    assert_strict_openapi_response(resp, ROUTE)


def test_delete_config_without_token_is_unauthorized(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.delete(_path(MISSING_TOOLSET_ID), auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_delete_config_with_unsafe_toolset_id_is_refused_before_auth(
    toolsets_client: ToolsetsClient,
) -> None:
    # guardPathParams is a router.param hook, so it answers before authenticate runs.
    resp = toolsets_client.delete(_path(UNSAFE_PATH_ID), auth=False)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
