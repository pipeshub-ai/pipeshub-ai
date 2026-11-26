"""P2.4a TC: connector credentials reach a client only at the effect boundary, in both loops."""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from app.agents.agent_loop.instance_creator import ToolInstanceCreator
from app.agents.agent_loop.mcp_access import MCPAccessResolver
from app.agents.tools.factories.registry import ClientFactoryRegistry
from app.api.routes.agent import _resolve_mcp_server_configs, _resolve_toolset_configs
from app.harness.assembly.teardown import close_environment
from app.harness.credentials.service import credential_service_for
from app.harness.credentials.tool_credentials import REF_KEY, toolset_reference
from harness_core.credentials import CredentialPurpose, CredentialRevoked
from harness_core.logs import HarnessLog
from tests.unit.agents.adapter.conftest import make_context
from tests.unit.harness.credentials.support import DictConfig, owned_toolsets

TOKEN = "ghp_" + "Z9y8X7w6V5u4T3s2R1q0P9o8N7m6L5k4J3i2"
REFRESHED = "ghp_" + "R" * 36
MCP_TOKEN = "xoxb-" + "1234567890-abcdefghijklmnop"
PATH = "/services/toolsets/inst-1/user-1"
LOG = logging.getLogger("tc")


def _record(token: str = TOKEN) -> dict[str, Any]:
    return {"isAuthenticated": True, "credentials": {"access_token": token}, "oauthConfigId": "oc-1"}


class _Factory:
    def __init__(self) -> None:
        self.configs: list[dict[str, Any]] = []

    async def create_client(self, config_service: object, logger: object, config: dict, state: dict) -> dict:
        self.configs.append(config)
        return {"client": True}


class _Jira:
    def __init__(self, client: object) -> None:
        self.client = client


def _context(config: DictConfig, **overrides: Any) -> Any:  # noqa: ANN401
    return make_context(
        config_service=config,
        toolset_configs={"inst-1": toolset_reference("inst-1", "user-1")},
        tool_to_toolset_map={"jira.search": "inst-1"},
        agent_toolsets=[{"name": "jira", "instanceId": "inst-1"}],
        run_id="run-1",
        **overrides,
    )


async def _create(context: Any, factory: _Factory) -> object:  # noqa: ANN401
    with (
        patch.object(ClientFactoryRegistry, "_factories", {"jira": factory}),
        patch.object(ClientFactoryRegistry, "_initialized", True),
    ):
        return await ToolInstanceCreator(context).create_instance_async(_Jira, "jira", "jira.search")


async def test_tc1_the_route_keeps_references_only() -> None:
    config = DictConfig(owned_toolsets({PATH: _record(), "/services/toolsets/inst-2/user-1": {"isAuthenticated": False}}))
    configs, configured, missing, unauthenticated = await _resolve_toolset_configs(
        config,  # type: ignore[arg-type]
        [{"name": "jira", "instanceId": "inst-1"}, {"name": "slack", "instanceId": "inst-2"}, {"name": "gh", "instanceId": "inst-3"}],
        credential_lookup_id="user-1",
        executing_user_id="user-1",
        prefetched={},
        agent_id="agent-1",
        is_service_account=False,
        logger=LOG,  # type: ignore[arg-type]
    )
    assert configs == {"inst-1": toolset_reference("inst-1", "user-1")}
    assert TOKEN not in json.dumps(configs)
    assert [t["instanceId"] for t in configured] == ["inst-1"]
    assert (len(missing), len(unauthenticated)) == (1, 1)


async def test_tc2_the_factory_gets_the_record_and_state_keeps_the_reference() -> None:
    config, factory = DictConfig(owned_toolsets({PATH: _record()})), _Factory()
    context = _context(config)
    _ = await _create(context, factory)
    assert factory.configs == [_record()]
    assert context.tool_state["toolset_configs"] == {"inst-1": toolset_reference("inst-1", "user-1")}
    (handle,) = context.tool_credentials().handles()
    assert TOKEN not in handle


