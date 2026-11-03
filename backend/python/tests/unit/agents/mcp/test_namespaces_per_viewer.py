"""A tool selection made from a listing names exactly one MCP instance.

Two instances of one type (an org GitHub and someone's personal GitHub) must not list the same
tool names, and the chat that receives a selection must name the tools the same way.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agents.agent_loop.mcp_session import MCPSessionManager
from app.agents.agent_loop.mcp_tool_loader import MCPToolProvider
from app.agents.mcp import service as mcp_service
from app.agents.mcp.models import MCPToolInfo
from app.agents.mcp.naming import assign_namespaces, instance_tag
from app.api.routes import mcp_servers
from app.api.routes.agent import _mcp_servers_for_chat
from app.api.routes.mcp_servers import get_my_mcp_servers
from tests.unit.agents.adapter.conftest import make_context
from tests.unit.api.routes.mcp_route_fakes import FakeConfigService, route_request


def _github(instance_id: str, created_at: int, **extra: Any) -> dict[str, Any]:  # noqa: ANN401
    return {
        "_id": instance_id, "orgId": "org-1", "createdBy": "u-alice", "name": "GitHub", "typeId": "github",
        "transport": "streamable_http", "authMode": "api_token", "url": "https://api.githubcopilot.com/mcp/",
        "createdAt": created_at, "updatedAt": created_at, **extra,
    }


ORG = _github("org-gh", 1)
MINE = _github("my-gh", 2, scope="personal")
TAG = instance_tag("my-gh")


class TestAssignNamespaces:
    def test_the_oldest_keeps_the_plain_key_and_the_order_given_does_not_matter(self) -> None:
        expected = {"org-gh": "github", "my-gh": f"github_{TAG}"}
        assert assign_namespaces([ORG, MINE]) == expected
        assert assign_namespaces([MINE, ORG]) == expected

    def test_different_keys_are_independent(self) -> None:
        jira = {**_github("jira", 3), "typeId": "jira", "name": "Jira"}
        assert assign_namespaces([ORG, jira]) == {"org-gh": "github", "jira": "jira"}

    def test_the_type_wins_over_the_name(self) -> None:
        renamed = {**_github("x", 1), "name": "Work GitHub"}
        assert assign_namespaces([renamed]) == {"x": "github"}


class TestListingNamesEachInstance:
    async def test_two_instances_of_a_type_list_different_names(self) -> None:
        store = FakeConfigService({
            "/services/mcp/instances/org-gh": ORG,
            "/services/mcp/user-instances/org-1/u-alice/my-gh": MINE,
            "/services/mcp/credentials/org-gh/u-alice": {"isAuthenticated": True, "apiToken": "t"},
            "/services/mcp/credentials/my-gh/u-alice": {"isAuthenticated": True, "apiToken": "t"},
        })

        async def _discover(instance: dict, auth: dict, owner: str, cfg: object, **kwargs: Any) -> tuple:  # noqa: ANN401
            ns = kwargs["namespace"]
            return [MCPToolInfo(name="create_issue", namespaced_name=f"mcp_{ns}_create_issue")], auth

        with patch.object(mcp_servers, "discover_tools_for_owner", new=_discover), \
             patch.object(mcp_servers, "_check_user_is_admin", new=AsyncMock(return_value=False)):
            listed = await get_my_mcp_servers(route_request(store, user_id="u-alice"), include_tools=True)

        names = {i["_id"]: [t["namespacedName"] for t in i["tools"]] for i in listed["instances"]}
        assert names == {"org-gh": ["mcp_github_create_issue"], "my-gh": [f"mcp_github_{TAG}_create_issue"]}


class TestTheAssistantUsesTheSameNames:
    async def test_authenticated_servers_carry_the_listing_namespace(self) -> None:
        async def _auth(instance: dict, owner: str, cfg: object) -> dict:
            return {"isAuthenticated": True}

        with patch.object(mcp_service, "resolve_effective_user_auth", new=_auth):
            servers = await mcp_service.get_authenticated_mcp_servers("u-alice", MagicMock(), [ORG, MINE])

        assert {s["instanceId"]: s["namespace"] for s in servers} == {"org-gh": "github", "my-gh": f"github_{TAG}"}

    def _assistant_servers(self) -> list[dict[str, Any]]:
        return [
            {"instanceId": "org-gh", "name": "GitHub", "typeId": "github", "namespace": "github"},
            {"instanceId": "my-gh", "name": "GitHub", "typeId": "github", "namespace": f"github_{TAG}"},
        ]

    def test_selecting_the_org_tool_enables_only_the_org_server(self) -> None:
        (server,) = _mcp_servers_for_chat(self._assistant_servers(), {"mcp_github_create_issue"})
        assert server["instanceId"] == "org-gh"
        assert server["tools"] == [{"name": "create_issue", "fullName": "mcp_github_create_issue"}]

    def test_selecting_my_tool_enables_only_my_server_and_gives_the_other_nothing(self) -> None:
        (server,) = _mcp_servers_for_chat(self._assistant_servers(), {f"mcp_github_{TAG}_create_issue"})
        assert server["instanceId"] == "my-gh"
        assert server["tools"] == [{"name": "create_issue", "fullName": f"mcp_github_{TAG}_create_issue"}]

    def test_a_custom_agents_attachment_takes_the_tagged_name_its_listing_gave(self) -> None:
        """Graph attachments carry no namespace; the builder listed this one tagged."""
        attached = [{"instanceId": "my-gh", "name": "GitHub", "typeId": "github", "allTools": True, "tools": []}]
        (server,) = _mcp_servers_for_chat(attached, {f"mcp_github_{TAG}_create_issue"})
        assert server["tools"] == [{"name": "create_issue", "fullName": f"mcp_github_{TAG}_create_issue"}]

    async def test_the_loader_registers_tools_under_the_given_namespace(self) -> None:
        context = make_context(
            mcp_servers=[{"instanceId": "my-gh", "name": "GitHub", "typeId": "github", "namespace": f"github_{TAG}"}],
            mcp_server_configs={"my-gh": {"instance": MINE, "auth": {}, "ownerId": "u-alice"}},
        )

        async def _tools(self: MCPSessionManager, server: Any, namespace: str) -> tuple[list[MCPToolInfo], None]:  # noqa: ANN401
            return [MCPToolInfo(name="create_issue", namespaced_name=f"mcp_{namespace}_create_issue")], None

        registry = ToolRegistry()
        with patch.object(MCPSessionManager, "tools", new=_tools):
            await MCPToolProvider().load_into(registry, context)

        assert registry.has(f"mcp_github_{TAG}_create_issue")
