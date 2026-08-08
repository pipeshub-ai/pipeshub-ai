"""Registration + boundedness tests for the KnowledgeGraph toolset (KG Clean
Rebuild plan, Phase 8 — complements the per-op unit tests in test_search.py,
test_catalog.py, test_listing.py, test_entity_filters.py, etc.).

Two invariants the plan calls out explicitly (Part B "Non-negotiables" #2,
Part I "Success criteria"):

1. Every ``@tool``-decorated method on `KnowledgeGraph` is discoverable via
   its live decorator metadata (``TOOL_META_ATTR``) with the expected path —
   i.e. nothing on this class silently uses the dead pre-rebuild ``@tool``
   kwargs (``app_name``, ``when_to_use``, ``ToolIntent``, ...).
2. No tool method can return an unbounded result set: agents never get a
   bulk entity/record dump, only paginated/top-k-capped progressive
   disclosure (`navigate`/`list_files` via ``page``+``limit``,
   `resolve_entity_filters` via ``top_k``).
"""
from __future__ import annotations

import dataclasses
import inspect

from app.agent_loop_lib.tools.decorators import TOOL_META_ATTR
from app.agents.actions.knowledge_graph.knowledge_graph import KnowledgeGraph

_EXPECTED_TOOL_PATHS = {
    "/tools/knowledgegraph/navigate",
    "/tools/knowledgegraph/lookup_record",
    "/tools/knowledgegraph/search",
    "/tools/knowledgegraph/resolve_entity_filters",
    "/tools/knowledgegraph/list_files",
    "/tools/knowledgegraph/search_entities",
    "/tools/knowledgegraph/find_records_by_entity",
    "/tools/knowledgegraph/expand_neighbors",
    "/tools/knowledgegraph/get_relationships",
}

# Parameters whose default is the tool's page/result-size cap.
_BOUND_PARAM_DEFAULTS = {
    "navigate": {"limit": 50},
    "resolve_entity_filters": {"top_k": 10},
    "list_files": {"limit": 20},
    "search_entities": {"top_k": 10},
    "find_records_by_entity": {"limit": 20},
}


def _decorated_tool_methods() -> dict[str, object]:
    """Every method on ``KnowledgeGraph`` carrying live ``@tool`` metadata,
    keyed by method name.
    """
    return {
        name: meta
        for name, func in inspect.getmembers(KnowledgeGraph, predicate=inspect.isfunction)
        if (meta := getattr(func, TOOL_META_ATTR, None)) is not None
    }


class TestDecoratorPathsRegister:
    def test_all_expected_paths_are_registered(self) -> None:
        registered_paths = {meta.path for meta in _decorated_tool_methods().values()}

        assert _EXPECTED_TOOL_PATHS.issubset(registered_paths)

    def test_every_tool_has_non_empty_descriptions(self) -> None:
        for name, meta in _decorated_tool_methods().items():
            assert meta.short_description.strip(), f"{name} missing short_description"
            assert meta.description.strip(), f"{name} missing description"

    def test_no_dead_pre_rebuild_kwargs_leak_onto_meta(self) -> None:
        """The old cherry-pick's ``@tool`` used ``app_name``/``when_to_use``/
        ``ToolIntent`` kwargs that don't exist on the live ``ToolMeta`` at
        all — this just pins that ``ToolMeta`` only has the live decorator's
        fields, so a future regression can't silently reintroduce them.
        """
        meta_fields = {f.name for f in dataclasses.fields(next(iter(_decorated_tool_methods().values())))}

        assert "app_name" not in meta_fields
        assert "when_to_use" not in meta_fields
        assert "ToolIntent" not in meta_fields


class TestNoUnboundedListing:
    def test_bounded_tools_declare_a_small_positive_default_cap(self) -> None:
        for method_name, expected_defaults in _BOUND_PARAM_DEFAULTS.items():
            sig = inspect.signature(getattr(KnowledgeGraph, method_name))
            for param_name, expected_default in expected_defaults.items():
                actual_default = sig.parameters[param_name].default
                assert actual_default == expected_default, (
                    f"{method_name}.{param_name} default changed to {actual_default!r} — "
                    "if this is meant to allow unbounded results, update this test deliberately."
                )

    def test_no_tool_method_accepts_a_limit_of_none_as_unbounded(self) -> None:
        """None isn't a valid sentinel for "no limit" on any bounded param;
        catches a regression where a caller could pass ``limit=None``/
        ``top_k=None`` to request everything.
        """
        for method_name, expected_defaults in _BOUND_PARAM_DEFAULTS.items():
            sig = inspect.signature(getattr(KnowledgeGraph, method_name))
            for param_name in expected_defaults:
                param = sig.parameters[param_name]
                assert param.default is not None
                assert isinstance(param.default, int)
                assert param.default > 0
