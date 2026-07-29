"""Ask User Tool Improvement Plan, Phase 1 — the two-tier
`clarification_severity` ("moderate" / "fatal") that `_parse_clarify_block`
extracts from the model's ```clarify escape-hatch block (`intent.py`).

Complements `test_intent.py`'s existing (severity-agnostic) `TestParseIntent`
clarify-block coverage: this file is scoped to the SEVERITY field itself —
which value each tier produces, the "fatal" default for older/malformed
blocks, and that `select_loop_and_goal()`/`router.py` thread the field
through to `context.tool_state["clarification_severity"]` unchanged (see
`test_router.py`/`test_router_intent.py` for the router-level half of that
contract).
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agent_loop_lib.core.messages import ToolCall
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport
from tests.unit.agents.adapter.test_intent import _run_with_transport


def _clarify_output(*, severity: str | None, question_count: int = 1) -> str:
    """Builds a ```clarify block with `severity` omitted entirely when
    `None` (the "older prompt behavior" `_parse_clarify_block`'s docstring
    calls out), so tests can pin the missing-field default independent of
    an explicitly-wrong value."""
    import json

    questions = [
        {
            "question": f"Which one, take {i}?",
            "multiSelect": False,
            "options": [
                {"label": "Option A"}, {"label": "Option B"}, {"label": "Option C"},
            ],
        }
        for i in range(question_count)
    ]
    payload: dict[str, Any] = {"user_intent": "unclear what they want", "questions": questions}
    if severity is not None:
        payload["severity"] = severity
    return f"```clarify\n{json.dumps(payload)}\n```"


class TestModerateSeverity:
    """A clear topic + action that maps to 2+ incompatible targets (e.g.
    "update the ticket" with several live in conversation) — one focused
    question, `clarification_severity="moderate"`."""

    async def test_moderate_severity_produces_one_question(self) -> None:
        transport = ScriptedTransport().add_tool_call(
            ToolCall(id="tc1", name="task_complete", arguments={
                "output": _clarify_output(severity="moderate", question_count=1),
            }),
        )

        decision = await _run_with_transport(transport, query="update the ticket")

        assert decision.clarification_severity == "moderate"
        assert len(decision.clarifying_questions) == 1

    async def test_moderate_severity_sets_rewritten_query_to_user_intent(self) -> None:
        transport = ScriptedTransport().add_tool_call(
            ToolCall(id="tc1", name="task_complete", arguments={
                "output": _clarify_output(severity="moderate"),
            }),
        )

        decision = await _run_with_transport(transport, query="update the ticket")

        assert decision.rewritten_query == "unclear what they want"
        assert decision.route is None


class TestFatalSeverity:
    """A bare fragment with no antecedent at all — 1-3 questions,
    `clarification_severity="fatal"`."""

    async def test_explicit_fatal_severity(self) -> None:
        transport = ScriptedTransport().add_tool_call(
            ToolCall(id="tc1", name="task_complete", arguments={
                "output": _clarify_output(severity="fatal", question_count=2),
            }),
        )

        decision = await _run_with_transport(transport, query="do it")

        assert decision.clarification_severity == "fatal"
        assert len(decision.clarifying_questions) == 2

    async def test_missing_severity_field_defaults_to_fatal(self) -> None:
        """`_parse_clarify_block`'s documented default: an older/simpler
        model response that omits `severity` entirely must resolve to the
        strict, narrow tier — never the wider `moderate` one."""
        transport = ScriptedTransport().add_tool_call(
            ToolCall(id="tc1", name="task_complete", arguments={
                "output": _clarify_output(severity=None),
            }),
        )

        decision = await _run_with_transport(transport, query="???")

        assert decision.clarification_severity == "fatal"

    async def test_unrecognized_severity_value_defaults_to_fatal(self) -> None:
        transport = ScriptedTransport().add_tool_call(
            ToolCall(id="tc1", name="task_complete", arguments={
                "output": _clarify_output(severity="urgent"),
            }),
        )

        decision = await _run_with_transport(transport, query="???")

        assert decision.clarification_severity == "fatal"


class TestSeverityAbsentOutsideClarifyPath:
    """`clarification_severity` must stay `None` whenever the model does
    NOT take the clarify escape hatch — a normal briefing or a route-only
    response never has a severity to report."""

    async def test_normal_briefing_has_no_severity(self) -> None:
        transport = ScriptedTransport().add_tool_call(
            ToolCall(id="tc1", name="task_complete", arguments={
                "output": "## Request\nFind Q3 revenue report.",
            }),
        )

        decision = await _run_with_transport(transport, query="find Q3 revenue report")

        assert decision.clarification_severity is None
        assert decision.clarifying_questions == []

    async def test_vague_but_searchable_query_has_no_severity(self) -> None:
        """A broad-but-answerable topic must go to normal retrieval, not
        either clarify tier — `_CLARIFY_INSTRUCTIONS` explicitly excludes
        this case from both `moderate` and `fatal`."""
        transport = ScriptedTransport().add_tool_call(
            ToolCall(id="tc1", name="task_complete", arguments={
                "output": "## Request\nSummarize the new pricing.",
            }),
        )

        decision = await _run_with_transport(transport, query="tell me about pricing")

        assert decision.clarification_severity is None

    async def test_malformed_clarify_block_leaves_severity_none(self) -> None:
        transport = ScriptedTransport().add_tool_call(
            ToolCall(id="tc1", name="task_complete", arguments={
                "output": "```clarify\nnot valid json\n```",
            }),
        )

        decision = await _run_with_transport(transport, query="do x")

        assert decision.clarification_severity is None
        assert decision.clarifying_questions == []

    async def test_llm_failure_fallback_has_no_severity(self) -> None:
        transport = ScriptedTransport().add_error(RuntimeError("boom"))

        decision = await _run_with_transport(transport, query="raw text")

        assert decision.clarification_severity is None


class TestClarifyInstructionsContent:
    """The system prompt fed to the model must actually describe both
    tiers — a regression here means the model has no way to know the
    `moderate` tier exists at all."""

    async def test_system_prompt_mentions_both_tiers(self) -> None:
        transport = ScriptedTransport().add_tool_call(
            ToolCall(id="tc1", name="task_complete", arguments={"output": "something"}),
        )

        await _run_with_transport(transport, query="do x")

        system_prompt = transport.calls[0]["system"]
        assert "moderate" in system_prompt
        assert "fatal" in system_prompt

    async def test_system_prompt_requires_at_least_three_options(self) -> None:
        """Pins the Phase 1 fix for the `options` min_length=3 conflict:
        the instructions must not ask for fewer options than
        `AskUserQuestionItemInput.options` will actually accept."""
        transport = ScriptedTransport().add_tool_call(
            ToolCall(id="tc1", name="task_complete", arguments={"output": "something"}),
        )

        await _run_with_transport(transport, query="do x")

        system_prompt = transport.calls[0]["system"]
        assert "3-7 concrete tappable options" in system_prompt


class TestClarifyingQuestionsValidateAgainstToolSchema:
    """A `moderate`-tier clarify block with the documented minimum (3
    options) must round-trip through `AskUserQuestionItemInput` without
    a validation error — this is the actual regression Phase 1 fixes:
    the plan's original 2-option example would have failed
    `options.min_length=3` silently at parse time."""

    async def test_three_option_moderate_question_parses_successfully(self) -> None:
        transport = ScriptedTransport().add_tool_call(
            ToolCall(id="tc1", name="task_complete", arguments={
                "output": _clarify_output(severity="moderate"),
            }),
        )

        decision = await _run_with_transport(transport, query="send it to the team")

        assert len(decision.clarifying_questions) == 1
        assert len(decision.clarifying_questions[0].options) == 3


@pytest.mark.parametrize("query", ["find Q3 revenue report", "list open PRs assigned to me"])
async def test_clear_queries_never_populate_clarifying_questions(query: str) -> None:
    transport = ScriptedTransport().add_tool_call(
        ToolCall(id="tc1", name="task_complete", arguments={
            "output": f"## Request\n{query}",
        }),
    )

    decision = await _run_with_transport(transport, query=query)

    assert decision.clarifying_questions == []
    assert decision.clarification_severity is None
