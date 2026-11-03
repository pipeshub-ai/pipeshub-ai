"""Personal MCP servers on agents, driven through the real routes in
`app/api/routes/agent.py`.

A personal MCP server is visible to its owner only, so it may be attached to the
owner's own unshared agents and nothing else: never another user's instance, and
never an agent that is shared with the org or runs as a service account.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.support.agent_routes import (
    AGENTS,
    PERMISSION,
    FakeConfigService,
    InMemoryGraph,
    as_user,
    make_client,
)

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


def _personal(instance_id: str, owner: str) -> dict:
    return {"_id": instance_id, "orgId": "org-1", "createdBy": owner, "name": instance_id, "scope": "personal"}


_INSTANCES = {
    "/services/mcp/instances/org-inst": {"_id": "org-inst", "orgId": "org-1", "name": "org-inst"},
    "/services/mcp/user-instances/org-1/u-alice/alice-own": _personal("alice-own", "u-alice"),
    "/services/mcp/user-instances/org-1/u-bob/bob-own": _personal("bob-own", "u-bob"),
}


@pytest.fixture
def graph() -> InMemoryGraph:
    g = InMemoryGraph()
    g.add_agent("private", "alice")
    g.add_agent("shared", "alice", share_with_org=True)
    return g


@pytest.fixture
def client(graph: InMemoryGraph) -> TestClient:
    c, _ = make_client(graph, FakeConfigService(dict(_INSTANCES)))
    return c


def _attach(instance_id: str) -> list[dict]:
    return [{"instanceId": instance_id, "name": instance_id, "displayName": instance_id, "tools": [{"name": "t"}]}]


def _org_edges(graph: InMemoryGraph, agent_key: str) -> list[dict]:
    return [e for e in graph.edges_to(PERMISSION, f"{AGENTS}/{agent_key}") if e.get("type") == "ORG"]


def _seed_attached(graph: InMemoryGraph, agent_key: str, instance_id: str) -> None:
    graph.add_node("agentMcpServers", {"_key": f"mcp-{instance_id}", "instanceId": instance_id})
    graph.add_edge("agentHasMcpServer", {"_from": f"{AGENTS}/{agent_key}", "_to": f"agentMcpServers/mcp-{instance_id}"})


class TestCreateAgent:
    def test_own_personal_server_on_an_unshared_agent(self, client, graph) -> None:
        response = client.post("/api/v1/agent/create", headers=as_user("alice"), json={
            "name": "Mine", "mcpServers": _attach("alice-own"),
        })
        assert response.status_code == 200
        assert [m["instanceId"] for m in graph.nodes["agentMcpServers"].values()] == ["alice-own"]

    @pytest.mark.parametrize("sharing", [{"shareWithOrg": True}, {"isServiceAccount": True}])
    def test_personal_server_on_a_shared_or_service_account_agent_is_rejected(self, client, graph, sharing) -> None:
        response = client.post("/api/v1/agent/create", headers=as_user("alice"), json={
            "name": "Team", "mcpServers": _attach("alice-own"), **sharing,
        })
        assert response.status_code == 400
        assert "Personal MCP servers (alice-own)" in response.json()["detail"]
        assert not graph.calls_to("begin_transaction")

    def test_org_server_on_a_shared_agent_is_fine(self, client) -> None:
        response = client.post("/api/v1/agent/create", headers=as_user("alice"), json={
            "name": "Team", "shareWithOrg": True, "mcpServers": _attach("org-inst"),
        })
        assert response.status_code == 200

    def test_another_users_personal_server_is_not_found(self, client, graph) -> None:
        response = client.post("/api/v1/agent/create", headers=as_user("alice"), json={
            "name": "Borrowed", "mcpServers": _attach("bob-own"),
        })
        assert response.status_code == 400
        assert "MCP server(s) not found: bob-own" in response.json()["detail"]
        assert not graph.calls_to("begin_transaction")


class TestUpdateAgent:
    def test_sharing_an_agent_that_has_a_personal_server_is_rejected(self, client, graph) -> None:
        _seed_attached(graph, "private", "alice-own")
        response = client.put("/api/v1/agent/private", headers=as_user("alice"), json={"shareWithOrg": True})
        assert response.status_code == 400
        assert "Personal MCP servers (alice-own)" in response.json()["detail"]
        assert _org_edges(graph, "private") == []
        assert not graph.calls_to("update_agent")

    def test_making_it_a_service_account_is_rejected_too(self, client, graph) -> None:
        _seed_attached(graph, "private", "alice-own")
        response = client.put("/api/v1/agent/private", headers=as_user("alice"), json={"isServiceAccount": True})
        assert response.status_code == 400
        assert graph.nodes[AGENTS]["private"].get("isServiceAccount") is not True

    def test_attaching_a_personal_server_to_a_shared_agent_is_rejected(self, client, graph) -> None:
        response = client.put("/api/v1/agent/shared", headers=as_user("alice"), json={"mcpServers": _attach("alice-own")})
        assert response.status_code == 400
        assert not graph.calls_to("update_agent")

    def test_other_edits_to_an_unshared_agent_with_a_personal_server_still_save(self, client, graph) -> None:
        _seed_attached(graph, "private", "alice-own")
        response = client.put("/api/v1/agent/private", headers=as_user("alice"), json={"name": "Renamed"})
        assert response.status_code == 200
        assert graph.nodes[AGENTS]["private"]["name"] == "Renamed"

    def test_sharing_after_swapping_to_an_org_server_in_the_same_edit(self, client, graph) -> None:
        _seed_attached(graph, "private", "alice-own")
        response = client.put("/api/v1/agent/private", headers=as_user("alice"), json={
            "shareWithOrg": True, "mcpServers": _attach("org-inst"),
        })
        assert response.status_code == 200
        assert len(_org_edges(graph, "private")) == 1


class TestSharingLooksAtEveryPersonalServer:
    """SEC-7: the check uses the org's view, not the editor's. A co-editor can't see the
    owner's personal server, and a check that dropped what it couldn't see let sharing through."""

    def _bobs_agent_with_alice_as_organizer(self, graph: InMemoryGraph) -> None:
        graph.add_agent("bobs", "bob")
        graph.add_edge(PERMISSION, {
            "_from": "users/k-alice", "_to": f"{AGENTS}/bobs", "type": "USER", "role": "ORGANIZER",
        })
        _seed_attached(graph, "bobs", "bob-own")

    def test_a_co_editor_cannot_share_an_agent_with_the_owners_personal_server(self, client, graph) -> None:
        self._bobs_agent_with_alice_as_organizer(graph)

        response = client.put("/api/v1/agent/bobs", headers=as_user("alice"), json={"shareWithOrg": True})

        assert response.status_code == 400
        assert "Personal MCP servers (bob-own)" in response.json()["detail"]
        assert _org_edges(graph, "bobs") == []

    def test_a_server_that_no_longer_exists_does_not_block_sharing(self, client, graph) -> None:
        _seed_attached(graph, "private", "deleted-since")

        response = client.put("/api/v1/agent/private", headers=as_user("alice"), json={"shareWithOrg": True})

        assert response.status_code == 200
        assert len(_org_edges(graph, "private")) == 1


class TestSharingIsRefusedWhenTheServersCantBeChecked:
    """An empty list from a failed read, or a store that doesn't answer, is not "no personal servers"."""

    def test_an_unreadable_mcp_list_blocks_sharing(self, client, graph) -> None:
        _seed_attached(graph, "private", "alice-own")
        real = graph.get_agent

        async def unreadable(*args: object, **kwargs: object) -> dict | None:
            agent = await real(*args, **kwargs)
            return {**agent, "mcpServers": [], "mcpServersUnavailable": True} if agent else agent
        graph.get_agent = unreadable

        response = client.put("/api/v1/agent/private", headers=as_user("alice"), json={"shareWithOrg": True})

        assert response.status_code == 503
        assert "couldn't be checked" in response.json()["detail"]
        assert _org_edges(graph, "private") == []

    def test_a_store_failure_while_checking_blocks_sharing(self, graph) -> None:
        class _Unlistable(FakeConfigService):
            async def list_keys_in_directory(self, directory: str) -> list[str]:
                raise ConnectionError("store down")

        client, _ = make_client(graph, _Unlistable(dict(_INSTANCES)))
        _seed_attached(graph, "private", "alice-own")

        response = client.put("/api/v1/agent/private", headers=as_user("alice"), json={"shareWithOrg": True})

        assert response.status_code == 503
        assert _org_edges(graph, "private") == []

    def test_an_unreadable_list_doesnt_block_an_edit_that_isnt_sharing(self, client, graph) -> None:
        real = graph.get_agent

        async def unreadable(*args: object, **kwargs: object) -> dict | None:
            agent = await real(*args, **kwargs)
            return {**agent, "mcpServers": [], "mcpServersUnavailable": True} if agent else agent
        graph.get_agent = unreadable

        response = client.put("/api/v1/agent/private", headers=as_user("alice"), json={"name": "Renamed"})

        assert response.status_code == 200
