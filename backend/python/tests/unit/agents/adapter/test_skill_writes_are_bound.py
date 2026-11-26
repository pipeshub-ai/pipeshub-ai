"""Every write `GraphSkillStore` makes to an existing skill is bound to the doc it read.

The store decides who may change a skill on a read of its doc. A write keyed only by
`{org}_{name}` would land on whatever doc holds the key by then: one deleted and created
again by someone else included. So every write to a skill doc is a conditional provider
call whose expectation comes from `_bound_to(<the doc that was read>)`, and no other
provider write appears in the class unless it is listed below with its reason. A provider
method this file does not know fails the check until it is classified.

The check is syntactic. It follows `self._graph` through a local alias assigned on its own line and through
`getattr(self._graph, "<literal>")`; a method name chosen at run time, `self._graph` kept
anywhere else, or handed to a function not listed in `PASSED` fails it. It does not judge
which doc a `_bound_to(...)` was built from: the real-store races do.
"""

from __future__ import annotations

import ast
import inspect

import pytest

from app.agents.agent_loop.skills import graph_store

READS = frozenset({"get_document", "get_nodes_by_filters", "get_edge", "get_edges_to_node", "get_edges_from_node"})
CREATES = frozenset({"create_node_if_absent"})
# Method -> where its expectation is: (positional index, keyword).
BOUND = {
    "update_node_if_match": (None, "expected"),
    "update_node_fields_if_match": (3, "expected"),
    "delete_node_if_match": (2, "expected"),
}
ALLOWED = {
    ("_snapshot_revision", "batch_upsert_nodes"):
        "a revision doc in agentSkillVersions, keyed by skill, generation and version; never a skill doc",
    ("_create_owner_permission_edge", "batch_create_edges"):
        "runs only after create_node_if_absent created the skill in this call",
    ("_sync_relation_edges", "delete_edges_from"):
        "relation edges are re-derived after the bound write landed; not bound to the generation (a known gap: relation edges are written by name)",
    ("_sync_relation_edges", "batch_create_edges"):
        "as above",
    ("_with_generation", "batch_upsert_nodes"):
        "revision docs in agentSkillVersions the skill already wrote, stamped with its generation; never a skill doc",
    ("queue_candidate", "update_node_if_match"):
        "a learning-loop candidate, bound to its org",
    ("remove_candidate", "delete_node_if_match"):
        "a learning-loop candidate, bound to its org",
}


# (function, callee) that may receive `self._graph`: the callee only reads.
PASSED = {("get_referential_usage", "collect_referential_usage")}


def _is_self_graph(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute) and node.attr == "_graph"
        and isinstance(node.value, ast.Name) and node.value.id == "self"
    )


def _graph_uses(func: ast.AST) -> tuple[list[tuple[str, ast.Call | None]], list[str]]:
    """Every provider call in `func` as (method, call) (`call` is None for a method taken
    with `getattr` and not called on the spot), and the uses the check cannot classify."""
    parents = {child: node for node in ast.walk(func) for child in ast.iter_child_nodes(node)}
    aliases = {
        target.id
        for node in ast.walk(func)
        if isinstance(node, ast.Assign | ast.AnnAssign | ast.NamedExpr) and node.value is not None and _is_self_graph(node.value)
        for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        if isinstance(target, ast.Name)
    }

    def is_graph(node: ast.AST) -> bool:
        return _is_self_graph(node) or (isinstance(node, ast.Name) and node.id in aliases)

    calls: list[tuple[str, ast.Call | None]] = []
    problems: list[str] = []
    for node in ast.walk(func):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and is_graph(node.func.value):
            calls.append((node.func.attr, node))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr" \
                and node.args and is_graph(node.args[0]):
            name = node.args[1] if len(node.args) > 1 else None
            if not (isinstance(name, ast.Constant) and isinstance(name.value, str)):
                problems.append(f"{node.lineno}: provider method chosen at run time")
                continue
            invoked = parents.get(node)
            calls.append((name.value, invoked if isinstance(invoked, ast.Call) and invoked.func is node else None))
        elif isinstance(node, ast.Call):
            callee = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "?")
            if any(is_graph(a) for a in [*node.args, *(k.value for k in node.keywords)]) \
                    and (getattr(func, "name", ""), callee) not in PASSED:
                problems.append(f"{node.lineno}: the provider is handed to {callee}()")
        elif _is_self_graph(node) and not isinstance(parents.get(node), ast.Attribute | ast.Call | ast.Assign | ast.AnnAssign | ast.NamedExpr):
            problems.append(f"{node.lineno}: the provider is kept or passed in a way the check cannot follow")
        elif isinstance(node, ast.Assign) and _is_self_graph(node.value) \
                and not all(isinstance(t, ast.Name) for t in node.targets):
            problems.append(f"{node.lineno}: the provider is stored where the check cannot follow it")
    return calls, problems


def _calls_bound_to(expr: ast.AST) -> bool:
    return any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "_bound_to"
        for n in ast.walk(expr)
    )


