"""In-memory stand-ins for the agent routes' outer I/O, shared by the
route-level tests in this directory.

`InMemoryGraph` mirrors the Arango provider's contract for the calls the agent
routes make (`arango_http_provider.py`): the same permission rules as
`check_agent_permission` (a USER edge wins, else the caller's ORG edge; roles
map to can_edit/can_delete/can_share), the same re-checks inside
`update_agent`/`delete_agent`, the same owner-only template writes, and the
same "no permission check" behaviour of `get_agent` and `clone_agent_template`.
Routes are driven through a real FastAPI app so routing, the scope check and
the request parsing are the production code.
"""

from __future__ import annotations

import copy
import itertools
import logging
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agents.agent_loop.cancellation.in_process import (
    InProcessRunCancellationRegistry,
)

USERS_COLL = "users"
ORGS_COLL = "organizations"
PERMISSION = "permission"
AGENTS = "agentInstances"
TEMPLATES = "agentTemplates"

_EDIT_ROLES = {"OWNER", "WRITER", "ORGANIZER"}
_SHARE_ROLES = {"OWNER", "ORGANIZER"}

USERS: dict[str, dict[str, Any]] = {
    "alice": {"userId": "u-alice", "orgId": "org-1", "email": "alice@acme.test"},
    "bob": {"userId": "u-bob", "orgId": "org-1", "email": "bob@acme.test"},
    "mallory": {"userId": "u-mallory", "orgId": "org-2", "email": "mallory@evil.test"},
}


def user_key(name: str) -> str:
    return f"k-{name}"


class GraphOutage(RuntimeError):
    """Raised by a method configured to fail, standing in for a DB outage."""


