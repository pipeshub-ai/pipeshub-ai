"""Why `search_tools` asks for capability keywords: it ranks by the share of
query words found in a tool's name and short description, so a query that
wraps the user's question buries the tool that would answer it. Checked
against the real knowledge graph, Jira and Slack tool metadata."""

from __future__ import annotations

import inspect

import pytest

from app.agent_loop_lib.tools.decorators import TOOL_META_ATTR, BoundMethodTool
from app.agent_loop_lib.tools.index import KeywordToolIndex
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agents.actions.jira.jira import Jira
from app.agents.actions.knowledge_graph.knowledge_graph import KnowledgeGraph
from app.agents.actions.slack.slack import Slack


@pytest.fixture(scope="module")
def registry() -> ToolRegistry:
    registry = ToolRegistry()
    for toolset in (KnowledgeGraph, Jira, Slack):
        for _, fn in inspect.getmembers(toolset, predicate=inspect.isfunction):
            meta = getattr(fn, TOOL_META_ATTR, None)
            if meta is not None:
                registry.register_tool(BoundMethodTool(fn, meta))
    return registry


async def _ranked(registry: ToolRegistry, query: str) -> list[tuple[str, float]]:
    matches = await KeywordToolIndex().search(registry, query, limit=len(registry.names()))
    return [(m.summary.name, m.relevance) for m in matches]


def _place(ranked: list[tuple[str, float]], name: str) -> int:
    names = [n for n, _ in ranked]
    return names.index(name) if name in names else len(names)


async def test_capability_keywords_put_the_knowledge_search_tool_first(registry: ToolRegistry) -> None:
    ranked = await _ranked(registry, "search indexed knowledge")

    assert ranked[0][0] == "knowledgegraph__search"


async def test_the_users_question_as_the_query_buries_the_knowledge_search_tool(registry: ToolRegistry) -> None:
    capability = await _ranked(registry, "search indexed knowledge")
    question = await _ranked(
        registry,
        "Use the knowledge search tool to find the parental leave policy for employees in Germany in 2024",
    )

    assert _place(question, "knowledgegraph__search") > _place(capability, "knowledgegraph__search")
    assert dict(question).get("knowledgegraph__search", 0.0) < dict(capability)["knowledgegraph__search"]


async def test_capability_keywords_rank_a_connector_write_tool_higher(registry: ToolRegistry) -> None:
    capability = dict(await _ranked(registry, "create jira issue"))
    question = dict(await _ranked(
        registry, "Use the jira tool to create an issue for the login bug Alice reported yesterday",
    ))

    assert capability["jira__create_issue"] > question.get("jira__create_issue", 0.0)
