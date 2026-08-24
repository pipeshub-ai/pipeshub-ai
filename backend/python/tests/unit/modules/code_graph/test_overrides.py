"""`OVERRIDES`: a concrete method and the base method it replaces.

The edge is a name match along a resolved heritage edge, not only an interface
or ABC. A call typed as the base still binds to the base method; this edge is
what an inbound walk follows to the implementation. The same pass decides
`INHERITS` vs `IMPLEMENTS`: `class Foo(ABC)` yields the base's name, and
whether that name is a contract is known once it resolves to a block.
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
        graph_provider=graph, facts_source=graph, org_id=ORG_ID, record_group_id=GROUP_ID,
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
    async def test_an_ordinary_subclass_overrides_the_parent_method(
        self, graph, index_file
    ) -> None:
        await index_file("recS", "src/plain_base.py", PLAIN_BASE, "python")
        await index_file("recQ", "src/square.py", PLAIN_CHILD, "python")
        await _build(graph)

        assert ("method:Square.area", "method:Shape.area") in _pairs(graph, OVERRIDES)


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


A_SRC = b'''
class A:
    def run(self):
        return 1
'''

B_SRC = b'''
from .a import A


class B(A):
    def run(self):
        return 2
'''

C_SRC = b'''
from .b import B


class C(B):
    def run(self):
        return 3
'''

B_NO_RUN = b'''
from .a import A


class B(A):
    def other(self):
        return 0
'''

MOD_PARENT = b'''
class Parent:
    def run(self):
        return 1
'''

DECOY_PARENT = b'''
class Parent:
    def run(self):
        return 0
'''

QUALIFIED_CHILD = b'''
class Child(pkg.mod.Parent):
    def run(self):
        return 2
'''

OUTER = b'''
class Outer:
    class Inner:
        def ping(self):
            return 1
'''

DECOY_INNER = b'''
class Inner:
    def ping(self):
        return 0
'''

NESTED_CHILD = b'''
class Child(Outer.Inner):
    def ping(self):
        return 2
'''


def _target_paths(graph, relation, source_name):
    """File path of each edge target whose source qualified name matches."""
    paths = []
    for edge in graph.code_edges(relation):
        src = graph.blocks.get(edge["_from"].partition("/")[2], {})
        if src.get("qualifiedName") != source_name:
            continue
        dst = graph.blocks.get(edge["_to"].partition("/")[2], {})
        paths.append(graph.code_files.get(dst.get("recordId")))
    return sorted(p for p in paths if p)


class TestMultilevel:
    @pytest.mark.asyncio
    async def test_each_hop_overrides_only_its_direct_base(self, graph, index_file) -> None:
        await index_file("recA", "src/a.py", A_SRC, "python")
        await index_file("recB", "src/b.py", B_SRC, "python")
        await index_file("recC", "src/c.py", C_SRC, "python")
        await _build(graph)

        pairs = _pairs(graph, OVERRIDES)
        assert ("method:C.run", "method:B.run") in pairs
        assert ("method:B.run", "method:A.run") in pairs
        assert ("method:C.run", "method:A.run") not in pairs

    @pytest.mark.asyncio
    async def test_a_middle_class_that_does_not_define_the_method_is_skipped(
        self, graph, index_file
    ) -> None:
        await index_file("recA", "src/a.py", A_SRC, "python")
        await index_file("recB", "src/b.py", B_NO_RUN, "python")
        await index_file("recC", "src/c.py", C_SRC, "python")
        await _build(graph)

        pairs = _pairs(graph, OVERRIDES)
        assert ("method:C.run", "method:A.run") in pairs
        assert not [p for p in pairs if p[0] == "method:B.other"]


class TestQualifiedBase:
    @pytest.mark.asyncio
    async def test_a_dotted_base_binds_to_that_module_not_a_same_named_class(
        self, graph, index_file
    ) -> None:
        await index_file("recM", "pkg/mod.py", MOD_PARENT, "python")
        await index_file("recD", "other/decoy.py", DECOY_PARENT, "python")
        await index_file("recC", "app/child.py", QUALIFIED_CHILD, "python")
        await _build(graph)

        assert _target_paths(graph, INHERITS, "class:Child") == ["pkg/mod.py"]
        assert ("method:Child.run", "method:Parent.run") in _pairs(graph, OVERRIDES)


class TestNestedBase:
    @pytest.mark.asyncio
    async def test_outer_inner_beats_another_class_named_inner(self, graph, index_file) -> None:
        await index_file("recO", "lib/a/outer.py", OUTER, "python")
        await index_file("recD", "lib/b/decoy.py", DECOY_INNER, "python")
        await index_file("recC", "app/child.py", NESTED_CHILD, "python")
        await _build(graph)

        assert _target_paths(graph, INHERITS, "class:Child") == ["lib/a/outer.py"]
        assert ("method:Child.ping", "method:Outer.Inner.ping") in _pairs(graph, OVERRIDES)
