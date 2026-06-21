"""Unit tests for app.modules.code_analysis.call_graph_builder.

Tests cover:
- Empty repo → empty stats
- Symbol table built from definitions
- Simple callee resolved via unique-name match → CALLS edge created
- Import-disambiguation: callee in multiple files but only one is imported
- Ambiguous callee (multiple defining files, none imported) → skipped
- External callee (not in symbol table) → skipped
- Self-loop skipped (caller calls symbol defined in same file)
- Idempotent re-run (same edges, no duplicate keys)
- Edge payload: relationshipType=CALLS, sourceSymbol, targetSymbol, sourceLineNumber, constraintName
- Stats accurate (files_processed, edges_created, edges_skipped)
- Uses get_records_by_record_group + batch_upsert_record_relations (not other methods)
- get_records_by_record_group exception → errors incremented
- batch_upsert_record_relations exception → errors counted; resolve_all does not raise
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.code_analysis.call_graph_builder import CallGraphBuilder


# ===========================================================================
# Helpers
# ===========================================================================


def _make_code_file(
    record_id: str,
    file_path: str,
    language: str = "python",
    imports: list[str] | None = None,
    definitions: list[str] | None = None,
    calls: list[dict] | None = None,
) -> MagicMock:
    """Return a minimal CodeFileRecord-like mock."""
    from app.models.entities import CodeFileRecord

    rec = MagicMock(spec=CodeFileRecord)
    rec.id = record_id
    rec.file_path = file_path
    rec.language = language
    rec.imports = imports or []
    rec.definitions = definitions or []
    rec.calls = calls or []
    return rec


def _make_builder(
    records: list | None = None,
    org_id: str = "org-1",
    record_group_id: str = "rg-1",
    connector_id: str = "conn-1",
) -> tuple[CallGraphBuilder, AsyncMock]:
    gp = AsyncMock()
    gp.get_records_by_record_group = AsyncMock(return_value=records or [])
    gp.batch_upsert_record_relations = AsyncMock()
    builder = CallGraphBuilder(
        graph_provider=gp,
        org_id=org_id,
        record_group_id=record_group_id,
        connector_id=connector_id,
    )
    return builder, gp


# ===========================================================================
# Tests
# ===========================================================================


class TestCallGraphBuilderResolveAll:
    @pytest.mark.asyncio
    async def test_empty_records_returns_zero_stats(self):
        builder, _ = _make_builder(records=[])
        stats = await builder.resolve_all()
        assert stats["files_processed"] == 0
        assert stats["edges_created"] == 0
        assert stats["edges_skipped"] == 0
        assert stats["errors"] == 0

    @pytest.mark.asyncio
    async def test_file_without_calls_processed_no_edges(self):
        rec = _make_code_file("r1", "src/a.py", definitions=["fn_a"], calls=[])
        builder, gp = _make_builder(records=[rec])
        stats = await builder.resolve_all()
        assert stats["files_processed"] == 1
        assert stats["edges_created"] == 0
        gp.batch_upsert_record_relations.assert_not_called()

    @pytest.mark.asyncio
    async def test_unique_name_match_creates_calls_edge(self):
        """Callee defined in exactly one other file → CALLS edge created."""
        caller = _make_code_file(
            "r-caller", "src/a.py",
            definitions=["my_fn"],
            calls=[{"name": "helper", "line": 5, "caller": "my_fn"}],
        )
        callee = _make_code_file(
            "r-callee", "src/b.py",
            definitions=["helper"],
            calls=[],
        )
        builder, gp = _make_builder(records=[caller, callee])
        stats = await builder.resolve_all()
        assert stats["edges_created"] == 1
        gp.batch_upsert_record_relations.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_edge_payload_fields(self):
        """Edge must have all required payload fields."""
        caller = _make_code_file(
            "r-caller", "src/a.py",
            definitions=["fn_a"],
            calls=[{"name": "fn_b", "line": 10, "caller": "fn_a"}],
        )
        callee = _make_code_file(
            "r-callee", "src/b.py",
            definitions=["fn_b"],
            calls=[],
        )
        builder, gp = _make_builder(records=[caller, callee], org_id="my-org")
        await builder.resolve_all()
        edges = gp.batch_upsert_record_relations.call_args[0][0]
        assert len(edges) == 1
        edge = edges[0]
        assert edge["relationshipType"] == "CALLS"
        assert edge["sourceSymbol"] == "fn_a"
        assert edge["targetSymbol"] == "fn_b"
        assert edge["sourceLineNumber"] == 10
        assert "constraintName" in edge
        assert edge["orgId"] == "my-org"
        assert "records/r-caller" in edge["_from"]
        assert "records/r-callee" in edge["_to"]

    @pytest.mark.asyncio
    async def test_constraint_name_is_caller_colon_callee(self):
        caller = _make_code_file(
            "r1", "src/a.py",
            definitions=["fn_a"],
            calls=[{"name": "fn_b", "line": 1, "caller": "fn_a"}],
        )
        callee = _make_code_file("r2", "src/b.py", definitions=["fn_b"], calls=[])
        builder, gp = _make_builder(records=[caller, callee])
        await builder.resolve_all()
        edges = gp.batch_upsert_record_relations.call_args[0][0]
        assert edges[0]["constraintName"] == "fn_a:fn_b"

    @pytest.mark.asyncio
    async def test_external_callee_skipped(self):
        """Callee not in any file's definitions → skipped."""
        caller = _make_code_file(
            "r1", "src/a.py",
            calls=[{"name": "os_exit", "line": 3, "caller": "main"}],
        )
        builder, gp = _make_builder(records=[caller])
        stats = await builder.resolve_all()
        assert stats["edges_skipped"] > 0
        gp.batch_upsert_record_relations.assert_not_called()

    @pytest.mark.asyncio
    async def test_self_loop_skipped(self):
        """Callee defined in the same file as the caller → no edge."""
        rec = _make_code_file(
            "r1", "src/a.py",
            definitions=["helper"],
            calls=[{"name": "helper", "line": 5, "caller": "main"}],
        )
        builder, gp = _make_builder(records=[rec])
        stats = await builder.resolve_all()
        gp.batch_upsert_record_relations.assert_not_called()

    @pytest.mark.asyncio
    async def test_ambiguous_callee_skipped(self):
        """Callee defined in multiple files with no import disambiguation → skipped."""
        caller = _make_code_file(
            "r-caller", "src/a.py",
            language="python",
            imports=[],  # no imports → no disambiguation
            calls=[{"name": "shared_fn", "line": 1, "caller": "main"}],
        )
        callee1 = _make_code_file("r1", "src/b.py", definitions=["shared_fn"])
        callee2 = _make_code_file("r2", "src/c.py", definitions=["shared_fn"])
        builder, gp = _make_builder(records=[caller, callee1, callee2])
        stats = await builder.resolve_all()
        gp.batch_upsert_record_relations.assert_not_called()

    @pytest.mark.asyncio
    async def test_import_disambiguation_picks_correct_file(self):
        """When callee is in two files but only one is imported, use the imported one."""
        caller = _make_code_file(
            "r-caller", "src/a.py",
            language="python",
            imports=["from src.b import shared_fn\n"],  # imports b, not c
            calls=[{"name": "shared_fn", "line": 1, "caller": "fn_a"}],
        )
        callee_b = _make_code_file("r-b", "src/b.py", definitions=["shared_fn"])
        callee_c = _make_code_file("r-c", "src/c.py", definitions=["shared_fn"])
        builder, gp = _make_builder(records=[caller, callee_b, callee_c])
        stats = await builder.resolve_all()
        assert stats["edges_created"] == 1
        edges = gp.batch_upsert_record_relations.call_args[0][0]
        assert "records/r-b" in edges[0]["_to"]

    @pytest.mark.asyncio
    async def test_idempotent_rerun_produces_same_edges(self):
        """Running resolve_all twice calls batch_upsert_record_relations both times."""
        caller = _make_code_file(
            "r-a", "src/a.py",
            definitions=["fn_a"],
            calls=[{"name": "fn_b", "line": 1, "caller": "fn_a"}],
        )
        callee = _make_code_file("r-b", "src/b.py", definitions=["fn_b"])
        builder, gp = _make_builder(records=[caller, callee])
        await builder.resolve_all()
        await builder.resolve_all()
        assert gp.batch_upsert_record_relations.await_count == 2

    @pytest.mark.asyncio
    async def test_get_records_by_record_group_exception_increments_errors(self):
        gp = AsyncMock()
        gp.get_records_by_record_group = AsyncMock(side_effect=RuntimeError("db error"))
        builder = CallGraphBuilder(
            graph_provider=gp, org_id="org", record_group_id="rg", connector_id="conn"
        )
        stats = await builder.resolve_all()
        assert stats["errors"] > 0
        assert stats["files_processed"] == 0

    @pytest.mark.asyncio
    async def test_batch_upsert_exception_counted_not_propagated(self):
        caller = _make_code_file(
            "r-a", "src/a.py",
            calls=[{"name": "fn_b", "line": 1, "caller": "main"}],
        )
        callee = _make_code_file("r-b", "src/b.py", definitions=["fn_b"])
        builder, gp = _make_builder(records=[caller, callee])
        gp.batch_upsert_record_relations = AsyncMock(side_effect=RuntimeError("write fail"))
        try:
            stats = await builder.resolve_all()
        except RuntimeError:
            pytest.fail("batch_upsert_record_relations exception propagated")
        assert stats["errors"] > 0

    @pytest.mark.asyncio
    async def test_uses_get_records_by_record_group_not_get_nodes_by_filters(self):
        builder, gp = _make_builder(records=[])
        gp.get_nodes_by_filters = AsyncMock()
        await builder.resolve_all()
        gp.get_records_by_record_group.assert_awaited_once()
        gp.get_nodes_by_filters.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_records_by_record_group_called_with_correct_args(self):
        builder, gp = _make_builder(
            records=[], org_id="org-99", record_group_id="rg-42", connector_id="conn-x"
        )
        await builder.resolve_all()
        gp.get_records_by_record_group.assert_awaited_once_with(
            "rg-42", "conn-x", "org-99", depth=-1
        )

    @pytest.mark.asyncio
    async def test_multiple_calls_from_same_file_deduplicated(self):
        """Two call sites for the same (caller, callee) pair → only one edge."""
        caller = _make_code_file(
            "r-a", "src/a.py",
            definitions=["fn_a"],
            calls=[
                {"name": "fn_b", "line": 1, "caller": "fn_a"},
                {"name": "fn_b", "line": 5, "caller": "fn_a"},
            ],
        )
        callee = _make_code_file("r-b", "src/b.py", definitions=["fn_b"])
        builder, gp = _make_builder(records=[caller, callee])
        stats = await builder.resolve_all()
        edges = gp.batch_upsert_record_relations.call_args[0][0]
        assert len(edges) == 1

    @pytest.mark.asyncio
    async def test_different_caller_symbols_produce_different_edges(self):
        """fn_a calls fn_b AND fn_c calls fn_b → two distinct CALLS edges."""
        caller = _make_code_file(
            "r-a", "src/a.py",
            definitions=["fn_a", "fn_c"],
            calls=[
                {"name": "fn_b", "line": 1, "caller": "fn_a"},
                {"name": "fn_b", "line": 8, "caller": "fn_c"},
            ],
        )
        callee = _make_code_file("r-b", "src/b.py", definitions=["fn_b"])
        builder, gp = _make_builder(records=[caller, callee])
        stats = await builder.resolve_all()
        edges = gp.batch_upsert_record_relations.call_args[0][0]
        assert len(edges) == 2
        constraint_names = {e["constraintName"] for e in edges}
        assert "fn_a:fn_b" in constraint_names
        assert "fn_c:fn_b" in constraint_names
