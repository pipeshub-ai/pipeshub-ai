"""Characterization of POST /create and PUT /{agent_id} on `app/api/routes/agent.py`.

Each scenario drives the real route against `InMemoryGraph` and records the
HTTP status, the JSON body, every graph call (in order, arguments included)
and the final graph state. UUIDs and timestamps are normalised so the record
is stable. The record in `golden/agent_create_update.json` was captured from
the code before `AgentService` was extracted; a refactor must reproduce it
byte for byte. Regenerate only for an intended behaviour change:
`REGEN_AGENT_GOLDEN=1 pytest <this file>`.
"""

from __future__ import annotations

import copy
import json
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests.support.agent_routes import (
    AGENTS,
    InMemoryGraph,
    as_user,
    make_client,
    user_key,
)

GOLDEN = Path(__file__).parent / "golden" / "agent_create_update.json"
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
Setup = Callable[[InMemoryGraph], None]


def _noop(_: InMemoryGraph) -> None:
    return None


class _Normalizer:
    def __init__(self) -> None:
        self.uuids: dict[str, str] = {}

    def __call__(self, value: object, key: str | None = None) -> object:
        if isinstance(value, dict):
            return {self(k) if isinstance(k, str) else k: self(v, k) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self(v, key) for v in value]
        if isinstance(value, bool) or value is None:
            return value
        if isinstance(value, int) and key and key.endswith("Timestamp"):
            return "<ts>"
        if isinstance(value, str):
            return UUID_RE.sub(self._uuid, value)
        return value

    def _uuid(self, match: re.Match[str]) -> str:
        return self.uuids.setdefault(match.group(0), f"<uuid-{len(self.uuids) + 1}>")


def _seed(g: InMemoryGraph) -> None:
    g.add_agent("private", "alice", description="alice only", systemPrompt="old prompt", tags=["a"])
    g.add_agent("shared", "alice", share_with_org=True)
    g.add_agent("sa", "alice", share_with_org=True, isServiceAccount=True)
    g.add_node("agentSkills", {"_key": "org-1_mine", "orgId": "org-1", "createdBy": user_key("alice"), "name": "mine"})


def _seed_attachments(g: InMemoryGraph) -> None:
    g.add_node("agentToolsets", {"_key": "ts-old", "name": "jira"})
    g.add_node("agentTools", {"_key": "tool-old", "name": "search", "fullName": "jira.search"})
    g.add_edge("agentHasToolset", {"_from": f"{AGENTS}/private", "_to": "agentToolsets/ts-old"})
    g.add_edge("toolsetHasTool", {"_from": "agentToolsets/ts-old", "_to": "agentTools/tool-old"})
    g.add_node("agentMcpServers", {"_key": "mcp-old", "instanceId": "old"})
    g.add_edge("agentHasMcpServer", {"_from": f"{AGENTS}/private", "_to": "agentMcpServers/mcp-old"})
    g.add_node("agentKnowledge", {"_key": "k-old", "connectorId": "c-old", "filters": "{}"})
    g.add_edge("agentHasKnowledge", {"_from": f"{AGENTS}/private", "_to": "agentKnowledge/k-old"})


def _fail_edges_in(collection: str) -> Setup:
    def setup(g: InMemoryGraph) -> None:
        async def flaky(edges: list[dict], coll: str, transaction: str | None = None) -> bool:
            if coll == collection:
                raise RuntimeError(f"write conflict on {collection}")
            return await InMemoryGraph.batch_create_edges(g, edges, coll, transaction)
        g.batch_create_edges = flaky  # type: ignore[method-assign]
    return setup


def _fail(method: str, seed: bool = False) -> Setup:
    def setup(g: InMemoryGraph) -> None:
        if seed:
            _seed_attachments(g)
        g.fail(method)
    return setup


def _update_returns_false(g: InMemoryGraph) -> None:
    async def refuse(*_: object, **__: object) -> bool:
        return False
    g.update_agent = refuse  # type: ignore[method-assign]


def _with_attachments(g: InMemoryGraph) -> None:
    _seed_attachments(g)


