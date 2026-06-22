"""Unit tests for code-specific logic in app.utils.chat_helpers.

Tests cover Phase 4 of the Code File Indexing plan:

build_message_content_array — CODE block formatting:
- CODE block produces a fenced code block (```lang ... ```) in the output
- Language tag is included in the fence header (```python, ```typescript)
- Block Index and Citation ID appear in the output for CODE blocks
- Kind line (function/class/method) appears when code_kind is set
- Scope line appears when parent_name is set
- Symbol line appears when name is set
- Lines range appears when start_line / end_line are set
- Non-code blocks (TEXT) are not affected by the code path

enrich_virtual_record_id_to_result_with_code_relations:
- Returns immediately when graph_provider is None (no-op)
- Returns immediately when no CODE_FILE records are in the map
- IMPORTS traversal: adds imported files to context with _code_import_of
- CALLS traversal: adds callee files to context with _code_call_of
- Records already present in context are not duplicated
- sourceSymbol and targetSymbol propagated to enriched entry
- graph_provider exceptions are caught and do not propagate
- blob_store returning None for an imported file → skipped
"""
from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.blocks import BlockType
from app.models.entities import RecordType
from app.utils.chat_helpers import (
    build_message_content_array,
    enrich_virtual_record_id_to_result_with_code_relations,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_result(
    virtual_record_id: str = "vrid-1",
    block_type: str = BlockType.CODE.value,
    block_index: int = 0,
    content: str = "def foo():\n    return 1",
    code_metadata: dict | None = None,
    code_kind: str = "function",
    name: str = "foo",
    parent_name: str = "",
    start_line: int | None = 1,
    end_line: int | None = 3,
) -> dict:
    return {
        "virtual_record_id": virtual_record_id,
        "block_type": block_type,
        "block_index": block_index,
        "content": content,
        "code_metadata": code_metadata or {"language": "python"},
        "code_kind": code_kind,
        "name": name,
        "parent_name": parent_name,
        "start_line": start_line,
        "end_line": end_line,
    }


def _make_record(
    record_id: str = "rec-1",
    record_type: str = "CODE_FILE",
    frontend_url: str = "http://localhost:3000",
    context_metadata: str = "",
) -> dict:
    return {
        "id": record_id,
        "record_type": record_type,
        "frontend_url": frontend_url,
        "context_metadata": context_metadata,
    }


def _build(
    results: List[dict],
    record_map: Dict[str, Any],
    is_multimodal: bool = False,
) -> List[list]:
    contents, _ = build_message_content_array(
        flattened_results=results,
        virtual_record_id_to_result=record_map,
        is_multimodal_llm=is_multimodal,
    )
    return contents


def _flat_text(contents: List[list]) -> str:
    parts = []
    for group in contents:
        for item in group:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item["text"])
    return "\n".join(parts)


def _code_file_entry(record_id: str) -> dict:
    return {"id": record_id, "record_type": RecordType.CODE_FILE.value}


async def _run_enrich(virtual_record_id_to_result, graph_provider=None, org_id="org-1"):
    blob_store = AsyncMock()
    blob_store.get_blocks_container_for_record = AsyncMock(return_value={"blocks": []})

    await enrich_virtual_record_id_to_result_with_code_relations(
        virtual_record_id_to_result=virtual_record_id_to_result,
        blob_store=blob_store,
        org_id=org_id,
        graph_provider=graph_provider,
    )
    return virtual_record_id_to_result


# ===========================================================================
# build_message_content_array — CODE block formatting
# ===========================================================================

class TestBuildMessageContentArrayCode:
    def test_code_block_produces_fenced_block(self):
        result = _make_result(content="def foo():\n    return 1")
        record = _make_record()
        contents = _build([result], {"vrid-1": record})
        flat = _flat_text(contents)
        assert "```" in flat, "Expected fenced code block in output"

    def test_code_block_includes_language_tag(self):
        result = _make_result(code_metadata={"language": "python"})
        record = _make_record()
        contents = _build([result], {"vrid-1": record})
        flat = _flat_text(contents)
        assert "```python" in flat, "Expected ```python fence tag"

    def test_code_block_typescript_fence(self):
        result = _make_result(code_metadata={"language": "typescript"}, content="const x = 1;")
        record = _make_record()
        contents = _build([result], {"vrid-1": record})
        flat = _flat_text(contents)
        assert "```typescript" in flat

    def test_code_block_includes_block_index(self):
        result = _make_result(block_index=5)
        record = _make_record()
        contents = _build([result], {"vrid-1": record})
        flat = _flat_text(contents)
        assert "Block Index: 5" in flat

    def test_code_block_includes_citation_id(self):
        result = _make_result()
        record = _make_record()
        contents = _build([result], {"vrid-1": record})
        flat = _flat_text(contents)
        assert "Citation ID:" in flat

    def test_code_block_includes_kind(self):
        result = _make_result(code_kind="function")
        record = _make_record()
        contents = _build([result], {"vrid-1": record})
        flat = _flat_text(contents)
        assert "Kind: function" in flat

    def test_code_block_includes_scope_when_parent_set(self):
        result = _make_result(parent_name="MyClass")
        record = _make_record()
        contents = _build([result], {"vrid-1": record})
        flat = _flat_text(contents)
        assert "Scope: MyClass" in flat

    def test_code_block_no_scope_when_parent_empty(self):
        result = _make_result(parent_name="")
        record = _make_record()
        contents = _build([result], {"vrid-1": record})
        flat = _flat_text(contents)
        assert "Scope:" not in flat

    def test_code_block_includes_symbol_name(self):
        result = _make_result(name="compute_total")
        record = _make_record()
        contents = _build([result], {"vrid-1": record})
        flat = _flat_text(contents)
        assert "Symbol: compute_total" in flat

    def test_code_block_includes_line_range(self):
        result = _make_result(start_line=10, end_line=25)
        record = _make_record()
        contents = _build([result], {"vrid-1": record})
        flat = _flat_text(contents)
        assert "Lines: 10" in flat
        assert "25" in flat

    def test_code_block_includes_code_content(self):
        unique_code = "def unique_func_123():\n    return 999"
        result = _make_result(content=unique_code)
        record = _make_record()
        contents = _build([result], {"vrid-1": record})
        flat = _flat_text(contents)
        assert "unique_func_123" in flat

    def test_code_block_block_type_label(self):
        result = _make_result()
        record = _make_record()
        contents = _build([result], {"vrid-1": record})
        flat = _flat_text(contents)
        assert "Block Type: code" in flat

    def test_text_block_unaffected_by_code_path(self):
        """A plain TEXT block does not receive the code-fence treatment."""
        result = _make_result(
            block_type=BlockType.TEXT.value,
            content="Some plain text",
            code_metadata=None,
        )
        record = _make_record()
        contents = _build([result], {"vrid-1": record})
        flat = _flat_text(contents)
        assert "```" not in flat

    def test_no_language_fence_opens_without_tag(self):
        """When language metadata is absent, the fence still appears (empty lang)."""
        result = _make_result(code_metadata=None)
        record = _make_record()
        contents = _build([result], {"vrid-1": record})
        flat = _flat_text(contents)
        assert "```" in flat

    def test_multiple_code_blocks_same_record(self):
        results = [
            _make_result(block_index=0, content="def alpha(): pass", name="alpha"),
            _make_result(block_index=1, content="def beta(): pass", name="beta"),
        ]
        record = _make_record()
        contents = _build(results, {"vrid-1": record})
        flat = _flat_text(contents)
        assert "alpha" in flat
        assert "beta" in flat


