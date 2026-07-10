"""`parse_intent_and_route` (`app/agents/agent_loop/intent.py`) — the merged
intent-understanding + tier-routing agent run every chat mode goes through
before the main `Agent.run()` (see `router.py::select_loop_and_goal`).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.agent_loop_lib.core.messages import ToolCall
from app.agent_loop_lib.transport.registry import TransportRegistry
from app.agents.actions.internal_tools.intrim_tools import AskUserQuestionItemInput
from app.agents.agent_loop.intent import IntentRouteDecision, parse_intent_and_route
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport

_LOGGER = logging.getLogger("test.agents.agent_loop.intent")


class _FakeLangChainLLM:
    """Placeholder `BaseChatModel` — the patched transport registry supplies
    the actual `ScriptedTransport` responses."""

    pass


def _script_task_complete(payload: dict[str, Any]) -> ScriptedTransport:
    return ScriptedTransport().add_tool_call(
        ToolCall(id="tc1", name="task_complete", arguments={"output": json.dumps(payload)}),
    )


def _query_info(query: str = "hello", **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "query": query,
        "knowledge": [],
        "connector_configs": {},
        "filters": {},
        "toolsets": [],
        "previous_conversations": [],
        "attachments": [],
    }
    base.update(overrides)
    return base


async def _run_with_transport(
    transport: ScriptedTransport,
    *,
    query: str = "hello",
    include_routing: bool = True,
    **query_overrides: Any,
) -> IntentRouteDecision:
    import app.agents.agent_loop.intent as intent_module

    original_registry = intent_module.TransportRegistry

    class _PatchedRegistry(TransportRegistry):
        def register(self, provider: str, factory: Any) -> None:  # noqa: ANN401
            if provider == "langchain":
                super().register(provider, lambda: transport)
            else:
                super().register(provider, factory)

    intent_module.TransportRegistry = _PatchedRegistry
    try:
        return await parse_intent_and_route(
            _query_info(query, **query_overrides),
            _LOGGER,
            _FakeLangChainLLM(),
            include_routing=include_routing,
        )
    finally:
        intent_module.TransportRegistry = original_registry


class TestParseIntentAndRoute:
    async def test_empty_query_short_circuits_without_llm_call(self) -> None:
        transport = ScriptedTransport().add_error(RuntimeError("must not be called"))

        decision = await _run_with_transport(transport, query="", include_routing=True)

        assert decision.rewritten_query == ""
        assert decision.route == "react"
        assert transport.remaining() == 1

    async def test_empty_query_without_routing_leaves_route_none(self) -> None:
        transport = ScriptedTransport().add_error(RuntimeError("must not be called"))

        decision = await _run_with_transport(transport, query="", include_routing=False)

        assert decision.route is None

    async def test_happy_path_returns_rewritten_query_and_route(self) -> None:
        transport = _script_task_complete({
            "reasoning": "user wants X",
            "rewritten_query": "Clear self-contained restatement of X",
            "requirements": ["req1"],
            "success_criteria": ["done when x happens"],
            "route": "quick",
        })

        decision = await _run_with_transport(transport, query="do x", include_routing=True)

        assert decision.rewritten_query == "Clear self-contained restatement of X"
        assert decision.route == "quick"
        assert decision.requirements == ["req1"]
        assert decision.success_criteria == ["done when x happens"]

    async def test_include_routing_false_leaves_llm_returned_route_as_is(self) -> None:
        transport = _script_task_complete({"reasoning": "r", "rewritten_query": "q", "route": "quick"})

        decision = await _run_with_transport(transport, include_routing=False)

        assert decision.route is None
        system_prompt = transport.calls[0]["system"]
        assert "Leave `route` null" in system_prompt

    async def test_include_routing_true_embeds_tier_rubric_in_system_prompt(self) -> None:
        transport = _script_task_complete({"reasoning": "r", "rewritten_query": "q", "route": "react"})

        await _run_with_transport(transport, include_routing=True)

        system_prompt = transport.calls[0]["system"]
        assert "## quick" in system_prompt
        assert "## react" in system_prompt
        assert "## deep" in system_prompt

    async def test_llm_failure_falls_back_to_raw_query_and_react(self) -> None:
        transport = ScriptedTransport().add_error(RuntimeError("boom"))

        decision = await _run_with_transport(transport, query="raw text", include_routing=True)

        assert decision.rewritten_query == "raw text"
        assert decision.route == "react"

    async def test_llm_failure_without_routing_leaves_route_none(self) -> None:
        transport = ScriptedTransport().add_error(RuntimeError("boom"))

        decision = await _run_with_transport(transport, query="raw text", include_routing=False)

        assert decision.rewritten_query == "raw text"
        assert decision.route is None

    async def test_blank_rewritten_query_falls_back_to_raw_query(self) -> None:
        transport = _script_task_complete({"reasoning": "r", "rewritten_query": "   ", "route": "react"})

        decision = await _run_with_transport(transport, query="raw text", include_routing=True)

        assert decision.rewritten_query == "raw text"

    async def test_gaps_and_clarifying_questions_default_empty(self) -> None:
        transport = _script_task_complete({"reasoning": "r", "rewritten_query": "q", "route": "react"})

        decision = await _run_with_transport(transport, query="hello", include_routing=True)

        assert decision.gaps == []
        assert decision.clarifying_questions == []

    async def test_clarifying_questions_threaded_through_on_ambiguous_request(self) -> None:
        question = AskUserQuestionItemInput(
            question="Which channel should I post in?",
            options=[{"label": "general"}, {"label": "engineering"}, {"label": "random"}],
            multiSelect=False,
        )
        transport = _script_task_complete({
            "reasoning": "ambiguous — missing channel",
            "rewritten_query": "Post the update in a channel",
            "gaps": ["missing target channel"],
            "clarifying_questions": [question.model_dump(mode="json")],
            "route": "react",
        })

        decision = await _run_with_transport(transport, query="post the update", include_routing=True)

        assert decision.gaps == ["missing target channel"]
        assert decision.clarifying_questions == [question]

    async def test_empty_query_leaves_gaps_and_clarifying_questions_empty(self) -> None:
        transport = ScriptedTransport().add_error(RuntimeError("must not be called"))

        decision = await _run_with_transport(transport, query="", include_routing=True)

        assert decision.gaps == []
        assert decision.clarifying_questions == []

    async def test_llm_failure_leaves_gaps_and_clarifying_questions_empty(self) -> None:
        transport = ScriptedTransport().add_error(RuntimeError("boom"))

        decision = await _run_with_transport(transport, query="raw text", include_routing=True)

        assert decision.gaps == []
        assert decision.clarifying_questions == []
