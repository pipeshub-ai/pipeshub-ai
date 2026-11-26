"""C4.08 (AD-09, AD-10, AD-11, AD-12) through the real agent routes: an agent of org-1 exported by whoever may read
it, and imported into org-2 as a new agent of the importer's, bound to org-2's own objects. Only the graph and the
config store are replaced (`tests/support/agent_routes.py`)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from tests.support.agent_routes import (
    AGENTS,
    ORGS_COLL,
    PERMISSION,
    USERS_COLL,
    FakeConfigService,
    InMemoryGraph,
    as_user,
    make_client,
    user_key,
)

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

# One deployment serves both orgs: the same configured models, under the keys this deployment gave them.
_CONFIG: dict[str, Any] = {
    "/services/aiModels": {"llm": [
        {"modelKey": "m-gpt", "provider": "openAI", "isReasoning": True, "configuration": {"model": "gpt-x"}},
    ]},
    "/services/toolset-instances": [
        {"_id": "inst-1", "orgId": "org-1", "instanceName": "Slack", "toolsetType": "slack"},
        {"_id": "inst-2", "orgId": "org-2", "instanceName": "slack", "toolsetType": "slack"},
    ],
    "/services/mcp/instances/i1": {"_id": "i1", "orgId": "org-1", "name": "github", "typeId": "github"},
    "/services/mcp/instances/i9": {"_id": "i9", "orgId": "org-2", "name": "github", "typeId": "github"},
}


@pytest.fixture(params=["arango", "neo4j"])
def graph(request: pytest.FixtureRequest) -> InMemoryGraph:
    g = InMemoryGraph(backend=request.param)
    for key, org, name, kind in (
        ("conn-1-drive", "org-1", "Support Drive", "DRIVE"), ("conn-1-wiki", "org-1", "Wiki", "CONFLUENCE"),
        ("conn-2-drive", "org-2", "Support Drive", "DRIVE"),
    ):
        g.add_node("apps", {"_key": key, "orgId": org, "name": name, "type": kind, "scope": "team"})
    g.add_node("agentSkills", {"_key": "org-1_mine", "orgId": "org-1", "createdBy": user_key("alice")})
    return g


@pytest.fixture
def client(graph: InMemoryGraph) -> TestClient:
    c, _ = make_client(graph, FakeConfigService(_CONFIG))
    return c


def _create(client: TestClient) -> str:
    response = client.post("/api/v1/agent/create", headers=as_user("alice"), json={
        "name": "Support triage",
        "systemPrompt": "You triage tickets.",
        "shareWithOrg": True,
        "models": [{"modelKey": "m-gpt", "modelName": "gpt-x", "isReasoning": True}],
        "toolsets": [{"name": "slack", "displayName": "Slack", "instanceId": "inst-1", "instanceName": "Slack",
                      "tools": [{"name": "send", "description": "Send"}]}],
        "mcpServers": [{"instanceId": "i1", "name": "github", "tools": [{"name": "list_prs"}]}],
        "knowledge": [
            {"connectorId": "conn-1-drive", "filters": {}},
            {"connectorId": "conn-1-wiki", "filters": {"recordGroups": ["rg-secret-1"]}},
        ],
        "skills": ["mine"],
    })
    assert response.status_code == 200, response.text
    return response.json()["agent"]["_key"]


def _export(client: TestClient, key: str, user: str = "alice", query: str = "") -> dict[str, Any]:
    response = client.get(f"/api/v1/agent/{key}/export{query}", headers=as_user(user))
    assert response.status_code == 200, response.text
    return response.json()


class TestAd09ExportRoute:
    def test_ad09_a_reader_exports_the_published_version_by_name_with_no_id_of_the_org(
        self, client: TestClient, graph: InMemoryGraph,
    ) -> None:
        key = _create(client)

        exported = _export(client, key, user="bob")

        assert exported["source"]["agentVersion"] == 1
        assert exported["source"]["digest"].startswith("sha256:")
        assert exported["excluded"] == ["sharing"]
        agent = exported["agent"]
        assert agent["models"] == [{"provider": "openai", "modelName": "gpt-x"}]
        assert agent["toolsets"][0]["instanceName"] == "Slack"
        assert agent["knowledge"] == [
            {"name": "Support Drive", "type": "DRIVE", "scope": "whole", "limitedItems": 0},
            {"name": "Wiki", "type": "CONFLUENCE", "scope": "limited", "limitedItems": 1},
        ]
        text = str(exported)
        for binding in (key, "m-gpt", "inst-1", "'i1'", "conn-1-drive", "rg-secret-1", "org-1", "u-alice", user_key("alice")):
            assert binding not in text, binding

    def test_ad09_the_draft_or_a_named_version_is_exported_on_request(self, client: TestClient, graph: InMemoryGraph) -> None:
        key = _create(client)
        saved = client.put(f"/api/v1/agent/{key}", headers=as_user("alice"), json={"systemPrompt": "Draft only.", "publish": False})
        assert saved.status_code == 200, saved.text

        assert _export(client, key)["agent"]["systemPrompt"] == "You triage tickets."
        assert _export(client, key, query="?version=1")["agent"]["systemPrompt"] == "You triage tickets."
        draft = _export(client, key, query="?version=draft")
        assert (draft["agent"]["systemPrompt"], draft["source"]) == ("Draft only.", {"agentVersion": None, "digest": None})
        assert client.get(f"/api/v1/agent/{key}/export?version=7", headers=as_user("alice")).status_code == 404
        assert client.get(f"/api/v1/agent/{key}/export?version=-1", headers=as_user("alice")).status_code == 400

    def test_ad09_who_cannot_read_the_agent_cannot_export_it(self, client: TestClient, graph: InMemoryGraph) -> None:
        key = _create(client)

        assert client.get(f"/api/v1/agent/{key}/export", headers=as_user("mallory")).status_code == 404


class TestAd10ImportRoute:
    def test_ad10_the_import_is_a_new_agent_of_the_importer_bound_to_their_own_objects(
        self, client: TestClient, graph: InMemoryGraph,
    ) -> None:
        exported = _export(client, _create(client))

        response = client.post("/api/v1/agent/import", headers=as_user("mallory"), json={"agent": exported["agent"]})

        assert response.status_code == 201, response.text
        result = response.json()
        new_key = result["agent"]["_key"]
        stored = graph.nodes[AGENTS][new_key]
        assert stored["createdBy"] == user_key("mallory")
        assert stored["models"] == ["m-gpt_gpt-x"]
        edges = graph.edges_to(PERMISSION, f"{AGENTS}/{new_key}")
        assert [(e["_from"], e["role"]) for e in edges] == [(f"{USERS_COLL}/{user_key('mallory')}", "OWNER")]
        assert not any(e["_from"].startswith(ORGS_COLL) for e in edges)
        assert [ts["instanceId"] for ts in graph.nodes["agentToolsets"].values() if ts.get("instanceId") == "inst-2"] == ["inst-2"]
        assert {m["instanceId"] for m in graph.nodes["agentMcpServers"].values()} == {"i1", "i9"}
        assert {k["connectorId"] for k in graph.nodes["agentKnowledge"].values()} >= {"conn-2-drive"}
        assert {(r["kind"], r["name"], r["reason"]) for r in result["rebind"]} == {
            ("knowledge", "Wiki", "limited"),
            ("skill", "mine", "not_found"),
        }
        assert client.get(f"/api/v1/agent/{new_key}", headers=as_user("mallory")).status_code == 200
        assert client.get(f"/api/v1/agent/{new_key}", headers=as_user("alice")).status_code == 404

    def test_ad10_the_import_offers_only_the_importers_org_whatever_the_instance_loader_returns(
        self, client: TestClient, graph: InMemoryGraph, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The org-wide instance loader is an extension point; one that returns every org's instances (what a save
        writes back) must still bind an import only to the importer's org's."""
        import app.edition_config as edition

        async def every_org(_org_id: str, _config: Any) -> list[dict[str, Any]]:  # noqa: ANN401
            return [dict(i) for i in _CONFIG["/services/toolset-instances"]]

        exported = _export(client, _create(client))
        monkeypatch.setattr(edition, "load_instances_for_mutation", every_org)

        response = client.post("/api/v1/agent/import", headers=as_user("mallory"), json={"agent": exported["agent"]})

        assert response.status_code == 201, response.text
        new_key = response.json()["agent"]["_key"]
        bound = [
            graph.nodes["agentToolsets"][e["_to"].split("/", 1)[1]].get("instanceId")
            for e in graph.edges_from("agentHasToolset", f"{AGENTS}/{new_key}")
        ]
        assert bound == ["inst-2"]

    def test_ad10_ids_and_owners_in_the_document_are_refused_and_nothing_is_written(
        self, client: TestClient, graph: InMemoryGraph,
    ) -> None:
        exported = _export(client, _create(client))
        before = len(graph.calls_to("begin_transaction"))

        for body in (
            {"agent": {**exported["agent"], "orgId": "org-1"}},
            {"agent": {**exported["agent"], "createdBy": user_key("alice")}},
            {"agent": exported["agent"], "orgId": "org-1"},
            {"agent": {**exported["agent"], "toolsets": [{**exported["agent"]["toolsets"][0], "instanceId": "inst-1"}]}},
            # ADR-AGB-01: the only binding modes are by name and the sole instance of a type.
            {"agent": exported["agent"], "bind": "any"},
        ):
            response = client.post("/api/v1/agent/import", headers=as_user("mallory"), json=body)
            assert response.status_code == 400, response.text

        assert len(graph.calls_to("begin_transaction")) == before


    def test_agb01_a_drafted_agent_binds_an_unnamed_action_to_the_sole_instance(
        self, client: TestClient, graph: InMemoryGraph,
    ) -> None:
        exported = _export(client, _create(client))
        unnamed = [{**ts, "instanceName": None} for ts in exported["agent"]["toolsets"]]
        client.app.state.toolset_registry = _Registry()  # type: ignore[attr-defined]

        response = client.post(
            "/api/v1/agent/import", headers=as_user("mallory"),
            json={"agent": {**exported["agent"], "toolsets": unnamed}, "bind": "sole_instance"},
        )

        assert response.status_code == 201, response.text
        new_key = response.json()["agent"]["_key"]
        stored = graph.nodes[AGENTS][new_key]
        assert stored["createdBy"] == user_key("mallory")
        assert any(ts.get("instanceId") for ts in graph.nodes["agentToolsets"].values()
                   if ts.get("name") == unnamed[0]["name"])
        assert (
            [graph.nodes["agentToolsets"][e["_to"].split("/", 1)[1]].get("instanceId")
             for e in graph.edges_from("agentHasToolset", f"{AGENTS}/{new_key}")]
            == ["inst-2"]
        )


