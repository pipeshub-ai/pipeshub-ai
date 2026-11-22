"""A bare call to a parameter or local is no CALLS fact, and what the shadow guard
must leave alone: a name the function binds by import, `global` or `nonlocal`, a comprehension's own
variable, a class-body name seen from that class's methods, and a nested function declaration all
still resolve. The guard runs for the families with the scope-names collector (Python, JS/TS).
"""
import pytest

from app.config.constants.arangodb import RecordRelations

from .test_import_binding import _build

CALLS = RecordRelations.CALLS.value


pytestmark = pytest.mark.asyncio


HELPERS = b'''
def g():
    return 1


def unused(task):
    return task
'''

PY = b'''
def local_import():
    from app.helpers import g
    return g()


def declared_global():
    global g
    return g()


def comprehension(xs):
    ys = [g for g in xs]
    return g(), ys


class Holder:
    g = 1
    h = g()

    def method(self):
        return g()


def factory():
    class Local:
        g = 1

        def method(self):
            return g()

    return Local


def parameter(g):
    return g()
'''

PY_WITH_MODULE_G = b'''
from app.helpers import g
''' + PY


async def _py(graph, index_file) -> set[str]:
    await index_file("recH", "app/helpers.py", HELPERS, "python")
    await index_file("recU", "app/use.py", PY_WITH_MODULE_G, "python")
    await _build(graph)
    return {(e["_from"].partition("/")[2], e["_to"].partition("/")[2]) for e in graph.code_edges(CALLS)}


def _callers_of(graph, edges: set, target: str) -> set[str]:
    return {graph.blocks[src]["qualifiedName"] for src, dst in edges
            if src in graph.blocks and graph.blocks.get(dst, {}).get("qualifiedName") == target}


async def test_names_bound_by_import_global_nonlocal_comprehension_or_class_body_still_resolve(
        graph, index_file) -> None:
    callers = _callers_of(graph, await _py(graph, index_file), "function:g")
    assert {"function:local_import", "function:declared_global", "function:comprehension",
            "method:Holder.method", "method:factory.Local.method"} <= callers, callers
    assert "function:parameter" not in callers  # the parameter `g` is called, never the module's g


async def test_a_class_body_name_shadows_the_body_itself(graph, index_file) -> None:
    callers = _callers_of(graph, await _py(graph, index_file), "function:g")
    assert not {c for c in callers if c.startswith("field:")}, callers  # `h = g()` calls Holder's own `g`


TS = b'''
import { helper } from "./helpers";

export function run(): number {
  function g(): number {
    return 1;
  }
  submit(g);
  return g() + helper();
}

export function shadowed(g: () => number): number {
  return g();
}
'''


TS_HELPERS = b"export function helper(): number {\n  return 1;\n}\n\nexport function g(): number {\n  return 2;\n}\n"


async def test_typescript_shares_the_guard_a_nested_function_resolves_and_a_parameter_shadows(
        graph, index_file) -> None:
    """The scope collector is shared with JS/TS: `run`'s `g()` is its nested `function g`, and
    `shadowed(g)`'s `g()` calls the parameter, never helpers.ts's exported `g`."""
    await index_file("recTH", "app/helpers.ts", TS_HELPERS, "typescript")
    await index_file("recTU", "app/use.ts", TS, "typescript")
    await _build(graph)
    edges = {(graph.blocks.get(e["_from"].partition("/")[2], {}).get("qualifiedName"),
              graph.blocks.get(e["_to"].partition("/")[2], {}).get("qualifiedName")) for e in graph.code_edges(CALLS)}
    assert edges == {("function:run", "function:run.g"), ("function:run", "function:helper")}
