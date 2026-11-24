"""INDIRECT_CALL edges: a function handed over as a value, resolved like a call.

The edge keeps its own relation (never CALLS: a value handed over is not a
call), lands only on a function or method, is always INFERRED, and
carries the value position it came from in `context`.
"""
import pytest

from app.config.constants.arangodb import RecordRelations
from app.modules.code_graph.edge_builder import build_code_graph_edges

from .conftest import GROUP_ID, ORG_ID

INDIRECT = RecordRelations.INDIRECT_CALL.value
CALLS = RecordRelations.CALLS.value

pytestmark = pytest.mark.asyncio

HANDLERS = b'''
def on_paid(ref):
    return ref


def on_refund(ref):
    return ref


class Receipt:
    pass
'''

LEGACY = b'''
def on_paid(ref):
    return "legacy"


def task(ref):
    return ref
'''

RUNNER = b'''
def submit(task, ref):
    return task(ref)
'''

FLOWS = b'''
from shop.handlers import Receipt, on_paid, on_refund
from shop.runner import submit


def settle(ref):
    return submit(on_paid, ref)


def route(kind, ref):
    table = {"refund": on_refund}
    return table[kind](ref)


def make(ref):
    return submit(Receipt, ref)


def guess(ref):
    return submit(task, ref)
'''


async def _build(graph) -> None:
    await build_code_graph_edges(graph_provider=graph, facts_source=graph, org_id=ORG_ID, record_group_id=GROUP_ID)


def _edges(graph, relation) -> dict[tuple[str, str], dict]:
    out = {}
    for edge in graph.code_edges(relation):
        src = graph.blocks.get(edge["_from"].partition("/")[2], {})
        dst = graph.blocks.get(edge["_to"].partition("/")[2], {})
        out[(graph.code_files.get(src.get("recordId")), src.get("qualifiedName"),
             graph.code_files.get(dst.get("recordId")), dst.get("qualifiedName"))] = edge
    return out


def _pairs(graph, relation) -> set[tuple[str, str]]:
    return {(k[1], k[3]) for k in _edges(graph, relation)}


async def _python_repo(graph, index_file) -> None:
    await index_file("recH", "shop/handlers.py", HANDLERS, "python")
    await index_file("recL", "shop/legacy.py", LEGACY, "python")
    await index_file("recR", "shop/runner.py", RUNNER, "python")
    await index_file("recF", "shop/flows.py", FLOWS, "python")
    await _build(graph)


async def test_a_callback_argument_is_an_inferred_indirect_call_with_its_context(graph, index_file) -> None:
    await _python_repo(graph, index_file)
    edges = _edges(graph, INDIRECT)
    hit = [e for k, e in edges.items() if k[1] == "function:settle" and k[3] == "function:on_paid"]
    assert len(hit) == 1
    assert hit[0]["confidence"] == "INFERRED"
    assert hit[0]["context"] == "argument"
    assert hit[0]["resolution"] == "import-binding"
    assert ("function:settle", "function:on_paid") not in _pairs(graph, CALLS)


async def test_the_import_decides_between_same_named_functions(graph, index_file) -> None:
    await _python_repo(graph, index_file)
    targets = {k[2] for k in _edges(graph, INDIRECT) if k[1] == "function:settle"}
    assert targets == {"shop/handlers.py"}


async def test_a_local_table_value(graph, index_file) -> None:
    await _python_repo(graph, index_file)
    assert ("function:route", "function:on_refund") in _pairs(graph, INDIRECT)


async def test_a_class_passed_as_a_value_is_not_an_indirect_call(graph, index_file) -> None:
    await _python_repo(graph, index_file)
    assert not {p for p in _pairs(graph, INDIRECT) if p[0] == "function:make"}


async def test_a_parameter_named_like_a_module_function_never_links(graph, index_file) -> None:
    await _python_repo(graph, index_file)
    # runner.submit's `task` is its parameter, not legacy.task.
    assert not {p for p in _pairs(graph, INDIRECT) if p[0] == "function:submit"}


