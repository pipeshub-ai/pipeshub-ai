"""`parse_intent` (`app/agents/agent_loop/intent.py`) — the intent-
understanding agent run every chat mode goes through before the main
`Agent.run()` (see `router.py::select_loop_and_goal`).

Route classification (quick/react/deep) is handled separately by
`classify_route()` in `app.modules.agents.qna.router`, NOT by this module.
The model's full output becomes `rewritten_query` without structured
parsing, and `route` is always `None`.
"""

from __future__ import annotations

import logging
from typing import Any

from app.agent_loop_lib.core.messages import ToolCall
from app.agent_loop_lib.transport.registry import TransportRegistry
from app.agents.agent_loop.intent import IntentRouteDecision, parse_intent, parse_intent_and_route
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
        return await parse_intent(
            _query_info(query, **query_overrides),
            _LOGGER,
            _FakeLangChainLLM(),
        )
    finally:
        intent_module.TransportRegistry = original_registry


class TestParseIntent:
    async def test_empty_query_short_circuits_without_llm_call(self) -> None:
        transport = ScriptedTransport().add_error(RuntimeError("must not be called"))

        decision = await _run_with_transport(transport, query="")

        assert decision.rewritten_query == ""
        assert decision.route is None
        assert transport.remaining() == 1

    async def test_model_output_becomes_rewritten_query_verbatim(self) -> None:
        output = "The user wants a summary of this week's bug bash from internal knowledge sources."
        transport = _script_task_complete(output)

        decision = await _run_with_transport(transport, query="do x")

        assert decision.rewritten_query == output

    async def test_route_is_always_none(self) -> None:
        """Route classification is handled by classify_route(), not parse_intent()."""
        output = "Summarize this week's bug bash.\n\nquick"
        transport = _script_task_complete(output)

        decision = await _run_with_transport(transport, query="do x")

        assert decision.route is None

    async def test_tier_words_in_output_are_preserved(self) -> None:
        """Since parse_intent no longer does routing, tier words like
        'quick', 'react', 'deep' in the output are just regular text
        — they should NOT be stripped."""
        output = "The user wants a quick check of their react components for deep issues."
        transport = _script_task_complete(output)

        decision = await _run_with_transport(transport, query="do x")

        assert "quick" in decision.rewritten_query
        assert "react" in decision.rewritten_query
        assert "deep" in decision.rewritten_query

    async def test_llm_failure_falls_back_to_raw_query(self) -> None:
        transport = ScriptedTransport().add_error(RuntimeError("boom"))

        decision = await _run_with_transport(transport, query="raw text")

        assert decision.rewritten_query == "raw text"
        assert decision.route is None

    async def test_blank_output_falls_back_to_raw_query(self) -> None:
        transport = _script_task_complete("   ")

        decision = await _run_with_transport(transport, query="raw text")

        assert decision.rewritten_query == "raw text"

    async def test_json_output_from_model_still_used_as_rewritten_query(self) -> None:
        json_output = '{"reasoning":"user wants X","rewritten_query":"do X"}'
        transport = _script_task_complete(json_output)

        decision = await _run_with_transport(transport, query="do x")

        assert decision.rewritten_query == json_output

    async def test_clarifying_questions_empty_for_normal_briefing(self) -> None:
        transport = _script_task_complete("something")

        decision = await _run_with_transport(transport, query="do x")

        assert decision.clarifying_questions == []

    async def test_clarify_block_populates_clarifying_questions(self) -> None:
        clarify_output = (
            "```clarify\n"
            '{"user_intent": "unclear what they want", '
            '"questions": [{"question": "What would you like help with?", '
            '"multiSelect": false, '
            '"options": [{"label": "Search documents"}, '
            '{"label": "Create a ticket"}, {"label": "Send a message"}]}]}\n'
            "```"
        )
        transport = _script_task_complete(clarify_output)

        decision = await _run_with_transport(transport, query="???")

        assert len(decision.clarifying_questions) == 1
        assert decision.clarifying_questions[0].question == "What would you like help with?"
        assert decision.rewritten_query == "unclear what they want"
        assert decision.route is None

    async def test_malformed_clarify_block_falls_back_to_normal_text(self) -> None:
        transport = _script_task_complete("```clarify\nnot valid json\n```")

        decision = await _run_with_transport(transport, query="do x")

        assert decision.clarifying_questions == []
        assert "not valid json" in decision.rewritten_query

    async def test_clarify_block_without_questions_key_is_ignored(self) -> None:
        transport = _script_task_complete('```clarify\n{"user_intent": "x"}\n```')

        decision = await _run_with_transport(transport, query="do x")

        assert decision.clarifying_questions == []

    async def test_requirements_and_gaps_always_empty(self) -> None:
        transport = _script_task_complete("something")

        decision = await _run_with_transport(transport, query="do x")

        assert decision.requirements == []
        assert decision.success_criteria == []
        assert decision.gaps == []

    async def test_system_prompt_does_not_contain_tier_rubric(self) -> None:
        """parse_intent should NOT include the quick/react/deep rubric
        — routing is a separate concern."""
        transport = _script_task_complete("something")

        await _run_with_transport(transport, query="do x")

        system_prompt = transport.calls[0]["system"]
        assert "## quick" not in system_prompt
        assert "## react" not in system_prompt
        assert "## deep" not in system_prompt

    async def test_system_prompt_does_not_contain_open_questions(self) -> None:
        """Open Questions section was removed — it added noise."""
        transport = _script_task_complete("something")

        await _run_with_transport(transport, query="do x")

        system_prompt = transport.calls[0]["system"]
        assert "## Open Questions" not in system_prompt


class TestParseIntentAndRouteTransportRegistryReuse:
    """`select_loop_and_goal()`/`PipesHubAgentFactory.create()` already
    build and register "langchain" on their own `TransportRegistry` before
    the intent call — passing it through here must resolve that SAME
    `LangChainTransport` instance rather than constructing (and, with Opik
    on, re-wrapping) a second one for the identical `llm`."""

    async def test_reuses_caller_supplied_registry_without_rebuilding(self) -> None:
        transport = _script_task_complete("rewritten by caller's transport")
        registry = TransportRegistry()
        registry.register("langchain", lambda: transport)

        decision = await parse_intent_and_route(
            _query_info("do x"), _LOGGER, _FakeLangChainLLM(),
            include_routing=False, transport_registry=registry,
        )

        assert decision.rewritten_query == "rewritten by caller's transport"
        # Still resolves to the exact caller-registered instance — never
        # overwritten by a second `register("langchain", ...)` call.
        assert registry.resolve("langchain") is transport

    async def test_no_registry_given_falls_back_to_building_one(self) -> None:
        """Every existing direct caller (no `transport_registry` kwarg)
        keeps working unchanged — `parse_intent()`'s own tests cover this
        path too, this just pins it directly on `parse_intent_and_route`."""
        import app.agents.agent_loop.intent as intent_module

        transport = _script_task_complete("built its own transport")
        original_registry_cls = intent_module.TransportRegistry

        class _PatchedRegistry(TransportRegistry):
            def register(self, provider: str, factory: Any) -> None:  # noqa: ANN401
                if provider == "langchain":
                    super().register(provider, lambda: transport)
                else:
                    super().register(provider, factory)

        intent_module.TransportRegistry = _PatchedRegistry
        try:
            decision = await parse_intent_and_route(
                _query_info("do x"), _LOGGER, _FakeLangChainLLM(), include_routing=False,
            )
        finally:
            intent_module.TransportRegistry = original_registry_cls

        assert decision.rewritten_query == "built its own transport"
