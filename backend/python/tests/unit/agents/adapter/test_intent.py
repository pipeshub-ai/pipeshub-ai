"""`parse_intent_and_route` (`app/agents/agent_loop/intent.py`) — the intent-
understanding + tier-routing agent run every chat mode goes through before
the main `Agent.run()` (see `router.py::select_loop_and_goal`).

The model's full output is used as `rewritten_query` without structured
parsing. Only `route` is extracted (last word matching quick/react/deep).
"""

from __future__ import annotations

import logging
from typing import Any

from app.agent_loop_lib.core.messages import ToolCall
from app.agent_loop_lib.transport.registry import TransportRegistry
from app.agents.agent_loop.intent import IntentRouteDecision, parse_intent_and_route
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport

_LOGGER = logging.getLogger("test.agents.agent_loop.intent")


class _FakeLangChainLLM:
    pass


def _script_task_complete(output: str) -> ScriptedTransport:
    return ScriptedTransport().add_tool_call(
        ToolCall(id="tc1", name="task_complete", arguments={"output": output}),
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

    async def test_model_output_becomes_rewritten_query_verbatim(self) -> None:
        output = "The user wants a summary of this week's bug bash from internal knowledge sources."
        transport = _script_task_complete(output)

        decision = await _run_with_transport(transport, query="do x", include_routing=False)

        assert decision.rewritten_query == output

    async def test_route_extracted_from_end_of_output(self) -> None:
        output = "Summarize this week's bug bash.\n\nquick"
        transport = _script_task_complete(output)

        decision = await _run_with_transport(transport, query="do x", include_routing=True)

        assert decision.route == "quick"
        assert "bug bash" in decision.rewritten_query

    async def test_include_routing_false_leaves_route_none_even_if_present(self) -> None:
        output = "Something something\n\nreact"
        transport = _script_task_complete(output)

        decision = await _run_with_transport(transport, include_routing=False)

        assert decision.route is None

    async def test_include_routing_true_embeds_tier_rubric_in_system_prompt(self) -> None:
        transport = _script_task_complete("something\n\nreact")

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

    async def test_blank_output_falls_back_to_raw_query(self) -> None:
        transport = _script_task_complete("   ")

        decision = await _run_with_transport(transport, query="raw text", include_routing=True)

        assert decision.rewritten_query == "raw text"

    async def test_route_case_insensitive(self) -> None:
        transport = _script_task_complete("summary of things\n\nDEEP")

        decision = await _run_with_transport(transport, include_routing=True)

        assert decision.route == "deep"

    async def test_no_route_word_in_output_yields_none(self) -> None:
        transport = _script_task_complete("just a restatement with no tier word")

        decision = await _run_with_transport(transport, include_routing=True)

        assert decision.route is None

    async def test_last_route_word_wins_over_earlier_occurrence(self) -> None:
        """The prompt asks for the tier word at the END — if 'quick' appears
        in the restatement body and 'react' at the end, 'react' wins."""
        output = "The user wants a quick summary of their data.\n\nreact"
        transport = _script_task_complete(output)

        decision = await _run_with_transport(transport, include_routing=True)

        assert decision.route == "react"

    async def test_json_output_from_model_still_used_as_rewritten_query(self) -> None:
        """If the model ignores the 'not JSON' instruction and produces
        JSON anyway, the whole JSON string becomes rewritten_query — the
        downstream Goal.description still carries the model's analysis,
        just in JSON form instead of prose."""
        json_output = '{"reasoning":"user wants X","rewritten_query":"do X","route":"react"}'
        transport = _script_task_complete(json_output)

        decision = await _run_with_transport(transport, include_routing=True)

        assert decision.rewritten_query == json_output
        assert decision.route == "react"

    async def test_clarifying_questions_always_empty(self) -> None:
        transport = _script_task_complete("something\n\nreact")

        decision = await _run_with_transport(transport, include_routing=True)

        assert decision.clarifying_questions == []

    async def test_requirements_and_gaps_always_empty(self) -> None:
        transport = _script_task_complete("something\n\nreact")

        decision = await _run_with_transport(transport, include_routing=True)

        assert decision.requirements == []
        assert decision.success_criteria == []
        assert decision.gaps == []