FULL_CREATE = {
    "name": "  Full  ", "description": " d ", "startMessage": "hi", "systemPrompt": "sp",
    "instructions": " ins ", "tags": ["t1", "t2"], "isServiceAccount": False,
    "sendUserContext": False, "shareWithOrg": True,
    "models": [{"modelKey": "m1", "modelName": "gpt-x", "isReasoning": True}, "m2"],
    "defaultReasoningEffort": "high",
    "webSearch": {"provider": "Tavily", "providerLabel": "Tavily"},
    "toolsets": [
        {"name": "Jira", "displayName": "Jira Cloud", "type": "app", "instanceId": "i-1",
         "instanceName": "Main", "tools": [{"name": "search", "description": "find"}, {"name": "get"}]},
        {"name": "slack", "tools": [{"name": "send"}]},
        "junk", {"name": ""},
    ],
    "mcpServers": [
        {"instanceId": "mi-1", "name": "github", "typeId": "gh", "tools": [{"name": "t1"}, {"name": "t2", "fullName": "gh.t2"}]},
        {"instanceId": "mi-2", "name": "linear_x"},
    ],
    "knowledge": [{"connectorId": "c1", "filters": {"recordGroups": ["g1"]}}, {"connectorId": "c2", "filters": "not json"}],
    "skills": ["mine", "ghost"],
}