class _Registry:
    """The toolset registry's answer for the types these tests use."""

    def get_toolset_metadata(self, toolset_name: str, serialize: bool = True) -> dict[str, Any] | None:
        return {"slack": {"name": "slack", "tools": [{"name": "send", "description": "Send a message"}]}}.get(toolset_name)


class TestUiAdopt2DraftedToolsRoute:
    """A chat-drafted agent's tools are checked against the bound instances' real tool lists (UI-ADOPT2)."""

    def _drafted(self, client: TestClient, toolset_tools: list[str], mcp_tools: list[str]) -> dict[str, Any]:
        exported = _export(client, _create(client))["agent"]
        toolsets = [{**ts, "instanceName": None, "tools": [{"name": t, "fullName": f"slack.{t}", "description": ""} for t in toolset_tools]}
                    for ts in exported["toolsets"]]
        servers = [{**m, "allTools": False, "tools": [{"name": t, "fullName": f"github.{t}", "description": ""} for t in mcp_tools]}
                   for m in exported["mcpServers"]]
        return {**exported, "toolsets": toolsets, "mcpServers": servers}

    def test_uiadopt2_unknown_tools_are_reported_and_never_created(
        self, client: TestClient, graph: InMemoryGraph, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import app.api.routes.agent as agent_routes

        async def listed(instance: dict[str, Any], _user_id: str, _config: Any) -> list[dict[str, Any]] | None:  # noqa: ANN401
            return [{"name": "list_prs", "fullName": "mcp_github_list_prs", "description": "List PRs"}] if instance["_id"] == "i9" else None

        client.app.state.toolset_registry = _Registry()  # type: ignore[attr-defined]
        monkeypatch.setattr(agent_routes, "_mcp_tool_list", listed)
        agent = self._drafted(client, ["send", "delete_channel"], ["list_prs", "force_push"])

        response = client.post("/api/v1/agent/import", headers=as_user("mallory"), json={"agent": agent, "bind": "sole_instance"})

        assert response.status_code == 201, response.text
        assert {(r["kind"], r["name"], r["reason"]) for r in response.json()["rebind"] if r["kind"] == "tool"} == {
            ("tool", "slack.delete_channel", "not_found"),
            ("tool", "github.force_push", "not_found"),
        }
        new_key = response.json()["agent"]["_key"]
        assert "delete_channel" not in str(graph.nodes) and "force_push" not in str(graph.nodes)
        assert "list_prs" in str(graph.nodes)
        assert client.get(f"/api/v1/agent/{new_key}", headers=as_user("mallory")).status_code == 200

    def test_uiadopt2_a_server_whose_tools_cannot_be_listed_is_left_out_and_said_so(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import app.api.routes.agent as agent_routes

        async def unknown(_instance: dict[str, Any], _user_id: str, _config: Any) -> None:  # noqa: ANN401
            return None

        client.app.state.toolset_registry = _Registry()  # type: ignore[attr-defined]
        monkeypatch.setattr(agent_routes, "_mcp_tool_list", unknown)
        agent = self._drafted(client, ["send"], ["list_prs"])

        response = client.post("/api/v1/agent/import", headers=as_user("mallory"), json={"agent": agent, "bind": "sole_instance"})

        assert response.status_code == 201, response.text
        assert {(r["kind"], r["name"], r["reason"]) for r in response.json()["rebind"]} >= {
            ("tool", "github.list_prs", "unverified"), ("mcpServer", "github", "no_tools"),
        }

    def test_uiadopt2_an_export_imported_by_name_is_not_held_to_the_tool_lists(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """C4.08 unchanged: an export carries a real agent's tools; only a model's draft is checked."""
        import app.api.routes.agent as agent_routes

        async def fail(*_a: Any) -> None:  # noqa: ANN401
            raise AssertionError("a named import does not list tools")

        monkeypatch.setattr(agent_routes, "_mcp_tool_list", fail)
        exported = _export(client, _create(client))

        response = client.post("/api/v1/agent/import", headers=as_user("mallory"), json={"agent": exported["agent"]})

        assert response.status_code == 201, response.text
        assert not any(r["kind"] == "tool" for r in response.json()["rebind"])


class TestAd12RoundTripRoute:
    def test_ad12_an_imported_agent_exports_as_the_document_it_came_from(self, client: TestClient, graph: InMemoryGraph) -> None:
        first = _export(client, _create(client))
        imported = client.post("/api/v1/agent/import", headers=as_user("alice"), json={"agent": first["agent"]})
        assert imported.status_code == 201, imported.text
        new_key = imported.json()["agent"]["_key"]

        second = _export(client, new_key)

        # Within one org every name binds again, except the limited source the importer must choose again.
        assert imported.json()["rebind"] == [{
            "kind": "knowledge", "name": "Wiki", "reason": "limited",
            "detail": "It searched 1 chosen folders or files; choose them again.",
        }]
        assert second["agent"] == {**first["agent"], "knowledge": first["agent"]["knowledge"][:1]}
        assert second["excluded"] == []


class TestWp56GatesRoute:
    """WP-56: an agent's own gates travel through the real routes: exported, stored on the imported agent, refused
    when they are not an agent's."""

    _GATES: dict[str, Any] = {"planApproval": "required", "verify": {"checks": [{"kind": "file", "path": "out.csv"}], "onFail": "continue", "maxRounds": 2}}

    def test_wp56_gates_3_an_imported_agent_runs_with_the_gates_it_was_exported_with(
        self, client: TestClient, graph: InMemoryGraph,
    ) -> None:
        key = _create(client)
        assert client.put(f"/api/v1/agent/{key}", headers=as_user("alice"), json={"gates": self._GATES}).status_code == 200
        first = _export(client, key, query="?version=draft")
        assert first["agent"]["gates"] == self._GATES
        assert "gates" not in first["excluded"]

        imported = client.post("/api/v1/agent/import", headers=as_user("mallory"), json={"agent": first["agent"]})

        assert imported.status_code == 201, imported.text
        new_key = imported.json()["agent"]["_key"]
        assert _export(client, new_key, user="mallory", query="?version=draft")["agent"]["gates"] == self._GATES

    def test_wp56_gates_2_gates_that_are_not_an_agents_are_refused_and_nothing_is_written(
        self, client: TestClient, graph: InMemoryGraph,
    ) -> None:
        exported = _export(client, _create(client))
        before = len(graph.calls_to("begin_transaction"))

        response = client.post(
            "/api/v1/agent/import", headers=as_user("mallory"),
            json={"agent": {**exported["agent"], "gates": {"dryRun": True}}},
        )

        assert response.status_code == 400, response.text
        assert "gates" in response.text
        assert len(graph.calls_to("begin_transaction")) == before


class TestWp100Fu9EnvironmentRoute:
    """WP100-FU-9: an agent's environment travels through the real routes: exported, stored on the imported agent,
    and one the agents API would refuse (a secret-looking variable) is a 400 with nothing written, not a 500."""

    _ENVIRONMENT: dict[str, Any] = {
        "environmentId": "node-toolchain", "level": "agent", "setup": "npm ci --prefix /opt/tools",
        "env": {"NODE_ENV": "test"}, "cachePaths": ["/opt/tools"],
    }

    def test_wp100_fu9_env_3_an_imported_agent_has_the_environment_it_was_exported_with(
        self, client: TestClient, graph: InMemoryGraph,
    ) -> None:
        key = _create(client)
        saved = client.put(f"/api/v1/agent/{key}", headers=as_user("alice"), json={"environment": self._ENVIRONMENT})
        assert saved.status_code == 200, saved.text
        first = _export(client, key, query="?version=draft")
        assert first["agent"]["environment"] == self._ENVIRONMENT

        imported = client.post("/api/v1/agent/import", headers=as_user("mallory"), json={"agent": first["agent"]})

        assert imported.status_code == 201, imported.text
        new_key = imported.json()["agent"]["_key"]
        assert _export(client, new_key, user="mallory", query="?version=draft")["agent"]["environment"] == self._ENVIRONMENT

    def test_wp100_fu9_env_3_an_environment_the_agents_api_refuses_is_a_400_and_nothing_is_written(
        self, client: TestClient, graph: InMemoryGraph,
    ) -> None:
        exported = _export(client, _create(client))
        before = len(graph.calls_to("begin_transaction"))
        secret = {"environmentId": "x", "level": "agent", "env": {"API_TOKEN": "s3cr3t-value"}}

        response = client.post(
            "/api/v1/agent/import", headers=as_user("mallory"), json={"agent": {**exported["agent"], "environment": secret}},
        )

        assert response.status_code == 400, response.text
        assert "environment" in response.text
        assert len(graph.calls_to("begin_transaction")) == before
