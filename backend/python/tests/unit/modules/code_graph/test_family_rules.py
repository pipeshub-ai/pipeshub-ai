"""The edge builder's per-language decisions come from one table, `family_rules`.

The table's rows must reproduce what the builder did when it branched on family
names, and each rule must actually steer the builder: both are checked here.
"""
from collections.abc import Callable
from dataclasses import replace

import pytest

from app.config.constants.arangodb import RecordRelations
from app.modules.code_graph import edge_builder
from app.modules.code_graph.family_rules import FamilyRules, rules_for

from .test_import_binding import _build, _targets

CALLS = RecordRelations.CALLS.value


def test_the_rows_reproduce_the_former_family_branches() -> None:
    assert rules_for("jvm").bare_call_reaches_method == "same_class"
    assert rules_for("csharp").bare_call_reaches_method == "same_class"
    go = rules_for("go")
    assert go.bare_call_reaches_method == "never" and not go.bare_names_from_imported_modules
    js = rules_for("js")
    assert js.lexical_module_scope and js.bare_call_reaches_method == "any"
    for family in ("py", "c", "rust", "php", "ruby", None, "no-such-family"):
        assert rules_for(family) == FamilyRules(), family


def test_the_defaults_are_the_builders_former_shared_behaviour() -> None:
    rules = FamilyRules()
    assert rules.self_receivers == {"self", "this"} and rules.super_receivers == {"super()", "super"}
    assert rules.bare_names_from_imported_modules and not rules.lexical_module_scope
    assert rules.out_of_line_methods == "same_folder"
    assert rules.enclosing_type is None and rules.implicit_scope is None


@pytest.fixture
def override(monkeypatch) -> Callable[..., None]:
    """Change one family's rules for the test; every other family keeps its row."""
    def _set(family: str, **changes) -> None:
        real = rules_for
        monkeypatch.setattr(edge_builder, "rules_for",
                            lambda f: replace(real(f), **changes) if f == family else real(f))
    return _set


CLASS_A = b"class A:\n    def run(self):\n        return 1\n"
# A second `run`, so a call on an untyped receiver is not linked to the only method so named.
OTHER_RUN = b"\n\nclass Z:\n    def run(self):\n        return 2\n"


@pytest.mark.asyncio
async def test_self_receivers_name_the_enclosing_class(graph, index_file, override) -> None:
    # `me` is no parameter (a method's first parameter is typed as its class anyway): only the row makes it a self.
    source = CLASS_A + b"\n    def go(self):\n        return me.run()\n" + OTHER_RUN
    await index_file("a", "pkg/a.py", source, "python")
    await _build(graph)
    assert _targets(graph, CALLS, "pkg/a.py") == set()

    override("py", self_receivers=frozenset({"self", "this", "me"}))
    await _build(graph)
    assert _targets(graph, CALLS, "pkg/a.py") == {"pkg/a.py::method:A.run"}


@pytest.mark.asyncio
async def test_super_receivers_name_the_nearest_base(graph, index_file, override) -> None:
    source = CLASS_A + b"\n\nclass B(A):\n    def go(self):\n        return parent.run()\n"
    await index_file("a", "pkg/a.py", source, "python")
    override("py", super_receivers=frozenset({"super()", "super", "parent"}))
    await _build(graph)
    assert "pkg/a.py::method:A.run" in _targets(graph, CALLS, "pkg/a.py")


@pytest.mark.asyncio
async def test_a_never_rule_keeps_bare_calls_off_methods(graph, index_file, override) -> None:
    source = CLASS_A + b"\n\ndef go():\n    return run()\n"
    await index_file("a", "pkg/a.py", source, "python")
    await _build(graph)
    assert _targets(graph, CALLS, "pkg/a.py") == {"pkg/a.py::method:A.run"}   # Python's rule today (D5)

    override("py", bare_call_reaches_method="never")
    await _build(graph)
    assert _targets(graph, CALLS, "pkg/a.py") == set()


@pytest.mark.asyncio
async def test_the_enclosing_type_hook_gives_a_block_its_type(graph, index_file, override) -> None:
    await index_file("a", "pkg/a.py", CLASS_A + b"\n\ndef go(self):\n    return self.run()\n" + OTHER_RUN, "python")
    await _build(graph)
    assert _targets(graph, CALLS, "pkg/a.py") == set()

    def class_a(index, block_id) -> str:
        return next(c for c in index.candidates("A") if c in index.type_blocks)
    override("py", enclosing_type=class_a)
    await _build(graph)
    assert _targets(graph, CALLS, "pkg/a.py") == {"pkg/a.py::method:A.run"}


@pytest.mark.asyncio
async def test_the_implicit_scope_hook_lets_a_bare_name_see_other_files(graph, index_file, override) -> None:
    await index_file("x", "x/helper.py", b"def helper():\n    return 1\n", "python")
    await index_file("y", "y/helper.py", b"def helper():\n    return 2\n", "python")
    await index_file("u", "a/use.py", b"def go():\n    return helper()\n", "python")
    await _build(graph)
    assert _targets(graph, CALLS, "a/use.py") == set()   # two equally near guesses: none

    override("py", implicit_scope=lambda index, path: ["x/helper.py"] if path == "a/use.py" else [])
    await _build(graph)
    assert _targets(graph, CALLS, "a/use.py") == {"x/helper.py::function:helper"}
    edge = next(e for e in graph.code_edges(CALLS))
    assert edge["confidence"] == "EXTRACTED"


# No in-class declaration of `bar`: the parser keeps one as a field, which `this->bar()` finds first.
HEADER = b"class Foo {\n public:\n  int baz() { this->bar(); return 1; }\n};\n"


@pytest.mark.asyncio
async def test_out_of_line_methods_are_looked_for_where_the_rule_says(graph, index_file, override) -> None:
    await index_file("h", "include/foo.hpp", HEADER, "cpp")
    await index_file("s", "src/foo.cpp", b"void Foo::bar() {}\n", "cpp")
    await _build(graph)
    assert "src/foo.cpp::method:Foo.bar" not in _targets(graph, CALLS, "include/foo.hpp")   # same folder only

    override("c", out_of_line_methods="repo")
    await _build(graph)
    assert "src/foo.cpp::method:Foo.bar" in _targets(graph, CALLS, "include/foo.hpp")


@pytest.mark.asyncio
async def test_a_repo_wide_out_of_line_method_must_be_unambiguous(graph, index_file, override) -> None:
    await index_file("h", "include/foo.hpp", HEADER, "cpp")
    await index_file("s", "src/a/foo.cpp", b"void Foo::bar() {}\n", "cpp")
    await index_file("t", "lib/b/foo.cpp", b"void Foo::bar() {}\n", "cpp")
    override("c", out_of_line_methods="repo")
    await _build(graph)
    assert not {t for t in _targets(graph, CALLS, "include/foo.hpp") if t.endswith("Foo.bar")}
