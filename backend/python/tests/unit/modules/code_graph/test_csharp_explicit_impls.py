"""A class implementing a generic interface overrides its declared member, not the interface's
explicit implementation of a non-generic base member (eShop IIntegrationEventHandler)."""
import pytest

from app.config.constants.arangodb import RecordRelations

from .test_csharp_global_usings import _build, _index

pytestmark = pytest.mark.asyncio
OVERRIDES = RecordRelations.OVERRIDES.value
CALLS = RecordRelations.CALLS.value

IFACE = (b"namespace Bus;\n"
         b"public interface IHandler<in T> : IHandler\n"                      # line 2
         b"{\n"
         b"    Task Handle(T e);\n"                                              # line 4: the declared member
         b"    Task IHandler.Handle(Event e) => Handle((T)e);\n"                 # line 5: explicit impl
         b"}\n"
         b"public interface IHandler\n"
         b"{\n"
         b"    Task Handle(Event e);\n"                                          # line 9
         b"}\n")
HANDLER = (b"namespace App;\nusing Bus;\n"
           b"public class Paid : IHandler<PaidEvent>\n{\n    public Task Handle(PaidEvent e) => null;\n}\n")


def _lines(graph, relation: str) -> set[tuple]:
    edges = set()
    for e in graph.code_edges(relation):
        src, dst = graph.blocks.get(e["_from"].partition("/")[2], {}), graph.blocks[e["_to"].partition("/")[2]]
        edges.add((src.get("recordId"), src.get("startLine"), dst.get("recordId"), dst.get("startLine")))
    return edges


async def test_handler_overrides_the_declared_generic_member(graph, index_file) -> None:
    await _index(index_file, {"rec-i": ("src/Bus/IHandler.cs", IFACE), "rec-h": ("src/App/Paid.cs", HANDLER)})
    await _build(graph)
    edges = _lines(graph, OVERRIDES)
    # Paid.Handle (line 5) implements the line-4 declaration, never the line-5 explicit implementation; the
    # explicit implementation itself implements IHandler.Handle (line 9).
    assert ("rec-h", 5, "rec-i", 4) in edges, edges
    assert ("rec-h", 5, "rec-i", 5) not in edges, edges
    assert ("rec-i", 5, "rec-i", 9) in edges, edges
    # The generic declaration `Handle(T e)` is not IHandler.Handle's implementation: the explicit one is.
    assert ("rec-i", 4, "rec-i", 9) not in edges, edges


TWO = (b"namespace Lib;\n"
       b"public interface IA { void M(); }\n"                                                  # line 2
       b"public interface IB { void M(); }\n")                                                 # line 3


async def _edges_from(graph, index_file, files: dict, relation: str, src: str) -> set[tuple]:
    await _index(index_file, files)
    await _build(graph)
    return {(ln, dl) for s, ln, d, dl in _lines(graph, relation) if s == src}


async def test_an_explicit_impl_of_an_inherited_interface_implements_it(graph, index_file) -> None:
    # `class C : IDerived`, `IDerived : IA`, `void IA.M()` beside a public M.
    derived = b"namespace Lib;\npublic interface IDerived : IA { }\n"
    c = (b"namespace App;\nusing Lib;\n"
         b"public class C : IDerived\n{\n"
         b"    void IA.M() { }\n"                                                              # line 5
         b"    public void M() { }\n"                                                          # line 6
         b"}\n")
    got = await _edges_from(graph, index_file, {"rec-t": ("src/Lib/Two.cs", TWO), "rec-d": ("src/Lib/IDerived.cs", derived),
                                                "rec-c": ("src/App/C.cs", c)}, OVERRIDES, "rec-c")
    assert got == {(5, 2)}, got   # the explicit IA.M implements IA.M; the public M claims nothing


async def test_an_explicit_non_generic_impl_beside_the_generic_interface(graph, index_file) -> None:
    # `class Paid : IHandler<PaidEvent>` with `Task IHandler.Handle(Event e)`: the explicit impl names
    # the plain IHandler (arity 0), so it implements line 9 and the public Handle still implements line 4.
    paid = (b"namespace App;\nusing Bus;\n"
            b"public class Paid : IHandler<PaidEvent>\n{\n"
            b"    public Task Handle(PaidEvent e) => null;\n"                                        # line 5
            b"    Task IHandler.Handle(Event e) => null;\n"                                          # line 6
            b"}\n")
    got = await _edges_from(graph, index_file, {"rec-i": ("src/Bus/IHandler.cs", IFACE), "rec-h": ("src/App/Paid.cs", paid)},
                            OVERRIDES, "rec-h")
    assert got == {(5, 4), (6, 9)}, got


async def test_a_call_through_the_interface_reaches_the_explicit_impl_by_its_override(graph, index_file) -> None:
    # PE (b): `IHandler h; h.Handle(..)` is IHandler.Handle (line 9); impact walks OVERRIDES inbound to the
    # explicit implementation (line 5) that runs.
    use = (b"namespace App;\nusing Bus;\n"
           b"public class Bus2 {\n    Task Go(IHandler h, Event e) => h.Handle(e);\n}\n")
    await _index(index_file, {"rec-i": ("src/Bus/IHandler.cs", IFACE), "rec-u": ("src/App/Use.cs", use)})
    await _build(graph)
    assert {(ln, dl) for s, ln, d, dl in _lines(graph, CALLS) if s == "rec-u"} == {(4, 9)}
    assert ("rec-i", 5, "rec-i", 9) in _lines(graph, OVERRIDES)


@pytest.mark.parametrize("family, path, want", [
    ("js", "a.ts", []),                  # a TS template-literal type name stays as written: no type `Foo`1`
    ("csharp", "a.cs", ["b-foo"]),       # C#'s arity suffix is read: `Foo`1` is a Foo
])
async def test_only_the_family_s_arity_rule_reads_a_backtick_suffix(family, path, want) -> None:
    # PE (a): stripping `\`n` is C#'s rule (disambiguate_repeats_by_arity), never another language's.
    import logging

    from app.modules.code_graph.edge_builder import _Builder
    from app.modules.code_graph.symbol_index import build_symbol_index

    index = build_symbol_index([{"_key": "b-foo", "recordId": "r1", "name": "Foo", "kind": "class",
                                 "language": "typescript" if family == "js" else "csharp"}], {"r1": path})
    builder = _Builder(index, "org", "group", logging.getLogger(__name__))
    assert builder._type_candidates("Foo`1", "r1", family) == want


async def test_a_javascript_receiver_holding_a_backtick_is_no_arity(graph, index_file) -> None:
    # bookstack's sidebar.js: a receiver type written from a template literal crashed the arity split.
    await index_file("rec-j", "a.js", b"const el = document.querySelector(`#x-${s}`);\nel.focus();\n", "javascript")
    await _build(graph)


async def test_a_call_through_the_generic_interface_lands_on_its_declared_member(graph, index_file) -> None:
    use = (b"namespace App;\nusing Bus;\n"
           b"public class Bus2 {\n    Task Go(IHandler<PaidEvent> h, PaidEvent e) => h.Handle(e);\n}\n")
    await _index(index_file, {"rec-i": ("src/Bus/IHandler.cs", IFACE), "rec-u": ("src/App/Use.cs", use)})
    await _build(graph)
    assert {(s, ln, d, dl) for s, ln, d, dl in _lines(graph, CALLS) if s == "rec-u"} == {("rec-u", 4, "rec-i", 4)}
