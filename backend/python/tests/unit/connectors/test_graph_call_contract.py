"""Every graph call in app/ names a method that exists and fits its signature.

Connectors, services and routes reach the graph through ``graph_provider.X(...)``
or a transaction's ``tx_store.X(...)``. Both are usually mocks in unit tests, and
a bare mock accepts any name and any arguments, so a renamed method, a parameter
made keyword-only, or a method that never existed all pass the tests and fail
only when that line runs in production, often inside a broad ``except`` that
turns the failure into a silently missing permission or an empty list.

This reads the source with ``ast`` and holds each call to the signature it would
hit: ``IGraphDBProvider`` (and the Neo4j and Arango providers, either of which
may be installed) for ``graph_provider``, ``GraphTransactionStore`` for
``tx_store``. It imports nothing from ``app``.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

APP_DIR = Path(__file__).resolve().parents[3] / "app"

CLASS_SOURCES = [
    "services/graph_db/interface/graph_db_provider.py",
    "services/graph_db/neo4j/neo4j_provider.py",
    "services/graph_db/arango/arango_http_provider.py",
    "modules/named_entities/graph_ops.py",
    "connectors/core/base/data_store/data_store.py",
    "connectors/core/base/data_store/graph_data_store.py",
]

INTERFACE = "IGraphDBProvider"
PROVIDERS = ("Neo4jProvider", "ArangoHTTPProvider")
TX_STORE = "GraphTransactionStore"

# Called through graph_provider but declared only on the concrete providers.
# Each is defined on every backend; a caller typed against the interface cannot
# see it. Recorded rather than asserted, like KNOWN_INCOMPLETE in
# test_connector_contract.py: deleting a name once the interface declares it is
# how the gap is closed, and the stale-entry test below enforces that.
KNOWN_INTERFACE_GAPS = frozenset({
    "_create_reindex_event_payload",
    "_get_kb_context_for_record",
    "clone_agent_template",
    "delete_all_edges_for_node",
    "delete_inherit_permissions_relation_record_group",
    "get_template",
    "get_user_app_ids",
    "get_user_apps",
    "get_user_permission_app_ids",
})

# Calls to a method no backend defines. Each raises AttributeError if reached;
# listed so this file gates new ones, not to excuse these.
KNOWN_BROKEN_CALLS: frozenset[tuple[str, str]] = frozenset()


@dataclass(frozen=True)
class Call:
    path: str
    line: int
    receiver: str
    method: str
    node: ast.Call

    def where(self) -> str:
        return f"{self.path}:{self.line} {self.receiver}.{self.method}"


def _classes() -> dict[str, ast.ClassDef]:
    found: dict[str, ast.ClassDef] = {}
    for rel in CLASS_SOURCES:
        tree = ast.parse((APP_DIR / rel).read_text(encoding="utf-8-sig"))
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                found[node.name] = node
    return found


CLASSES = _classes()


def _mro(name: str) -> list[ast.ClassDef]:
    order: list[ast.ClassDef] = []

    def visit(cls_name: str) -> None:
        cls = CLASSES.get(cls_name)
        if cls is None or cls in order:
            return
        order.append(cls)
        for base in cls.bases:
            if isinstance(base, ast.Name):
                visit(base.id)

    visit(name)
    return order


def _methods(class_name: str) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    """First definition of each method along the class's bases, as Python would find it."""
    resolved: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for cls in _mro(class_name):
        for node in cls.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                resolved.setdefault(node.name, node)
    return resolved


INTERFACE_METHODS = _methods(INTERFACE)
PROVIDER_METHODS = {name: _methods(name) for name in PROVIDERS}
TX_STORE_METHODS = _methods(TX_STORE)


def _receiver(func: ast.expr) -> str | None:
    if not isinstance(func, ast.Attribute):
        return None
    value = func.value
    if isinstance(value, ast.Name) and value.id in ("graph_provider", "tx_store"):
        return value.id
    if (
        isinstance(value, ast.Attribute)
        and value.attr == "graph_provider"
        and isinstance(value.value, ast.Name)
        and value.value.id == "self"
    ):
        return "self.graph_provider"
    return None


def _calls() -> Iterator[Call]:
    for path in sorted(APP_DIR.rglob("*.py")):
        source = path.read_text(encoding="utf-8-sig")
        if "graph_provider." not in source and "tx_store." not in source:
            continue
        tree = ast.parse(source)
        rel = path.relative_to(APP_DIR.parent).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                receiver = _receiver(node.func)
                if receiver:
                    yield Call(rel, node.lineno, receiver, node.func.attr, node)


CALLS = list(_calls())


