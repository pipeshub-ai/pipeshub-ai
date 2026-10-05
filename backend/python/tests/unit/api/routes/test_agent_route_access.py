"""Who may read, change, share and delete an agent, driven through the real
routes in `app/api/routes/agent.py`.

Only the graph database and the config store are replaced (see
`tests/support/agent_routes.py`); routing, the scope check, body parsing and
every permission decision are the production code. The identity always comes
from the authenticated request, so each test also sends ids of its own in the
query string or body to prove they are ignored.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from tests.support.agent_routes import (
    AGENTS,
    ORGS_COLL,
    PERMISSION,
    USERS_COLL,
    InMemoryGraph,
    as_user,
    make_client,
    user_key,
)

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

NOT_FOUND = "Agent not found or you don't have access to it"


@pytest.fixture
def graph() -> InMemoryGraph:
    g = InMemoryGraph()
    g.add_agent("private", "alice", description="alice only")
    g.add_agent("shared", "alice", share_with_org=True)
    return g


@pytest.fixture
def client(graph: InMemoryGraph) -> TestClient:
    c, _ = make_client(graph)
    return c


def _org_edges(graph: InMemoryGraph, agent_key: str) -> list[dict]:
    return [e for e in graph.edges_to(PERMISSION, f"{AGENTS}/{agent_key}") if e.get("type") == "ORG"]


def _no_leak(response) -> None:
    text = response.text
    assert "10.0.0.7" not in text and "connection refused" not in text
    assert "Traceback" not in text


class TestAuthentication:
    def test_request_without_identity_is_rejected(self, client) -> None:
        response = client.get("/api/v1/agent/private")
        assert response.status_code == 401

    def test_identity_without_org_is_rejected(self, graph) -> None:
        c, _ = make_client(graph, extra_users={"orgless": {"userId": "u-alice"}})
        response = c.get("/api/v1/agent/private", headers=as_user("orgless"))
        assert response.status_code == 401
        assert "Authentication required" in response.json()["detail"]

    def test_oauth_token_without_agent_read_scope_is_rejected(self, graph) -> None:
        c, _ = make_client(graph, extra_users={"app": {
            "userId": "u-alice", "orgId": "org-1", "isOAuth": True, "oauthScopes": ["agent:write"],
        }})
        response = c.get("/api/v1/agent/private", headers=as_user("app"))
        assert response.status_code == 403
        assert "Insufficient scope" in response.json()["detail"]


class TestReadAgent:
    def test_owner_reads_own_agent_with_edit_rights(self, client) -> None:
        response = client.get("/api/v1/agent/private", headers=as_user("alice"))
        assert response.status_code == 200
        agent = response.json()["agent"]
        assert agent["can_edit"] is True and agent["can_delete"] is True
        # createdBy goes out as the creator's public userId, not the graph key.
        assert agent["createdBy"] == "u-alice"
        assert agent["usesOrgDefault"] is True

    @pytest.mark.parametrize("caller", ["bob", "mallory"])
    def test_unshared_agent_is_invisible_to_everyone_else(self, client, caller) -> None:
        response = client.get(
            "/api/v1/agent/private?userId=u-alice&orgId=org-1", headers=as_user(caller),
        )
        assert response.status_code == 404
        assert response.json()["detail"] == NOT_FOUND

    def test_org_shared_agent_is_read_only_for_a_colleague(self, client) -> None:
        response = client.get("/api/v1/agent/shared", headers=as_user("bob"))
        assert response.status_code == 200
        agent = response.json()["agent"]
        assert agent["access_type"] == "ORG"
        assert agent["can_edit"] is False and agent["can_delete"] is False

    def test_org_shared_agent_is_invisible_to_another_org(self, client, graph) -> None:
        response = client.get("/api/v1/agent/shared?orgId=org-1", headers=as_user("mallory"))
        assert response.status_code == 404
        # The permission check ran with mallory's own org, not the query string's.
        (args, _), = graph.calls_to("check_agent_permission")
        assert args == ("shared", user_key("mallory"), "org-2")

    def test_deleted_agent_reads_as_not_found(self, client, graph) -> None:
        graph.nodes[AGENTS]["private"]["isDeleted"] = True
        response = client.get("/api/v1/agent/private", headers=as_user("alice"))
        assert response.status_code == 404

    def test_database_outage_is_not_reported_as_not_found(self, client, graph) -> None:
        graph.fail("check_agent_permission")
        response = client.get("/api/v1/agent/private", headers=as_user("alice"))
        assert response.status_code != 404
        assert response.json()["detail"].startswith("We couldn't load this agent.")
        _no_leak(response)

    def test_user_lookup_outage_is_not_reported_as_not_found(self, client, graph) -> None:
        graph.fail("get_user_by_user_id")
        response = client.get("/api/v1/agent/private", headers=as_user("alice"))
        assert response.status_code == 500
        assert response.json()["detail"] == "Failed to retrieve user information"
        _no_leak(response)

    def test_models_are_enriched_from_the_model_catalogue(self, graph) -> None:
        from tests.support.agent_routes import FakeConfigService

        graph.nodes[AGENTS]["private"]["models"] = ["m1_gpt-x", "gone"]
        config = FakeConfigService({"/services/aiModels": {"llm": [
            {"modelKey": "m1", "provider": "openai", "isReasoning": True},
        ]}})
        c, _ = make_client(graph, config)
        agent = c.get("/api/v1/agent/private", headers=as_user("alice")).json()["agent"]
        assert agent["usesOrgDefault"] is False
        assert agent["models"][0] == {
            "modelKey": "m1", "modelName": "gpt-x", "provider": "openai", "isReasoning": True,
            "isMultimodal": False, "isDefault": False, "modelType": "llm", "modelFriendlyName": "gpt-x",
        }
        # A model removed from the catalogue is still listed, marked unknown.
        assert agent["models"][1]["provider"] == "unknown"


class TestUpdateAgent:
    def test_owner_can_rename(self, client, graph) -> None:
        response = client.put(
            "/api/v1/agent/private", headers=as_user("alice"), json={"name": "Renamed"},
        )
        assert response.status_code == 200
        assert graph.nodes[AGENTS]["private"]["name"] == "Renamed"

    def test_colleague_with_read_access_cannot_edit(self, client, graph) -> None:
        response = client.put(
            "/api/v1/agent/shared", headers=as_user("bob"),
            json={"name": "Hijacked", "createdBy": user_key("bob")},
        )
        assert response.status_code == 403
        assert "only owner can edit" in response.json()["detail"]
        assert graph.nodes[AGENTS]["shared"]["name"] == "Agent shared"
        assert not graph.calls_to("update_agent")

    @pytest.mark.parametrize("caller,agent", [("bob", "private"), ("mallory", "private"), ("mallory", "shared")])
    def test_agent_outside_reach_reads_as_not_found(self, client, graph, caller, agent) -> None:
        response = client.put(f"/api/v1/agent/{agent}", headers=as_user(caller), json={"name": "x"})
        assert response.status_code == 404
        assert graph.nodes[AGENTS][agent]["name"] == f"Agent {agent}"

    def test_body_cannot_rewrite_ownership_fields(self, client, graph) -> None:
        response = client.put("/api/v1/agent/private", headers=as_user("alice"), json={
            "name": "ok", "createdBy": user_key("mallory"), "orgId": "org-2", "isDeleted": True,
        })
        assert response.status_code == 200
        stored = graph.nodes[AGENTS]["private"]
        assert stored["createdBy"] == user_key("alice")
        assert stored["isDeleted"] is False
        assert "orgId" not in stored

    def test_reader_cannot_turn_org_sharing_off(self, client, graph) -> None:
        response = client.put("/api/v1/agent/shared", headers=as_user("bob"), json={"shareWithOrg": False})
        assert response.status_code == 403
        assert len(_org_edges(graph, "shared")) == 1

    def test_owner_shares_with_their_own_org_only(self, client, graph) -> None:
        response = client.put(
            "/api/v1/agent/private", headers=as_user("alice"),
            json={"shareWithOrg": True, "orgId": "org-2"},
        )
        assert response.status_code == 200
        (edge,) = _org_edges(graph, "private")
        assert edge["_from"] == f"{ORGS_COLL}/org-1"
        assert edge["role"] == "READER"
        # Now visible to a colleague, still not to another org.
        assert client.get("/api/v1/agent/private", headers=as_user("bob")).status_code == 200
        assert client.get("/api/v1/agent/private", headers=as_user("mallory")).status_code == 404

    def test_owner_stops_sharing(self, client, graph) -> None:
        response = client.put("/api/v1/agent/shared", headers=as_user("alice"), json={"shareWithOrg": False})
        assert response.status_code == 200
        assert _org_edges(graph, "shared") == []
        assert client.get("/api/v1/agent/shared", headers=as_user("bob")).status_code == 404

    def test_sharing_flag_already_in_place_writes_nothing(self, client, graph) -> None:
        response = client.put("/api/v1/agent/shared", headers=as_user("alice"), json={"shareWithOrg": True})
        assert response.status_code == 200
        assert not graph.calls_to("batch_create_edges")
        assert len(_org_edges(graph, "shared")) == 1

    def test_service_account_agent_cannot_be_unshared(self, client, graph) -> None:
        graph.nodes[AGENTS]["shared"]["isServiceAccount"] = True
        response = client.put("/api/v1/agent/shared", headers=as_user("alice"), json={"shareWithOrg": False})
        assert response.status_code == 400
        assert "must always be shared" in response.json()["detail"]
        assert len(_org_edges(graph, "shared")) == 1

    def test_service_account_agent_cannot_become_a_regular_agent(self, client, graph) -> None:
        graph.nodes[AGENTS]["shared"]["isServiceAccount"] = True
        response = client.put("/api/v1/agent/shared", headers=as_user("alice"), json={"isServiceAccount": False})
        assert response.status_code == 400
        assert graph.nodes[AGENTS]["shared"]["isServiceAccount"] is True

    def test_becoming_a_service_account_forces_org_sharing(self, client, graph) -> None:
        response = client.put("/api/v1/agent/private", headers=as_user("alice"), json={"isServiceAccount": True})
        assert response.status_code == 200
        assert len(_org_edges(graph, "private")) == 1
        assert graph.nodes[AGENTS]["private"]["isServiceAccount"] is True

    @pytest.mark.parametrize("body,fragment", [
        ({"models": [{"modelKey": "m1", "isReasoning": False}]}, "at least one reasoning model"),
        ({"defaultReasoningEffort": "extreme"}, "Invalid defaultReasoningEffort 'extreme'"),
    ])
    def test_invalid_input_is_rejected_before_anything_is_written(self, client, graph, body, fragment) -> None:
        response = client.put("/api/v1/agent/private", headers=as_user("alice"), json=body)
        assert response.status_code == 400
        assert fragment in response.json()["detail"]
        assert not graph.calls_to("update_agent")
        assert not graph.calls_to("begin_transaction")

    def test_invalid_mcp_servers_leave_the_whole_edit_unsaved(self, client, graph) -> None:
        response = client.put("/api/v1/agent/private", headers=as_user("alice"), json={
            "name": "Renamed",
            "mcpServers": [
                {"instanceId": "i1", "name": "a", "typeId": "github"},
                {"instanceId": "i2", "name": "b", "typeId": "github"},
            ],
        })
        assert response.status_code == 400
        assert "two MCP server instances of the same type" in response.json()["detail"]
        assert graph.nodes[AGENTS]["private"]["name"] == "Agent private"
        assert not graph.calls_to("update_agent")

    def test_empty_models_list_reverts_to_org_default(self, client, graph) -> None:
        graph.nodes[AGENTS]["private"]["models"] = ["m1"]
        response = client.put("/api/v1/agent/private", headers=as_user("alice"), json={"models": []})
        assert response.status_code == 200
        assert graph.nodes[AGENTS]["private"]["models"] == []

    def test_empty_body_is_rejected(self, client) -> None:
        response = client.put("/api/v1/agent/private", headers=as_user("alice"), content=b"")
        assert response.status_code == 400
        assert response.json()["detail"] == "Invalid request: Request body is required"

    @pytest.mark.parametrize("raw,fragment", [
        (b"{not json", "Invalid JSON. Check that the request body is valid JSON"),
        (b"\xff\xfe", "Invalid JSON. Check that the request body is valid JSON"),
        (b'["name"]', "must be a JSON object"),
    ])
    def test_unreadable_body_is_explained_without_parser_internals(self, client, graph, raw, fragment) -> None:
        response = client.put("/api/v1/agent/private", headers=as_user("alice"), content=raw)
        assert response.status_code == 400
        detail = response.json()["detail"]
        assert fragment in detail
        assert "line 1 column" not in detail and "codec" not in detail
        assert not graph.calls_to("update_agent")

    def test_storage_refusing_the_update_is_a_server_error(self, client, graph) -> None:
        async def refuse(*_a: object, **_k: object) -> bool:
            return False
        graph.update_agent = refuse
        response = client.put("/api/v1/agent/private", headers=as_user("alice"), json={"name": "x"})
        assert response.status_code == 500

    def test_database_outage_is_not_reported_as_not_found(self, client, graph) -> None:
        graph.fail("check_agent_permission")
        response = client.put("/api/v1/agent/private", headers=as_user("alice"), json={"name": "x"})
        assert response.status_code != 404
        assert response.json()["detail"].startswith("We couldn't save this agent.")
        _no_leak(response)


class TestUpdateAttachments:
    """Replacing toolsets, knowledge, MCP servers and skills on an agent."""

    def _seed_toolset(self, graph: InMemoryGraph) -> None:
        graph.add_node("agentToolsets", {"_key": "ts-old", "name": "jira"})
        graph.add_node("agentTools", {"_key": "tool-old", "name": "search", "fullName": "jira.search"})
        graph.add_edge("agentHasToolset", {"_from": f"{AGENTS}/private", "_to": "agentToolsets/ts-old"})
        graph.add_edge("toolsetHasTool", {"_from": "agentToolsets/ts-old", "_to": "agentTools/tool-old"})

    def test_toolsets_are_replaced(self, client, graph) -> None:
        self._seed_toolset(graph)
        response = client.put("/api/v1/agent/private", headers=as_user("alice"), json={"toolsets": [
            {"name": "Slack", "instanceId": "inst-1", "tools": [{"name": "send"}]},
        ]})
        assert response.status_code == 200
        assert "ts-old" not in graph.nodes["agentToolsets"]
        assert "tool-old" not in graph.nodes["agentTools"]
        (toolset,) = graph.nodes["agentToolsets"].values()
        assert toolset["name"] == "slack" and toolset["instanceId"] == "inst-1"
        (tool,) = graph.nodes["agentTools"].values()
        assert tool["fullName"] == "slack.send"

    def test_replacing_with_several_toolsets_files_each_tool_correctly(self, client, graph) -> None:
        response = client.put("/api/v1/agent/private", headers=as_user("alice"), json={"toolsets": [
            {"name": "jira", "tools": [{"name": "search"}]},
            {"name": "slack", "tools": [{"name": "send"}]},
        ]})
        assert response.status_code == 200
        stored = {t["fullName"]: t["toolsetName"] for t in graph.nodes["agentTools"].values()}
        assert stored == {"jira.search": "jira", "slack.send": "slack"}

    def test_failed_toolset_removal_is_rolled_back(self, client, graph) -> None:
        self._seed_toolset(graph)
        graph.fail("delete_nodes")
        response = client.put("/api/v1/agent/private", headers=as_user("alice"), json={"toolsets": []})
        assert response.status_code == 500
        assert response.json()["detail"].startswith("We couldn't save this agent.")
        assert graph.rolled_back
        _no_leak(response)

    def test_mcp_servers_are_replaced(self, client, graph) -> None:
        graph.add_node("agentMcpServers", {"_key": "mcp-old", "instanceId": "old"})
        graph.add_node("agentTools", {"_key": "mcp-tool-old", "name": "t"})
        graph.add_edge("agentHasMcpServer", {"_from": f"{AGENTS}/private", "_to": "agentMcpServers/mcp-old"})
        graph.add_edge("mcpServerHasTool", {"_from": "agentMcpServers/mcp-old", "_to": "agentTools/mcp-tool-old"})
        response = client.put("/api/v1/agent/private", headers=as_user("alice"), json={"mcpServers": [
            {"instanceId": "inst-9", "name": "github", "typeId": "github", "tools": [{"name": "list_issues"}]},
        ]})
        assert response.status_code == 200
        assert "mcp-old" not in graph.nodes["agentMcpServers"]
        (server,) = graph.nodes["agentMcpServers"].values()
        assert server["instanceId"] == "inst-9" and server["typeId"] == "github"
        assert server["userId"] == "u-alice"
        (tool,) = graph.nodes["agentTools"].values()
        assert tool["fullName"] == "github.list_issues"
        assert len(graph.committed) == 2

    def test_detaching_every_mcp_server(self, client, graph) -> None:
        graph.add_node("agentMcpServers", {"_key": "mcp-old", "instanceId": "old"})
        graph.add_edge("agentHasMcpServer", {"_from": f"{AGENTS}/private", "_to": "agentMcpServers/mcp-old"})
        response = client.put("/api/v1/agent/private", headers=as_user("alice"), json={"mcpServers": []})
        assert response.status_code == 200
        assert graph.nodes["agentMcpServers"] == {}

    def test_failed_mcp_attach_is_rolled_back(self, client, graph) -> None:
        async def flaky(edges: list[dict], collection: str, transaction: str | None = None) -> bool:
            if collection == "mcpServerHasTool":
                raise RuntimeError("edge write failed")
            return await InMemoryGraph.batch_create_edges(graph, edges, collection, transaction)
        graph.batch_create_edges = flaky
        response = client.put("/api/v1/agent/private", headers=as_user("alice"), json={"mcpServers": [
            {"instanceId": "inst-9", "name": "github", "tools": [{"name": "list_issues"}]},
        ]})
        assert response.status_code == 500
        assert graph.rolled_back
        _no_leak(response)

    def test_knowledge_is_replaced(self, client, graph) -> None:
        graph.add_node("agentKnowledge", {"_key": "kn-old", "connectorId": "old"})
        graph.add_edge("agentHasKnowledge", {"_from": f"{AGENTS}/private", "_to": "agentKnowledge/kn-old"})
        response = client.put("/api/v1/agent/private", headers=as_user("alice"), json={"knowledge": [
            {"connectorId": "conn-1", "filters": '{"recordGroups": ["g1"]}'},
            {"connectorId": "  "},
        ]})
        assert response.status_code == 200
        (node,) = graph.nodes["agentKnowledge"].values()
        assert node["connectorId"] == "conn-1"
        assert json.loads(node["filters"]) == {"recordGroups": ["g1"]}

    @pytest.mark.parametrize("rollback_undoes_writes", [True, False], ids=["transactional", "auto_commit"])
    @pytest.mark.parametrize("failing", ["batch_upsert_nodes", "batch_create_edges"])
    @pytest.mark.parametrize("how", ["raises", "returns_false"])
    def test_failed_knowledge_save_keeps_the_old_knowledge(
        self, client, graph, failing, how, rollback_undoes_writes,
    ) -> None:
        graph.rollback_undoes_writes = rollback_undoes_writes
        graph.add_node("agentKnowledge", {"_key": "kn-old", "connectorId": "old"})
        graph.add_edge("agentHasKnowledge", {"_from": f"{AGENTS}/private", "_to": "agentKnowledge/kn-old"})
        real = getattr(graph, failing)

        async def fail_for_knowledge(items: list[dict], collection: str, transaction: str | None = None) -> bool:
            if collection in ("agentKnowledge", "agentHasKnowledge"):
                if how == "raises":
                    raise RuntimeError("write timed out on 10.0.0.7")
                return False
            return await real(items, collection, transaction)
        setattr(graph, failing, fail_for_knowledge)
        response = client.put("/api/v1/agent/private", headers=as_user("alice"), json={
            "knowledge": [{"connectorId": "conn-1"}],
        })
        assert response.status_code == 500
        assert response.json()["detail"].startswith("We couldn't save this agent.")
        _no_leak(response)
        assert graph.rolled_back
        assert set(graph.nodes["agentKnowledge"]) == {"kn-old"}
        assert [e["_to"] for e in graph.edges_from("agentHasKnowledge", f"{AGENTS}/private")] == [
            "agentKnowledge/kn-old",
        ]

    @pytest.mark.parametrize("rollback_undoes_writes", [True, False], ids=["transactional", "auto_commit"])
    def test_failed_removal_of_old_knowledge_leaves_exactly_the_old_knowledge(
        self, client, graph, rollback_undoes_writes,
    ) -> None:
        graph.rollback_undoes_writes = rollback_undoes_writes
        graph.add_node("agentKnowledge", {"_key": "kn-old", "connectorId": "old"})
        graph.add_edge("agentHasKnowledge", {"_from": f"{AGENTS}/private", "_to": "agentKnowledge/kn-old"})
        real = graph.delete_all_edges_for_node

        async def fail_on_old(node_key: str, collection: str, transaction: str | None = None) -> int:
            if node_key == "agentKnowledge/kn-old":
                raise RuntimeError("write timed out on 10.0.0.7")
            return await real(node_key, collection, transaction)
        graph.delete_all_edges_for_node = fail_on_old
        response = client.put("/api/v1/agent/private", headers=as_user("alice"), json={
            "knowledge": [{"connectorId": "conn-1"}],
        })
        assert response.status_code == 500
        _no_leak(response)
        assert set(graph.nodes["agentKnowledge"]) == {"kn-old"}
        assert [e["_to"] for e in graph.edges_from("agentHasKnowledge", f"{AGENTS}/private")] == [
            "agentKnowledge/kn-old",
        ]

    def _seed_two_old(self, graph: InMemoryGraph) -> None:
        for key in ("kn-old", "kn-old2"):
            graph.add_node("agentKnowledge", {"_key": key, "connectorId": key})
            graph.add_edge("agentHasKnowledge", {"_from": f"{AGENTS}/private", "_to": f"agentKnowledge/{key}"})

    def _linked(self, graph: InMemoryGraph) -> set[str]:
        return {e["_to"] for e in graph.edges_from("agentHasKnowledge", f"{AGENTS}/private")}

    def _new_ids(self, graph: InMemoryGraph) -> set[str]:
        return {f"agentKnowledge/{k}" for k in graph.nodes["agentKnowledge"] if not k.startswith("kn-old")}

    @pytest.mark.parametrize("rollback_undoes_writes", [True, False], ids=["transactional", "auto_commit"])
    def test_failure_removing_old_nodes_after_their_links_keeps_the_new_set(
        self, client, graph, rollback_undoes_writes,
    ) -> None:
        # Auto-commit Neo4j: the old links are already gone, so the new set is all the agent has.
        graph.rollback_undoes_writes = rollback_undoes_writes
        self._seed_two_old(graph)
        real = graph.delete_nodes

        async def fail_on_old_nodes(keys: list[str], collection: str, transaction: str | None = None) -> bool:
            if collection == "agentKnowledge" and "kn-old" in keys:
                raise RuntimeError("write timed out on 10.0.0.7")
            return await real(keys, collection, transaction)
        graph.delete_nodes = fail_on_old_nodes
        response = client.put("/api/v1/agent/private", headers=as_user("alice"), json={
            "knowledge": [{"connectorId": "conn-1"}],
        })
        assert response.status_code == 500
        assert response.json()["detail"].startswith("We couldn't save this agent.")
        if rollback_undoes_writes:
            assert self._linked(graph) == {"agentKnowledge/kn-old", "agentKnowledge/kn-old2"}
        else:
            new_ids = self._new_ids(graph)
            assert len(new_ids) == 1
            assert self._linked(graph) == new_ids

    @pytest.mark.parametrize("rollback_undoes_writes", [True, False], ids=["transactional", "auto_commit"])
    def test_failure_between_old_link_removals_finishes_on_the_new_set(
        self, client, graph, rollback_undoes_writes,
    ) -> None:
        graph.rollback_undoes_writes = rollback_undoes_writes
        self._seed_two_old(graph)
        real = graph.delete_all_edges_for_node
        failed: list[str] = []

        async def fail_second_once(node_key: str, collection: str, transaction: str | None = None) -> int:
            if node_key == "agentKnowledge/kn-old2" and not failed:
                failed.append(node_key)
                raise RuntimeError("write timed out on 10.0.0.7")
            return await real(node_key, collection, transaction)
        graph.delete_all_edges_for_node = fail_second_once
        response = client.put("/api/v1/agent/private", headers=as_user("alice"), json={
            "knowledge": [{"connectorId": "conn-1"}],
        })
        assert response.status_code == 500
        assert response.json()["detail"].startswith("We couldn't save this agent.")
        if rollback_undoes_writes:
            assert self._linked(graph) == {"agentKnowledge/kn-old", "agentKnowledge/kn-old2"}
            assert self._new_ids(graph) == set()
        else:
            new_ids = self._new_ids(graph)
            assert len(new_ids) == 1
            assert self._linked(graph) == new_ids

    @pytest.mark.parametrize("rollback_undoes_writes", [True, False], ids=["transactional", "auto_commit"])
    def test_new_knowledge_that_could_not_be_linked_is_not_left_behind(
        self, client, graph, rollback_undoes_writes,
    ) -> None:
        graph.rollback_undoes_writes = rollback_undoes_writes
        real = graph.batch_create_edges

        async def fail_links(edges: list[dict], collection: str, transaction: str | None = None) -> bool:
            if collection == "agentHasKnowledge":
                raise RuntimeError("write timed out on 10.0.0.7")
            return await real(edges, collection, transaction)
        graph.batch_create_edges = fail_links
        response = client.put("/api/v1/agent/private", headers=as_user("alice"), json={
            "knowledge": [{"connectorId": "conn-1"}, {"connectorId": "conn-2"}],
        })
        assert response.status_code == 500
        assert graph.nodes.get("agentKnowledge", {}) == {}
        assert graph.edges_from("agentHasKnowledge", f"{AGENTS}/private") == []

    def test_skills_link_only_to_the_callers_own_or_builtin_active_skills(self, client, graph) -> None:
        skills = [
            {"_key": "org-1_mine", "orgId": "org-1", "createdBy": user_key("alice")},
            {"_key": "org-1_builtin", "orgId": "org-1", "source": "builtin"},
            {"_key": "org-1_bobs", "orgId": "org-1", "createdBy": user_key("bob")},
            {"_key": "org-1_old", "orgId": "org-1", "createdBy": user_key("alice"), "status": "deprecated"},
        ]
        for s in skills:
            graph.add_node("agentSkills", s)
        # Another org's skill stored under a key that looks like ours.
        graph.add_node("agentSkills", {"_key": "org-1_foreign", "orgId": "org-2", "source": "builtin"})
        response = client.put("/api/v1/agent/private", headers=as_user("alice"), json={
            "skills": [{"name": "mine"}, "builtin", "bobs", "old", "foreign", "missing", "mine", 7],
        })
        assert response.status_code == 200
        linked = [e["skillName"] for e in graph.edges_from("agentHasSkill", f"{AGENTS}/private")]
        assert linked == ["mine", "builtin"]


class TestDeleteAgent:
    def test_owner_deletes_and_agent_disappears(self, client, graph) -> None:
        response = client.delete("/api/v1/agent/private", headers=as_user("alice"))
        assert response.status_code == 200
        assert response.json()["deleted"]["agents"] == 1
        assert graph.nodes[AGENTS]["private"]["isDeleted"] is True
        assert graph.committed
        assert client.get("/api/v1/agent/private", headers=as_user("alice")).status_code == 404

    def test_colleague_with_read_access_cannot_delete(self, client, graph) -> None:
        response = client.delete("/api/v1/agent/shared", headers=as_user("bob"))
        assert response.status_code == 403
        assert graph.nodes[AGENTS]["shared"]["isDeleted"] is False
        assert not graph.calls_to("begin_transaction")

    @pytest.mark.parametrize("caller,agent", [("bob", "private"), ("mallory", "shared")])
    def test_agent_outside_reach_reads_as_not_found(self, client, graph, caller, agent) -> None:
        response = client.delete(f"/api/v1/agent/{agent}", headers=as_user(caller))
        assert response.status_code == 404
        assert graph.nodes[AGENTS][agent]["isDeleted"] is False

    def test_failed_delete_is_rolled_back(self, client, graph) -> None:
        async def refuse(*_a: object, **_k: object) -> bool:
            return False
        graph.delete_agent = refuse
        response = client.delete("/api/v1/agent/private", headers=as_user("alice"))
        assert response.status_code == 500
        assert graph.rolled_back and not graph.committed

    def test_outage_mid_delete_is_rolled_back_without_leaking(self, client, graph) -> None:
        graph.fail("delete_agent")
        response = client.delete("/api/v1/agent/private", headers=as_user("alice"))
        assert response.status_code != 404
        assert response.json()["detail"].startswith("We couldn't delete this agent.")
        assert graph.rolled_back
        _no_leak(response)

    def test_service_account_delete_stops_only_its_own_token_refreshes(self, client, graph, monkeypatch) -> None:
        from tests.support.agent_routes import FakeConfigService

        graph.nodes[AGENTS]["shared"]["isServiceAccount"] = True
        config = FakeConfigService({
            "/services/toolsets/inst-1/shared": {}, "/services/toolsets/inst-1/other-agent": {},
        })
        cancelled: list[str] = []

        class _Refresh:
            def cancel_refresh_task(self, key: str) -> None:
                cancelled.append(key)

        from app.connectors.core.base.token_service import (
            startup_service as startup_module,
        )
        monkeypatch.setattr(
            startup_module.startup_service, "get_toolset_token_refresh_service", lambda: _Refresh(),
        )
        c, _ = make_client(graph, config)
        response = c.delete("/api/v1/agent/shared", headers=as_user("alice"))
        assert response.status_code == 200
        assert cancelled == ["/services/toolsets/inst-1/shared"]


class TestListAgents:
    @pytest.fixture
    def many(self) -> InMemoryGraph:
        g = InMemoryGraph()
        for i in range(45):
            g.add_agent(f"a{i:02d}", "alice")
        g.add_agent("bobs", "bob")
        return g

    @pytest.mark.parametrize("page,has_next,has_prev,count", [
        (1, True, False, 20), (2, True, True, 20), (3, False, True, 5), (4, False, True, 0),
    ])
    def test_pagination_envelope(self, many, page, has_next, has_prev, count) -> None:
        c, _ = make_client(many)
        body = c.get(f"/api/v1/agent/?page={page}&limit=20", headers=as_user("alice")).json()
        assert body["pagination"] == {
            "currentPage": page, "limit": 20, "totalItems": 45, "totalPages": 3,
            "hasNext": has_next, "hasPrev": has_prev,
        }
        assert len(body["agents"]) == count
        assert all(a["createdBy"] == "u-alice" for a in body["agents"])

    def test_listing_uses_the_callers_identity_not_query_ids(self, many) -> None:
        c, _ = make_client(many)
        body = c.get("/api/v1/agent/?limit=200&userId=u-bob&orgId=org-2", headers=as_user("alice")).json()
        assert "bobs" not in {a["_key"] for a in body["agents"]}
        (args, kwargs), = many.calls_to("get_all_agents")
        assert args == (user_key("alice"), "org-1")

    def test_deleted_agents_listed_only_on_request(self, many) -> None:
        many.nodes[AGENTS]["a00"]["isDeleted"] = True
        c, _ = make_client(many)
        live = c.get("/api/v1/agent/?limit=200", headers=as_user("alice")).json()
        deleted = c.get("/api/v1/agent/?limit=200&isDeleted=true", headers=as_user("alice")).json()
        assert "a00" not in {a["_key"] for a in live["agents"]}
        assert [a["_key"] for a in deleted["agents"]] == ["a00"]

    @pytest.mark.parametrize("query", ["page=0", "limit=0", "limit=201", "sort_order=sideways"])
    def test_invalid_pagination_is_rejected(self, many, query) -> None:
        c, _ = make_client(many)
        assert c.get(f"/api/v1/agent/?{query}", headers=as_user("alice")).status_code == 422

    def test_plain_list_from_provider_is_still_paginated(self, graph) -> None:
        async def as_list(*_a: object, **_k: object) -> list:
            return [{"_key": "x", "createdBy": "system", "models": ["m"]}, "not-a-dict"]
        graph.get_all_agents = as_list
        c, _ = make_client(graph)
        body = c.get("/api/v1/agent/", headers=as_user("alice")).json()
        assert body["pagination"]["totalItems"] == 2
        assert body["agents"][0]["createdBy"] == "system"
        assert body["agents"][0]["usesOrgDefault"] is False

    def test_outage_is_reported_in_plain_words(self, client, graph) -> None:
        graph.fail("get_all_agents")
        response = client.get("/api/v1/agent/", headers=as_user("alice"))
        assert response.json()["detail"].startswith("We couldn't load your agents.")
        _no_leak(response)


class TestUsageLookups:
    def test_model_usage_is_scoped_to_the_callers_org(self, graph) -> None:
        graph.nodes[AGENTS]["private"]["models"] = ["m1"]
        graph.add_agent("theirs", "mallory", models=["m1"])
        c, _ = make_client(graph)
        body = c.get("/api/v1/agent/model-usage/m1?orgId=org-2", headers=as_user("alice")).json()
        assert [a["_key"] for a in body["agents"]] == ["private"]

    def test_blank_model_key_matches_nothing(self, client, graph) -> None:
        body = client.get("/api/v1/agent/model-usage/%20", headers=as_user("alice")).json()
        assert body == {"success": True, "agents": []}
        assert not graph.calls_to("get_agents_by_model_key")

    def test_model_usage_outage_is_a_server_error(self, client, graph) -> None:
        # Callers use this before deleting a model; an outage must not read as "unused".
        graph.fail("get_agents_by_model_key")
        response = client.get("/api/v1/agent/model-usage/m1", headers=as_user("alice"))
        assert response.status_code == 500
        _no_leak(response)

    def test_web_search_usage(self, graph) -> None:
        graph.nodes[AGENTS]["private"]["webSearch"] = "serper"
        c, _ = make_client(graph)
        body = c.get("/api/v1/agent/web-search-usage/SERPER", headers=as_user("alice")).json()
        assert [a["_key"] for a in body["agents"]] == ["private"]
        assert c.get("/api/v1/agent/web-search-usage/bing", headers=as_user("alice")).json()["agents"] == []


class TestServiceAccountInternalRoute:
    def test_service_account_agent_in_callers_org(self, graph) -> None:
        graph.nodes[AGENTS]["shared"]["isServiceAccount"] = True
        c, _ = make_client(graph)
        response = c.get("/api/v1/agent/shared/internal/service-account", headers=as_user("bob"))
        assert response.status_code == 200
        assert response.json()["isServiceAccount"] is True

    def test_regular_agent_is_refused(self, client) -> None:
        response = client.get("/api/v1/agent/shared/internal/service-account", headers=as_user("bob"))
        assert response.status_code == 403

    def test_other_org_gets_the_same_answer_as_a_missing_agent(self, graph) -> None:
        graph.nodes[AGENTS]["shared"]["isServiceAccount"] = True
        c, _ = make_client(graph)
        other_org = c.get("/api/v1/agent/shared/internal/service-account", headers=as_user("mallory"))
        missing = c.get("/api/v1/agent/nope/internal/service-account", headers=as_user("mallory"))
        assert other_org.status_code == missing.status_code == 404
        assert other_org.json() == missing.json()


class TestCreateAgent:
    def test_creator_and_org_come_from_the_caller(self, client, graph) -> None:
        response = client.post("/api/v1/agent/create", headers=as_user("alice"), json={
            "name": "  Helper  ", "shareWithOrg": True,
            "createdBy": user_key("mallory"), "orgId": "org-2", "userId": "u-mallory",
        })
        assert response.status_code == 200
        agent = response.json()["agent"]
        assert agent["name"] == "Helper"
        assert agent["createdBy"] == "u-alice"
        stored = graph.nodes[AGENTS][agent["_key"]]
        assert stored["createdBy"] == user_key("alice")
        edges = graph.edges_to(PERMISSION, f"{AGENTS}/{agent['_key']}")
        assert {(e["_from"], e["role"]) for e in edges} == {
            (f"{USERS_COLL}/{user_key('alice')}", "OWNER"), (f"{ORGS_COLL}/org-1", "READER"),
        }
        assert graph.committed

    def test_service_account_agent_is_always_shared(self, client, graph) -> None:
        response = client.post("/api/v1/agent/create", headers=as_user("alice"), json={
            "name": "Bot", "isServiceAccount": True, "shareWithOrg": False,
        })
        key = response.json()["agent"]["_key"]
        assert len(_org_edges(graph, key)) == 1

    def test_unshared_by_default(self, client, graph) -> None:
        key = client.post("/api/v1/agent/create", headers=as_user("alice"), json={"name": "Solo"}).json()["agent"]["_key"]
        assert _org_edges(graph, key) == []
        assert client.get(f"/api/v1/agent/{key}", headers=as_user("bob")).status_code == 404

    @pytest.mark.parametrize("body,fragment", [
        ({"description": "no name"}, "'name' is required"),
        ({"name": "   "}, "'name' is required"),
        ({"name": "x", "models": [{"modelKey": "m1"}]}, "at least one reasoning model"),
        ({"name": "x", "defaultReasoningEffort": "turbo"}, "Invalid defaultReasoningEffort"),
    ])
    def test_invalid_input_is_rejected_before_anything_is_written(self, client, graph, body, fragment) -> None:
        response = client.post("/api/v1/agent/create", headers=as_user("alice"), json=body)
        assert response.status_code == 400
        assert fragment in response.json()["detail"]
        assert not graph.calls_to("begin_transaction")

    def test_full_payload_creates_every_attachment_in_one_transaction(self, client, graph) -> None:
        graph.add_node("agentSkills", {"_key": "org-1_mine", "orgId": "org-1", "createdBy": user_key("alice")})
        response = client.post("/api/v1/agent/create", headers=as_user("alice"), json={
            "name": "Full",
            "models": [{"modelKey": "m1", "modelName": "gpt-x", "isReasoning": True}, "m2"],
            "defaultReasoningEffort": "high",
            "webSearch": {"provider": "Tavily", "providerLabel": "Tavily"},
            "toolsets": [{"name": "jira", "tools": [{"name": "search"}]}, "junk", {"name": ""}],
            "mcpServers": [{"instanceId": "i1", "name": "github", "tools": [{"name": "t"}]}],
            "knowledge": [{"connectorId": "c1", "filters": "not json"}],
            "skills": ["mine"],
        })
        assert response.status_code == 200
        agent = response.json()["agent"]
        assert agent["models"] == ["m1_gpt-x", "m2"]
        assert agent["webSearch"] == {"provider": "tavily"}
        assert [t["name"] for t in agent["toolsets"]] == ["jira"]
        assert [m["name"] for m in agent["mcpServers"]] == ["github"]
        assert agent["knowledge"][0]["filters"] == {}
        assert agent["skills"] == [{"name": "mine"}]
        assert len(graph.calls_to("begin_transaction")) == 1
        assert len(graph.committed) == 1

    def test_each_tool_is_filed_under_its_own_toolset(self, client, graph) -> None:
        response = client.post("/api/v1/agent/create", headers=as_user("alice"), json={
            "name": "Two toolsets",
            "toolsets": [
                {"name": "jira", "tools": [{"name": "search"}]},
                {"name": "slack", "tools": [{"name": "send"}]},
            ],
        })
        assert response.status_code == 200
        assert [(t["name"], [x["fullName"] for x in t["tools"]]) for t in response.json()["agent"]["toolsets"]] == [
            ("jira", ["jira.search"]), ("slack", ["slack.send"]),
        ]
        stored = {t["fullName"]: t["toolsetName"] for t in graph.nodes["agentTools"].values()}
        assert stored == {"jira.search": "jira", "slack.send": "slack"}

    def test_failure_mid_create_rolls_everything_back(self, client, graph) -> None:
        async def flaky(edges: list[dict], collection: str, transaction: str | None = None) -> bool:
            if collection == "agentHasKnowledge":
                raise RuntimeError("write conflict on agentHasKnowledge/123")
            return await InMemoryGraph.batch_create_edges(graph, edges, collection, transaction)
        graph.batch_create_edges = flaky
        response = client.post("/api/v1/agent/create", headers=as_user("alice"), json={
            "name": "x", "knowledge": [{"connectorId": "c1"}],
        })
        assert response.status_code == 500
        assert response.json()["detail"].startswith("We couldn't create this agent.")
        assert "write conflict" not in response.text
        assert graph.rolled_back and not graph.committed


class TestTemplates:
    @pytest.fixture
    def tgraph(self, graph) -> InMemoryGraph:
        graph.add_template("tpl", "alice")
        return graph

    def test_create_template_is_owned_by_the_caller(self, client, graph) -> None:
        response = client.post("/api/v1/agent/template/create", headers=as_user("alice"), json={
            "name": "T", "description": "d", "systemPrompt": "p", "orgId": "org-2",
        })
        assert response.status_code == 200
        template = response.json()["template"]
        assert template["orgId"] == "org-1"
        assert template["createdBy"] == user_key("alice")
        (edge,) = graph.edges_to(PERMISSION, f"agentTemplates/{template['_key']}")
        assert edge["_from"] == f"{USERS_COLL}/{user_key('alice')}" and edge["role"] == "OWNER"

    def test_create_template_requires_fields(self, client) -> None:
        response = client.post("/api/v1/agent/template/create", headers=as_user("alice"), json={"name": "T"})
        assert response.status_code == 400
        assert "'description' is required" in response.json()["detail"]

    def test_templates_are_private_to_their_owner(self, tgraph) -> None:
        c, _ = make_client(tgraph)
        assert c.get("/api/v1/agent/template/tpl", headers=as_user("alice")).status_code == 200
        for caller in ("bob", "mallory"):
            response = c.get("/api/v1/agent/template/tpl", headers=as_user(caller))
            assert response.status_code == 404
            assert c.get("/api/v1/agent/template/list", headers=as_user(caller)).json()["templates"] == []

    def test_only_the_owner_can_change_or_delete(self, tgraph) -> None:
        c, _ = make_client(tgraph)
        assert c.put("/api/v1/agent/template/tpl", headers=as_user("mallory"), json={"name": "x"}).status_code != 200
        assert c.delete("/api/v1/agent/template/tpl", headers=as_user("bob")).status_code != 200
        assert tgraph.nodes["agentTemplates"]["tpl"]["name"] == "Template tpl"
        assert tgraph.nodes["agentTemplates"]["tpl"]["isDeleted"] is False
        assert c.put("/api/v1/agent/template/tpl", headers=as_user("alice"), json={"name": "New"}).status_code == 200
        assert c.delete("/api/v1/agent/template/tpl", headers=as_user("alice")).status_code == 200
        assert tgraph.nodes["agentTemplates"]["tpl"]["name"] == "New"
        assert tgraph.nodes["agentTemplates"]["tpl"]["isDeleted"] is True

    @pytest.mark.parametrize("rollback_undoes_writes", [True, False], ids=["transactional", "auto_commit"])
    @pytest.mark.parametrize("how", ["raises", "returns_false"])
    def test_copy_whose_owner_edge_fails_is_not_left_behind(self, tgraph, how, rollback_undoes_writes) -> None:
        tgraph.rollback_undoes_writes = rollback_undoes_writes
        real = tgraph.batch_create_edges

        async def refuse_permission(edges: list[dict], collection: str, transaction: str | None = None) -> bool:
            if collection == PERMISSION:
                if how == "raises":
                    raise RuntimeError("write timed out on 10.0.0.7")
                return False
            return await real(edges, collection, transaction)
        tgraph.batch_create_edges = refuse_permission
        c, _ = make_client(tgraph)
        response = c.post("/api/v1/agent/template/tpl/clone", headers=as_user("alice"))
        assert response.status_code in (400, 500)
        _no_leak(response)
        assert set(tgraph.nodes["agentTemplates"]) == {"tpl"}
        assert tgraph.rolled_back and not tgraph.committed

    @pytest.mark.parametrize("caller", ["bob", "mallory"])
    def test_a_template_you_cannot_see_cannot_be_copied(self, tgraph, caller) -> None:
        c, _ = make_client(tgraph)
        response = c.post("/api/v1/agent/template/tpl/clone", headers=as_user(caller))
        assert response.status_code == 404
        assert set(tgraph.nodes["agentTemplates"]) == {"tpl"}

    def test_the_copy_belongs_to_whoever_made_it(self, tgraph) -> None:
        c, _ = make_client(tgraph)
        response = c.post("/api/v1/agent/template/tpl/clone", headers=as_user("alice"))
        assert response.status_code == 200
        copy_id = response.json()["templateId"]
        fetched = c.get(f"/api/v1/agent/template/{copy_id}", headers=as_user("alice"))
        assert fetched.status_code == 200
        assert fetched.json()["template"]["user_role"] == "OWNER"
        assert c.get(f"/api/v1/agent/template/{copy_id}", headers=as_user("bob")).status_code == 404

    @pytest.mark.parametrize("method,path", [
        ("get", "/api/v1/agent/template/list"),
        ("get", "/api/v1/agent/template/tpl"),
        ("delete", "/api/v1/agent/template/tpl"),
    ])
    def test_template_outages_are_reported_in_plain_words(self, tgraph, method, path) -> None:
        c, _ = make_client(tgraph)
        tgraph.fail({"get": "get_template", "delete": "delete_agent_template"}[method]
                    if "tpl" in path else "get_all_agent_templates")
        response = getattr(c, method)(path, headers=as_user("alice"))
        assert response.json()["detail"].startswith("We couldn't")
        _no_leak(response)


class TestGraphUnavailable:
    """The graph provider itself failing to come up (DB down at request time)."""

    @pytest.mark.parametrize("method,path,body", [
        ("get", "/api/v1/agent/private", None),
        ("get", "/api/v1/agent/", None),
        ("put", "/api/v1/agent/private", {"name": "x"}),
        ("post", "/api/v1/agent/create", {"name": "x"}),
        ("post", "/api/v1/agent/private/chat/stream", {"query": "hi"}),
        ("get", "/api/v1/agent/template/list", None),
        ("get", "/api/v1/agent/template/tpl", None),
        ("post", "/api/v1/agent/template/create", {"name": "T", "description": "d", "systemPrompt": "p"}),
        ("post", "/api/v1/agent/template/tpl/clone", None),
        ("put", "/api/v1/agent/template/tpl", {"name": "x"}),
        ("delete", "/api/v1/agent/template/tpl", None),
    ])
    def test_answer_is_a_plain_error_not_a_crash(self, graph, method, path, body) -> None:
        c, container = make_client(graph)
        container.graph_provider_error = ConnectionError("arangodb at 10.0.0.7:8529 refused the connection")
        kwargs = {"json": body} if body is not None else {}
        response = getattr(c, method)(path, headers=as_user("alice"), **kwargs)
        assert response.headers["content-type"].startswith("application/json")
        detail = response.json()["detail"]
        assert detail.startswith("We couldn't") or "try again" in detail.lower()
        _no_leak(response)
