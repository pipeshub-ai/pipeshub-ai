"""`log_ask_user_question_quality` / `ask_user_question_quality` /
`ask_outcome_tracking` (`app/agents/agent_loop/hooks/ask_user_quality.py`)
— Ask User Tool Improvement Plan, Phase 5.

Deliberately separate from `test_ask_user_question.py` (`ask_user_question_
sse`, the SSE-delivery hook): that hook gates on `has_ui_client`/
`event_sink`; this one must NOT — see this module's own docstring on why
quality logging is a distinct concern.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.agent_loop_lib.tools.base import ToolOutput
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.hooks.ask_user_quality import (
    ask_outcome_tracking,
    ask_user_question_quality,
    log_ask_user_question_quality,
)
from tests.unit.agents.adapter.support.hook_helpers import run_post_tool, run_pre_turn

_TOOL_PATH = "/internal/internaltools/ask_user_question"


def _make_context(**overrides) -> AgentContext:
    context = AgentContext(
        org_id="org-1", user_id="user-1", user_email="u@example.com", logger=MagicMock(),
    )
    for key, value in overrides.items():
        setattr(context, key, value)
    return context


class TestLogAskUserQuestionQuality:
    def test_logs_structured_fields(self, caplog) -> None:
        context = _make_context()
        with caplog.at_level(logging.INFO, logger="app.agents.agent_loop.hooks.ask_user_quality"):
            log_ask_user_question_quality(
                context,
                ambiguity_type="pre_run_moderate",
                user_intent="Understood: X. Ambiguous: Y.",
                question_count=2,
                reasoning="the target is ambiguous",
            )

        assert len(caplog.records) == 1
        message = caplog.records[0].message
        assert "ambiguity_type=pre_run_moderate" in message
        assert "question_count=2" in message
        assert "org_id=org-1" in message

    def test_options_grounded_true_when_a_tool_already_ran_this_turn(self, caplog) -> None:
        context = _make_context()
        context.tool_state["all_tool_results"] = [{"tool": "search", "result": "..."}]
        with caplog.at_level(logging.INFO, logger="app.agents.agent_loop.hooks.ask_user_quality"):
            log_ask_user_question_quality(
                context, ambiguity_type="mid_run", user_intent="x", question_count=1,
            )
        assert "options_grounded=True" in caplog.records[0].message

    def test_options_grounded_false_for_pre_run_with_no_prior_tool_calls(self, caplog) -> None:
        context = _make_context()
        with caplog.at_level(logging.INFO, logger="app.agents.agent_loop.hooks.ask_user_quality"):
            log_ask_user_question_quality(
                context, ambiguity_type="pre_run_fatal", user_intent="x", question_count=1,
            )
        assert "options_grounded=False" in caplog.records[0].message

    def test_reasoning_defaults_to_empty_length(self, caplog) -> None:
        context = _make_context()
        with caplog.at_level(logging.INFO, logger="app.agents.agent_loop.hooks.ask_user_quality"):
            log_ask_user_question_quality(
                context, ambiguity_type="mid_run", user_intent="x", question_count=1,
            )
        assert "reasoning_len=0" in caplog.records[0].message


class TestAskUserQuestionQualityHook:
    """POST_TOOL_USE hook — the mid-run call site."""

    async def test_logs_when_ask_user_question_tool_fires(self, caplog) -> None:
        context = _make_context()
        middleware = ask_user_question_quality(context)

        with caplog.at_level(logging.INFO, logger="app.agents.agent_loop.hooks.ask_user_quality"):
            await run_post_tool(
                middleware,
                ToolOutput(success=True, data="{}"),
                tool_path=_TOOL_PATH,
                metadata={"_result_accum_args": {
                    "user_intent": "Understood: X. Ambiguous: Y.",
                    "questions": [{"question": "Which?"}],
                    "reasoning": "two live tickets in this thread",
                }},
            )

        assert len(caplog.records) == 1
        message = caplog.records[0].message
        assert "ambiguity_type=mid_run" in message
        assert "question_count=1" in message
        assert "reasoning_len=" in message

    async def test_no_op_for_unrelated_tool(self, caplog) -> None:
        context = _make_context()
        middleware = ask_user_question_quality(context)

        with caplog.at_level(logging.INFO, logger="app.agents.agent_loop.hooks.ask_user_quality"):
            await run_post_tool(
                middleware, ToolOutput(success=True, data="ok"), tool_path="/connectors/jira/search",
            )

        assert caplog.records == []

    async def test_logs_even_when_has_ui_client_is_false(self, caplog) -> None:
        """The behavior that distinguishes this hook from `ask_user_question_
        sse`: a headless/API caller's bad question is exactly as measurable
        as one with a UI attached."""
        context = _make_context(has_ui_client=False, event_sink=None)
        middleware = ask_user_question_quality(context)

        with caplog.at_level(logging.INFO, logger="app.agents.agent_loop.hooks.ask_user_quality"):
            await run_post_tool(
                middleware,
                ToolOutput(success=True, data="{}"),
                tool_path=_TOOL_PATH,
                metadata={"_result_accum_args": {"user_intent": "x", "questions": [{"question": "q"}]}},
            )

        assert len(caplog.records) == 1

    async def test_logs_even_when_tool_call_failed(self, caplog) -> None:
        """A failed ask_user_question call is itself a signal worth
        counting, not something to suppress."""
        context = _make_context()
        middleware = ask_user_question_quality(context)

        with caplog.at_level(logging.INFO, logger="app.agents.agent_loop.hooks.ask_user_quality"):
            await run_post_tool(
                middleware,
                ToolOutput(success=False, error="boom"),
                tool_path=_TOOL_PATH,
                metadata={"_result_accum_args": {"user_intent": "x", "questions": [{"question": "q"}]}},
            )

        assert len(caplog.records) == 1

    async def test_question_count_from_json_string_questions(self, caplog) -> None:
        """`call_args["questions"]` may be a JSON-string-encoded list —
        same coercion shape `AskUserQuestionInput` accepts (raw call args
        are pre-validation)."""
        import json

        context = _make_context()
        middleware = ask_user_question_quality(context)

        with caplog.at_level(logging.INFO, logger="app.agents.agent_loop.hooks.ask_user_quality"):
            await run_post_tool(
                middleware,
                ToolOutput(success=True, data="{}"),
                tool_path=_TOOL_PATH,
                metadata={"_result_accum_args": {
                    "user_intent": "x",
                    "questions": json.dumps([{"question": "a"}, {"question": "b"}]),
                }},
            )

        assert "question_count=2" in caplog.records[0].message

    async def test_missing_call_args_logs_zero_question_count(self, caplog) -> None:
        context = _make_context()
        middleware = ask_user_question_quality(context)

        with caplog.at_level(logging.INFO, logger="app.agents.agent_loop.hooks.ask_user_quality"):
            await run_post_tool(
                middleware, ToolOutput(success=True, data="{}"), tool_path=_TOOL_PATH,
            )

        assert "question_count=0" in caplog.records[0].message


class TestAskOutcomeTracking:
    """PRE_TURN hook — the cross-turn half of Phase 5 outcome measurement."""

    def _scope_with_query(self, query: str):
        return SimpleNamespace(run=SimpleNamespace(goal=SimpleNamespace(description=query)))

    def _prior_ask_turn(self, labels: list[str]) -> dict:
        import json

        return {
            "role": "bot_response",
            "tool_results": [
                {
                    "tool_name": "internaltools__ask_user_question",
                    "result": json.dumps({
                        "name": "ask_user_question",
                        "userIntent": "x",
                        "questions": [
                            {"question": "Which?", "options": [{"label": lbl} for lbl in labels]},
                        ],
                    }),
                },
            ],
        }

    async def test_logs_picked_known_option_true_when_message_matches_a_label(self, caplog) -> None:
        context = _make_context(previous_conversations=[self._prior_ask_turn(["Engineering", "Marketing"])])
        middleware = ask_outcome_tracking(context)

        with caplog.at_level(logging.INFO, logger="app.agents.agent_loop.hooks.ask_user_quality"):
            await run_pre_turn(middleware, turn_index=0, scope=self._scope_with_query("Engineering"))

        assert len(caplog.records) == 1
        assert "picked_known_option=True" in caplog.records[0].message
        assert "option_count=2" in caplog.records[0].message

    async def test_logs_picked_known_option_false_when_message_is_something_else(self, caplog) -> None:
        context = _make_context(previous_conversations=[self._prior_ask_turn(["Engineering", "Marketing"])])
        middleware = ask_outcome_tracking(context)

        with caplog.at_level(logging.INFO, logger="app.agents.agent_loop.hooks.ask_user_quality"):
            await run_pre_turn(middleware, turn_index=0, scope=self._scope_with_query("Actually, do something totally different"))

        assert len(caplog.records) == 1
        assert "picked_known_option=False" in caplog.records[0].message

    async def test_no_op_when_turn_index_is_not_zero(self, caplog) -> None:
        """Only the FIRST turn of a request can be a direct follow-up to
        the previous turn's ask — later turns are mid-run tool activity."""
        context = _make_context(previous_conversations=[self._prior_ask_turn(["A", "B"])])
        middleware = ask_outcome_tracking(context)

        with caplog.at_level(logging.INFO, logger="app.agents.agent_loop.hooks.ask_user_quality"):
            await run_pre_turn(middleware, turn_index=1, scope=self._scope_with_query("A"))

        assert caplog.records == []

    async def test_no_op_when_no_previous_conversations(self, caplog) -> None:
        context = _make_context(previous_conversations=[])
        middleware = ask_outcome_tracking(context)

        with caplog.at_level(logging.INFO, logger="app.agents.agent_loop.hooks.ask_user_quality"):
            await run_pre_turn(middleware, turn_index=0, scope=self._scope_with_query("A"))

        assert caplog.records == []

    async def test_no_op_when_scope_is_none(self, caplog) -> None:
        context = _make_context(previous_conversations=[self._prior_ask_turn(["A", "B"])])
        middleware = ask_outcome_tracking(context)

        with caplog.at_level(logging.INFO, logger="app.agents.agent_loop.hooks.ask_user_quality"):
            await run_pre_turn(middleware, turn_index=0, scope=None)

        assert caplog.records == []

    async def test_no_op_when_previous_turn_did_not_ask_a_question(self, caplog) -> None:
        context = _make_context(previous_conversations=[
            {"role": "bot_response", "tool_results": [{"tool_name": "jira__search_issues", "result": "..."}]},
        ])
        middleware = ask_outcome_tracking(context)

        with caplog.at_level(logging.INFO, logger="app.agents.agent_loop.hooks.ask_user_quality"):
            await run_pre_turn(middleware, turn_index=0, scope=self._scope_with_query("anything"))

        assert caplog.records == []

    async def test_looks_only_at_most_recent_bot_turn(self, caplog) -> None:
        """An OLDER ask_user_question several turns back must not be
        confused with the immediately preceding turn."""
        context = _make_context(previous_conversations=[
            self._prior_ask_turn(["Old option"]),
            {"role": "user_query", "content": "..."},
            {"role": "bot_response", "tool_results": []},
        ])
        middleware = ask_outcome_tracking(context)

        with caplog.at_level(logging.INFO, logger="app.agents.agent_loop.hooks.ask_user_quality"):
            await run_pre_turn(middleware, turn_index=0, scope=self._scope_with_query("Old option"))

        assert caplog.records == []

    async def test_unparseable_result_string_is_a_safe_noop(self, caplog) -> None:
        context = _make_context(previous_conversations=[
            {
                "role": "bot_response",
                "tool_results": [
                    {"tool_name": "internaltools__ask_user_question", "result": "not json"},
                ],
            },
        ])
        middleware = ask_outcome_tracking(context)

        with caplog.at_level(logging.INFO, logger="app.agents.agent_loop.hooks.ask_user_quality"):
            await run_pre_turn(middleware, turn_index=0, scope=self._scope_with_query("anything"))

        assert caplog.records == []