# (id, setup, method, path, user, body)
SCENARIOS: list[tuple[str, Setup, str, str, str | None, Any]] = [
    # ---- create: success
    ("create-minimal", _noop, "POST", "/create", "alice", {"name": "Solo"}),
    ("create-full", _noop, "POST", "/create", "alice", FULL_CREATE),
    ("create-sa-forced-share", _noop, "POST", "/create", "alice", {"name": "Bot", "isServiceAccount": True, "shareWithOrg": False}),
    ("create-body-ids-ignored", _noop, "POST", "/create", "alice",
     {"name": "H", "createdBy": "k-mallory", "orgId": "org-2", "userId": "u-mallory"}),
    ("create-empty-models-ok", _noop, "POST", "/create", "alice", {"name": "M", "models": []}),
    ("create-null-fields", _noop, "POST", "/create", "alice", {"name": "N", "tags": None, "description": "", "instructions": ""}),
    ("create-knowledge-only", _noop, "POST", "/create", "bob", {"name": "K", "knowledge": [{"connectorId": "c1", "filters": {"x": 1}}]}),
    ("create-toolsets-only", _noop, "POST", "/create", "alice",
     {"name": "T", "toolsets": [{"name": "jira", "tools": [{"name": "a"}]}, {"name": "slack", "tools": [{"name": "b"}]}]}),
    # ---- create: validation and auth errors
    ("create-no-name", _noop, "POST", "/create", "alice", {"description": "x"}),
    ("create-blank-name", _noop, "POST", "/create", "alice", {"name": "   "}),
    ("create-non-reasoning-models", _noop, "POST", "/create", "alice", {"name": "x", "models": [{"modelKey": "m1"}]}),
    ("create-bad-effort", _noop, "POST", "/create", "alice", {"name": "x", "defaultReasoningEffort": "turbo"}),
    ("create-bad-web-search", _noop, "POST", "/create", "alice", {"name": "x", "webSearch": {"provider": 5}}),
    ("create-duplicate-mcp-type", _noop, "POST", "/create", "alice",
     {"name": "x", "mcpServers": [{"instanceId": "a", "name": "n", "typeId": "t"}, {"instanceId": "b", "name": "m", "typeId": "t"}]}),
    ("create-empty-body", _noop, "POST", "/create", "alice", {}),
    ("create-not-json", _noop, "POST", "/create", "alice", "{nope"),
    ("create-unauthenticated", _noop, "POST", "/create", None, {"name": "x"}),
    # ---- create: failure and rollback
    ("create-fail-begin", _fail("begin_transaction"), "POST", "/create", "alice", {"name": "x"}),
    ("create-fail-agent-node", _fail("batch_upsert_nodes"), "POST", "/create", "alice", {"name": "x"}),
    ("create-rollback-knowledge-edges", _fail_edges_in("agentHasKnowledge"), "POST", "/create", "alice",
     {"name": "x", "knowledge": [{"connectorId": "c1"}]}),
    ("create-rollback-toolset-edges", _fail_edges_in("agentHasToolset"), "POST", "/create", "alice",
     {"name": "x", "toolsets": [{"name": "jira", "tools": [{"name": "a"}]}], "knowledge": [{"connectorId": "c1"}]}),
    ("create-rollback-permission", _fail_edges_in("permission"), "POST", "/create", "alice", {"name": "x"}),
    ("create-rollback-then-abort-fails", lambda g: (_fail_edges_in("agentHasKnowledge")(g), g.fail("rollback_transaction")),
     "POST", "/create", "alice", {"name": "x", "knowledge": [{"connectorId": "c1"}]}),
    ("create-fail-commit", _fail("commit_transaction"), "POST", "/create", "alice", {"name": "x"}),
    ("create-user-lookup-outage", _fail("get_user_by_user_id"), "POST", "/create", "alice", {"name": "x"}),
    # ---- update: each field type
    ("update-rename", _noop, "PUT", "/private", "alice", {"name": "Renamed"}),
    ("update-scalars", _noop, "PUT", "/private", "alice", {
        "description": "d2", "startMessage": "s2", "systemPrompt": "p2", "instructions": "i2",
        "tags": ["x", "y"], "isActive": False, "sendUserContext": False}),
    ("update-models", _noop, "PUT", "/private", "alice",
     {"models": [{"modelKey": "m1", "modelName": "n", "isReasoning": True}]}),
    ("update-models-clear", _noop, "PUT", "/private", "alice", {"models": []}),
    ("update-models-no-reasoning", _noop, "PUT", "/private", "alice", {"models": [{"modelKey": "m1"}]}),
    ("update-effort", _noop, "PUT", "/private", "alice", {"defaultReasoningEffort": "low"}),
    ("update-effort-clear", _noop, "PUT", "/private", "alice", {"defaultReasoningEffort": None}),
    ("update-effort-bad", _noop, "PUT", "/private", "alice", {"defaultReasoningEffort": "turbo"}),
    ("update-web-search", _noop, "PUT", "/private", "alice", {"webSearch": {"provider": "Tavily"}}),
    ("update-web-search-clear", _noop, "PUT", "/private", "alice", {"webSearch": None}),
    ("update-share-on", _noop, "PUT", "/private", "alice", {"shareWithOrg": True}),
    ("update-share-off", _noop, "PUT", "/shared", "alice", {"shareWithOrg": False}),
    ("update-share-unchanged", _noop, "PUT", "/shared", "alice", {"shareWithOrg": True}),
    ("update-sa-no-downgrade", _noop, "PUT", "/sa", "alice", {"isServiceAccount": False}),
    ("update-sa-no-unshare", _noop, "PUT", "/sa", "alice", {"shareWithOrg": False}),
    ("update-become-sa", _noop, "PUT", "/private", "alice", {"isServiceAccount": True}),
    ("update-toolsets-replace", _with_attachments, "PUT", "/private", "alice", {"toolsets": [
        {"name": "Slack", "instanceId": "inst-1", "instanceName": "S", "tools": [{"name": "send"}]},
        {"name": "jira", "tools": [{"name": "a"}, {"name": "b"}]}]}),
    ("update-toolsets-clear", _with_attachments, "PUT", "/private", "alice", {"toolsets": []}),
    ("update-toolsets-fail-delete", _fail("delete_nodes", seed=True), "PUT", "/private", "alice", {"toolsets": []}),
    ("update-toolsets-fail-create", lambda g: (_seed_attachments(g), _fail_edges_in("agentHasToolset")(g)),
     "PUT", "/private", "alice", {"toolsets": [{"name": "jira", "tools": [{"name": "a"}]}]}),
    ("update-mcp-replace", _with_attachments, "PUT", "/private", "alice", {"mcpServers": [
        {"instanceId": "mi-1", "name": "github", "tools": [{"name": "t"}]}]}),
    ("update-mcp-clear", _with_attachments, "PUT", "/private", "alice", {"mcpServers": []}),
    ("update-mcp-invalid-leaves-edit-unsaved", _with_attachments, "PUT", "/private", "alice", {
        "name": "nope", "mcpServers": [{"instanceId": "a", "name": "n", "typeId": "t"}, {"instanceId": "b", "name": "m", "typeId": "t"}]}),
    ("update-mcp-fail-delete", _fail("delete_nodes", seed=True), "PUT", "/private", "alice", {"mcpServers": []}),
    ("update-mcp-fail-create", lambda g: (_seed_attachments(g), _fail_edges_in("agentHasMcpServer")(g)),
     "PUT", "/private", "alice", {"mcpServers": [{"instanceId": "mi-1", "name": "github"}]}),
    ("update-knowledge-replace", _with_attachments, "PUT", "/private", "alice", {"knowledge": [
        {"connectorId": "c1", "filters": {"a": 1}}, {"connectorId": "c2"}]}),
    ("update-knowledge-clear", _with_attachments, "PUT", "/private", "alice", {"knowledge": []}),
    ("update-knowledge-fail-create", lambda g: (_seed_attachments(g), _fail_edges_in("agentHasKnowledge")(g)),
     "PUT", "/private", "alice", {"knowledge": [{"connectorId": "c1"}]}),
    ("update-knowledge-fail-delete-old", _fail("delete_nodes", seed=True), "PUT", "/private", "alice",
     {"knowledge": [{"connectorId": "c1"}]}),
    ("update-skills", _noop, "PUT", "/private", "alice", {"skills": ["mine", "ghost"]}),
    ("update-skills-clear", lambda g: g.add_edge("agentHasSkill", {"_from": f"{AGENTS}/private", "_to": "agentSkills/org-1_mine", "skillName": "mine"}),
     "PUT", "/private", "alice", {"skills": []}),
    ("update-skills-fail", _fail_edges_in("agentHasSkill"), "PUT", "/private", "alice", {"skills": ["mine"]}),
    ("update-everything", _with_attachments, "PUT", "/private", "alice", {
        "name": "All", "models": [{"modelKey": "m", "modelName": "n", "isReasoning": True}],
        "toolsets": [{"name": "jira", "tools": [{"name": "a"}]}],
        "mcpServers": [{"instanceId": "mi", "name": "gh"}],
        "knowledge": [{"connectorId": "c9"}], "skills": ["mine"], "shareWithOrg": True}),
    # ---- update: permissions and errors
    ("update-non-owner-reader", _noop, "PUT", "/shared", "bob", {"name": "hijack"}),
    ("update-stranger-same-org", _noop, "PUT", "/private", "bob", {"name": "hijack"}),
    ("update-other-org", _noop, "PUT", "/shared", "mallory", {"name": "hijack"}),
    ("update-missing-agent", _noop, "PUT", "/nope", "alice", {"name": "x"}),
    ("update-deleted-agent", lambda g: g.nodes[AGENTS]["private"].update(isDeleted=True), "PUT", "/private", "alice", {"name": "x"}),
    ("update-body-owner-fields-ignored", _noop, "PUT", "/private", "alice",
     {"name": "x", "createdBy": "k-mallory", "isDeleted": True}),
    ("update-empty-body", _noop, "PUT", "/private", "alice", {}),
    ("update-not-json", _noop, "PUT", "/private", "alice", "[1,2"),
    ("update-storage-refuses", _update_returns_false, "PUT", "/private", "alice", {"name": "x"}),
    ("update-fail-update-agent", _fail("update_agent"), "PUT", "/private", "alice", {"name": "x"}),
    ("update-fail-check-permission", _fail("check_agent_permission"), "PUT", "/private", "alice", {"name": "x"}),
    ("update-unauthenticated", _noop, "PUT", "/private", None, {"name": "x"}),
]