def _signature_errors(fn: ast.FunctionDef | ast.AsyncFunctionDef, call: ast.Call) -> list[str]:
    """Why ``call`` would raise TypeError against ``fn``; empty when it fits."""
    args = fn.args
    decorators = {d.id for d in fn.decorator_list if isinstance(d, ast.Name)}
    positional = [*args.posonlyargs, *args.args]
    if "staticmethod" not in decorators:
        positional = positional[1:]
    posonly = {a.arg for a in args.posonlyargs}
    named = {a.arg for a in positional if a.arg not in posonly} | {a.arg for a in args.kwonlyargs}

    star_args = any(isinstance(a, ast.Starred) for a in call.args)
    star_kwargs = any(k.arg is None for k in call.keywords)
    n_pos = sum(1 for a in call.args if not isinstance(a, ast.Starred))
    given_kw = [k.arg for k in call.keywords if k.arg is not None]

    errors = []
    if not star_args and args.vararg is None and n_pos > len(positional):
        errors.append(f"{n_pos} positional args, at most {len(positional)} accepted")
    filled = {a.arg for a in positional[:n_pos]}
    for kw in given_kw:
        if kw in filled:
            errors.append(f"{kw!r} given positionally and by keyword")
        elif kw not in named and args.kwarg is None:
            errors.append(f"unknown keyword {kw!r}")
    if not (star_args or star_kwargs):
        n_required_pos = len(positional) - len(args.defaults)
        missing = [a.arg for a in positional[:n_required_pos] if a.arg not in filled and a.arg not in given_kw]
        missing += [
            a.arg for a, default in zip(args.kwonlyargs, args.kw_defaults, strict=True)
            if default is None and a.arg not in given_kw
        ]
        if missing:
            errors.append(f"missing required {missing}")
    return errors


def _targets(call: Call) -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] | None:
    """The definitions this call can reach at runtime, or None when the name exists nowhere it may."""
    if call.receiver == "tx_store":
        fn = TX_STORE_METHODS.get(call.method)
        return [(TX_STORE, fn)] if fn else None
    if call.method in INTERFACE_METHODS:
        defs = [(INTERFACE, INTERFACE_METHODS[call.method])]
    elif call.method in KNOWN_INTERFACE_GAPS:
        defs = []
    else:
        return None
    defs += [(p, PROVIDER_METHODS[p][call.method]) for p in PROVIDERS if call.method in PROVIDER_METHODS[p]]
    return defs or None


def test_the_scan_found_the_calls() -> None:
    """Guards against a path or pattern change that would make every check below vacuous."""
    assert {INTERFACE, *PROVIDERS, TX_STORE} <= set(CLASSES)
    assert len(INTERFACE_METHODS) > 200
    by_receiver = {r: sum(1 for c in CALLS if c.receiver == r) for r in ("graph_provider", "self.graph_provider", "tx_store")}
    assert all(n > 100 for n in by_receiver.values()), by_receiver


def test_every_graph_call_names_a_method_that_exists() -> None:
    unknown = [c.where() for c in CALLS if _targets(c) is None and (c.path, c.method) not in KNOWN_BROKEN_CALLS]
    assert not unknown, (
        "these calls name a method the graph provider / transaction store does not define "
        "(an interface-only gap belongs in KNOWN_INTERFACE_GAPS):\n  " + "\n  ".join(unknown)
    )


def test_every_graph_call_fits_the_signature_it_reaches() -> None:
    misfits = []
    for call in CALLS:
        for owner, fn in _targets(call) or []:
            misfits += [f"{call.where()} vs {owner}: {e}" for e in _signature_errors(fn, call.node)]
    assert not misfits, "these calls would raise TypeError:\n  " + "\n  ".join(misfits)


def test_known_interface_gaps_are_still_gaps() -> None:
    called = {c.method for c in CALLS if c.receiver != "tx_store"}
    closed = sorted(KNOWN_INTERFACE_GAPS & set(INTERFACE_METHODS))
    unused = sorted(KNOWN_INTERFACE_GAPS - called)
    partial = sorted(m for m in KNOWN_INTERFACE_GAPS if not all(m in PROVIDER_METHODS[p] for p in PROVIDERS))
    assert not (closed or unused or partial), (
        f"KNOWN_INTERFACE_GAPS is stale - now on the interface: {closed}; no longer called: {unused}; "
        f"missing on a backend (a real bug, not a gap): {partial}"
    )


def test_known_broken_calls_are_still_broken() -> None:
    fixed = sorted(
        entry for entry in KNOWN_BROKEN_CALLS
        if not any((c.path, c.method) == entry and _targets(c) is None for c in CALLS)
    )
    assert not fixed, f"remove from KNOWN_BROKEN_CALLS, the call is gone or now resolves: {fixed}"


@pytest.mark.parametrize(
    ("source", "fits"),
    [
        ("def f(self, a, *, b): ...\nx.f(1, b=2)", True),
        ("def f(self, a, *, b): ...\nx.f(1, 2)", False),
        ("def f(self, a, *, b): ...\nx.f(1)", False),
        ("def f(self, a, *, b): ...\nx.f(1, b=2, c=3)", False),
        ("def f(self, a, **kw): ...\nx.f(1, c=3)", True),
        ("def f(self, a, b=1): ...\nx.f(1, a=1)", False),
        ("def f(self, a, b=1): ...\nx.f(*rest)", True),
    ],
)
def test_signature_check(source: str, fits: bool) -> None:
    fn, expr = ast.parse(source).body
    assert (not _signature_errors(fn, expr.value)) is fits
