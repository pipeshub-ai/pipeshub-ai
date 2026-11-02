"""Languages with no import resolution get calls matched by name, labelled INFERRED (P-3)."""
import pytest

from app.agents.actions.code_graph.ops import NAME_ONLY_LANGUAGES, coverage_for
from app.config.constants.arangodb import RecordRelations
from app.modules.parsers.code_parser.engine import parse_code

from .test_import_binding import _build, _targets

CALLS = RecordRelations.CALLS.value


@pytest.mark.parametrize(("language", "source", "expected"), [
    ("go", "package m\nfunc f() { g(1); obj.H(2); pkg.K() }\n",
     [("g", False, None), ("H", True, "obj"), ("K", True, "pkg")]),
    ("java", "class A { void f() { g(1); obj.h(2); new B(); } }",
     [("g", False, None), ("h", True, "obj"), ("B", False, None)]),
    ("rust", "fn f() { g(1); obj.h(2); m::k(3); }",
     [("g", False, None), ("h", True, "obj"), ("k", True, "m")]),
    ("c", "void f() { g(1); obj->h(2); }", [("g", False, None), ("h", True, "obj")]),
    ("cpp", "void f() { g(1); ns::k(3); }", [("g", False, None), ("k", True, "ns")]),
    ("csharp", "class A { void F() { G(1); obj.H(2); new B(); } }",
     [("G", False, None), ("H", True, "obj"), ("B", False, None)]),
    ("php", "<?php function f() { g(1); $o->h(2); A::k(3); }",
     [("g", False, None), ("h", True, "$o"), ("k", True, "A")]),
    ("ruby", "def f\n  g(1)\n  obj.h(2)\nend\n", [("g", False, None), ("h", True, "obj")]),
    ("kotlin", "fun f() { g(1); obj.h(2) }", [("g", False, None), ("h", True, "obj")]),
    ("swift", "func f() { g(1); obj.h(2) }", [("g", False, None), ("h", True, "obj")]),
    ("scala", "object A { def f() = { g(1); obj.h(2) } }",
     [("g", False, None), ("h", True, "obj")]),
    ("lua", "function f() g(1); obj:h(2); m.k(3) end",
     [("g", False, None), ("h", True, "obj"), ("k", True, "m")]),
])
def test_each_grammar_yields_its_callees(language: str, source: str, expected: list) -> None:
    parsed = parse_code(source.encode(), language)
    calls = [(f.to_name, f.is_member_call, f.receiver) for f in parsed.pending
             if getattr(f.relation, "value", f.relation) == CALLS]
    assert calls == expected


@pytest.mark.asyncio
async def test_a_unique_name_in_another_file_is_an_inferred_call(graph, index_file) -> None:
    await index_file("lib", "svc/lib.go", b"package svc\n\nfunc Helper() int { return 1 }\n", "go")
    await index_file("main", "svc/main.go", b"package svc\n\nfunc Run() int { return Helper() }\n", "go")
    await _build(graph)
    assert _targets(graph, CALLS, "svc/main.go") == {"svc/lib.go::function:Helper"}
    edge = next(e for e in graph.code_edges(CALLS))
    assert edge["confidence"] == "INFERRED"


@pytest.mark.asyncio
async def test_an_ambiguous_name_is_not_guessed(graph, index_file) -> None:
    await index_file("a", "svc/a.go", b"package svc\n\nfunc Helper() int { return 1 }\n", "go")
    await index_file("b", "svc/b.go", b"package svc\n\nfunc Helper() int { return 2 }\n", "go")
    await index_file("main", "svc/main.go", b"package svc\n\nfunc Run() int { return Helper() }\n", "go")
    await _build(graph)
    assert _targets(graph, CALLS, "svc/main.go") == set()


@pytest.mark.asyncio
async def test_a_name_never_crosses_languages(graph, index_file) -> None:
    await index_file("py", "tools/helper.py", b"def Helper():\n    return 1\n", "python")
    await index_file("main", "svc/main.go", b"package svc\n\nfunc Run() int { return Helper() }\n", "go")
    await _build(graph)
    assert _targets(graph, CALLS, "svc/main.go") == set()


def test_coverage_says_which_languages_are_name_matched() -> None:
    assert {"rust", "kotlin"} <= NAME_ONLY_LANGUAGES
    assert "java" not in NAME_ONLY_LANGUAGES, "Java resolves imports and heritage now"
    assert "go" not in NAME_ONLY_LANGUAGES, "Go resolves package imports now"
    gap = coverage_for([{"language": "rust"}, {"language": "dart"}])
    assert gap["name_matched_calls"] == ["rust"]
    assert "INFERRED" in gap["note"] and "dart" in gap["note"]
    assert coverage_for([{"language": "python"}]) is None


def test_a_go_method_is_named_for_its_receiver_and_a_cpp_member_for_its_class() -> None:
    """Without the owner, every edge into `Server.handleGet` named a `handleGet` that
    does not exist (P-3)."""
    go = parse_code(b"package m\ntype Server struct{}\nfunc (s *Server) handleGet() {}\n", "go")
    assert ("method", "handleGet", ("Server",)) in {(s.kind, s.name, s.parent_chain) for s in go.symbols}
    cpp = parse_code(b"namespace img {\nint Resizer::run(int x) { return x; }\n}\n", "cpp")
    assert ("method", "run", ("img", "Resizer")) in {(s.kind, s.name, s.parent_chain) for s in cpp.symbols}


@pytest.mark.asyncio
async def test_a_call_binds_to_a_type_or_a_callable_never_a_field(graph, index_file) -> None:
    await index_file("cart", "Sources/Cart.swift",
                     b"struct Cart {\n    var items: [Int] = []\n    func add() { items(1) }\n}\n", "swift")
    await _build(graph)
    assert _targets(graph, CALLS, "Sources/Cart.swift") == set()


@pytest.mark.asyncio
async def test_a_constructor_call_points_at_the_class(graph, index_file) -> None:
    await index_file("res", "src/ChargeResult.java",
                     b"class ChargeResult {\n  ChargeResult(int x) {}\n}\n", "java")
    await index_file("gw", "src/Gateway.java",
                     b"class Gateway {\n  ChargeResult charge() { return new ChargeResult(1); }\n}\n",
                     "java")
    await _build(graph)
    assert _targets(graph, CALLS, "src/Gateway.java") == {"src/ChargeResult.java::class:ChargeResult"}
