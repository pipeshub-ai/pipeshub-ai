"""With record query on, ``find_records`` is the one listing tool the model is
offered: ``find_records_by_entity``, ``find_records_by_person`` and
``find_records_by_value`` are withdrawn everywhere, yet stay callable by
their old names. Pins the tool budget and the definition size."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.agent_loop_lib.core.tokens import count_schema_tokens
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.tools.toolset import ToolsetBuilder
from app.agents.actions.knowledge_graph.entity_search_surface import (
    MERGED_INTO_FIND_RECORDS,
)
from app.agents.actions.knowledge_graph.knowledge_graph import KnowledgeGraph
from app.agents.actions.knowledge_graph.ops.timeline import TIMELINE_TOOL_NAME
from app.agents.agent_loop.domain_agents import plan_domain_agents
from app.agents.agent_loop.knowledge_graph_policy import (
    KNOWLEDGE_GRAPH_POLICY_STATE_KEY,
    KnowledgeGraphMode,
    KnowledgeGraphPolicy,
)

FIND_RECORDS = "knowledgegraph__find_records"
BY_ENTITY = "knowledgegraph__find_records_by_entity"
BY_PERSON = "knowledgegraph__find_records_by_person"
BY_VALUE = "knowledgegraph__find_records_by_value"


def _build(*, record_query: bool, mode: KnowledgeGraphMode = KnowledgeGraphMode.FULL, named: bool = True,
           search_by_entities: bool = False) -> tuple[KnowledgeGraph, list[Any], list[Any]]:
    full = mode is KnowledgeGraphMode.FULL
    policy = KnowledgeGraphPolicy(mode=mode, named_entities=named and full,
                                  search_by_entities=search_by_entities and named and full, record_query=record_query)
    instance = KnowledgeGraph(state={KNOWLEDGE_GRAPH_POLICY_STATE_KEY: policy})
    built = ToolsetBuilder(instance, name="knowledgegraph", description="", path_prefix="/tools/knowledgegraph").tools
    declared = [t for t in built if t.name != TIMELINE_TOOL_NAME]
    return instance, declared, instance.tools_for_request(list(declared))


def _names(tools: list[Any]) -> set[str]:
    return {t.name for t in tools}


def test_the_knowledge_graph_tools_offered_drop_by_two() -> None:
    _, _, before = _build(record_query=False)
    _, _, after = _build(record_query=True)
    assert len(before) == 9 and {BY_ENTITY, BY_PERSON, BY_VALUE} <= _names(before) and FIND_RECORDS not in _names(before)
    assert len(after) == 7
    assert _names(after) == (_names(before) - {BY_ENTITY, BY_PERSON, BY_VALUE}) | {FIND_RECORDS}


def test_find_records_is_no_larger_than_the_three_tools_it_replaces() -> None:
    _, _, before = _build(record_query=False)
    _, _, after = _build(record_query=True)
    replaced = count_schema_tokens([t.to_schema() for t in before if t.name in MERGED_INTO_FIND_RECORDS])
    (merged,) = [t for t in after if t.name == FIND_RECORDS]
    assert count_schema_tokens([merged.to_schema()]) <= replaced
    # The whole knowledge-graph surface the model reads shrinks too.
    assert count_schema_tokens([t.to_schema() for t in after]) < count_schema_tokens([t.to_schema() for t in before])


@pytest.mark.parametrize("mode", [KnowledgeGraphMode.FULL, KnowledgeGraphMode.TAXONOMY])
@pytest.mark.parametrize("search_by_entities", [False, True])
def test_no_offered_tool_points_the_model_at_a_withdrawn_one(mode: KnowledgeGraphMode, search_by_entities: bool) -> None:
    _, _, served = _build(record_query=True, mode=mode, search_by_entities=search_by_entities)
    texts = [t.description for t in served] + [p.description for t in served for p in t.parameters]
    assert not [t for t in texts if "find_records_by_" in t]


def test_the_taxonomy_arm_with_record_query_takes_people_out_of_find_records() -> None:
    _, _, served = _build(record_query=True, mode=KnowledgeGraphMode.TAXONOMY)
    (find_records,) = [t for t in served if t.name == FIND_RECORDS]
    assert '"person"' not in find_records.description and "Roles:" not in find_records.description


def test_without_named_entities_find_records_offers_no_mentions() -> None:
    _, _, served = _build(record_query=True, named=False)
    (find_records,) = [t for t in served if t.name == FIND_RECORDS]
    assert "mentions" not in find_records.description


def test_with_record_query_off_the_merged_names_are_offered_as_declared_and_none_is_retired() -> None:
    instance, declared, served = _build(record_query=False)
    by_name = {t.name: t for t in declared}
    for name in (BY_ENTITY, BY_PERSON, BY_VALUE):
        (tool,) = [t for t in served if t.name == name]
        assert tool is by_name[name]
    assert instance.retired_tools_for_request(declared) == []


async def test_a_merged_name_is_callable_but_never_listed() -> None:
    instance, declared, served = _build(record_query=True)
    registry = ToolRegistry()
    for tool in served:
        registry.register_tool(tool)
    retired = instance.retired_tools_for_request(declared)
    assert sorted(t.name for t, _ in retired) == sorted(MERGED_INTO_FIND_RECORDS)
    assert {successor for _, successor in retired} == {"find_records"}
    for tool, _ in retired:
        registry.register_hidden_tool(tool)
    assert not MERGED_INTO_FIND_RECORDS & {s.name for s in registry.schemas()}
    assert not MERGED_INTO_FIND_RECORDS & set(registry.names())
    assert not any(registry.has(name) for name in MERGED_INTO_FIND_RECORDS)
    route = AsyncMock(return_value=(True, "routed"))
    with patch("app.agents.actions.knowledge_graph.ops.merged_listings.find_records_for_person", route):
        output = await registry.resolve_by_name(BY_PERSON).execute(person="me")
    assert output.success and route.await_args.kwargs == {"person": "me"}
    assert registry.path_for_name(BY_PERSON) == "/tools/knowledgegraph/find_records_by_person"


def test_a_tool_only_retired_where_it_would_have_been_offered() -> None:
    instance, declared, _ = _build(record_query=True, mode=KnowledgeGraphMode.TAXONOMY)
    assert sorted(t.name for t, _ in instance.retired_tools_for_request(declared)) == [BY_ENTITY]


def test_the_exploration_child_gets_find_records_in_place_of_find_records_by_person() -> None:
    for record_query, listing, gone in ((False, BY_PERSON, FIND_RECORDS), (True, FIND_RECORDS, BY_PERSON)):
        _, _, served = _build(record_query=record_query)
        registry = ToolRegistry()
        for tool in served:
            registry.register_tool(tool)
        claims = plan_domain_agents(registry).claims["internal_exploration_agent"]
        assert listing in claims and gone not in claims
