"""SEC-8: an agent's MCP attachments are bounded, and take their name and type from the stored
server rather than from the request."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.api.routes.agent import (
    MAX_MCP_SERVERS_PER_AGENT,
    MAX_TOOLS_PER_MCP_SERVER,
    InvalidRequestError,
    _bind_mcp_attachments_to_instances,
    _parse_mcp_servers,
)
from tests.support.agent_routes import (
    FakeConfigService,
    InMemoryGraph,
    as_user,
    make_client,
)

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


def _attachment(instance_id: str = "inst-1", **overrides: object) -> dict:
    return {"instanceId": instance_id, "name": "github", "tools": [{"name": "list_issues"}], **overrides}


class TestLimits:
    def test_too_many_servers(self) -> None:
        servers = [_attachment(f"inst-{n}") for n in range(MAX_MCP_SERVERS_PER_AGENT + 1)]
        with pytest.raises(InvalidRequestError, match=f"at most {MAX_MCP_SERVERS_PER_AGENT} MCP servers"):
            _parse_mcp_servers(servers)

    def test_too_many_tools_on_one_server(self) -> None:
        tools = [{"name": f"tool_{n}"} for n in range(MAX_TOOLS_PER_MCP_SERVER + 1)]
        with pytest.raises(InvalidRequestError, match="attach all of its tools instead"):
            _parse_mcp_servers([_attachment(tools=tools)])

    def test_with_all_tools_on_a_bigger_server_is_kept_and_its_list_capped(self) -> None:
        """The agent gets every tool anyway; the saved list is only what the builder shows."""
        tools = [{"name": f"tool_{n}"} for n in range(MAX_TOOLS_PER_MCP_SERVER + 20)]
        parsed = _parse_mcp_servers([_attachment(allTools=True, tools=tools)])
        assert parsed["inst-1"]["allTools"] is True
        assert len(parsed["inst-1"]["tools"]) == MAX_TOOLS_PER_MCP_SERVER

    def test_the_limits_themselves_are_allowed(self) -> None:
        tools = [{"name": f"tool_{n}"} for n in range(MAX_TOOLS_PER_MCP_SERVER)]
        servers = [_attachment(f"inst-{n}", tools=tools if n == 0 else [{"name": "t"}]) for n in range(MAX_MCP_SERVERS_PER_AGENT)]
        parsed = _parse_mcp_servers(servers)
        assert len(parsed) == MAX_MCP_SERVERS_PER_AGENT
        assert len(parsed["inst-0"]["tools"]) == MAX_TOOLS_PER_MCP_SERVER

    @pytest.mark.parametrize("name", ["x" * 201, "list\nissues", "bad\x00name"])
    def test_a_tool_name_that_is_too_long_or_has_control_characters(self, name: str) -> None:
        with pytest.raises(InvalidRequestError, match="is not a valid tool name"):
            _parse_mcp_servers([_attachment(tools=[{"name": name}])])

    def test_long_descriptions_and_display_names_are_cut(self) -> None:
        parsed = _parse_mcp_servers([_attachment(displayName="D" * 500, tools=[{"name": "t", "description": "d" * 5000}])])
        assert len(parsed["inst-1"]["displayName"]) == 200
        assert len(parsed["inst-1"]["tools"][0]["description"]) == 2000


class TestIdentityComesFromTheStoredServer:
    def test_name_and_type_are_the_servers(self) -> None:
        parsed = _parse_mcp_servers([_attachment(name="anything", typeId="lies")])
        bound = _bind_mcp_attachments_to_instances(parsed, {"inst-1": {"_id": "inst-1", "name": "GitHub", "typeId": "github"}})
        assert bound["inst-1"]["name"] == "GitHub"
        assert bound["inst-1"]["typeId"] == "github"

    def test_leaving_out_the_type_does_not_skip_the_one_per_type_rule(self) -> None:
        parsed = _parse_mcp_servers([_attachment("inst-1"), _attachment("inst-2")])
        instances = {
            "inst-1": {"_id": "inst-1", "name": "GitHub A", "typeId": "github"},
            "inst-2": {"_id": "inst-2", "name": "GitHub B", "typeId": "github"},
        }
        with pytest.raises(InvalidRequestError, match="same type \\('github'\\)"):
            _bind_mcp_attachments_to_instances(parsed, instances)

    def test_custom_servers_have_no_type_and_never_conflict(self) -> None:
        parsed = _parse_mcp_servers([_attachment("inst-1"), _attachment("inst-2")])
        instances = {"inst-1": {"_id": "inst-1", "name": "A"}, "inst-2": {"_id": "inst-2", "name": "B"}}
        bound = _bind_mcp_attachments_to_instances(parsed, instances)
        assert [bound[i]["typeId"] for i in ("inst-1", "inst-2")] == [None, None]


@pytest.fixture
def graph() -> InMemoryGraph:
    g = InMemoryGraph()
    g.add_agent("private", "alice")
    return g


@pytest.fixture
def client(graph: InMemoryGraph) -> TestClient:
    c, _ = make_client(graph, FakeConfigService({
        "/services/mcp/instances/inst-1": {"_id": "inst-1", "orgId": "org-1", "name": "GitHub", "typeId": "github"},
    }))
    return c


def test_a_saved_attachment_carries_the_servers_name_and_type(client: TestClient, graph: InMemoryGraph) -> None:
    response = client.put("/api/v1/agent/private", headers=as_user("alice"), json={
        "mcpServers": [_attachment(name="Totally Not GitHub", typeId="slack")],
    })

    assert response.status_code == 200
    (node,) = graph.nodes["agentMcpServers"].values()
    assert (node["name"], node["typeId"]) == ("GitHub", "github")
