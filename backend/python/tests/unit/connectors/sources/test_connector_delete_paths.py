"""Every connector deletes records through a call that honours the trash.

``tests/integration/test_soft_delete_checklist_e2e.py`` proves on real Neo4j and
ArangoDB that each of these entry points puts the record in the trash when
``ENABLE_SOFT_DELETE`` is on. That only covers the connectors if they have no
other way to remove a record, which is what this checks, statically, over every
module under ``app/connectors/sources``:

- a connector may remove records only through the trash-aware entry points;
- the raw hard deletes of the data store are allowed only in modules that read
  the flag and send the same set to the trash instead
  (``test_sync_deletes_soft_delete.py`` covers those two);
- every entry point a connector uses is one the end-to-end test drives.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

SOURCES = Path(__file__).resolve().parents[4] / "app" / "connectors" / "sources"

# Entry point -> the case of CONNECTOR_DELETES in test_soft_delete_checklist_e2e.py that drives it.
TRASH_AWARE = {
    "on_record_deleted": "record",
    "on_records_deleted_cascade": "cascade",
    "delete_record_by_external_id": "by external id",
    "on_records_soft_deleted": "own batch",
    "remove_records_not_listed": "listing scan",
    # Calls on_record_deleted for each record outside the folders kept in scope.
    "remove_records_outside_scope": "listing scan",
}

# The data store's record deletes that never look at the flag.
RAW_HARD_DELETES = {
    "delete_records_and_relations",
    "delete_record_by_key",
    "delete_nodes_and_edges",
    "delete_records_recursive",
    "delete_single_record",
}

# Read ENABLE_SOFT_DELETE themselves and call on_records_soft_deleted when it is on.
# Linear and Jira Data Center delete through on_records_deleted_cascade, which
# reads the flag itself, so no connector checks it around a raw hard delete.
FLAG_CHECKING_HARD_DELETERS: set[str] = set()

# Whole-collection delete: a hard delete by design, as in the soft-delete design (section 6).
KB_DELETE = {"localKB/handlers/kb_service.py": {"delete_connector_instance"}}

# A push whose create committed after the knowledge base was marked DELETING removes that
# record itself, because the cascade may have collected before it: the record belongs to a
# knowledge base being hard-deleted (as KB_DELETE above), and nobody was ever shown it.
KB_DELETE_COMPENSATION = {"localKB/push/service.py": ("_undo_create_in_deleted_kb", "delete_nodes_and_edges")}


def _called_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                names.add(func.attr)
            elif isinstance(func, ast.Name):
                names.add(func.id)
    return names


def _calls_by_module() -> dict[str, set[str]]:
    calls: dict[str, set[str]] = {}
    for path in sorted(SOURCES.rglob("*.py")):
        calls[path.relative_to(SOURCES).as_posix()] = _called_names(path)
    return calls


def _raw_delete_references(tree: ast.AST, approved: str | None = None) -> set[str]:
    """The raw hard deletes a module mentions outside the ``approved`` function.

    A mention is a call through an attribute or a bare name, an import, an alias
    (``drop = graph.delete_nodes_and_edges``) or the name as a string (``getattr``), so a call
    that goes round the attribute check is still seen. The approved function's body is left out.
    """
    names: set[str] = set()

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if approved and isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == approved:
                continue
            if isinstance(child, ast.Attribute):
                names.add(child.attr)
            elif isinstance(child, ast.Name):
                names.add(child.id)
            elif isinstance(child, ast.alias):
                names.add(child.name)
            elif isinstance(child, ast.Constant) and isinstance(child.value, str):
                names.add(child.value)
            visit(child)

    visit(tree)
    return names & RAW_HARD_DELETES


def _raw_deletes_by_module() -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for path in sorted(SOURCES.rglob("*.py")):
        module = path.relative_to(SOURCES).as_posix()
        approved = KB_DELETE_COMPENSATION.get(module, (None, None))[0]
        result[module] = _raw_delete_references(ast.parse(path.read_text(encoding="utf-8")), approved)
    return result


def test_the_compensating_hard_delete_is_in_its_function() -> None:
    for module, (function, call) in KB_DELETE_COMPENSATION.items():
        tree = ast.parse((SOURCES / module).read_text(encoding="utf-8"))
        (fn,) = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == function]
        # The exemption is the function's body, so it has to contain the call it exempts.
        assert _raw_delete_references(fn) == {call}, (module, function)


def test_the_scan_for_raw_deletes_sees_every_way_to_call_one() -> None:
    for source in (
        "async def other(g):\n    await g.delete_nodes_and_edges([1], 'records')\n",
        "from x import delete_nodes_and_edges\nasync def other():\n    await delete_nodes_and_edges([1])\n",
        "async def other(g):\n    drop = g.delete_nodes_and_edges\n    await drop([1])\n",
        "async def other(g):\n    await getattr(g, 'delete_nodes_and_edges')([1])\n",
    ):
        tree = ast.parse(source + "async def approved(g):\n    await g.delete_nodes_and_edges([2])\n")
        assert _raw_delete_references(tree, "approved") == {"delete_nodes_and_edges"}, source
    only_approved = ast.parse("async def approved(g):\n    await g.delete_nodes_and_edges([2])\n")
    assert _raw_delete_references(only_approved, "approved") == set()


def test_the_scan_sees_the_connectors() -> None:
    calls = _calls_by_module()
    users = {module for module, names in calls.items() if names & TRASH_AWARE.keys()}
    # A scan that read nothing would pass every check below.
    assert len(users) >= 25, sorted(users)
    assert "google/drive/team/connector.py" in users and "gitlab/repos.py" in users


def test_only_flag_checking_connectors_call_a_raw_hard_delete() -> None:
    offenders = {
        module: sorted(names)
        for module, names in _raw_deletes_by_module().items()
        if names and module not in FLAG_CHECKING_HARD_DELETERS
    }
    assert offenders == {}, (
        "These connectors remove records without the trash. Use on_record_deleted, "
        "on_records_deleted_cascade or on_records_soft_deleted, or read ENABLE_SOFT_DELETE "
        f"and send the same set to the trash: {offenders}"
    )


def test_the_flag_checking_connectors_send_to_the_trash() -> None:
    calls = _calls_by_module()
    for module in FLAG_CHECKING_HARD_DELETERS:
        assert {"is_soft_delete_enabled", "on_records_soft_deleted"} <= calls[module], module


def test_every_entry_point_a_connector_uses_is_driven_end_to_end() -> None:
    from tests.integration.test_soft_delete_checklist_e2e import CONNECTOR_DELETES

    driven = {case.entry for case in CONNECTOR_DELETES}
    used: dict[str, list[str]] = defaultdict(list)
    for module, names in _calls_by_module().items():
        for name in names & TRASH_AWARE.keys():
            used[TRASH_AWARE[name]].append(module)
    missing = {entry: modules for entry, modules in used.items() if entry not in driven}
    assert missing == {}, missing


def test_the_only_whole_connector_delete_is_the_collection_delete() -> None:
    whole = {
        module: names & {"delete_connector_instance"}
        for module, names in _calls_by_module().items()
        if "delete_connector_instance" in names
    }
    assert whole == KB_DELETE