class InMemoryGraph:
    def __init__(self, *, rollback_undoes_writes: bool = True) -> None:
        # False behaves like Neo4j without explicit transactions: every write commits
        # at once and rollback only closes the session.
        self.rollback_undoes_writes = rollback_undoes_writes
        self.nodes: dict[str, dict[str, dict[str, Any]]] = {}
        self.edges: dict[str, list[dict[str, Any]]] = {}
        self.failures: dict[str, BaseException] = {}
        self.calls: list[tuple[str, tuple, dict]] = []
        self.committed: list[str] = []
        self.rolled_back: list[str] = []
        # Rollback restores the state from when the transaction began, as Arango discards
        # every write made inside it.
        self._snapshots: dict[str, tuple[dict, dict]] = {}
        self._txn = itertools.count(1)
        for name, user in USERS.items():
            self.add_node(USERS_COLL, {"_key": user_key(name), **user, "fullName": name.title()})
        for org in ("org-1", "org-2"):
            self.add_node(ORGS_COLL, {"_key": org, "accountType": "enterprise", "name": org})

    # -- test helpers --------------------------------------------------------
    def add_node(self, collection: str, doc: dict[str, Any]) -> None:
        self.nodes.setdefault(collection, {})[doc["_key"]] = copy.deepcopy(doc)

    def add_edge(self, collection: str, edge: dict[str, Any]) -> None:
        self.edges.setdefault(collection, []).append(copy.deepcopy(edge))

    def add_agent(self, key: str, owner: str, *, share_with_org: bool = False, **fields: Any) -> None:
        self.add_node(AGENTS, {
            "_key": key, "name": f"Agent {key}", "createdBy": user_key(owner),
            "isDeleted": False, "models": [], **fields,
        })
        self.add_edge(PERMISSION, {
            "_from": f"{USERS_COLL}/{user_key(owner)}", "_to": f"{AGENTS}/{key}",
            "type": "USER", "role": "OWNER",
        })
        if share_with_org:
            org = USERS[owner]["orgId"]
            self.add_edge(PERMISSION, {
                "_from": f"{ORGS_COLL}/{org}", "_to": f"{AGENTS}/{key}",
                "type": "ORG", "role": "READER",
            })

    def add_template(self, key: str, owner: str, **fields: Any) -> None:
        self.add_node(TEMPLATES, {
            "_key": key, "name": f"Template {key}", "orgId": USERS[owner]["orgId"],
            "createdBy": user_key(owner), "isDeleted": False, **fields,
        })
        self.add_edge(PERMISSION, {
            "_from": f"{USERS_COLL}/{user_key(owner)}", "_to": f"{TEMPLATES}/{key}",
            "type": "USER", "role": "OWNER",
        })

    def fail(self, method: str, exc: BaseException | None = None) -> None:
        self.failures[method] = exc or GraphOutage(f"{method}: connection refused to 10.0.0.7:8529")

    def edges_to(self, collection: str, to_id: str) -> list[dict[str, Any]]:
        return [e for e in self.edges.get(collection, []) if e.get("_to") == to_id]

    def edges_from(self, collection: str, from_id: str) -> list[dict[str, Any]]:
        return [e for e in self.edges.get(collection, []) if e.get("_from") == from_id]

    def _enter(self, method: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((method, args, kwargs))
        if method in self.failures:
            raise self.failures[method]

    def calls_to(self, method: str) -> list[tuple[tuple, dict]]:
        return [(a, k) for m, a, k in self.calls if m == method]

    # -- IGraphDBProvider surface used by the agent routes -------------------
    async def get_user_by_user_id(self, user_id: str, **_: Any) -> dict[str, Any] | None:
        self._enter("get_user_by_user_id", user_id)
        for doc in self.nodes.get(USERS_COLL, {}).values():
            if doc.get("userId") == user_id:
                return copy.deepcopy(doc)
        return None

    async def get_document(self, document_key: str, collection: str, transaction: str | None = None) -> dict | None:
        self._enter("get_document", document_key, collection)
        doc = self.nodes.get(collection, {}).get(document_key)
        return copy.deepcopy(doc) if doc is not None else None

    async def get_nodes_by_field_in(
        self, collection: str, field: str, values: list[Any],
        return_fields: list[str] | None = None, transaction: str | None = None,
    ) -> list[dict]:
        self._enter("get_nodes_by_field_in", collection, field, values)
        stored_field = "_key" if field == "id" else field
        out = []
        for doc in self.nodes.get(collection, {}).values():
            if doc.get(stored_field) in values:
                view = {**doc, "id": doc["_key"]}
                out.append({f: view.get(f) for f in return_fields} if return_fields else view)
        return out

    async def check_agent_permission(self, agent_id: str, user_id: str, org_id: str) -> dict | None:
        self._enter("check_agent_permission", agent_id, user_id, org_id)
        agent = self.nodes.get(AGENTS, {}).get(agent_id)
        if agent is None or agent.get("isDeleted"):
            return None
        agent_path = f"{AGENTS}/{agent_id}"
        for access_type, from_id, edge_type in (
            ("INDIVIDUAL", f"{USERS_COLL}/{user_id}", "USER"),
            ("ORG", f"{ORGS_COLL}/{org_id}" if org_id is not None else None, "ORG"),
        ):
            if from_id is None:
                continue
            for perm in self.edges.get(PERMISSION, []):
                if perm["_from"] == from_id and perm["_to"] == agent_path and perm.get("type") == edge_type:
                    role = perm.get("role")
                    return {
                        "access_type": access_type, "user_role": role,
                        "can_edit": role in _EDIT_ROLES, "can_delete": role == "OWNER",
                        "can_share": role in _SHARE_ROLES, "can_view": True,
                    }
        return None

    async def get_agent(self, agent_id: str, org_id: str | None = None, transaction: str | None = None) -> dict | None:
        self._enter("get_agent", agent_id, org_id)
        agent = self.nodes.get(AGENTS, {}).get(agent_id)
        if agent is None or agent.get("isDeleted"):
            return None
        agent_path = f"{AGENTS}/{agent_id}"
        toolsets = []
        for edge in self.edges_from("agentHasToolset", agent_path):
            ts = self.nodes.get("agentToolsets", {}).get(edge["_to"].split("/", 1)[1])
            if ts is None:
                continue
            tools = [
                self.nodes["agentTools"][t["_to"].split("/", 1)[1]]
                for t in self.edges_from("toolsetHasTool", edge["_to"])
                if t["_to"].split("/", 1)[1] in self.nodes.get("agentTools", {})
            ]
            toolsets.append({**ts, "tools": copy.deepcopy(tools)})
        knowledge = [
            copy.deepcopy(self.nodes["agentKnowledge"][e["_to"].split("/", 1)[1]])
            for e in self.edges_from("agentHasKnowledge", agent_path)
            if e["_to"].split("/", 1)[1] in self.nodes.get("agentKnowledge", {})
        ]
        mcp_servers = [
            copy.deepcopy(self.nodes["agentMcpServers"][e["_to"].split("/", 1)[1]])
            for e in self.edges_from("agentHasMcpServer", agent_path)
            if e["_to"].split("/", 1)[1] in self.nodes.get("agentMcpServers", {})
        ]
        skills = [{"name": e.get("skillName")} for e in self.edges_from("agentHasSkill", agent_path)]
        share_with_org = bool(org_id) and any(
            e["_from"] == f"{ORGS_COLL}/{org_id}" and e.get("type") == "ORG"
            for e in self.edges_to(PERMISSION, agent_path)
        )
        return {
            **copy.deepcopy(agent), "toolsets": toolsets, "knowledge": knowledge,
            "mcpServers": mcp_servers, "skills": skills, "shareWithOrg": share_with_org,
        }

    async def update_agent(self, agent_id: str, agent_updates: dict, user_id: str, org_id: str, transaction: str | None = None) -> bool:
        self._enter("update_agent", agent_id, agent_updates, user_id, org_id)
        perm = await self.check_agent_permission(agent_id, user_id, org_id)
        if not perm or not perm.get("can_edit"):
            return False
        agent = self.nodes[AGENTS][agent_id]
        agent.update({f: agent_updates[f] for f in AGENT_UPDATE_FIELDS if f in agent_updates})
        if "models" in agent_updates:
            normalized = _normalize_models(agent_updates["models"])
            if normalized is not None:
                agent["models"] = normalized
        agent["updatedBy"] = user_id
        return True

    async def delete_agent(self, agent_id: str, user_id: str, org_id: str, transaction: str | None = None) -> bool:
        self._enter("delete_agent", agent_id, user_id, org_id)
        if agent_id not in self.nodes.get(AGENTS, {}):
            return False
        perm = await self.check_agent_permission(agent_id, user_id, org_id)
        if not perm or not perm.get("can_delete"):
            return False
        self.nodes[AGENTS][agent_id].update({"isDeleted": True, "deletedByUserId": user_id})
        return True

    async def get_all_agents(self, user_id: str, org_id: str, **kwargs: Any) -> dict[str, Any]:
        self._enter("get_all_agents", user_id, org_id, **kwargs)
        page, limit = kwargs.get("page") or 1, kwargs.get("limit") or 20
        visible = []
        for key, agent in sorted(self.nodes.get(AGENTS, {}).items()):
            if bool(agent.get("isDeleted")) != bool(kwargs.get("is_deleted")):
                continue
            if agent.get("isDeleted"):
                owns = any(
                    e["_from"] == f"{USERS_COLL}/{user_id}" and e.get("role") == "OWNER"
                    for e in self.edges_to(PERMISSION, f"{AGENTS}/{key}")
                )
                if owns:
                    visible.append(copy.deepcopy(agent))
            elif await self.check_agent_permission(key, user_id, org_id):
                visible.append(copy.deepcopy(agent))
        start = (page - 1) * limit
        return {"agents": visible[start:start + limit], "totalItems": len(visible)}

    async def get_agents_by_model_key(self, org_id: str, model_key: str) -> list[dict]:
        self._enter("get_agents_by_model_key", org_id, model_key)
        return [{"_key": k, "name": a["name"]} for k, a in self.nodes.get(AGENTS, {}).items()
                if USERS_BY_KEY[a["createdBy"]]["orgId"] == org_id and model_key in (a.get("models") or [])]

    async def get_agents_by_web_search_provider(self, org_id: str, provider: str) -> list[dict]:
        self._enter("get_agents_by_web_search_provider", org_id, provider)
        return [{"_key": k, "name": a["name"]} for k, a in self.nodes.get(AGENTS, {}).items()
                if USERS_BY_KEY[a["createdBy"]]["orgId"] == org_id and a.get("webSearch") == provider]

    # templates
    def _template_role(self, template_id: str, user_id: str) -> str | None:
        template = self.nodes.get(TEMPLATES, {}).get(template_id)
        if template is None or template.get("isDeleted"):
            return None
        for perm in self.edges_to(PERMISSION, f"{TEMPLATES}/{template_id}"):
            if perm["_from"] == f"{USERS_COLL}/{user_id}" and perm.get("type") == "USER":
                return perm.get("role")
        return None

    async def get_all_agent_templates(self, user_id: str, transaction: str | None = None) -> list[dict]:
        self._enter("get_all_agent_templates", user_id)
        return [copy.deepcopy(t) for k, t in self.nodes.get(TEMPLATES, {}).items() if self._template_role(k, user_id)]

    async def get_template(self, template_id: str, user_id: str, transaction: str | None = None) -> dict | None:
        self._enter("get_template", template_id, user_id)
        role = self._template_role(template_id, user_id)
        if role is None:
            return None
        return {**copy.deepcopy(self.nodes[TEMPLATES][template_id]), "user_role": role,
                "can_edit": role in _EDIT_ROLES, "can_delete": role == "OWNER"}

    async def clone_agent_template(self, template_id: str, transaction: str | None = None) -> str | None:
        # The real provider reads the template with no permission check.
        self._enter("clone_agent_template", template_id)
        template = self.nodes.get(TEMPLATES, {}).get(template_id)
        if template is None:
            return None
        new_key = f"{template_id}-copy{len(self.nodes[TEMPLATES])}"
        self.add_node(TEMPLATES, {**template, "_key": new_key, "createdBy": None, "isDeleted": False})
        return new_key

    async def delete_agent_template(self, template_id: str, user_id: str, transaction: str | None = None) -> bool:
        self._enter("delete_agent_template", template_id, user_id)
        if self._template_role(template_id, user_id) != "OWNER":
            return False
        self.nodes[TEMPLATES][template_id]["isDeleted"] = True
        return True

    async def update_agent_template(self, template_id: str, template_updates: dict, user_id: str, transaction: str | None = None) -> bool:
        self._enter("update_agent_template", template_id, template_updates, user_id)
        if self._template_role(template_id, user_id) != "OWNER":
            return False
        allowed = ["name", "description", "startMessage", "systemPrompt", "tools", "models", "memory", "tags"]
        self.nodes[TEMPLATES][template_id].update({f: template_updates[f] for f in allowed if f in template_updates})
        return True

    # generic writes
    async def begin_transaction(self, read: list[str], write: list[str]) -> str:
        self._enter("begin_transaction", read, write)
        txn = f"txn-{next(self._txn)}"
        self._snapshots[txn] = (copy.deepcopy(self.nodes), copy.deepcopy(self.edges))
        return txn

    async def commit_transaction(self, transaction: str) -> None:
        self._enter("commit_transaction", transaction)
        self._snapshots.pop(transaction, None)
        self.committed.append(transaction)

    async def rollback_transaction(self, transaction: str) -> None:
        self._enter("rollback_transaction", transaction)
        snapshot = self._snapshots.pop(transaction, None)
        if snapshot is not None and self.rollback_undoes_writes:
            self.nodes, self.edges = snapshot
        self.rolled_back.append(transaction)

    async def batch_upsert_nodes(self, nodes: list[dict], collection: str, transaction: str | None = None) -> bool:
        self._enter("batch_upsert_nodes", nodes, collection)
        for node in nodes:
            self.add_node(collection, node)
        return True

    async def batch_create_edges(self, edges: list[dict], collection: str, transaction: str | None = None) -> bool:
        self._enter("batch_create_edges", edges, collection)
        for edge in edges:
            self.add_edge(collection, edge)
        return True

    async def delete_edge(self, from_id: str, from_collection: str, to_id: str, to_collection: str,
                          collection: str, transaction: str | None = None) -> bool:
        self._enter("delete_edge", from_id, from_collection, to_id, to_collection, collection)
        before = len(self.edges.get(collection, []))
        self.edges[collection] = [
            e for e in self.edges.get(collection, [])
            if not (e["_from"] == f"{from_collection}/{from_id}" and e["_to"] == f"{to_collection}/{to_id}")
        ]
        return len(self.edges[collection]) < before

    async def get_edges_from_node(self, node_id: str, edge_collection: str, transaction: str | None = None) -> list[dict]:
        self._enter("get_edges_from_node", node_id, edge_collection)
        return copy.deepcopy(self.edges_from(edge_collection, node_id))

    async def delete_all_edges_for_node(self, node_key: str, collection: str, transaction: str | None = None) -> int:
        self._enter("delete_all_edges_for_node", node_key, collection)
        kept = [e for e in self.edges.get(collection, []) if node_key not in (e["_from"], e["_to"])]
        removed = len(self.edges.get(collection, [])) - len(kept)
        self.edges[collection] = kept
        return removed

    async def delete_nodes(self, keys: list[str], collection: str, transaction: str | None = None) -> bool:
        self._enter("delete_nodes", keys, collection)
        for key in keys:
            self.nodes.get(collection, {}).pop(key, None)
        return True


USERS_BY_KEY = {user_key(name): user for name, user in USERS.items()}

# The scalar fields ArangoHTTPProvider.update_agent copies from an update; models are handled apart.
AGENT_UPDATE_FIELDS = (
    "name", "description", "startMessage", "systemPrompt", "instructions", "tags",
    "isActive", "isServiceAccount", "sendUserContext", "webSearch", "defaultReasoningEffort",
)


def _normalize_models(raw: Any) -> list[str] | None:
    """ArangoHTTPProvider.update_agent's models rule: a list replaces the stored models
    (an empty list clears them), None clears them, anything else leaves them as they are."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        return None
    entries: list[str] = []
    for model in raw:
        if isinstance(model, dict):
            key, name = model.get("modelKey"), model.get("modelName", "")
        elif isinstance(model, str):
            key, _, name = model.partition("_")
        else:
            continue
        entry = f"{key}_{name}" if key and name else key
        if entry and entry not in entries:
            entries.append(entry)
    return entries


class FakeConfigService:
    def __init__(self, values: dict[str, Any] | None = None) -> None:
        self.values = values or {}

    async def get_config(self, key: str, default: Any = None, use_cache: bool = True) -> Any:
        return copy.deepcopy(self.values.get(key, default))

    async def list_keys_in_directory(self, directory: str) -> list[str]:
        return [k for k in self.values if k.startswith(directory)]


class FakeContainer:
    """Mirrors the provider methods `get_services` resolves on the query container."""

    def __init__(self, graph: InMemoryGraph, config: FakeConfigService) -> None:
        self.graph = graph
        self.config = config
        self.graph_provider_error: BaseException | None = None
        self.registry = InProcessRunCancellationRegistry()

    async def retrieval_service(self) -> object:
        return object()

    async def graph_provider(self) -> InMemoryGraph:
        if self.graph_provider_error is not None:
            raise self.graph_provider_error
        return self.graph

    def reranker_service(self) -> object:
        return object()

    def config_service(self) -> FakeConfigService:
        return self.config

    def logger(self) -> logging.Logger:
        return logging.getLogger("test.agent_routes")

    def run_cancellation_registry(self) -> InProcessRunCancellationRegistry:
        return self.registry


class _TestAuth:
    """Stands in for the JWT middleware: the `x-test-user` header picks the
    already-authenticated identity, exactly as `request.state.user` would hold it."""

    def __init__(self, app: Any, users: dict[str, dict[str, Any]]) -> None:
        self.app = app
        self.users = users

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            name = dict(scope["headers"]).get(b"x-test-user")
            if name is not None:
                scope.setdefault("state", {})["user"] = dict(self.users[name.decode()])
        await self.app(scope, receive, send)


def make_client(graph: InMemoryGraph, config: FakeConfigService | None = None,
                extra_users: dict[str, dict[str, Any]] | None = None) -> tuple[TestClient, FakeContainer]:
    from app.api.routes.agent import router

    app = FastAPI()
    app.include_router(router, prefix="/api/v1/agent")
    container = FakeContainer(graph, config or FakeConfigService())
    app.container = container
    app.add_middleware(_TestAuth, users={**USERS, **(extra_users or {})})
    return TestClient(app, raise_server_exceptions=False), container


def as_user(name: str) -> dict[str, str]:
    return {"x-test-user": name}
