"""Ask User Tool Improvement Plan, Phase 7 (Testing Plan item 5) — pins the
fact that pre-run clarification is REACHABLE for `chatMode=internal_search`
and skipped ONLY for `chatMode=quick`.

`internal_search` deliberately is not, and must never become, an entry in
`modes.MODE_CATALOG`: `resolve_mode("internal_search")` returning `None` is
exactly what makes `select_loop_and_goal()` treat it like `auto` for intent
purposes (`include_routing=True`, full `parse_intent_and_route()` call, so
its ```clarify escape hatch is live). A well-intentioned "fix" that adds
`internal_search` to the catalog with `skip_intent=True` would silently
regress weakness 4b from the improvement plan for this mode specifically —
this file exists so that change fails a test instead of shipping quietly.

Complements (does not duplicate) `test_router.py`'s broader `TestSelectLoop
AndGoal` suite, which already covers every CATALOG mode's routing/skip
behavior — this file is scoped to the one mode that is NOT in the catalog.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agent_loop_lib.agent.loops import ReActLoop
from app.agents.agent_loop.intent import IntentRouteDecision
from app.agents.agent_loop.modes import resolve_mode
from app.agents.agent_loop.router import select_loop_and_goal
from tests.unit.agents.adapter.conftest import FakeChatModel, make_context


class TestInternalSearchModeReachesIntentParsing:
    def test_internal_search_is_not_in_the_mode_catalog(self) -> None:
        assert resolve_mode("internal_search") is None

    async def test_internal_search_requests_routing_classification(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`mode is None` -> `include_routing=True` -> the intent call's
        ```clarify escape hatch is live for this mode, exactly like `auto`."""
        captured: dict[str, Any] = {}

        async def _fake_parse_intent(*_args: Any, **kwargs: Any) -> IntentRouteDecision:
            captured.update(kwargs)
            return IntentRouteDecision(reasoning="r", rewritten_query="hello", route="react")

        monkeypatch.setattr("app.agents.agent_loop.router.parse_intent_and_route", _fake_parse_intent)
        context = make_context(llm=FakeChatModel())

        loop, goal, _clarifying, mode = await select_loop_and_goal(
            chat_mode="internal_search", query="hello", llm=context.llm, context=context,
        )

        assert captured["include_routing"] is True
        assert isinstance(loop, ReActLoop)
        assert mode.name == "react"
        assert goal.description == "hello"

    async def test_internal_search_clarify_block_propagates_severity(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The whole point of the fix: a clarify-block decision for this
        mode must reach `context.tool_state["clarification_severity"]`
        unchanged, exactly as it would for `auto`/any unrecognized mode."""
        from app.agents.actions.internal_tools.intrim_tools import AskUserQuestionItemInput

        question = AskUserQuestionItemInput(
            question="Which project?",
            options=[{"label": "Project A"}, {"label": "Project B"}, {"label": "Project C"}],
            multiSelect=False,
        )

        async def _fake_parse_intent(*_args: Any, **_kwargs: Any) -> IntentRouteDecision:
            return IntentRouteDecision(
                reasoning="ambiguous",
                rewritten_query="unclear",
                clarifying_questions=[question],
                clarification_severity="moderate",
            )

        monkeypatch.setattr("app.agents.agent_loop.router.parse_intent_and_route", _fake_parse_intent)
        context = make_context(llm=FakeChatModel())

        _loop, _goal, clarifying, _mode = await select_loop_and_goal(
            chat_mode="internal_search", query="do the thing", llm=context.llm, context=context,
        )

        assert clarifying == [question]
        assert context.tool_state["clarification_severity"] == "moderate"

    async def test_unrecognized_mode_string_behaves_identically_to_internal_search(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Any OTHER unrecognized `chatMode` value gets the same treatment
        — `internal_search` is not special-cased anywhere, it simply
        happens to be the unrecognized value production code sends."""
        captured_modes: list[str] = []

        async def _fake_parse_intent(*_args: Any, **kwargs: Any) -> IntentRouteDecision:
            captured_modes.append("called")
            return IntentRouteDecision(reasoning="r", rewritten_query="q", route="react")

        monkeypatch.setattr("app.agents.agent_loop.router.parse_intent_and_route", _fake_parse_intent)
        context = make_context(llm=FakeChatModel())

        for chat_mode in ("internal_search", "some-other-unrecognized-value"):
            await select_loop_and_goal(
                chat_mode=chat_mode, query="q", llm=context.llm, context=context,
            )

        assert len(captured_modes) == 2


class TestQuickModeSkipsIntentEntirely:
    """The one mode that DOES skip the intent call — see `modes.py`'s
    `skip_intent` docstring for the accepted trade-off this implies for
    pre-run clarification specifically."""

    async def test_quick_mode_never_calls_parse_intent_and_route(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        called = False

        async def _fake_parse_intent(*_args: Any, **_kwargs: Any) -> IntentRouteDecision:
            nonlocal called
            called = True
            return IntentRouteDecision(reasoning="r", rewritten_query="unused")

        monkeypatch.setattr("app.agents.agent_loop.router.parse_intent_and_route", _fake_parse_intent)
        context = make_context(llm=FakeChatModel())

        _loop, _goal, clarifying, mode = await select_loop_and_goal(
            chat_mode="quick", query="do it", llm=context.llm, context=context,
        )

        assert called is False
        assert mode.skip_intent is True
        assert clarifying == []

    async def test_quick_mode_sets_clarification_severity_to_none(self) -> None:
        """No intent call means no clarify-block severity to propagate —
        `router.py` must still leave `tool_state` in a consistent state
        (explicit `None`, not a missing key) so downstream readers
        (`stream_bridge.py`) never need a `.get(..., default)` guess."""
        context = make_context(llm=FakeChatModel())

        await select_loop_and_goal(
            chat_mode="quick", query="do it", llm=context.llm, context=context,
        )

        assert "clarification_severity" in context.tool_state
        assert context.tool_state["clarification_severity"] is None