def _is_bound(func: ast.AST, call: ast.Call, method: str) -> bool:
    index, keyword = BOUND[method]
    expr = next((k.value for k in call.keywords if k.arg == keyword), None)
    if expr is None and index is not None and len(call.args) > index:
        expr = call.args[index]
    if expr is None:
        return False
    if _calls_bound_to(expr):
        return True
    # A name the function built from `_bound_to(...)` (and then narrowed further).
    if isinstance(expr, ast.Name):
        return any(
            isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == expr.id for t in n.targets)
            and _calls_bound_to(n.value)
            for n in ast.walk(func)
        )
    return False


def unbound_writes(source: str) -> tuple[list[str], set[tuple[str, str]]]:
    """(violations, allowlisted sites seen) for the `GraphSkillStore` class in `source`."""
    tree = ast.parse(source)
    store = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "GraphSkillStore")
    violations: list[str] = []
    seen: set[tuple[str, str]] = set()
    for func in store.body:
        if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        calls, problems = _graph_uses(func)
        violations.extend(f"{func.name}:{problem}" for problem in problems)
        for method, call in calls:
            where = f"{func.name}:{call.lineno if call else '?'} {method}"
            if method in READS or method in CREATES:
                continue
            if (func.name, method) in ALLOWED:
                seen.add((func.name, method))
                continue
            if method in BOUND:
                if call is None or not _is_bound(func, call, method):
                    violations.append(f"{where}: expectation not built from _bound_to(...)")
                continue
            violations.append(f"{where}: unconditional or unclassified provider write")
    return violations, seen


@pytest.fixture(scope="module")
def source() -> str:
    return inspect.getsource(graph_store)


def test_the_store_binds_every_write_to_the_doc_it_read(source: str) -> None:
    violations, seen = unbound_writes(source)
    assert violations == []
    assert seen == set(ALLOWED), f"allowlist entries no longer used: {set(ALLOWED) - seen}"


@pytest.mark.parametrize(
    ("old", "new"),
    [
        pytest.param(
            "await self._graph.delete_node_if_match(self._key(name), _SKILLS, self._bound_to(doc))",
            "await self._graph.delete_nodes_and_edges([self._key(name)], _SKILLS)",
            id="delete-by-key",
        ),
        pytest.param(
            "self._key(name), _SKILLS, updates, self._bound_to(read))",
            'self._key(name), _SKILLS, updates, {"updatedAtTimestamp": read.get("updatedAtTimestamp")})',
            id="resource-write-on-the-token-alone",
        ),
        pytest.param(
            'expected={"generation": self._bound_to(read)["generation"]},',
            "",
            id="content-write-on-the-token-alone",
        ),
        pytest.param(
            "        expected = self._bound_to(doc)\n",
            '        expected = {"updatedAtTimestamp": doc.get("updatedAtTimestamp")}\n',
            id="status-write-on-the-token-alone",
        ),
        pytest.param(
            "        then: an admin may moderate a skill created again in between, too.\"\"\"\n        return\n",
            "        then: an admin may moderate a skill created again in between, too.\"\"\"\n"
            "        await self._graph.batch_delete_edges([], _AGENT_HAS_SKILL)\n",
            id="detach-by-key",
        ),
        pytest.param(
            "        await self._write_resources(skill_name, doc, resources)\n        return True\n\n    async def remove_resource",
            "        await self._graph.update_node(self._key(skill_name), _SKILLS, {})\n        return True\n\n"
            "    async def remove_resource",
            id="update-node-by-key",
        ),
        pytest.param(
            "        if not await self._graph.update_node_fields_if_match(self._key(name), _SKILLS, updates, self._bound_to(read)):\n",
            "        g = self._graph\n        if not await g.update_node(self._key(name), _SKILLS, updates):\n",
            id="update-node-through-an-alias",
        ),
        pytest.param(
            "        if not await self._graph.update_node_fields_if_match(self._key(name), _SKILLS, updates, self._bound_to(read)):\n",
            '        if not await getattr(self._graph, "update_node")(self._key(name), _SKILLS, updates):\n',
            id="update-node-through-getattr",
        ),
        pytest.param(
            "        if not await self._graph.update_node_fields_if_match(self._key(name), _SKILLS, updates, self._bound_to(read)):\n",
            "        write = getattr(self._graph, \"update_node\")\n        if not await write(self._key(name), _SKILLS, updates):\n",
            id="update-node-taken-with-getattr",
        ),
        pytest.param(
            "        if not await self._graph.update_node_fields_if_match(self._key(name), _SKILLS, updates, self._bound_to(read)):\n",
            "        method = \"update_node\"\n        if not await getattr(self._graph, method)(self._key(name), _SKILLS, updates):\n",
            id="method-chosen-at-run-time",
        ),
        pytest.param(
            "        if not await self._graph.update_node_fields_if_match(self._key(name), _SKILLS, updates, self._bound_to(read)):\n",
            "        if not await _write_anyway(self._graph, self._key(name), updates):\n",
            id="provider-handed-to-a-helper",
        ),
    ],
)
def test_the_check_catches_a_write_that_is_not_bound(source: str, old: str, new: str) -> None:
    assert source.count(old) == 1, old
    violations, _ = unbound_writes(source.replace(old, new))
    assert violations, "the mutated store passed the check"