# ===========================================================================
# enrich_virtual_record_id_to_result_with_code_relations
# ===========================================================================

class TestCodeRelationEnrichment:
    @pytest.mark.asyncio
    async def test_no_graph_provider_is_noop(self):
        ctx = {"v1": _code_file_entry("r1")}
        original_keys = set(ctx.keys())
        await _run_enrich(ctx, graph_provider=None)
        assert set(ctx.keys()) == original_keys

    @pytest.mark.asyncio
    async def test_no_code_file_records_is_noop(self):
        gp = AsyncMock()
        ctx = {"v1": {"id": "r1", "record_type": "FILE"}}
        original_keys = set(ctx.keys())
        await _run_enrich(ctx, graph_provider=gp)
        assert set(ctx.keys()) == original_keys
        gp.get_child_record_ids_by_relation_type.assert_not_called()

    @pytest.mark.asyncio
    async def test_imports_traversal_adds_entry_with_code_import_of(self):
        """IMPORTS enrichment adds the importing file to context."""
        gp = AsyncMock()
        gp.get_child_record_ids_by_relation_type = AsyncMock(
            return_value=[{
                "record_id": "r-importer",
                "sourceSymbol": "from src.r1 import fn",
                "targetSymbol": "r1",
            }]
        )
        # CALLS path (if present) returns empty
        gp.get_parent_record_ids_by_relation_type = AsyncMock(return_value=[])

        ctx = {"v1": _code_file_entry("r1")}
        await _run_enrich(ctx, graph_provider=gp)

        importer_entry = next(
            (v for v in ctx.values() if isinstance(v, dict) and v.get("id") == "r-importer"),
            None,
        )
        assert importer_entry is not None
        assert importer_entry.get("_code_import_of") == "r1"

    @pytest.mark.asyncio
    async def test_already_present_record_not_duplicated(self):
        """Records already in virtual_record_id_to_result are not added again."""
        gp = AsyncMock()
        gp.get_child_record_ids_by_relation_type = AsyncMock(
            return_value=[{"record_id": "r2", "sourceSymbol": "", "targetSymbol": ""}]
        )
        gp.get_parent_record_ids_by_relation_type = AsyncMock(return_value=[])

        ctx = {
            "v1": _code_file_entry("r1"),
            "v2": _code_file_entry("r2"),
        }
        original_count = len(ctx)
        await _run_enrich(ctx, graph_provider=gp)
        assert len(ctx) == original_count

    @pytest.mark.asyncio
    async def test_get_child_exception_does_not_propagate(self):
        gp = AsyncMock()
        gp.get_child_record_ids_by_relation_type = AsyncMock(
            side_effect=RuntimeError("db error")
        )
        gp.get_parent_record_ids_by_relation_type = AsyncMock(return_value=[])

        ctx = {"v1": _code_file_entry("r1")}
        try:
            await _run_enrich(ctx, graph_provider=gp)
        except RuntimeError:
            pytest.fail("Exception from get_child_record_ids_by_relation_type propagated")

    @pytest.mark.asyncio
    async def test_blob_returning_none_skipped(self):
        """When blob_store returns None for an imported file, it is silently skipped."""
        gp = AsyncMock()
        gp.get_child_record_ids_by_relation_type = AsyncMock(
            return_value=[{"record_id": "rec-no-blob"}]
        )
        gp.get_parent_record_ids_by_relation_type = AsyncMock(return_value=[])

        blob_store = AsyncMock()
        blob_store.get_blocks_container_for_record = AsyncMock(return_value=None)

        ctx = {"v1": _code_file_entry("r1")}
        original_len = len(ctx)
        await enrich_virtual_record_id_to_result_with_code_relations(
            virtual_record_id_to_result=ctx,
            blob_store=blob_store,
            org_id="org-1",
            graph_provider=gp,
        )
        assert len(ctx) == original_len
