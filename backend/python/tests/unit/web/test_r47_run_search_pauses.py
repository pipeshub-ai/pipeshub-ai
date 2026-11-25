"""R39RES-FU-3: a run's web search pauses after unproductive searches instead of turning off for the whole run.

``web_search`` stops after 4 searches in a row that found nothing or failed (main #4570). A chat answer builds the tool
per request, so that ends one answer's searching. An engine run builds it once for its whole job (``web_scope="run"``,
a 150-search allowance), so a provider's bad minute ended search for a 30-minute research job. Decided (ADR-RUN-06
Amendment R47-POLISH): a run-scoped tool pauses, tells the model for how long and to keep working, and then lets a
search out again; each pause after an unproductive probe is twice the last (capped), a productive search resets it.
A chat answer's give-up is unchanged.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from app.agent_loop_lib.core.types import ToolResult
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.tool_loader import _build_dynamic_tools
from app.agents.agent_loop.tool_summarizer import PipesHubToolSummarizer
from app.harness.engines.durable import present_for_engines
from app.web.chain import ChainFailed
from app.web.errors import Blocked
from app.web.models import Attempt, CallerContext, WebResult
from app.web.services import SearchOutcome
from app.web.tools.web_search import (
    FIRST_PAUSE_S,
    MAX_PAUSE_S,
    MAX_UNPRODUCTIVE_SEARCHES,
    create_web_search_tool,
)

CALLER = CallerContext(org_id="org-1", user_id="user-1")
FAILED = ChainFailed([Blocked("bot check")], (Attempt("ddgs", "blocked", 0.5),))
HIT = SearchOutcome("q", (WebResult("https://example.com/a", "T", "S", "ddgs", None),), "ddgs", ())


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _service(*answers: object) -> MagicMock:
    fake = MagicMock()
    fake.search = AsyncMock(side_effect=list(answers))
    return fake


async def _ask(tool: Any, n: int = 0) -> dict[str, Any]:  # noqa: ANN401
    return json.loads(await tool.ainvoke({"query": f"q{n}"}))


async def test_res3_1_a_run_pauses_and_searches_again_after_the_pause() -> None:
    clock = Clock()
    fake = _service(*[FAILED] * MAX_UNPRODUCTIVE_SEARCHES, HIT)
    tool = create_web_search_tool(fake, CALLER, scope="run", clock=clock)
    for n in range(MAX_UNPRODUCTIVE_SEARCHES):
        await _ask(tool, n)
    paused = await _ask(tool)
    assert paused["ok"] is False and paused["error_type"] == "search_paused" and paused["retryable"] is True
    assert paused["retry_after_s"] == FIRST_PAUSE_S
    assert "Keep working" in paused["error"] and "turned off" not in paused["error"]
    assert fake.search.await_count == MAX_UNPRODUCTIVE_SEARCHES, "no provider call while paused"
    clock.now += FIRST_PAUSE_S
    assert (await _ask(tool))["ok"] is True
    assert fake.search.await_count == MAX_UNPRODUCTIVE_SEARCHES + 1


async def test_res3_2_each_unproductive_probe_doubles_the_pause_up_to_its_cap() -> None:
    clock = Clock()
    fake = _service(*[FAILED] * 20)
    tool = create_web_search_tool(fake, CALLER, scope="run", clock=clock)
    for n in range(MAX_UNPRODUCTIVE_SEARCHES):
        await _ask(tool, n)
    pauses = []
    for _ in range(6):
        pause = (await _ask(tool))["retry_after_s"]
        pauses.append(pause)
        clock.now += pause
        assert (await _ask(tool))["error_type"] == "blocked", "the probe after a pause goes out"
    assert pauses == [60, 120, 240, 480, MAX_PAUSE_S, MAX_PAUSE_S]


async def test_res3_3_a_productive_search_resets_the_count_and_the_pause() -> None:
    clock = Clock()
    answers = [*[FAILED] * MAX_UNPRODUCTIVE_SEARCHES, FAILED, HIT, *[FAILED] * MAX_UNPRODUCTIVE_SEARCHES]
    tool = create_web_search_tool(_service(*answers), CALLER, scope="run", clock=clock)
    for n in range(MAX_UNPRODUCTIVE_SEARCHES):
        await _ask(tool, n)
    clock.now += FIRST_PAUSE_S
    await _ask(tool)  # unproductive probe: the next pause doubles
    clock.now += 2 * FIRST_PAUSE_S
    assert (await _ask(tool))["ok"] is True
    for n in range(MAX_UNPRODUCTIVE_SEARCHES - 1):
        assert (await _ask(tool, n))["error_type"] == "blocked", "the count started again"
    await _ask(tool)
    assert (await _ask(tool))["retry_after_s"] == FIRST_PAUSE_S


async def test_res3_4_a_chat_answer_still_gives_up_for_the_rest_of_the_answer() -> None:
    clock = Clock()
    fake = _service(*[FAILED] * MAX_UNPRODUCTIVE_SEARCHES)
    tool = create_web_search_tool(fake, CALLER, clock=clock)
    for n in range(MAX_UNPRODUCTIVE_SEARCHES):
        await _ask(tool, n)
    clock.now += MAX_PAUSE_S * 10
    gave_up = await _ask(tool)
    assert gave_up["error_type"] == "budget_exhausted" and "turned off for the rest of this answer" in gave_up["error"]
    assert fake.search.await_count == MAX_UNPRODUCTIVE_SEARCHES


async def test_res3_5_the_engines_tool_set_carries_the_pausing_search() -> None:
    """The tool an engine run is given (``present_for_engines``) pauses; a chat answer's gives up."""

    async def outcomes(scope_engine: bool) -> list[str]:
        context = AgentContext(
            org_id="org-1", user_id="user-1", user_email="u@example.com",
            user_info={"userId": "user-1", "orgId": "org-1"}, org_info={"name": "TestOrg"},
            logger=MagicMock(), llm=MagicMock(), retrieval_service=MagicMock(), graph_provider=MagicMock(),
            config_service=MagicMock(), blob_store=MagicMock(),
        )
        if scope_engine:
            present_for_engines(context)
        context.tool_state["web_search_config"] = {"provider": "duckduckgo"}
        services = MagicMock()
        services.search.search = AsyncMock(side_effect=FAILED)
        with patch("app.web.container.build_web_services", return_value=services), \
                patch("app.edition_services.get_web_access_gate", return_value=MagicMock()):
            tools = _build_dynamic_tools(context)
        (search,) = [t for t in tools if t._structured_tool.name == "web_search"]
        return [
            json.loads(await search._structured_tool.ainvoke({"query": f"q{n}"}))["error_type"]
            for n in range(MAX_UNPRODUCTIVE_SEARCHES + 1)
        ]

    assert (await outcomes(scope_engine=True))[-1] == "search_paused"
    assert (await outcomes(scope_engine=False))[-1] == "budget_exhausted"


def test_res3_6_the_step_card_says_search_is_paused_in_plain_words() -> None:
    content = json.dumps({"ok": False, "error_type": "search_paused", "error": "raw text"})
    result = ToolResult(tool_call_id="1", name="dynamic__web_search", content=content)
    summary = PipesHubToolSummarizer().summarize_result("dynamic__web_search", {}, result).result_summary
    assert summary == "Web search failed: Paused for a while after several searches found nothing"
