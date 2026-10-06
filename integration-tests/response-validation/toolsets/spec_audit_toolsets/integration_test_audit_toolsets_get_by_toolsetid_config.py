"""Strict OpenAPI audit of GET /api/v1/toolsets/:toolsetId/config.

Legacy route: Node pastes the id into `/api/v1/toolsets/{id}/config` on the connector
service, which has no such route, so an ordinary id ends in the relayed routing 404.
Three ids make that URL one Python does serve: `oauth-configs` (the OAuth-config list
of a toolset type named `config`), `instances` (the instance with id `config`) and
`agents` (the toolsets of an agent with key `config`).
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
    NO_OAUTH_CONFIGS,
    OAUTH_CONFIGS_SEGMENT,
    TOOLSET_TYPE,
    UNSAFE_PATH_ID,
    SeedToolsetInstance,
    ToolsetsClient,
    assert_not_found,
    request_as,
)

from helper.second_user import SecondUser

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/toolsets/:toolsetId/config"


def _path(toolset_id: str) -> str:
    return f"/{toolset_id}/config"


# A real toolset type is no more routable in Python than an unknown id.
@pytest.mark.parametrize("toolset_id", [MISSING_TOOLSET_ID, TOOLSET_TYPE], ids=["unknown-id", "toolset-type"])
def test_config_has_no_backend_handler(toolsets_client: ToolsetsClient, toolset_id: str) -> None:
    resp = toolsets_client.get(_path(toolset_id))
    assert_not_found(resp, NO_BACKEND_ROUTE)
    assert_strict_openapi_exchange(resp, ROUTE)


def test_config_of_an_existing_instance_is_not_found(
    toolsets_client: ToolsetsClient,
    seed_toolset_instance: SeedToolsetInstance,
) -> None:
    instance_id = seed_toolset_instance()["_id"]

    resp = toolsets_client.get(_path(instance_id))
    assert_not_found(resp, NO_BACKEND_ROUTE)
    assert_strict_openapi_exchange(resp, ROUTE)

    # The same id answers on the instance route, so the 404 above is the route's, not the id's.
    live = toolsets_client.get(f"/instances/{instance_id}")
    assert live.status_code == 200, live.text[:500]


def test_config_as_member_is_not_found(second_user: SecondUser) -> None:
    resp = request_as(second_user, "GET", _path(MISSING_TOOLSET_ID))
    assert_not_found(resp, NO_BACKEND_ROUTE)
    assert_strict_openapi_exchange(resp, ROUTE)


def test_config_of_the_id_oauth_configs_is_an_empty_oauth_config_list(
    toolsets_client: ToolsetsClient, second_user: SecondUser
) -> None:
    # API bug: the only 200 this route has. Python serves the URL as "list the OAuth
    # configs of toolset type 'config'", a type no config can be stored under.
    resp = toolsets_client.get(_path(OAUTH_CONFIGS_SEGMENT))
    assert resp.status_code == 200, resp.text[:500]
    assert resp.json() == NO_OAUTH_CONFIGS
    assert_strict_openapi_exchange(resp, ROUTE)

    as_member = request_as(second_user, "GET", _path(OAUTH_CONFIGS_SEGMENT))
    assert as_member.status_code == 200, as_member.text[:500]
    assert as_member.json() == NO_OAUTH_CONFIGS
    assert_strict_openapi_exchange(as_member, ROUTE)


@pytest.mark.parametrize(
    ("toolset_id", "message"),
    [
        (INSTANCES_SEGMENT, INSTANCE_NOT_FOUND),
        (AGENTS_SEGMENT, "Agent 'config' not found."),
    ],
    ids=["instances", "agents"],
)
def test_config_of_a_reserved_id_is_not_found_by_another_handler(
    toolsets_client: ToolsetsClient, toolset_id: str, message: str
) -> None:
    # Python looks 'config' up as an instance id or an agent key; neither can exist.
    resp = toolsets_client.get(_path(toolset_id))
    assert_not_found(resp, message)
    assert_strict_openapi_exchange(resp, ROUTE)


def test_config_does_not_read_the_query_string(toolsets_client: ToolsetsClient) -> None:
    with outside_request_contract("the route reads no query parameter and forwards none"):
        resp = toolsets_client.get(_path(OAUTH_CONFIGS_SEGMENT), params={"specAuditUnknown": "1"})
    assert resp.status_code == 200, resp.text[:500]
    assert resp.json() == NO_OAUTH_CONFIGS
    assert_strict_openapi_response(resp, ROUTE)


def test_config_without_token_is_unauthorized(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.get(_path(MISSING_TOOLSET_ID), auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_config_with_unsafe_toolset_id_is_rejected_before_auth(
    toolsets_client: ToolsetsClient,
) -> None:
    resp = toolsets_client.get(_path(UNSAFE_PATH_ID), auth=False)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
