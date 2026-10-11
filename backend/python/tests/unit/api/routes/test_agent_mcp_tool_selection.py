"""Which tools of an attached MCP server an agent gets.

An attachment either lists its tools or takes all of them (`allTools`), which includes
tools the server adds after the agent was saved.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from app.api.routes.agent import (
    InvalidRequestError,
    _mcp_servers_for_chat,
    _parse_mcp_servers,
)
from tests.support.agent_routes import (
    AGENTS,
    FakeConfigService,
    InMemoryGraph,
    as_user,
    make_client,
)

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


def _server(**overrides: object) -> dict:
    return {"instanceId": "inst-1", "name": "github", "typeId": "github", **overrides}


class TestParse:
    def test_all_tools_needs_no_list(self) -> None:
        parsed = _parse_mcp_servers([_server(allTools=True, tools=[])])
        assert parsed["inst-1"]["allTools"] is True
        assert parsed["inst-1"]["tools"] == []

    def test_a_server_with_no_tools_and_not_all_is_rejected(self) -> None:
        with pytest.raises(InvalidRequestError, match="Choose at least one tool for MCP server"):
            _parse_mcp_servers([_server(displayName="GitHub", tools=[])])

    def test_missing_full_name_gets_the_discovery_name(self) -> None:
        parsed = _parse_mcp_servers([_server(tools=[{"name": "list_issues"}])])
        assert parsed["inst-1"]["tools"][0]["fullName"] == "mcp_github_list_issues"

    def test_a_given_full_name_is_kept(self) -> None:
        parsed = _parse_mcp_servers([_server(tools=[{"name": "list_issues", "fullName": "mcp_github_ab12_list_issues"}])])
        assert parsed["inst-1"]["tools"][0]["fullName"] == "mcp_github_ab12_list_issues"

    def test_a_tool_listed_twice_is_kept_once(self) -> None:
        parsed = _parse_mcp_servers([_server(tools=[{"name": "list_issues"}, {"name": "list_issues"}])])
        assert [t["name"] for t in parsed["inst-1"]["tools"]] == ["list_issues"]

    def test_all_tools_must_be_exactly_true(self) -> None:
        parsed = _parse_mcp_servers([_server(allTools="yes", tools=[{"name": "t"}])])
        assert parsed["inst-1"]["allTools"] is False


class TestChatSelection:
    SAVED = [{"name": "list_issues", "fullName": "mcp_github_list_issues"}]

    def test_all_tools_without_a_selection_discovers_everything(self) -> None:
        (server,) = _mcp_servers_for_chat([{**_server(), "allTools": True, "tools": self.SAVED}], None)
        assert server["tools"] is None

    def test_a_listed_attachment_keeps_its_list(self) -> None:
        (server,) = _mcp_servers_for_chat([{**_server(), "tools": self.SAVED}], None)
        assert server["tools"] == self.SAVED

    def test_a_selection_on_an_all_tools_server_also_matches_tools_added_later(self) -> None:
        attached = [{**_server(), "allTools": True, "tools": self.SAVED}]
        (server,) = _mcp_servers_for_chat(attached, {"mcp_github_list_issues", "mcp_github_create_pr"})
        assert sorted(t["fullName"] for t in server["tools"]) == ["mcp_github_create_pr", "mcp_github_list_issues"]

    def test_a_selection_on_a_listed_attachment_never_adds_tools(self) -> None:
        attached = [{**_server(), "tools": self.SAVED}]
        (server,) = _mcp_servers_for_chat(attached, {"mcp_github_list_issues", "mcp_github_create_pr"})
        assert [t["fullName"] for t in server["tools"]] == ["mcp_github_list_issues"]

    def test_a_server_the_selection_leaves_empty_is_dropped(self) -> None:
        assert _mcp_servers_for_chat([{**_server(), "tools": self.SAVED}], {"mcp_jira_search"}) == []

    def test_assistant_servers_ignore_other_servers_and_bare_prefixes(self) -> None:
        jira = {"instanceId": "j", "name": "Jira", "typeId": "jira", "tools": None}
        brave = {"instanceId": "b", "name": "Brave", "typeId": "brave-search", "tools": None}
        selected = _mcp_servers_for_chat(
            [jira, brave], {"mcp_jira_getIssue", "mcp_jira_", "mcp_exa_search", "slack.send", "mcp_brave_search_web_search"},
        )
        by_id = {s["instanceId"]: s["tools"] for s in selected}
        assert by_id == {
            "j": [{"name": "getIssue", "fullName": "mcp_jira_getIssue"}],
            "b": [{"name": "web_search", "fullName": "mcp_brave_search_web_search"}],
        }

    def test_assistant_servers_match_by_prefix(self) -> None:
        (server,) = _mcp_servers_for_chat([{**_server(), "tools": None}], {"mcp_github_create_pr"})
        assert server["tools"] == [{"name": "create_pr", "fullName": "mcp_github_create_pr"}]


@pytest.fixture
def graph() -> InMemoryGraph:
    g = InMemoryGraph()
    g.add_agent("private", "alice")
    return g


@pytest.fixture
def client(graph: InMemoryGraph) -> TestClient:
    c, _ = make_client(graph, FakeConfigService({
        "/services/mcp/instances/inst-1": {"_id": "inst-1", "orgId": "org-1", "name": "github"},
    }))
    return c


class TestSavedAttachment:
    def test_all_tools_is_stored_and_returned(self, client, graph) -> None:
        response = client.put("/api/v1/agent/private", headers=as_user("alice"), json={
            "mcpServers": [_server(allTools=True, tools=[{"name": "list_issues"}])],
        })
        assert response.status_code == 200
        (node,) = graph.nodes["agentMcpServers"].values()
        assert node["allTools"] is True
        (attached,) = client.get("/api/v1/agent/private", headers=as_user("alice")).json()["agent"]["mcpServers"]
        assert attached["allTools"] is True

    def test_saving_a_server_with_no_tools_writes_nothing(self, client, graph) -> None:
        response = client.put("/api/v1/agent/private", headers=as_user("alice"), json={
            "name": "Renamed", "mcpServers": [_server(tools=[])],
        })
        assert response.status_code == 400
        assert graph.nodes[AGENTS]["private"]["name"] != "Renamed"
        assert not graph.calls_to("update_agent")


class TestSavingIsAllOrNothing:
    """Replacing an agent's MCP servers removes the old ones and attaches the new ones in one
    transaction, so a failure leaves the agent with the servers it had."""

    def _save(self, client: "TestClient", **server: object) -> Any:  # noqa: ANN401
        return client.put("/api/v1/agent/private", headers=as_user("alice"), json={"mcpServers": [_server(**server)]})

    def _attached(self, graph: InMemoryGraph) -> list[dict]:
        from app.config.constants.arangodb import CollectionNames

        edges = graph.edges_from(CollectionNames.AGENT_HAS_MCP_SERVER.value, f"{CollectionNames.AGENT_INSTANCES.value}/private")
        nodes = graph.nodes.get(CollectionNames.AGENT_MCP_SERVERS.value, {})
        return [nodes[e["_to"].split("/", 1)[1]] for e in edges]

    @pytest.mark.parametrize("failing", ["batch_create_edges", "batch_upsert_nodes"])
    def test_a_failed_save_keeps_the_servers_the_agent_had(self, client: "TestClient", graph: InMemoryGraph, failing: str) -> None:
        assert self._save(client, allTools=True).status_code == 200
        (before,) = self._attached(graph)

        graph.fail(failing)
        response = self._save(client, tools=[{"name": "create_pr"}])

        assert response.status_code == 500
        assert self._attached(graph) == [before]

    def test_the_swap_runs_in_one_committed_transaction(self, client: "TestClient", graph: InMemoryGraph) -> None:
        from app.config.constants.arangodb import CollectionNames

        assert self._save(client, allTools=True).status_code == 200
        graph.calls.clear()
        graph.committed.clear()

        assert self._save(client, tools=[{"name": "create_pr"}]).status_code == 200

        mcp_transactions = [
            args for args, _ in graph.calls_to("begin_transaction")
            if CollectionNames.AGENT_MCP_SERVERS.value in args[1]
        ]
        assert len(mcp_transactions) == 1
        (attached,) = self._attached(graph)
        assert attached["allTools"] is False


class TestASaveNeverLeavesTheAgentWithoutItsServers:
    """The new servers are written, tools first and the agent link last, before the old ones are
    unlinked. Neo4j without explicit transactions undoes nothing on rollback (`auto_commit`), so
    a failure is cleaned up from what the graph shows rather than by the rollback."""

    SERVERS, TOOLS = "agentMcpServers", "agentTools"
    LINKS, TOOL_LINKS = "agentHasMcpServer", "mcpServerHasTool"
    NEW = {"mcpServers": [_server(tools=[{"name": "create_pr"}])]}

    def _seed(self, graph: InMemoryGraph, key: str = "mcp-old", tool: str = "tool-old") -> None:
        graph.add_node(self.SERVERS, {"_key": key, "instanceId": f"inst-{key}", "name": "jira"})
        graph.add_node(self.TOOLS, {"_key": tool, "name": "search", "fullName": "mcp_jira_search"})
        graph.add_edge(self.LINKS, {"_from": f"{AGENTS}/private", "_to": f"{self.SERVERS}/{key}"})
        graph.add_edge(self.TOOL_LINKS, {"_from": f"{self.SERVERS}/{key}", "_to": f"{self.TOOLS}/{tool}"})

    def _linked(self, graph: InMemoryGraph) -> list[str]:
        return sorted(e["_to"] for e in graph.edges_from(self.LINKS, f"{AGENTS}/private"))

    def _assert_old_intact(self, graph: InMemoryGraph) -> None:
        assert self._linked(graph) == [f"{self.SERVERS}/mcp-old"]
        assert set(graph.nodes[self.SERVERS]) == {"mcp-old"}
        assert set(graph.nodes[self.TOOLS]) == {"tool-old"}
        assert [e["_to"] for e in graph.edges_from(self.TOOL_LINKS, f"{self.SERVERS}/mcp-old")] == [
            f"{self.TOOLS}/tool-old",
        ]

    def _assert_new_only(self, graph: InMemoryGraph) -> None:
        (linked,) = self._linked(graph)
        key = linked.split("/", 1)[1]
        assert graph.nodes[self.SERVERS][key]["instanceId"] == "inst-1"
        assert set(graph.nodes[self.SERVERS]) == {key}
        (tool,) = graph.nodes[self.TOOLS].values()
        assert tool["name"] == "create_pr"

    def _save(self, client: TestClient, body: dict | None = None) -> Any:  # noqa: ANN401
        return client.put("/api/v1/agent/private", headers=as_user("alice"), json=body or self.NEW)

    @pytest.mark.parametrize("rollback_undoes_writes", [True, False], ids=["transactional", "auto_commit"])
    @pytest.mark.parametrize("failing,collection", [
        ("batch_upsert_nodes", "agentMcpServers"),
        ("batch_upsert_nodes", "agentTools"),
        ("batch_create_edges", "mcpServerHasTool"),
        ("batch_create_edges", "agentHasMcpServer"),
    ])
    @pytest.mark.parametrize("how", ["raises", "returns_false"])
    def test_a_failed_write_keeps_the_old_servers(
        self, client, graph, failing, collection, how, rollback_undoes_writes,
    ) -> None:
        graph.rollback_undoes_writes = rollback_undoes_writes
        self._seed(graph)
        real = getattr(graph, failing)

        async def fail_for(items: list[dict], target: str, transaction: str | None = None) -> bool:
            if target == collection:
                if how == "raises":
                    raise RuntimeError("write timed out on 10.0.0.7")
                return False
            return await real(items, target, transaction)
        setattr(graph, failing, fail_for)

        response = self._save(client)

        assert response.status_code == 500
        assert response.json()["detail"].startswith("We couldn't save this agent.")
        assert "10.0.0.7" not in response.text
        self._assert_old_intact(graph)

    def test_tools_are_linked_before_the_agent_and_all_in_the_saves_transaction(self, client, graph) -> None:
        self._seed(graph)
        writes: list[tuple[str, str | None]] = []
        for name in ("batch_upsert_nodes", "batch_create_edges"):
            real = getattr(graph, name)

            async def record(items: list[dict], target: str, transaction: str | None = None, real=real) -> bool:
                writes.append((target, transaction))
                return await real(items, target, transaction)
            setattr(graph, name, record)

        assert self._save(client).status_code == 200

        targets = [target for target, _ in writes]
        assert targets == [self.SERVERS, self.TOOLS, self.TOOL_LINKS, self.LINKS]
        (transaction,) = {txn for _, txn in writes}
        assert transaction in graph.committed
        self._assert_new_only(graph)

    @pytest.mark.parametrize("rollback_undoes_writes", [True, False], ids=["transactional", "auto_commit"])
    def test_a_failure_between_old_unlinks_finishes_on_the_new_servers(
        self, client, graph, rollback_undoes_writes,
    ) -> None:
        graph.rollback_undoes_writes = rollback_undoes_writes
        self._seed(graph)
        self._seed(graph, "mcp-old2", "tool-old2")
        real = graph.delete_all_edges_for_node
        failed: list[str] = []

        async def fail_second_once(node_key: str, collection: str, transaction: str | None = None) -> int:
            if node_key == f"{self.SERVERS}/mcp-old2" and collection == self.LINKS and not failed:
                failed.append(node_key)
                raise RuntimeError("write timed out on 10.0.0.7")
            return await real(node_key, collection, transaction)
        graph.delete_all_edges_for_node = fail_second_once

        response = self._save(client)

        assert response.status_code == 500
        if rollback_undoes_writes:
            assert self._linked(graph) == [f"{self.SERVERS}/mcp-old", f"{self.SERVERS}/mcp-old2"]
            assert set(graph.nodes[self.SERVERS]) == {"mcp-old", "mcp-old2"}
        else:
            # Writes that can't be undone: the old links were going anyway, so the save finishes.
            self._assert_new_only(graph)

    @pytest.mark.parametrize("rollback_undoes_writes", [True, False], ids=["transactional", "auto_commit"])
    def test_a_failure_removing_old_nodes_keeps_the_new_servers_linked(
        self, client, graph, rollback_undoes_writes,
    ) -> None:
        graph.rollback_undoes_writes = rollback_undoes_writes
        self._seed(graph)
        real = graph.delete_nodes
        failed: list[str] = []

        async def fail_old_tools_once(keys: list[str], collection: str, transaction: str | None = None) -> bool:
            if collection == self.TOOLS and "tool-old" in keys and not failed:
                failed.append(collection)
                raise RuntimeError("write timed out on 10.0.0.7")
            return await real(keys, collection, transaction)
        graph.delete_nodes = fail_old_tools_once

        response = self._save(client)

        assert response.status_code == 500
        if rollback_undoes_writes:
            self._assert_old_intact(graph)
        else:
            self._assert_new_only(graph)

    @pytest.mark.parametrize("rollback_undoes_writes", [True, False], ids=["transactional", "auto_commit"])
    def test_detaching_every_server_removes_them(self, client, graph, rollback_undoes_writes) -> None:
        graph.rollback_undoes_writes = rollback_undoes_writes
        self._seed(graph)

        assert self._save(client, {"mcpServers": []}).status_code == 200

        assert self._linked(graph) == []
        assert not graph.nodes.get(self.SERVERS)
        assert not graph.nodes.get(self.TOOLS)

    @pytest.mark.parametrize("rollback_undoes_writes", [True, False], ids=["transactional", "auto_commit"])
    def test_detaching_every_server_then_failing_to_remove_them_still_finishes(
        self, client, graph, rollback_undoes_writes,
    ) -> None:
        graph.rollback_undoes_writes = rollback_undoes_writes
        self._seed(graph)
        real = graph.delete_nodes
        failed: list[str] = []

        async def fail_old_tools_once(keys: list[str], collection: str, transaction: str | None = None) -> bool:
            if collection == self.TOOLS and not failed:
                failed.append(collection)
                raise RuntimeError("write timed out on 10.0.0.7")
            return await real(keys, collection, transaction)
        graph.delete_nodes = fail_old_tools_once

        response = self._save(client, {"mcpServers": []})

        assert response.status_code == 500
        if rollback_undoes_writes:
            self._assert_old_intact(graph)
        else:
            # Every link was already gone: the old server and its tool go too, none left orphaned.
            assert self._linked(graph) == []
            assert not graph.nodes.get(self.SERVERS)
            assert not graph.nodes.get(self.TOOLS)

    @pytest.mark.parametrize("rollback_undoes_writes", [True, False], ids=["transactional", "auto_commit"])
    def test_a_failed_create_leaves_no_mcp_nodes_behind(self, client, graph, rollback_undoes_writes) -> None:
        graph.rollback_undoes_writes = rollback_undoes_writes
        real = graph.batch_create_edges

        async def fail_agent_link(items: list[dict], target: str, transaction: str | None = None) -> bool:
            if target == self.LINKS:
                raise RuntimeError("write timed out on 10.0.0.7")
            return await real(items, target, transaction)
        graph.batch_create_edges = fail_agent_link

        response = client.post("/api/v1/agent/create", headers=as_user("alice"), json={"name": "New", **self.NEW})

        assert response.status_code == 500
        assert not graph.nodes.get(self.SERVERS)
        assert not graph.nodes.get(self.TOOLS)
        assert not graph.edges.get(self.TOOL_LINKS)

