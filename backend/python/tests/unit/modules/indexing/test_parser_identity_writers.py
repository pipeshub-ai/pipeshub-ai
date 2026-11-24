"""Every call site of ``indexed_copy_fields`` (the writers of ``hasIndexedCopy``) is accounted for.

A writer that makes a COMPLETED copy writes the parser identity with it, through the builders of ``parser_identity`` (checked in the
writer's own function body). One that cannot make a tracked COMPLETED copy says why. A new site fails here until it is placed.
"""
from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[4] / "app"

#: (file, enclosing function) -> the builder its body must call.
WRITES_THE_IDENTITY = {
    ("modules/transformers/sink_orchestrator.py", "SinkOrchestrator._update_indexing_status"): "indexed_parser_fields",
    ("events/events.py", "EventProcessor._attach_to_finished_twin"): "copied_identity_fields",
    ("services/graph_db/arango/arango_http_provider.py", "ArangoHTTPProvider.update_queued_duplicates_status"): "promotion_plan",
    ("services/graph_db/neo4j/neo4j_provider.py", "Neo4jProvider.update_queued_duplicates_status"): "promotion_plan",
    ("services/graph_db/postgres/sync_state.py", "SyncStateMixin.update_queued_duplicates_status"): "plan_promotions",
}

#: (file, enclosing function) -> why it writes no identity.
NEEDS_NONE = {
    ("events/events.py", "EventProcessor._orchestrate_via_services"): "EMPTY: the copy is cleared",
    ("events/events.py", "EventProcessor.mark_record_status"): "never writes COMPLETED (IN_PROGRESS, EMPTY, unsupported)",
    ("events/processor.py", "Processor._mark_record"): "EMPTY: the copy is cleared",
    ("modules/transformers/pipeline.py", "IndexingPipeline.apply"): "EMPTY: the copy is cleared",
    ("services/graph_db/arango/arango_http_provider.py", "ArangoHTTPProvider.compare_and_set_record_fields"): "a stage status by revision: the copy and its timestamp are not changed",
    ("services/graph_db/neo4j/neo4j_provider.py", "Neo4jProvider.compare_and_set_record_fields"): "a stage status by revision: the copy and its timestamp are not changed",
    ("services/graph_db/postgres/codegraph.py", "CodeGraphMixin.compare_and_set_record_fields"): "a stage status by revision: the copy and its timestamp are not changed",
}


def _call_name(call: ast.Call) -> str | None:
    func = call.func
    return func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None


def _direct_calls(function: ast.AST) -> list[ast.Call]:
    """The calls in a function's own body, not in the functions nested in it."""
    calls: list[ast.Call] = []
    pending = list(ast.iter_child_nodes(function))
    while pending:
        node = pending.pop()
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        if isinstance(node, ast.Call):
            calls.append(node)
        pending.extend(ast.iter_child_nodes(node))
    return calls


def _walk(node: ast.AST, scope: tuple[str, ...]):  # noqa: ANN202
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.ClassDef):
            yield from _walk(child, (*scope, child.name))
        elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
            yield ".".join((*scope, child.name)), child
            yield from _walk(child, (*scope, child.name))
        else:
            yield from _walk(child, scope)


def _sites() -> dict[tuple[str, str], str]:
    """``(file, qualified function) -> source of that function`` for every function that itself calls ``indexed_copy_fields``."""
    found: dict[tuple[str, str], str] = {}
    for path in APP.rglob("*.py"):
        source = path.read_text(encoding="utf-8-sig")
        if "indexed_copy_fields(" not in source:
            continue
        relative = path.relative_to(APP).as_posix()
        for name, function in _walk(ast.parse(source), ()):
            if any(_call_name(c) == "indexed_copy_fields" for c in _direct_calls(function)):
                found[(relative, name)] = ast.get_source_segment(source, function) or ""
    return found


def test_every_call_site_of_indexed_copy_fields_is_listed_and_none_is_stale() -> None:
    sites = set(_sites())
    listed = set(WRITES_THE_IDENTITY) | set(NEEDS_NONE)
    assert sites - listed == set(), f"a writer of hasIndexedCopy that is not accounted for: {sorted(sites - listed)}"
    assert listed - sites == set(), f"listed but gone: {sorted(listed - sites)}"


def test_each_writer_of_a_completed_copy_uses_the_identity_builder_in_its_own_body() -> None:
    sites = _sites()
    for site, builder in WRITES_THE_IDENTITY.items():
        assert builder in sites[site], f"{site} writes COMPLETED but not through {builder}"
