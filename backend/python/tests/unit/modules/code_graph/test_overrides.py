"""`OVERRIDES`: the rung between a concrete method and the contract it satisfies.

A call site typed to the base resolves to the ABSTRACT method -- the router
holds `connector_obj: BaseConnector` and calls `.stream_record()`, so
`_resolve_member_call` binds to `BaseConnector.stream_record`. Without this edge
an inbound walk from any of the ~40 concrete `stream_record` implementations
returns nothing, and "how is stream_record implemented" is unanswerable from the
graph even though every piece of it is indexed.

The same pass decides `INHERITS` vs `IMPLEMENTS`. That cannot happen in the
parser: `class Foo(ABC)` yields the base's NAME, and whether that name is a
contract is only known once it resolves to a block.
"""
import pytest

from app.config.constants.arangodb import RecordRelations
from app.modules.code_graph.edge_builder import build_code_graph_edges

from .conftest import GROUP_ID, ORG_ID

OVERRIDES = RecordRelations.OVERRIDES.value
IMPLEMENTS = RecordRelations.IMPLEMENTS.value
INHERITS = RecordRelations.INHERITS.value

# Shaped like `connector_service.py`: an ABC with abstract methods, which is
# what ~40 connectors in this repo actually subclass.
BASE = b'''
from abc import ABC, abstractmethod


class BaseConnector(ABC):
    @abstractmethod
    def stream_record(self, record):
        raise NotImplementedError

    @abstractmethod
    def run_sync(self):
        raise NotImplementedError

    def shared_helper(self):
        return 1
'''

CONCRETE = b'''
from .base import BaseConnector


class GitHubConnector(BaseConnector):
    def stream_record(self, record):
        return self.fetch(record)

    def fetch(self, record):
        return record
'''

# No ABC anywhere -- an ordinary subclass overriding an ordinary parent.
PLAIN_BASE = b'''
class Shape:
    def area(self):
        return 0
'''

PLAIN_CHILD = b'''
from .plain_base import Shape


class Square(Shape):
    def area(self):
        return 4
'''

TS_INTERFACE = b'''
export interface Store {
  get(key: string): string;
  put(key: string, value: string): void;
}
'''

TS_IMPL = b'''
import { Store } from "./store";

export class RedisStore implements Store {
  get(key: string): string { return key; }
  put(key: string, value: string): void { return; }
}
'''


async def _build(graph):
    return await build_code_graph_edges(
        graph_provider=graph, org_id=ORG_ID, record_group_id=GROUP_ID,
    )


def _pairs(graph, relation):
    """(from, to) as qualified names, so assertions read like the source."""
    out = []
    for edge in graph.code_edges(relation):
        src = graph.blocks.get(edge["_from"].partition("/")[2], {})
        dst = graph.blocks.get(edge["_to"].partition("/")[2], {})
        out.append((src.get("qualifiedName"), dst.get("qualifiedName")))
    return sorted(p for p in out if p[0] and p[1])


class TestAbstractBase:
    @pytest.mark.asyncio
    async def test_the_concrete_method_links_to_the_abstract_one(
        self, graph, index_file
    ) -> None:
        await index_file("recB", "src/base.py", BASE, "python")
        await index_file("recG", "src/github.py", CONCRETE, "python")
        await _build(graph)

        assert ("method:GitHubConnector.stream_record",
                "method:BaseConnector.stream_record") in _pairs(graph, OVERRIDES)

    @pytest.mark.asyncio
    async def test_a_method_the_base_does_not_declare_is_not_linked(
        self, graph, index_file
    ) -> None:
        """`fetch` is the subclass's own -- linking it would invent a contract."""
        await index_file("recB", "src/base.py", BASE, "python")
        await index_file("recG", "src/github.py", CONCRETE, "python")
        await _build(graph)

        assert not [p for p in _pairs(graph, OVERRIDES) if "fetch" in (p[0] or "")]

    @pytest.mark.asyncio
    async def test_an_unimplemented_abstract_method_produces_no_edge(
        self, graph, index_file
    ) -> None:
        """`run_sync` is abstract but this subclass never defines it."""
        await index_file("recB", "src/base.py", BASE, "python")
        await index_file("recG", "src/github.py", CONCRETE, "python")
        await _build(graph)

        assert not [p for p in _pairs(graph, OVERRIDES) if "run_sync" in (p[1] or "")]

    @pytest.mark.asyncio
    async def test_inheriting_an_abc_is_reported_as_implements(
        self, graph, index_file
    ) -> None:
        await index_file("recB", "src/base.py", BASE, "python")
        await index_file("recG", "src/github.py", CONCRETE, "python")
        await _build(graph)

        assert ("class:GitHubConnector", "class:BaseConnector") in _pairs(graph, IMPLEMENTS)
        assert "class:BaseConnector" not in graph.edge_targets(INHERITS)


class TestPlainSubclass:
    @pytest.mark.asyncio
    async def test_an_ordinary_base_stays_inherits(self, graph, index_file) -> None:
        await index_file("recS", "src/plain_base.py", PLAIN_BASE, "python")
        await index_file("recQ", "src/square.py", PLAIN_CHILD, "python")
        await _build(graph)

        assert "class:Shape" in graph.edge_targets(INHERITS)
        assert "class:Shape" not in graph.edge_targets(IMPLEMENTS)

    @pytest.mark.asyncio
    async def test_no_override_edge_without_a_contract(self, graph, index_file) -> None:
        """`Square.area` overrides `Shape.area` in the language sense, but the
        base is not a contract -- the call site binds to the concrete parent,
        so there is no polymorphic hop to bridge."""
        await index_file("recS", "src/plain_base.py", PLAIN_BASE, "python")
        await index_file("recQ", "src/square.py", PLAIN_CHILD, "python")
        await _build(graph)

        assert _pairs(graph, OVERRIDES) == []


class TestDeclaredInterface:
    @pytest.mark.asyncio
    async def test_a_typescript_interface_links_every_member(
        self, graph, index_file
    ) -> None:
        """`kind == "interface"` needs no ABC heuristic -- the grammar says so."""
        await index_file("recS", "src/store.ts", TS_INTERFACE, "typescript")
        await index_file("recR", "src/redis.ts", TS_IMPL, "typescript")
        await _build(graph)

        linked = {dst for _, dst in _pairs(graph, OVERRIDES)}
        assert "method:Store.get" in linked
        assert "method:Store.put" in linked
