"""INDIRECT_CALL edges end to end on real Neo4j and ArangoDB.

A small Python + TypeScript repo is written the way production writes it and built by the real edge builder:
a function handed over as a value (a callback, a handler table, a bound method) comes back through the provider
as INDIRECT_CALL, INFERRED and with its value position, never as CALLS, and a property read is no edge at all.

Reads NEO4J_IT_URI / ARANGO_IT_URL (through tests.integration.real_graph), which is how backend-matrix finds it.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.config.constants.arangodb import CollectionNames, RecordRelations
from tests.integration.graph_db import test_code_graph_real_backends as base

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

INDIRECT, CALLS = RecordRelations.INDIRECT_CALL.value, RecordRelations.CALLS.value

FILES = {
    "shop/__init__.py": "",
    "shop/handlers.py": "def on_paid(ref):\n    return ref\n\n\ndef on_refund(ref):\n    return ref\n",
    "shop/flows.py": (
        "from shop.handlers import on_paid, on_refund\n\n\n"
        "COMMANDS = {'refund': on_refund}\n\n\n"
        "def settle(bus, ref):\n    bus.subscribe(on_paid)\n    return COMMANDS['refund'](ref)\n\n\n"
        "class Button:\n    @property\n    def label(self):\n        return 'x'\n\n"
        "    def wire(self, bus):\n        bus.subscribe(self.on_click)\n        print(self.label)\n\n"
        "    def on_click(self):\n        return 1\n"
    ),
    "web/handlers.ts": "export function onOpen(ref: string): string { return ref; }\n",
    "web/routes.ts": (
        'import { onOpen } from "./handlers";\n\n'
        "export function wire(bus: { on(n: string, f: unknown): void }): void {\n"
        '  bus.on("open", onOpen);\n}\n'
    ),
}


@pytest.fixture(params=["neo4j", "arango"])
async def repo(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[base._Repo]:
    async with base._connected_repo(request, monkeypatch, FILES) as built:
        yield built


async def _out(repo: base._Repo, key: str, relation: str) -> dict[str, dict]:
    rows = await repo.graph.get_neighbors_for_nodes_by_relationship_types(
        node_keys=[key], node_collection=base.BLOCKS, relationship_types=[relation], direction="outbound",
        raise_on_error=True)
    return {r["key"]: r for r in rows}


async def _contexts(repo: base._Repo) -> dict[tuple[str, str], str]:
    """(from block, to block) -> the stored `context` of every INDIRECT_CALL edge of this repo."""
    if repo.backend == "arango":
        rows = await repo.graph.http_client.execute_aql(
            "FOR e IN @@links FILTER e.relationshipType == @rel AND e.orgId == @org "
            "RETURN {src: PARSE_IDENTIFIER(e._from).key, dst: PARSE_IDENTIFIER(e._to).key, context: e.context}",
            {"@links": CollectionNames.RECORD_LINKS.value, "rel": INDIRECT, "org": repo.org_id})
    else:
        rows = await repo.graph.client.execute_query(
            "MATCH (a)-[e]->(b) WHERE e.relationshipType = $rel AND e.orgId = $org "
            "RETURN a.id AS src, b.id AS dst, e.context AS context",
            {"rel": INDIRECT, "org": repo.org_id})
    return {(r["src"], r["dst"]): r["context"] for r in rows}


async def test_values_handed_over_come_back_as_indirect_calls(repo: base._Repo) -> None:
    await base._build(repo)
    settle = repo.block("shop/flows.py", "function:settle")
    on_paid = repo.block("shop/handlers.py", "function:on_paid")
    on_refund = repo.block("shop/handlers.py", "function:on_refund")
    wire = repo.block("shop/flows.py", "method:Button.wire")
    on_click = repo.block("shop/flows.py", "method:Button.on_click")
    ts_wire = repo.block("web/routes.ts", "function:wire")
    on_open = repo.block("web/handlers.ts", "function:onOpen")

    indirect = await _out(repo, settle, INDIRECT)
    assert set(indirect) == {on_paid, on_refund}
    assert {row["confidence"] for row in indirect.values()} == {"INFERRED"}
    assert not {on_paid, on_refund} & set(await _out(repo, settle, CALLS))
    assert set(await _out(repo, wire, INDIRECT)) == {on_click}  # `self.label` reads a property: no edge
    assert set(await _out(repo, ts_wire, INDIRECT)) == {on_open}

    contexts = await _contexts(repo)
    assert contexts[(settle, on_paid)] == "argument"
    assert contexts[(settle, on_refund)] == "dispatch"
    assert contexts[(ts_wire, on_open)] == "argument"


async def test_an_inbound_walk_reaches_whoever_registers_the_handler(repo: base._Repo) -> None:
    await base._build(repo)
    on_paid = repo.block("shop/handlers.py", "function:on_paid")
    rows = await repo.graph.get_neighbors_for_nodes_by_relationship_types(
        node_keys=[on_paid], node_collection=base.BLOCKS, relationship_types=[INDIRECT], direction="inbound",
        raise_on_error=True)
    assert {r["key"] for r in rows} == {repo.block("shop/flows.py", "function:settle")}