async def test_a_bare_name_with_no_evidence_is_not_guessed(graph, index_file) -> None:
    await _python_repo(graph, index_file)
    # flows.guess passes `task`, imported from nowhere: the repo-wide name guess is not taken.
    assert not {p for p in _pairs(graph, INDIRECT) if p[0] == "function:guess"}


SELF_HANDLER = b'''
class Button:
    def wire(self, bus):
        bus.subscribe(self.on_click)
        self.label = "x"

    def on_click(self):
        return 1
'''


async def test_a_bound_method_keeps_its_own_relation(graph, index_file) -> None:
    await index_file("recB", "ui/button.py", SELF_HANDLER, "python")
    await _build(graph)
    assert ("method:Button.wire", "method:Button.on_click") in _pairs(graph, INDIRECT)
    assert ("method:Button.wire", "method:Button.on_click") not in _pairs(graph, CALLS)


BASE_CONNECTOR = b'''
class BaseConnector:
    @property
    def display_name(self):
        return "base"

    def on_error(self, exc):
        return exc
'''

CONFLUENCE = b'''
from conn.base import BaseConnector


class ConfluenceConnector(BaseConnector):
    @property
    def space(self):
        return "s"

    def fetch(self, bus):
        log(self.display_name, self.space)
        bus.subscribe(self.on_error)
'''


async def test_a_property_read_is_not_an_indirect_call_in_its_class_or_a_subclass(graph, index_file) -> None:
    # `self.display_name` runs an inherited @property in another file there and then.
    await index_file("recB", "conn/base.py", BASE_CONNECTOR, "python")
    await index_file("recC", "conn/confluence.py", CONFLUENCE, "python")
    await _build(graph)
    targets = {dst for src, dst in _pairs(graph, INDIRECT) if src == "method:ConfluenceConnector.fetch"}
    assert targets == {"method:BaseConnector.on_error"}


VARIABLE_WRAPPER = b'''
class VariableWrapper:
    def __init__(self, var):
        self.var = var

    def __getattr__(self, key):
        return getattr(self.var, key)


class Cursor:
    def var(self, kind):
        return kind
'''


async def test_an_attribute_named_like_another_classs_method_is_not_guessed(graph, index_file) -> None:
    # `self.var` is the wrapped object: no type places it on Cursor.var, so the name alone links nothing.
    await index_file("recV", "db/base.py", VARIABLE_WRAPPER, "python")
    await _build(graph)
    assert not {p for p in _pairs(graph, INDIRECT) if p[0] == "method:VariableWrapper.__getattr__"}


TS_GETTERS = b'''
export class History {
  get isUndoStackEmpty(): boolean { return true; }
  onChange(): void {}
}

export class AppHistory extends History {
  emit(): void {
    fire(this.isUndoStackEmpty, this.onChange);
  }
}
'''


async def test_a_get_accessor_read_is_not_an_indirect_call(graph, index_file) -> None:
    await index_file("recG", "web/history.ts", TS_GETTERS, "typescript")
    await _build(graph)
    targets = {dst for src, dst in _pairs(graph, INDIRECT) if src == "method:AppHistory.emit"}
    assert targets == {"method:History.onChange"}


TS_GETTER_SETTER = b'''
export class Box {
  get size(): number { return 1; }
  set size(v: number) {}
  onChange(): void {}
  emit(): void {
    fire(this.size, this.onChange);
  }
}
'''


async def test_a_property_with_a_getter_and_a_setter_is_read_whichever_the_name_lands_on(graph, index_file) -> None:
    await index_file("recS", "web/box.ts", TS_GETTER_SETTER, "typescript")
    await _build(graph)
    targets = {dst for src, dst in _pairs(graph, INDIRECT) if src == "method:Box.emit"}
    assert targets == {"method:Box.onChange"}


