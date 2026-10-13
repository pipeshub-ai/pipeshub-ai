"""The knowledge-graph tools with and without ``search_by_entities`` offered
(``entity_search_surface``)."""

from __future__ import annotations

from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.agent_loop_lib.tools.decorators import TOOL_META_ATTR
from app.agent_loop_lib.tools.errors import ToolValidationError
from app.agent_loop_lib.tools.toolset import ToolsetBuilder
from app.agents.actions.knowledge_graph import entity_search_surface as surface
from app.agents.actions.knowledge_graph.knowledge_graph import KnowledgeGraph
from app.agents.agent_loop.entity_search_tool import (
    ENTITY_SEARCH_OFFERED_STATE_KEY,
    SEARCH_BY_ENTITIES_TOOL_NAME,
)

_VARIED = {
    "knowledgegraph__search", "knowledgegraph__search_entities",
    "knowledgegraph__find_records_by_entity", "knowledgegraph__find_records_by_value",
}


def _tools(offered: bool | None) -> tuple[list[Any], list[Any]]:
    state: dict[str, Any] = {} if offered is None else {ENTITY_SEARCH_OFFERED_STATE_KEY: offered}
    instance = KnowledgeGraph(state=state)
    declared = ToolsetBuilder(instance, name="knowledgegraph", description="", path_prefix="/tools/knowledgegraph").tools
    return declared, instance.tools_for_request(list(declared))


def _schema(tool: Any) -> tuple[str, str, list[Any]]:  # noqa: ANN401
    return tool.name, tool.description, tool.parameters


@pytest.mark.parametrize("offered", [None, False])
def test_not_offered_is_the_declared_toolset_without_the_tool(offered: bool | None) -> None:
    declared, served = _tools(offered)
    assert [t.name for t in served] == [t.name for t in declared if t.name != SEARCH_BY_ENTITIES_TOOL_NAME]
    by_name = {t.name: t for t in declared}
    assert all(t is by_name[t.name] for t in served), "the declared tools themselves, unchanged"
    assert not any("search_by_entities" in t.description or "named_entity'" in t.description for t in served)
    assert not any(
        "search_by_entities" in p.description for t in served for p in t.parameters
    )


def test_offered_adds_the_tool_and_the_named_entity_descriptions() -> None:
    declared, served = _tools(True)
    assert [t.name for t in served] == [t.name for t in declared]
    by_name = {t.name: t for t in served}
    declared_by_name = {t.name: t for t in declared}
    for name, tool in by_name.items():
        if name in _VARIED:
            assert _schema(tool) != _schema(declared_by_name[name]), name
        else:
            assert tool is declared_by_name[name], name

    search_entities = by_name["knowledgegraph__search_entities"]
    params = {p.name: p for p in search_entities.parameters}
    assert "queries" in params and not params["query"].required
    assert "'named_entity'" in params["entity_types"].description
    assert "knowledgegraph__search_by_entities" in params["named_entity_kinds"].description
    assert "entityType 'named_entity'" in search_entities.description
    assert "named entities" in {p.name: p for p in by_name["knowledgegraph__search"].parameters}["entity_ids"].description
    assert "mentions a named entity" in by_name["knowledgegraph__find_records_by_entity"].description
    assert "rather than words" in by_name["knowledgegraph__find_records_by_value"].description


def test_the_named_kinds_listed_are_the_kinds_the_search_can_filter_on() -> None:
    from app.modules.named_entities.domain.kinds import VECTOR_KINDS

    _, served = _tools(True)
    (tool,) = [t for t in served if t.name == "knowledgegraph__search_entities"]
    text = {p.name: p for p in tool.parameters}["named_entity_kinds"].description
    listed = text.split(":", 1)[1].split(".", 1)[0]
    assert {kind.strip() for kind in listed.split(",")} == {kind.value for kind in VECTOR_KINDS}


@pytest.mark.asyncio
async def test_the_offered_search_entities_takes_queries_and_says_so_to_the_op() -> None:
    _, served = _tools(True)
    (tool,) = [t for t in served if t.name == "knowledgegraph__search_entities"]
    tool.validate({"queries": ["Contoso"]})
    with patch(
        "app.agents.actions.knowledge_graph.ops.entity_discovery.execute_search_entities",
        AsyncMock(return_value=(True, "{}")),
    ) as op:
        await tool.execute(queries=["Contoso"])
    assert op.await_args.kwargs["queries"] == ["Contoso"]
    assert op.await_args.kwargs["entity_search_offered"] is True


def test_the_declared_search_entities_still_refuses_queries() -> None:
    _, served = _tools(False)
    (tool,) = [t for t in served if t.name == "knowledgegraph__search_entities"]
    with pytest.raises(ToolValidationError):
        tool.validate({"queries": ["Contoso"]})


def test_an_edit_whose_anchor_is_gone_fails_loudly() -> None:
    declared = getattr(KnowledgeGraph.find_records_by_value, TOOL_META_ATTR)
    reworded = replace(declared, description="Find records by a typed value.")
    with pytest.raises(ValueError, match="no longer reads"):
        surface.variant_meta("knowledgegraph__find_records_by_value", reworded)


def test_the_offered_search_entities_names_every_query_in_its_activity_line() -> None:
    _, served = _tools(True)
    (tool,) = [t for t in served if t.name == "knowledgegraph__search_entities"]
    assert tool.summarize_args({"queries": ["Contoso", "Initech"]}) == 'Searched entities for "Contoso", "Initech"'
    assert tool.summarize_args({"query": "Acme"}) == 'Searched entities for "Acme"'
    assert tool.summarize_args({}) is None


@pytest.mark.parametrize(("payload", "expected"), [
    (
        {"status": "success", "queries": [
            {"query": "Contoso", "results": [{"name": "Contoso"}, {"name": "Contoso Ltd"}]},
            {"query": "Nobody", "message": "No accessible entities matched", "results": []},
            {"query": "Down", "error": "Entity search failed"},
        ]},
        "Found 2 entities across 3 queries",
    ),
    ({"status": "success", "queries": [{"query": "A", "results": [{"name": "A"}]}]}, "Found 1 entity across 1 query"),
])
def test_a_grouped_result_is_summarized_across_its_queries(payload: dict[str, Any], expected: str) -> None:
    import json
    from types import SimpleNamespace

    from app.agents.actions.knowledge_graph.knowledge_graph import (
        _search_entities_result_summary,
    )

    result = SimpleNamespace(content=json.dumps(payload), is_error=False)
    assert _search_entities_result_summary({}, result) == expected
