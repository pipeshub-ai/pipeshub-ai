"""`MCPAccessResolver` (`app/agents/agent_loop/mcp_access.py`) — joins
`AgentContext.mcp_servers` (attached refs) with `mcp_server_configs`
(resolved instance/auth, keyed by instanceId) into `ResolvedMCPServer`s."""

from __future__ import annotations

from typing import Any

from app.agents.agent_loop.mcp_access import MCPAccessResolver
from tests.unit.agents.adapter.conftest import make_context


def _instance(instance_id: str = "inst-1") -> dict[str, Any]:
    return {
        "_id": instance_id, "orgId": "org-1", "createdBy": "user-1",
        "name": "JiraMCP", "typeId": "jira_mcp", "transport": "sse", "authMode": "none",
        "createdAt": 0, "updatedAt": 0,
    }


class TestResolve:
    def test_resolves_matching_instance_into_resolved_server(self) -> None:
        context = make_context(
            mcp_servers=[{"instanceId": "inst-1", "name": "JiraMCP", "displayName": "Jira MCP", "tools": []}],
            mcp_server_configs={"inst-1": {"instance": _instance(), "auth": {}, "ownerId": "user-1"}},
        )

        resolved = MCPAccessResolver.resolve(context)

        assert len(resolved) == 1
        server = resolved[0]
        assert server.instance_id == "inst-1"
        assert server.name == "JiraMCP"
        assert server.display_name == "Jira MCP"
        assert server.instance == _instance()
        assert server.auth == {}
        assert server.owner_id == "user-1"
        assert server.attached_tools == []

    def test_skips_entry_without_instance_id(self) -> None:
        context = make_context(
            mcp_servers=[{"name": "JiraMCP"}],
            mcp_server_configs={},
        )

        assert MCPAccessResolver.resolve(context) == []

    def test_skips_entry_missing_from_configs(self) -> None:
        """The chat route already hard-blocks unauthenticated/missing
        instances before this ever runs — a miss here is just skipped, not
        raised, mirroring `PipesHubToolLoader`'s unconfigured-toolset skip."""
        context = make_context(
            mcp_servers=[{"instanceId": "inst-1", "name": "JiraMCP"}],
            mcp_server_configs={},
        )

        assert MCPAccessResolver.resolve(context) == []

    def test_skips_entry_whose_config_has_no_instance_key(self) -> None:
        context = make_context(
            mcp_servers=[{"instanceId": "inst-1", "name": "JiraMCP"}],
            mcp_server_configs={"inst-1": {"auth": {}}},
        )

        assert MCPAccessResolver.resolve(context) == []

    def test_owner_id_falls_back_to_context_user_id_when_missing(self) -> None:
        context = make_context(
            user_id="user-42",
            mcp_servers=[{"instanceId": "inst-1", "name": "JiraMCP"}],
            mcp_server_configs={"inst-1": {"instance": _instance(), "auth": {}}},
        )

        resolved = MCPAccessResolver.resolve(context)

        assert resolved[0].owner_id == "user-42"

    def test_attached_tools_none_for_assistant_path(self) -> None:
        """The assistant/placeholder agent's auto-attached servers carry no
        `"tools"` key at all — `attached_tools` must stay `None`, not `[]`,
        so `MCPToolProvider` knows there's no stored fallback list."""
        context = make_context(
            mcp_servers=[{"instanceId": "inst-1", "name": "JiraMCP"}],
            mcp_server_configs={"inst-1": {"instance": _instance(), "auth": {}}},
        )

        resolved = MCPAccessResolver.resolve(context)

        assert resolved[0].attached_tools is None

    def test_resolves_multiple_servers_in_order(self) -> None:
        context = make_context(
            mcp_servers=[
                {"instanceId": "inst-1", "name": "JiraMCP"},
                {"instanceId": "inst-2", "name": "SlackMCP"},
            ],
            mcp_server_configs={
                "inst-1": {"instance": _instance("inst-1"), "auth": {}},
                "inst-2": {"instance": _instance("inst-2"), "auth": {}},
            },
        )

        resolved = MCPAccessResolver.resolve(context)

        assert [s.instance_id for s in resolved] == ["inst-1", "inst-2"]

    def test_no_attached_servers_returns_empty_list(self) -> None:
        context = make_context()
        assert MCPAccessResolver.resolve(context) == []