TS_HANDLERS = b'''
export function onOpen(ref: string): string { return ref; }
export function onShip(ref: string): string { return ref; }
'''

TS_ROUTES = b'''
import { onOpen, onShip } from "./handlers";

const ROUTES: Record<string, (ref: string) => string> = { open: onOpen };

export function dispatch(name: string, ref: string): string {
  return ROUTES[name](ref);
}

export function subscribe(bus: { on(n: string, f: unknown): void }): void {
  bus.on("ship", onShip);
}
'''


async def test_typescript_dispatch_and_subscription(graph, index_file) -> None:
    await index_file("recTH", "web/handlers.ts", TS_HANDLERS, "typescript")
    await index_file("recTR", "web/routes.ts", TS_ROUTES, "typescript")
    await _build(graph)
    edges = _edges(graph, INDIRECT)
    by_pair = {(k[1], k[3]): e for k, e in edges.items()}
    assert by_pair[("function:dispatch", "function:onOpen")]["context"] == "dispatch"
    assert by_pair[("function:subscribe", "function:onShip")]["context"] == "argument"
    assert all(e["confidence"] == "INFERRED" for e in edges.values())


def _registry(n: int) -> bytes:
    """A module-level command table of *n* functions and one site dispatching through it."""
    defs = "".join(f"def cmd_{i}(ref):\n    return ref\n\n\n" for i in range(n))
    table = ", ".join(f"'c{i}': cmd_{i}" for i in range(n))
    return f"{defs}COMMANDS = {{{table}}}\n\n\ndef run(name, ref):\n    return COMMANDS[name](ref)\n".encode()


async def test_a_dispatch_site_links_every_value_up_to_the_cap(graph, index_file) -> None:
    from app.modules.code_graph.edge_builder import _MAX_HEURISTIC_CANDIDATES

    await index_file("recC", "cli/commands.py", _registry(_MAX_HEURISTIC_CANDIDATES), "python")
    await _build(graph)
    dispatched = [e for e in graph.code_edges(INDIRECT) if e.get("context") == "dispatch"]
    assert len(dispatched) == _MAX_HEURISTIC_CANDIDATES


async def test_a_dispatch_site_over_the_cap_links_nothing_and_is_counted(graph, index_file) -> None:
    from app.modules.code_graph.edge_builder import _MAX_HEURISTIC_CANDIDATES

    await index_file("recC", "cli/commands.py", _registry(_MAX_HEURISTIC_CANDIDATES + 1), "python")
    result = await build_code_graph_edges(graph_provider=graph, facts_source=graph, org_id=ORG_ID,
                                          record_group_id=GROUP_ID)
    assert not [e for e in graph.code_edges(INDIRECT) if e.get("context") == "dispatch"]
    assert result.ambiguous_skipped >= 1
    # The table's own entries are still references from the module block.
    assert any(e.get("context") == "collection" for e in graph.code_edges(INDIRECT))


TSX_PANEL = b'''
import { Button } from "./button";

function handleClick(): void {}

export function Panel() {
  return <Button onClick={handleClick} label="x" />;
}
'''


async def test_a_jsx_prop_handler_is_an_indirect_call(graph, index_file) -> None:
    await index_file("recBt", "web/button.tsx", b"export function Button(p: unknown) { return null; }\n", "tsx")
    await index_file("recPn", "web/panel.tsx", TSX_PANEL, "tsx")
    await _build(graph)
    by_pair = {(k[1], k[3]): e for k, e in _edges(graph, INDIRECT).items()}
    assert by_pair[("function:Panel", "function:handleClick")]["context"] == "prop"
    assert ("function:Panel", "function:handleClick") not in _pairs(graph, CALLS)


async def test_calls_edges_are_unchanged_by_value_references(graph, index_file) -> None:
    await _python_repo(graph, index_file)
    assert ("function:settle", "function:submit") in _pairs(graph, CALLS)
    assert not any(e.get("context") for e in graph.code_edges(CALLS))