def _run(scenario: tuple[str, Setup, str, str, str | None, Any]) -> dict[str, Any]:
    _, setup, method, path, user, body = scenario
    graph = InMemoryGraph()
    _seed(graph)
    setup(graph)
    graph.calls.clear()
    client, _ = make_client(graph)
    headers = as_user(user) if user else {}
    kwargs: dict[str, Any] = {"content": body} if isinstance(body, str) else {"json": body}
    response = client.request(method, f"/api/v1/agent{path}", headers=headers, **kwargs)
    try:
        payload: Any = response.json()
    except ValueError:
        payload = response.text
    norm = _Normalizer()
    record = {
        "status": response.status_code,
        "body": payload,
        "calls": [[m, list(a), k] for m, a, k in graph.calls],
        "nodes": graph.nodes,
        "edges": graph.edges,
        "committed": len(graph.committed),
        "rolled_back": len(graph.rolled_back),
    }
    return json.loads(json.dumps(norm(copy.deepcopy(record)), sort_keys=True, default=str))


def _load() -> dict[str, Any]:
    return json.loads(GOLDEN.read_text()) if GOLDEN.exists() else {}


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s[0] for s in SCENARIOS])
def test_route_behaviour_is_unchanged(scenario) -> None:
    actual = _run(scenario)
    if os.environ.get("REGEN_AGENT_GOLDEN"):
        golden = _load()
        golden[scenario[0]] = actual
        GOLDEN.write_text(json.dumps(golden, indent=1, sort_keys=True) + "\n")
        return
    assert scenario[0] in _load(), "golden record missing; regenerate deliberately"
    assert actual == _load()[scenario[0]]


def test_golden_has_no_stale_scenarios() -> None:
    assert set(_load()) == {s[0] for s in SCENARIOS}


def test_create_response_shape() -> None:
    record = _load()["create-full"]
    assert record["status"] == 200
    assert set(record["body"]) == {"status", "message", "agent", "warnings"}
    assert set(record["body"]["agent"]) >= {
        "_key", "name", "description", "startMessage", "systemPrompt", "instructions", "models", "tags",
        "webSearch", "defaultReasoningEffort", "isActive", "isServiceAccount", "sendUserContext",
        "createdBy", "updatedBy", "createdAtTimestamp", "updatedAtTimestamp", "isDeleted",
        "toolsets", "mcpServers", "knowledge", "skills",
    }


def test_update_response_shape() -> None:
    assert _load()["update-rename"]["body"] == {"status": "success", "message": "Agent updated successfully"}
