"""MG-3, MG-4 (CPD-7), Python half: the legacy driver reads one org's facts and nothing of another org's.

A dict-backed graph and KV stand in for the stores; the reader is the production one. The route's own
authority (only ``capabilities:read``, the org from the token) is DW-4's Python half (``test_control_plane_drivers_route.py``).
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.legacy_capabilities import AGENT_PAGE, LegacyCapabilityReader

D = "sha256:" + "a" * 64
E = "sha256:" + "e" * 64


class Graph:
    def __init__(self) -> None:
        self.docs: dict[str, dict[str, dict[str, Any]]] = {}
        self.edges: dict[tuple[str, str], list[dict[str, Any]]] = {}

    def add(self, collection: str, key: str, **fields: Any) -> None:  # noqa: ANN401 - document fields
        self.docs.setdefault(collection, {})[key] = {"_key": key, **fields}

    def link(self, edge: str, source: str, target: str) -> None:
        self.edges.setdefault((source, edge), []).append({"_from": source, "_to": target})

    async def get_documents_paginated(
        self, collection: str, skip: int = 0, limit: int = 50, *, sort_field: str | None = None, raise_on_error: bool = False
    ) -> list[dict[str, Any]]:
        rows = sorted(self.docs.get(collection, {}).values(), key=lambda d: d["_key"])
        return rows[skip : skip + limit]

    async def get_edges_from_node(self, node_id: str, edge_collection: str) -> list[dict[str, Any]]:
        return self.edges.get((node_id, edge_collection), [])

    async def get_document(self, document_key: str, collection: str, *, raise_on_error: bool = False) -> dict[str, Any] | None:
        return self.docs.get(collection, {}).get(document_key)


class Kv:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data

    async def get_config(self, key: str, default: object = None, use_cache: bool = False, **_: object) -> object:
        return self.data.get(key, default)

    async def list_keys_in_directory(self, directory: str) -> list[str]:
        if self.data.get("down"):
            raise ConnectionError("kv down")
        return [k for k in self.data if k.startswith(directory)]


def world() -> tuple[Graph, Kv, list[str]]:
    graph = Graph()
    graph.add("users", "u1", orgId="org-a", userId="mongo-u1")
    graph.add("users", "u9", orgId="org-b", userId="mongo-u9")
    graph.add("agentInstances", "a7", createdBy="u1")
    graph.add("agentInstances", "a8", createdBy="u1", isDeleted=True)
    graph.add("agentInstances", "b1", createdBy="u9")
    graph.add("agentToolsets", "t1", type="jira", name="jira", instanceId="jira-1")
    graph.add("agentToolsets", "t2", type="calculator", name="calculator")
    graph.add("agentToolsets", "t3", type="jira", name="jira")
    graph.add("agentTools", "x1", name="search_issues")
    graph.add("agentMcpServers", "m1", instanceId="mcp-1")
    graph.add("agentSkills", "org-a_notes", name="notes", version="1.0.0", orgId="org-a", source="manual", createdBy="u1", status="active")
    graph.add("agentSkills", "org-a_pdf", name="pdf", version="1.0.1", orgId="org-a", source="builtin", createdBy="system:builtin-seeder", status="active")
    graph.add("agentSkills", "org-a_old", name="old", version="1.0.0", orgId="org-a", source="manual", createdBy="u1", status="deprecated")
    for target in ("agentToolsets/t1", "agentToolsets/t2", "agentToolsets/t3"):
        graph.link("agentHasToolset", "agentInstances/a7", target)
    graph.link("toolsetHasTool", "agentToolsets/t1", "agentTools/x1")
    graph.link("agentHasMcpServer", "agentInstances/a7", "agentMcpServers/m1")
    for skill in ("org-a_notes", "org-a_pdf", "org-a_old"):
        graph.link("agentHasSkill", "agentInstances/a7", f"agentSkills/{skill}")
    kv = Kv({
        "/services/toolset-instances": [
            {"_id": "jira-1", "orgId": "org-a", "toolsetType": "jira", "instanceName": "Jira", "authType": "OAUTH",
             "createdBy": "u1", "createdAtTimestamp": 5, "updatedAtTimestamp": 7, "auth": {"apiToken": "secret"}},
            {"_id": "jira-b", "orgId": "org-b", "toolsetType": "jira", "instanceName": "B", "authType": "OAUTH"},
        ],
    })
    return graph, kv, []


def reader(graph: Graph, kv: Kv, asked: list[str], manifests: dict[str, Any] | None = None) -> LegacyCapabilityReader:
    async def mcp_instances(_config: object, org_id: str) -> list[dict[str, Any]]:
        asked.append(org_id)
        return [{"_id": "mcp-1", "orgId": org_id, "name": "Linear", "typeId": "linear", "authMode": "oauth",
                 "createdBy": "u1", "updatedAt": 3, "headerValue": "Bearer secret"}] if org_id == "org-a" else []

    async def read_manifest(_config: object, instance_id: str) -> dict[str, Any] | None:
        return (manifests or {}).get(instance_id)

    async def skill_digest(_tenant: str, name: str, _version: str) -> str | None:
        return D if name != "broken" else None

    return LegacyCapabilityReader(
        graph,  # type: ignore[arg-type]
        kv,  # type: ignore[arg-type]
        provider_of=lambda kind: "platform" if kind == "calculator" else "connector",
        mcp_instances=mcp_instances,
        read_manifest=read_manifest,
        skill_digest=skill_digest,
    )


async def test_mg2_toolset_instances_of_the_org_only_and_no_auth_fields() -> None:
    graph, kv, asked = world()
    facts = (await reader(graph, kv, asked).read("org-a", agents=False)).model_dump(mode="json", by_alias=True)
    assert facts["toolsetInstances"] == [
        {"instanceId": "jira-1", "instanceName": "Jira", "toolsetType": "jira", "authType": "OAUTH", "createdBy": "u1", "updatedAt": 7,
         "ownerId": None}
    ]
    assert facts["agents"] == []
    assert "secret" not in str(facts)


def _personal(org: str, owner: str, instance_id: str, **fields: Any) -> tuple[str, dict[str, Any]]:  # noqa: ANN401 - record fields
    record = {"_id": instance_id, "orgId": org, "createdBy": owner, "scope": "personal", "toolsetType": "jira",
              "instanceName": f"{owner}'s Jira", "authType": "API_TOKEN", "updatedAtTimestamp": 9, "auth": {"apiToken": "secret"},
              **fields}
    return f"/services/toolset-user-instances/{org}/{owner}/{instance_id}", record


async def test_syncfix1_personal_toolset_instances_are_read_with_their_owner_and_no_auth_fields() -> None:
    graph, kv, asked = world()
    kv.data.update(dict([
        _personal("org-a", "u2", "mine-2"),
        _personal("org-a", "u3", "mine-3"),
        _personal("org-b", "u9", "theirs-9"),
        # A record under u3's key that names another owner is not read as anyone's.
        _personal("org-a", "u3", "forged", createdBy="u2"),
    ]))
    facts = (await reader(graph, kv, asked).read("org-a", agents=False)).model_dump(mode="json", by_alias=True)
    assert [(i["instanceId"], i["ownerId"]) for i in facts["toolsetInstances"]] == [
        ("jira-1", None), ("mine-2", "u2"), ("mine-3", "u3"),
    ]
    assert facts["toolsetInstances"][1] == {
        "instanceId": "mine-2", "instanceName": "u2's Jira", "toolsetType": "jira", "authType": "API_TOKEN",
        "createdBy": "u2", "updatedAt": 9, "ownerId": "u2",
    }
    assert "secret" not in str(facts)


async def test_syncfix1_an_unlistable_personal_store_fails_the_read_rather_than_answering_none() -> None:
    graph, kv, asked = world()
    kv.data["down"] = True
    with pytest.raises(ConnectionError):
        await reader(graph, kv, asked).read("org-a", agents=False)


async def test_mg3_mcp_instances_come_through_the_org_scoped_lookup_with_their_manifest() -> None:
    graph, kv, asked = world()
    manifest = {"tools": {"list_issues": {"digest": D, "annotations": {"readOnlyHint": True}}, "create_issue": {"digest": E}},
                "approvedBy": "u-admin", "approvedAt": 9}
    facts = await reader(graph, kv, asked, {"mcp-1": manifest}).read("org-a", agents=False)
    assert asked == ["org-a"]
    mcp = facts.model_dump(mode="json", by_alias=True)["mcpInstances"]
    assert mcp == [{
        "instanceId": "mcp-1", "name": "Linear", "typeId": "linear", "authMode": "oauth", "createdBy": "u1", "updatedAt": 3,
        "manifest": {"tools": {"list_issues": {"digest": D, "effect": "read"}, "create_issue": {"digest": E, "effect": None}},
                     "approvedBy": "u-admin", "approvedAt": 9},
    }]
    assert (await reader(graph, kv, [], {}).read("org-a", agents=False)).mcp_instances[0].manifest is None


async def test_mg4_an_orgs_agents_with_their_edges_platform_toolsets_left_out() -> None:
    graph, kv, asked = world()
    facts = (await reader(graph, kv, asked).read("org-a")).model_dump(mode="json", by_alias=True)
    assert [a["agentKey"] for a in facts["agents"]] == ["a7"]
    agent = facts["agents"][0]
    assert agent["toolsets"] == [
        {"nodeKey": "t1", "instanceId": "jira-1", "toolsetType": "jira", "tools": ["search_issues"]},
        {"nodeKey": "t3", "instanceId": None, "toolsetType": "jira", "tools": None},
    ]
    assert agent["mcpServers"] == [{"instanceId": "mcp-1", "tools": None}]
    assert agent["skills"] == [
        {"name": "notes", "version": "1.0.0", "builtin": False, "createdBy": "mongo-u1", "bundleDigest": D},
        {"name": "pdf", "version": "1.0.1", "builtin": True, "createdBy": None, "bundleDigest": D},
    ]
    assert facts["nextAgent"] is None
    assert agent["createdBy"] == "mongo-u1"


async def test_mg4_one_agent_of_another_org_reads_as_none() -> None:
    graph, kv, asked = world()
    assert (await reader(graph, kv, asked).read("org-a", agent_key="b1")).agents == []
    assert [a.agent_key for a in (await reader(graph, kv, asked).read("org-b", agent_key="b1")).agents] == ["b1"]


async def test_mg4_pages_continue_where_the_last_one_stopped() -> None:
    graph, kv, asked = world()
    for n in range(AGENT_PAGE + 3):
        graph.add("agentInstances", f"z{n:04d}", createdBy="u1")
    first = await reader(graph, kv, asked).read("org-a")
    assert first.next_agent is not None
    second = await reader(graph, kv, asked).read("org-a", after=first.model_dump(by_alias=True)["nextAgent"])
    keys = [a.agent_key for a in [*first.agents, *second.agents]]
    assert len(keys) == len(set(keys)) == AGENT_PAGE + 3 + 1
    assert second.toolset_instances == [] and second.next_agent is None


async def test_dw4_the_route_reads_the_tokens_org_and_answers_503_when_a_store_fails() -> None:
    from fastapi import HTTPException

    import app.edition_config  # noqa: F401 - binds the routes in the order their imports need
    from app.api.routes.control_plane_drivers import legacy_capabilities

    graph, kv, asked = world()
    answered = await legacy_capabilities(None, None, True, {"orgId": "org-b"}, reader(graph, kv, asked))  # type: ignore[arg-type]
    assert answered["orgId"] == "org-b" and asked == ["org-b"]
    assert [a["agentKey"] for a in answered["agents"]] == ["b1"]

    class Failing:
        async def read(self, *_a: object, **_k: object) -> None:
            raise ConnectionError("graph down")

    try:
        await legacy_capabilities(None, None, True, {"orgId": "org-a"}, Failing())  # type: ignore[arg-type]
    except HTTPException as refused:
        assert refused.status_code == 503
    else:
        raise AssertionError("a failed read must not answer")


async def test_mg3_the_production_reader_reads_mcp_through_sec5s_org_scoped_lookup() -> None:
    import app.edition_config  # noqa: F401
    from app.agents.mcp import instance_scope
    from app.api.routes.control_plane_drivers import legacy_capability_reader

    built = legacy_capability_reader(Graph(), Kv({}))  # type: ignore[arg-type]
    assert built._mcp_instances is instance_scope.list_instances_for_org


async def test_mg3_another_orgs_mcp_instance_in_the_same_store_is_never_read() -> None:
    import app.edition_config  # noqa: F401
    from app.agents.mcp.instance_scope import list_instances_for_org
    from tests.unit.harness.credentials.support import DictConfig
    from tests.unit.harness.mcp_hub.support import http_instance

    config = DictConfig({
        "/services/mcp/instances/mine": http_instance(_id="mine", orgId="org-a"),
        "/services/mcp/instances/theirs": http_instance(_id="theirs", orgId="org-b"),
    })

    async def no_manifest(_config: object, _instance: str) -> None:
        return None

    async def no_skill(*_: str) -> None:
        return None

    facts = await LegacyCapabilityReader(
        Graph(), config,  # type: ignore[arg-type]
        provider_of=lambda _kind: None, mcp_instances=list_instances_for_org,
        read_manifest=no_manifest, skill_digest=no_skill,
    ).read("org-a", agents=False)
    assert [i["instanceId"] for i in facts.model_dump(by_alias=True)["mcpInstances"]] == ["mine"]


async def test_mg10_an_unreadable_instance_store_fails_the_read_rather_than_answering_no_mcp() -> None:
    graph, kv, asked = world()
    kv.data["down"] = True
    with pytest.raises(ConnectionError):
        await reader(graph, kv, asked).read("org-a", agents=False)


async def test_mg5_a_skill_the_runtime_stamped_with_a_user_id_keeps_it() -> None:
    graph, kv, asked = world()
    graph.add("agentSkills", "org-a_notes", name="notes", version="1.0.0", orgId="org-a", source="agent_created",
              createdBy="mongo-u7", status="active")
    agent = (await reader(graph, kv, asked).read("org-a", agent_key="a7")).model_dump(by_alias=True)["agents"][0]
    assert [s["createdBy"] for s in agent["skills"]] == ["mongo-u7", None]
