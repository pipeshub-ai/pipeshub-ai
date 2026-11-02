"""Which questions get the turn's pointer to the timeline tool, and when."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.agents.actions.knowledge_graph.ops.timeline import TIMELINE_TOOL_NAME
from app.agents.agent_loop.period_summary import (
    PERIOD_SUMMARY_HINT,
    PERIOD_SUMMARY_SECTION,
    asks_for_period_summary,
    period_summary_hint,
)

ABOUT_A_SPAN = [
    "Between March and May 2026, what problems came up with the Slack bot, and what was decided about each?",
    "What happened in #eng-infra from Q1 2026 to Q2 2026?",
    "What process rules did leadership lay down for the engineering team over 2026?",
    "Late in September the team ran a couple of rounds of verification. What problems or gaps came up?",
    "Summarise the pricing discussion over the past few months",
    "How did the connector rewrite evolve?",
    "What has been going on with the billing migration?",
    "Give me a timeline of the outage",
    "Across our recent trials, why did teams not move to production?",
    "Which accounts went quiet between July and mid-September?",
    "What did we commit to after the meetings in late July?",
    "Since January, what was decided about the on-call rotation?",
    "What problems came up with the export in May 2026?",
    "What was decided about pricing since then?",
    "What problems came up last March?",
    "What was decided this March?",
    "What happened over March?",
    "What happened throughout March?",
    "Between last March and May, what happened?",
    "What happened between 2026-03-01 and 2026-05-31?",
    "What happened between March 1 and May 31?",
    "List the changes between last March 1 and May 31",
    "List the changes from last March 2026 to May",
    "List the changes between this March 15 and June 2",
    "Since March, what was decided?",
    "Since May 31, what was decided?",
    "Since May 31st, what was decided?",
    "Since 2026-03-01, what was decided?",
    "What was decided over the summer?",
]

NOT_ABOUT_A_SPAN = [
    "What is the redirect URI for the Dropbox team app?",
    "Who owns the code graph work?",
    "Has that changed since the last release?",
    "What timeline did we give the partner for shared chats?",
    "Summarize this document",
    "What problems does the S3 connector have with keys containing %25?",
    "When did the role dropdown fix merge?",
    "What problems came up so I may share it with the team?",
    "What was decided since I missed the call?",
    "Where are we with the fall migration?",
    "What happened after the fall of the old auth service?",
    "Catch me up before the spring launch",
    "What problems came up so we may 1) fix auth",
    "How did we go from June to market?",
    "Notes from June 3 to market",
    "What changed between March 1 and maybe the release?",
    "Who moved from March 1 to Jane?",
    "What changed between March and the fallback?",
    "What problems came up with the export, and what was decided?",
    "",
    "   ",
]


@pytest.mark.parametrize("query", ABOUT_A_SPAN)
def test_a_question_about_a_span_is_recognised(query: str) -> None:
    assert asks_for_period_summary(query)


@pytest.mark.parametrize("query", NOT_ABOUT_A_SPAN)
def test_a_point_question_is_left_alone(query: str) -> None:
    assert not asks_for_period_summary(query)


def test_none_is_left_alone() -> None:
    assert not asks_for_period_summary(None)


def test_the_hint_names_the_tool() -> None:
    assert f"`{TIMELINE_TOOL_NAME}`" in PERIOD_SUMMARY_HINT


def _scope(*, granted: bool = True, registered: bool = True, tool_names: list[str] | None = None,
           parent_run_id: str | None = None) -> SimpleNamespace:
    registry = SimpleNamespace(has=lambda name: registered and name == TIMELINE_TOOL_NAME)
    names = tool_names if tool_names is not None else ([TIMELINE_TOOL_NAME] if granted else ["knowledgegraph__search"])
    return SimpleNamespace(
        identity=SimpleNamespace(parent_run_id=parent_run_id),
        runtime=SimpleNamespace(tool_registry=registry),
        spec=SimpleNamespace(tool_names=names),
        extra_prompt_sections={},
    )


async def _run(query: str, scope: Any) -> dict[str, str]:  # noqa: ANN401
    middleware = period_summary_hint(SimpleNamespace(query=query))
    next_fn = AsyncMock()
    await middleware(SimpleNamespace(scope=scope), next_fn)
    next_fn.assert_awaited_once()
    return scope.extra_prompt_sections


@pytest.mark.asyncio
async def test_a_span_question_with_the_tool_granted_gets_the_section() -> None:
    sections = await _run(ABOUT_A_SPAN[0], _scope())
    assert sections == {PERIOD_SUMMARY_SECTION: PERIOD_SUMMARY_HINT}


@pytest.mark.asyncio
async def test_an_empty_grant_means_every_registered_tool() -> None:
    sections = await _run(ABOUT_A_SPAN[0], _scope(tool_names=[]))
    assert PERIOD_SUMMARY_SECTION in sections


@pytest.mark.asyncio
@pytest.mark.parametrize("scope_kwargs", [
    {"registered": False},
    {"granted": False},
    {"parent_run_id": "run-1"},
])
async def test_no_section_where_the_tool_cannot_be_called_or_for_a_sub_agent(scope_kwargs: dict[str, Any]) -> None:
    sections = await _run(ABOUT_A_SPAN[0], _scope(**scope_kwargs))
    assert sections == {}


@pytest.mark.asyncio
async def test_no_section_for_a_point_question() -> None:
    assert await _run(NOT_ABOUT_A_SPAN[0], _scope()) == {}


@pytest.mark.asyncio
async def test_no_scope_still_runs_the_rest_of_the_chain() -> None:
    middleware = period_summary_hint(SimpleNamespace(query=ABOUT_A_SPAN[0]))
    next_fn = AsyncMock()
    await middleware(SimpleNamespace(scope=None), next_fn)
    next_fn.assert_awaited_once()