async def test_tc3_a_refreshed_record_reaches_the_next_request() -> None:
    config = DictConfig(owned_toolsets({PATH: _record()}))
    first, second = _Factory(), _Factory()
    _ = await _create(_context(config), first)
    config.values[PATH] = _record(REFRESHED)
    _ = await _create(_context(config), second)
    assert first.configs[0]["credentials"]["access_token"] == TOKEN
    assert second.configs[0]["credentials"]["access_token"] == REFRESHED


async def test_tc4_a_revoked_handle_fails_client_creation_at_once() -> None:
    config, factory = DictConfig(owned_toolsets({PATH: _record()})), _Factory()
    context = _context(config)
    _ = await context.tool_credentials().toolset_config(toolset_reference("inst-1", "user-1"))
    (handle,) = context.tool_credentials().handles()
    await credential_service_for(config).revoke(handle)
    with pytest.raises(CredentialRevoked):
        _ = await _create(context, factory)
    assert factory.configs == []
    assert context.tool_credentials().handles() == (handle,)


def _mcp_instance(instance_id: str, config: object) -> Any:  # noqa: ANN401
    async def resolved(*_: object) -> dict[str, Any]:
        return {"_id": "mcp-1", "orgId": "org-1", "createdBy": "user-1", "name": "Slack MCP", "typeId": "slack",
                "transport": "sse", "authMode": "api_token", "createdAt": 0, "updatedAt": 0}

    return resolved


async def test_tc5_mcp_auth_is_resolved_by_handle() -> None:
    record = {"isAuthenticated": True, "apiToken": MCP_TOKEN}
    config = DictConfig({"/services/mcp/credentials/mcp-1/user-1": record})
    with patch("app.edition_config.get_mcp_instance_resolved", new=_mcp_instance("mcp-1", config)):
        configs, configured, missing, unauthenticated = await _resolve_mcp_server_configs(
            config,  # type: ignore[arg-type]
            [{"instanceId": "mcp-1", "name": "Slack MCP"}],
            org_id="org-1",
            credential_lookup_id="user-1",
            executing_user_id="user-1",
            agent_id="agent-1",
            is_service_account=False,
            logger=LOG,  # type: ignore[arg-type]
        )
        assert MCP_TOKEN not in json.dumps(configs) and "auth" not in configs["mcp-1"]
        assert configs["mcp-1"][REF_KEY] == {"kind": "mcp", "name": "mcp-1/user-1"}
        context = make_context(
            config_service=config,
            mcp_servers=[{"instanceId": "mcp-1", "name": "Slack MCP", "tools": []}],
            mcp_server_configs=configs,
            run_id="run-1",
        )
        (server,) = await MCPAccessResolver.resolve(context)
    assert server.auth == record
    assert MCP_TOKEN not in json.dumps(context.tool_state["mcp_server_configs"])
    assert (configured, missing, unauthenticated) == ([{"instanceId": "mcp-1", "name": "Slack MCP"}], [], [])


async def test_tc6_teardown_revokes_the_attempts_handles() -> None:
    config, factory = DictConfig(owned_toolsets({PATH: _record()})), _Factory()
    context = _context(config)
    _ = await _create(context, factory)
    (handle,) = context.tool_credentials().handles()
    await close_environment(context, HarnessLog.named("tc"))
    purpose = CredentialPurpose("org-1", "toolset:inst-1", "run-1")
    with pytest.raises(CredentialRevoked):
        _ = await credential_service_for(config).resolve(handle, purpose)


async def test_tc7_a_raw_record_is_used_as_before() -> None:
    factory = _Factory()
    context = make_context(
        config_service=SimpleNamespace(),
        toolset_configs={"inst-1": _record()},
        tool_to_toolset_map={"jira.search": "inst-1"},
    )
    _ = await _create(context, factory)
    assert factory.configs == [_record()]
