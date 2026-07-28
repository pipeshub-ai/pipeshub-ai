"""Phase 1 contract tests — pin tool schema surfaces BEFORE the
knowledge-toolset merge.  These tests must stay green throughout every
refactor phase.  After Phase 3 the imports will point at the new
locations; update the import lines only, never the assertions.

What is pinned:
  A. `KnowledgeGraph` toolset: metadata flags, tool names, and parameter
     contracts for `navigate` and `lookup_record`.
  B. `KnowledgeHub` toolset: metadata flags and parameter contracts for
     `list_files`.
  C. `Retrieval` toolset: metadata flags and parameter contracts for
     `search_internal_knowledge`.
  D. Scope-narrowing logic: `_get_scoping` never widens when the caller
     supplies a `connector_ids` subset.
  E. Dedup logic: `_dedupe_append_final_results` is idempotent.
"""
from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# A. KnowledgeGraph toolset
# ---------------------------------------------------------------------------


class TestKnowledgeGraphMetadata:
    def _meta(self):
        from app.agents.actions.knowledge_graph.knowledge_graph import KnowledgeGraph
        return KnowledgeGraph._toolset_metadata

    def test_is_essential(self):
        assert self._meta()["essential"] is True

    def test_is_internal(self):
        assert self._meta()["isInternal"] is True


class TestNavigateSchema:
    def _tool_meta(self):
        from app.agents.actions.knowledge_graph.knowledge_graph import KnowledgeGraph
        import inspect
        for name, method in inspect.getmembers(KnowledgeGraph, predicate=inspect.isfunction):
            if name == "navigate":
                return getattr(method, "_agent_tool_meta", None)
        return None

    def test_navigate_meta_exists(self):
        assert self._tool_meta() is not None

    def test_navigate_path(self):
        meta = self._tool_meta()
        assert meta.path == "/tools/knowledgegraph/navigate"

    def test_navigate_has_node_id_param(self):
        meta = self._tool_meta()
        param_names = {p.name for p in meta.parameters}
        assert "node_id" in param_names

    def test_navigate_has_page_param(self):
        meta = self._tool_meta()
        param_names = {p.name for p in meta.parameters}
        assert "page" in param_names

    def test_navigate_has_limit_param(self):
        meta = self._tool_meta()
        param_names = {p.name for p in meta.parameters}
        assert "limit" in param_names

    def test_navigate_node_id_not_required(self):
        meta = self._tool_meta()
        node_id_param = next(p for p in meta.parameters if p.name == "node_id")
        assert node_id_param.required is False


class TestLookupRecordSchema:
    def _tool_meta(self):
        from app.agents.actions.knowledge_graph.knowledge_graph import KnowledgeGraph
        import inspect
        for name, method in inspect.getmembers(KnowledgeGraph, predicate=inspect.isfunction):
            if name == "lookup_record":
                return getattr(method, "_agent_tool_meta", None)
        return None

    def test_lookup_record_meta_exists(self):
        assert self._tool_meta() is not None

    def test_lookup_record_path(self):
        meta = self._tool_meta()
        assert meta.path == "/tools/knowledgegraph/lookup_record"

    def test_identifiers_required(self):
        meta = self._tool_meta()
        identifiers = next(p for p in meta.parameters if p.name == "identifiers")
        assert identifiers.required is True

    def test_connector_name_optional(self):
        meta = self._tool_meta()
        param_names = {p.name for p in meta.parameters}
        assert "connector_name" in param_names
        cn = next(p for p in meta.parameters if p.name == "connector_name")
        assert cn.required is False


# ---------------------------------------------------------------------------
# B. KnowledgeHub toolset
# ---------------------------------------------------------------------------


class TestKnowledgeHubMetadata:
    def _meta(self):
        from app.agents.actions.knowledge_hub.knowledge_hub import KnowledgeHub
        return KnowledgeHub._toolset_metadata

    def test_is_essential(self):
        assert self._meta()["essential"] is True

    def test_is_internal(self):
        assert self._meta()["isInternal"] is True


class TestListFilesSchema:
    def _tool_meta(self):
        from app.agents.actions.knowledge_hub.knowledge_hub import KnowledgeHub
        import inspect
        for name, method in inspect.getmembers(KnowledgeHub, predicate=inspect.isfunction):
            if name == "list_files":
                return getattr(method, "_agent_tool_meta", None)
        return None

    def test_list_files_meta_exists(self):
        assert self._tool_meta() is not None

    def test_list_files_path(self):
        meta = self._tool_meta()
        assert meta.path == "/tools/knowledgehub/list_files"

    def test_query_in_advertised_params(self):
        meta = self._tool_meta()
        assert any(p.name == "query" for p in meta.parameters)

    def test_node_types_in_advertised_params(self):
        meta = self._tool_meta()
        assert any(p.name == "node_types" for p in meta.parameters)

    def test_record_types_in_advertised_params(self):
        meta = self._tool_meta()
        assert any(p.name == "record_types" for p in meta.parameters)

    def test_connector_ids_in_advertised_params(self):
        meta = self._tool_meta()
        assert any(p.name == "connector_ids" for p in meta.parameters)

    def test_record_group_ids_in_advertised_params(self):
        # After Phase 3, this parameter disappears from the LLM surface.
        # At Phase 1 it must be present.
        meta = self._tool_meta()
        assert any(p.name == "record_group_ids" for p in meta.parameters)


