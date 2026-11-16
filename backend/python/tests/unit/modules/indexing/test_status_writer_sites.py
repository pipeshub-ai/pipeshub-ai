"""Every writer of a record's status, and every transactional writer of records, is classified.

The scan is by call site of the status-writing provider methods (not by a literal in a dict), plus the generic writers
(``update_node`` ...) when their arguments name ``indexingStatus`` or ``indexing_status``. A writer that is added
without being listed fails here; so does a listed one that is gone, and a transactional writer with no rerun owner
(a ``RecordSchemaNarrowError`` aborts its transaction, so something must run the unit again).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.unit.modules.indexing.status_writer_sites import (
    CLASSES,
    DEAD,
    HELPER_WRITERS,
    OWNERS,
    SITES,
    TX_OWNERS,
)

APP = Path(__file__).resolve().parents[4] / "app"

DEDICATED = frozenset({
    "compare_and_set_indexing_status", "compare_and_set_record_fields", "update_indexing_status_for_record_ids",
    "update_queued_duplicates_status", "reset_indexing_status_for_connector", "transition_unsupported", "restore_records",
    "reindex_single_record", "write_attempt_start",
})
GENERIC = frozenset({
    "update_node", "batch_update_nodes", "update_node_fields_if_match", "batch_upsert_nodes", "update_record_fields",
    "_mark_record", "mark_record_status", "__update_document_status", "_update_document_status",
})
# any call that writes a document: a function that names ``indexingStatus`` and calls one of these writes a status
RECORD_WRITERS = GENERIC | frozenset({
    "update_document", "update_node_if_match", "update_node_if_unchanged", "batch_upsert_records", "upsert_records", "update_nodes",
    "batch_update_records", "upsert_node", "create_node_if_absent", "update_records",
})
TX_WRITERS = frozenset({
    "batch_upsert_records", "batch_upsert_nodes", "batch_update_nodes", "restore_records", "update_node", "update_record_fields",
    "create_node_if_absent", "upsert_record_under_parent", "claim_push_record", "update_node_fields_if_match",
})


def _qualname(stack: list[str]) -> str:
    return ".".join(stack)


def scan(source: str) -> tuple[list[tuple[str, str]], list[tuple[str, str]], set[str]]:
    """(status-writer sites, transactional record writes, names of functions that have a status or run parameter)."""
    tree = ast.parse(source)
    status_sites: list[tuple[str, str]] = []
    tx_sites: list[tuple[str, str]] = []
    helpers: set[str] = set()
    stack: list[str] = []
    function_stack: list[ast.AST] = []

    class Visitor(ast.NodeVisitor):
        def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            stack.append(node.name)
            function_stack.append(node)
            if any(a.arg in ("indexing_status", "status") for a in node.args.args + node.args.kwonlyargs):
                helpers.add(_qualname(stack))
            self.generic_visit(node)
            function_stack.pop()
            names_status = any(isinstance(n, ast.Constant) and n.value == "indexingStatus" for n in ast.walk(node))
            if names_status:
                written = sorted({
                    n.func.attr for n in ast.walk(node)
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in RECORD_WRITERS
                })
                # a function that names the status and writes a document is a writer, whichever way the status reaches the write
                here = _qualname(stack)
                if written and not any(function == here or function.startswith(here + ".") for function, _ in status_sites):
                    status_sites.append((_qualname(stack), written[0]))
            stack.pop()

        visit_FunctionDef = _function
        visit_AsyncFunctionDef = _function

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            stack.append(node.name)
            self.generic_visit(node)
            stack.pop()

        def _names_the_status_through_a_variable(self, call: ast.Call) -> bool:
            """A ``Name`` argument that the enclosing function builds as a dict holding the status, or fills with it."""
            names = {a.id for a in [*call.args, *(k.value for k in call.keywords)] if isinstance(a, ast.Name)}
            if not names or not function_stack:
                return False
            for node in ast.walk(function_stack[-1]):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id in names and "indexingStatus" in ast.unparse(node.value):
                            return True
                        if (isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name) and target.value.id in names
                                and "indexingStatus" in ast.unparse(target.slice)):
                            return True
            return False

        def visit_Call(self, node: ast.Call) -> None:
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None
            if name in DEDICATED:
                status_sites.append((_qualname(stack), name))
            elif name in GENERIC:
                blob = " ".join([ast.unparse(a) for a in node.args] + [f"{k.arg} {ast.unparse(k.value)}" for k in node.keywords])
                if "indexingStatus" in blob or "indexing_status" in blob or self._names_the_status_through_a_variable(node):
                    status_sites.append((_qualname(stack), name))
            if isinstance(func, ast.Attribute) and name in TX_WRITERS:
                receiver = ast.unparse(func.value)
                transactional = "tx_store" in receiver or any(k.arg == "transaction" for k in node.keywords)
                text = ast.unparse(node)
                if transactional and (name in ("batch_upsert_records", "restore_records") or "records" in text.lower()):
                    tx_sites.append((_qualname(stack), name))
            self.generic_visit(node)

    Visitor().visit(tree)
    return status_sites, tx_sites, helpers


def _walk() -> tuple[set[tuple[str, str, str]], set[tuple[str, str]]]:
    status: set[tuple[str, str, str]] = set()
    tx: set[tuple[str, str]] = set()
    for path in sorted(APP.rglob("*.py")):
        relative = path.relative_to(APP).as_posix()
        if relative.startswith("services/graph_db/") and "reindex_single_record" not in path.read_text(encoding="utf-8-sig"):
            continue
        source = path.read_text(encoding="utf-8-sig")
        statuses, txs, _ = scan(source)
        status |= {(relative, function, method) for function, method in statuses}
        if not relative.startswith("services/graph_db/"):
            tx |= {(relative, function) for function, _ in txs}
    return status, tx


def test_every_site_classified() -> None:
    status, _ = _walk()
    helpers = {(relative, function) for relative, function in HELPER_WRITERS}
    unlisted = sorted(site for site in status - SITES.keys() if site[:2] not in helpers)
    gone = sorted(SITES.keys() - status)
    assert unlisted == [], f"status-writing call sites not in status_writer_sites.py (classify them): {unlisted}"
    assert gone == [], f"listed sites that no longer exist: {gone}"
    assert set(SITES.values()) <= CLASSES


def test_every_transactional_writer_names_its_rerun_owner() -> None:
    """A writer with no owner that runs its whole transaction again is a build blocker."""
    _, tx = _walk()
    unlisted = sorted(tx - TX_OWNERS.keys())
    gone = sorted(TX_OWNERS.keys() - tx)
    assert unlisted == [], f"transactional record writers with no listed rerun owner: {unlisted}"
    assert gone == [], f"listed transactional writers that no longer exist: {gone}"
    for key, (owner, note) in TX_OWNERS.items():
        assert owner in OWNERS and note, key


def test_the_retry_owners_are_decorated() -> None:
    """A writer listed under ``retry_on_deadlock`` is reached through a public method of its class that carries the decorator."""
    source = (APP / "connectors/core/base/data_processor/data_source_entities_processor.py").read_text(encoding="utf-8-sig")
    decorated = {
        node.name for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any("retry_on_deadlock" in ast.unparse(d) for d in node.decorator_list)
    }
    for (relative, function), (owner, note) in TX_OWNERS.items():
        if owner != "retry_on_deadlock" or not relative.endswith("data_source_entities_processor.py"):
            continue
        method = function.rsplit(".", 1)[-1]
        if note == "itself":
            assert method in decorated, f"{function} is listed as retried by itself but is not decorated"
        else:
            assert {"on_new_records"} & decorated, "on_new_records must carry retry_on_deadlock"


def test_the_dead_writer_has_no_caller() -> None:
    dead = [key for key, kind in SITES.items() if kind == DEAD]
    assert dead, "the list names the dead writer"
    for _relative, function, _ in dead:
        name = function.rsplit(".", 1)[-1]
        callers = []
        for path in APP.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
                callers.extend(
                    str(path) for _ in [node] if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == name
                )
        assert callers == [], f"{name} is listed as dead but is called from {callers}"


def test_a_helper_that_writes_a_status_it_is_given_is_listed() -> None:
    """The listed helpers exist and still take the status as a parameter; a new such helper is a new writer."""
    for (relative, function), kind in HELPER_WRITERS.items():
        source = (APP / relative).read_text(encoding="utf-8-sig")
        names = set(_all_functions(source))
        assert function in names, f"{relative}: {function} is gone"
        assert kind in CLASSES


def _all_functions(source: str) -> list[str]:
    found: list[str] = []
    stack: list[str] = []

    class Visitor(ast.NodeVisitor):
        def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            stack.append(node.name)
            found.append(_qualname(stack))
            self.generic_visit(node)
            stack.pop()

        visit_FunctionDef = _function
        visit_AsyncFunctionDef = _function

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            stack.append(node.name)
            self.generic_visit(node)
            stack.pop()

    Visitor().visit(ast.parse(source))
    return found


def test_an_unlisted_writer_that_passes_its_status_as_a_variable_is_seen() -> None:
    """The negative control: a call that writes ``{"indexingStatus": status}`` through ``update_node`` is a site."""
    sites, _, _ = scan("async def sneaky(graph, status):\n    await graph.update_node('r', 'records', {'indexingStatus': status})\n")
    assert sites == [("sneaky", "update_node")]
    sites, _, _ = scan("async def variable(graph, status):\n    fields = {'indexingStatus': status}\n    await graph.update_node('r', 'records', fields)\n")
    assert sites == [("variable", "update_node")]
    sites, _, _ = scan("async def filled(graph, status):\n    fields = {}\n    fields['indexingStatus'] = status\n    await graph.update_node('r', 'records', fields)\n")
    assert sites == [("filled", "update_node")]
    sites, _, _ = scan("async def keyword(handler, status):\n    await handler.update_record_fields(doc, indexing_status=status)\n")
    assert sites == [("keyword", "update_record_fields")]
    sites, _, _ = scan("async def other(graph):\n    await graph.update_node('r', 'users', {'name': 'x'})\n")
    assert sites == []


def test_a_transactional_records_write_with_no_owner_is_seen() -> None:
    _, tx, _ = scan("async def save(self, tx_store, record):\n    await tx_store.batch_upsert_records([record])\n")
    assert tx == [("save", "batch_upsert_records")]


@pytest.mark.parametrize("key", sorted(HELPER_WRITERS))
def test_the_helper_writers_are_classified(key: tuple[str, str]) -> None:
    assert HELPER_WRITERS[key] in CLASSES