# ---------------------------------------------------------------------------
# C. Retrieval toolset
# ---------------------------------------------------------------------------


class TestRetrievalMetadata:
    def _meta(self):
        from app.agents.actions.retrieval.retrieval import Retrieval
        return Retrieval._toolset_metadata

    def test_is_essential(self):
        assert self._meta()["essential"] is True

    def test_is_internal(self):
        assert self._meta()["isInternal"] is True


class TestSearchInternalKnowledgeSchema:
    def _tool_meta(self):
        from app.agents.actions.retrieval.retrieval import Retrieval
        import inspect
        for name, method in inspect.getmembers(Retrieval, predicate=inspect.isfunction):
            if name == "search_internal_knowledge":
                return getattr(method, "_agent_tool_meta", None)
        return None

    def test_search_meta_exists(self):
        assert self._tool_meta() is not None

    def test_search_path(self):
        meta = self._tool_meta()
        assert meta.path == "/tools/retrieval/search_internal_knowledge"

    def test_query_required(self):
        meta = self._tool_meta()
        q = next(p for p in meta.parameters if p.name == "query")
        assert q.required is True

    def test_connector_ids_optional(self):
        meta = self._tool_meta()
        assert any(p.name == "connector_ids" for p in meta.parameters)
        cid = next(p for p in meta.parameters if p.name == "connector_ids")
        assert cid.required is False


# ---------------------------------------------------------------------------
# D. Scope-narrowing contract
# ---------------------------------------------------------------------------


class TestGetScopingNarrowing:
    """_get_scoping never widens when agent state already has sources."""

    def _scoping(self, state):
        from app.agents.actions.knowledge_graph.knowledge_graph import _get_scoping
        return _get_scoping(state)

    def test_returns_state_apps_when_present(self):
        state = {"apps": ["app-1", "app-2"], "kb": ["kb-1"]}
        connector_ids, kb_ids = self._scoping(state)
        assert "app-1" in connector_ids
        assert "app-2" in connector_ids

    def test_returns_state_kb_when_present(self):
        state = {"apps": [], "kb": ["kb-1", "kb-2"]}
        connector_ids, kb_ids = self._scoping(state)
        assert "kb-1" in kb_ids
        assert "kb-2" in kb_ids

    def test_filters_fallback_when_apps_empty(self):
        state = {
            "apps": [],
            "kb": [],
            "filters": {"apps": ["filter-app-1"], "kb": []},
        }
        connector_ids, kb_ids = self._scoping(state)
        assert "filter-app-1" in connector_ids

    def test_no_kb_selected_sentinel_is_excluded(self):
        state = {"apps": [], "kb": ["NO_KB_SELECTED", "kb-real"]}
        connector_ids, kb_ids = self._scoping(state)
        assert "NO_KB_SELECTED" not in kb_ids
        assert "kb-real" in kb_ids

    def test_empty_state_returns_empty_lists(self):
        state = {}
        connector_ids, kb_ids = self._scoping(state)
        assert connector_ids == []
        assert kb_ids == []


# ---------------------------------------------------------------------------
# E. Dedup / idempotency contract
# ---------------------------------------------------------------------------


class TestDedupeAppendFinalResults:
    def _dedupe(self, existing, new):
        from app.agents.actions.retrieval.retrieval import _dedupe_append_final_results
        return _dedupe_append_final_results(existing, new)

    def _block(self, vrid, idx):
        return {"virtual_record_id": vrid, "block_index": idx, "content": "x"}

    def test_new_blocks_appended(self):
        existing = [self._block("vr-1", 0)]
        new = [self._block("vr-1", 1), self._block("vr-2", 0)]
        result = self._dedupe(existing, new)
        assert len(result) == 3

    def test_duplicate_block_not_appended(self):
        existing = [self._block("vr-1", 0)]
        new = [self._block("vr-1", 0)]
        result = self._dedupe(existing, new)
        assert len(result) == 1

    def test_idempotent_second_call(self):
        existing = [self._block("vr-1", 0), self._block("vr-2", 0)]
        new = [self._block("vr-1", 0)]
        result1 = self._dedupe(existing, new)
        result2 = self._dedupe(result1, new)
        assert len(result1) == len(result2)

    def test_none_key_blocks_pass_through(self):
        """Blocks missing virtual_record_id or block_index always append."""
        existing = [{"content": "x"}]
        new = [{"content": "y"}]
        result = self._dedupe(existing, new)
        assert len(result) == 2

    def test_non_list_existing_treated_as_empty(self):
        result = self._dedupe(None, [self._block("vr-1", 0)])  # type: ignore[arg-type]
        assert len(result) == 1

    def test_empty_new_blocks_no_change(self):
        existing = [self._block("vr-1", 0)]
        result = self._dedupe(existing, [])
        assert result == existing
