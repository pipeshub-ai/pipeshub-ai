"""`TranscriptCollector` ("Parts-Based Agent Message Transcript" plan) —
the `EventEmitter` sibling of `AGUIEventEmitter` (see `test_agui_emitter.py`)
that assembles the ordered `MessagePart[]` transcript from the SAME
`AgentEvent` stream, including nested sub-agent activity via
`run_context.parent_run_id`.
"""

from __future__ import annotations

import json
from typing import Any

from app.agent_loop_lib.events.base import AgentEvent, EventType, RunContext, ToolCallStatus
from app.agents.agent_loop.protocol.transcript_collector import TranscriptCollector

_ROOT_CTX = RunContext(role_name="pipeshub-agent", model="gpt-4")
_CHILD_CTX = _ROOT_CTX.child(role_name="internal_exploration_agent", model="gpt-4")


def _event(event_type: EventType, payload: dict[str, Any], run_context: RunContext = _ROOT_CTX) -> AgentEvent:
    return AgentEvent(event_type=event_type, run_context=run_context, payload=payload)


class TestTextAndReasoningParts:
    async def test_text_message_lifecycle_produces_one_text_part(self) -> None:
        collector = TranscriptCollector()

        await collector.emit(_event(EventType.RUN_STARTED, {}))
        await collector.emit(_event(EventType.TEXT_MESSAGE_START, {}))
        await collector.emit(_event(EventType.TEXT_MESSAGE_CONTENT, {"delta": "Hel"}))
        await collector.emit(_event(EventType.TEXT_MESSAGE_CONTENT, {"delta": "lo"}))
        await collector.emit(_event(EventType.TEXT_MESSAGE_END, {}))

        assert collector.parts == [{"type": "text", "content": "Hello", "runId": _ROOT_CTX.run_id}]

    async def test_reasoning_lifecycle_produces_one_reasoning_part(self) -> None:
        collector = TranscriptCollector()

        await collector.emit(_event(EventType.RUN_STARTED, {}))
        await collector.emit(_event(EventType.REASONING_MESSAGE_START, {}))
        await collector.emit(_event(EventType.REASONING_MESSAGE_CONTENT, {"delta": "thinking..."}))
        await collector.emit(_event(EventType.REASONING_MESSAGE_END, {}))

        assert collector.parts == [{"type": "reasoning", "content": "thinking...", "runId": _ROOT_CTX.run_id}]

    async def test_multiple_turns_produce_separate_ordered_parts(self) -> None:
        collector = TranscriptCollector()

        await collector.emit(_event(EventType.RUN_STARTED, {}))
        await collector.emit(_event(EventType.REASONING_MESSAGE_START, {}))
        await collector.emit(_event(EventType.REASONING_MESSAGE_CONTENT, {"delta": "plan"}))
        await collector.emit(_event(EventType.REASONING_MESSAGE_END, {}))
        await collector.emit(_event(EventType.TEXT_MESSAGE_START, {}))
        await collector.emit(_event(EventType.TEXT_MESSAGE_CONTENT, {"delta": "answer"}))
        await collector.emit(_event(EventType.TEXT_MESSAGE_END, {}))

        assert [p["type"] for p in collector.parts] == ["reasoning", "text"]

    async def test_content_before_start_is_dropped(self) -> None:
        collector = TranscriptCollector()

        await collector.emit(_event(EventType.TEXT_MESSAGE_CONTENT, {"delta": "orphaned"}))

        assert collector.parts == []


class TestToolCallParts:
    async def test_tool_call_lifecycle_produces_completed_part(self) -> None:
        collector = TranscriptCollector()

        await collector.emit(_event(EventType.RUN_STARTED, {}))
        await collector.emit(
            _event(EventType.TOOL_CALL_START, {"tool": "jira_search", "args": {"q": "bug"}, "tool_call_id": "call-1"})
        )
        await collector.emit(
            _event(EventType.TOOL_CALL_END, {"tool": "jira_search", "content": "3 issues", "is_error": False, "tool_call_id": "call-1"})
        )

        assert len(collector.parts) == 1
        part = collector.parts[0]
        assert part["type"] == "tool_call"
        assert part["toolCallId"] == "call-1"
        assert part["toolName"] == "jira_search"
        assert json.loads(part["args"]) == {"q": "bug"}
        assert part["status"] == "completed"
        assert part["resultPreview"] == "3 issues"

    async def test_tool_call_failure_sets_failed_status(self) -> None:
        collector = TranscriptCollector()

        await collector.emit(_event(EventType.RUN_STARTED, {}))
        await collector.emit(_event(EventType.TOOL_CALL_START, {"tool": "jira_search", "args": {}, "tool_call_id": "call-2"}))
        await collector.emit(
            _event(EventType.TOOL_CALL_END, {"tool": "jira_search", "content": "boom", "is_error": True, "tool_call_id": "call-2"})
        )

        assert collector.parts[0]["status"] == "failed"

    async def test_tool_blocked_alias_sets_blocked_status_and_reason_preview(self) -> None:
        collector = TranscriptCollector()

        await collector.emit(_event(EventType.RUN_STARTED, {}))
        await collector.emit(_event(EventType.TOOL_CALL_START, {"tool": "jira_search", "args": {}, "tool_call_id": "call-3"}))
        await collector.emit(
            _event(EventType.TOOL_CALL_END, {
                "tool": "jira_search", "reason": "blocked after 3 failures", "tool_call_id": "call-3",
                "status": ToolCallStatus.BLOCKED,
            })
        )

        part = collector.parts[0]
        assert part["status"] == "blocked"
        assert part["resultPreview"] == "blocked after 3 failures"

    async def test_result_preview_is_truncated(self) -> None:
        collector = TranscriptCollector()

        await collector.emit(_event(EventType.RUN_STARTED, {}))
        await collector.emit(_event(EventType.TOOL_CALL_START, {"tool": "jira_search", "args": {}, "tool_call_id": "call-4"}))
        await collector.emit(
            _event(EventType.TOOL_CALL_END, {"tool": "jira_search", "content": "x" * 10_000, "is_error": False, "tool_call_id": "call-4"})
        )

        assert len(collector.parts[0]["resultPreview"]) == 500

    async def test_end_without_matching_start_is_dropped(self) -> None:
        collector = TranscriptCollector()

        await collector.emit(_event(EventType.RUN_STARTED, {}))
        await collector.emit(
            _event(EventType.TOOL_CALL_END, {"tool": "jira_search", "content": "orphaned", "tool_call_id": "no-such-call"})
        )

        assert collector.parts == []

    async def test_tool_call_lifecycle_stores_args_summary(self) -> None:
        collector = TranscriptCollector()

        await collector.emit(_event(EventType.RUN_STARTED, {}))
        await collector.emit(
            _event(EventType.TOOL_CALL_START, {
                "tool": "jira_search", "args": {"q": "bug"}, "tool_call_id": "call-6",
                "args_summary": "Searched for 'bug'",
            })
        )

        assert collector.parts[0]["argsSummary"] == "Searched for 'bug'"

    async def test_tool_call_lifecycle_stores_result_summary(self) -> None:
        collector = TranscriptCollector()

        await collector.emit(_event(EventType.RUN_STARTED, {}))
        await collector.emit(_event(EventType.TOOL_CALL_START, {"tool": "jira_search", "args": {}, "tool_call_id": "call-7"}))
        await collector.emit(
            _event(EventType.TOOL_CALL_END, {
                "tool": "jira_search", "content": "3 issues", "is_error": False, "tool_call_id": "call-7",
                "result_summary": "Found 3 issues",
            })
        )

        assert collector.parts[0]["resultSummary"] == "Found 3 issues"

    async def test_tool_call_without_summaries_still_works(self) -> None:
        collector = TranscriptCollector()

        await collector.emit(_event(EventType.RUN_STARTED, {}))
        await collector.emit(_event(EventType.TOOL_CALL_START, {"tool": "jira_search", "args": {}, "tool_call_id": "call-8"}))
        await collector.emit(
            _event(EventType.TOOL_CALL_END, {"tool": "jira_search", "content": "3 issues", "is_error": False, "tool_call_id": "call-8"})
        )

        part = collector.parts[0]
        assert "argsSummary" not in part
        assert "resultSummary" not in part


class TestSubAgentNesting:
    async def test_child_activity_nests_under_sub_agent_part(self) -> None:
        collector = TranscriptCollector()

        await collector.emit(_event(EventType.RUN_STARTED, {}))
        await collector.emit(_event(EventType.RUN_STARTED, {}, run_context=_CHILD_CTX))
        await collector.emit(_event(EventType.TEXT_MESSAGE_START, {}, run_context=_CHILD_CTX))
        await collector.emit(_event(EventType.TEXT_MESSAGE_CONTENT, {"delta": "delegate answer"}, run_context=_CHILD_CTX))
        await collector.emit(_event(EventType.TEXT_MESSAGE_END, {}, run_context=_CHILD_CTX))
        await collector.emit(_event(EventType.RUN_FINISHED, {}, run_context=_CHILD_CTX))

        assert len(collector.parts) == 1
        sub_agent_part = collector.parts[0]
        assert sub_agent_part["type"] == "sub_agent"
        assert sub_agent_part["runId"] == _CHILD_CTX.run_id
        assert sub_agent_part["roleName"] == "internal_exploration_agent"
        assert sub_agent_part["parts"] == [
            {"type": "text", "content": "delegate answer", "runId": _CHILD_CTX.run_id}
        ]

    async def test_root_level_activity_is_unaffected_by_child_activity(self) -> None:
        collector = TranscriptCollector()

        await collector.emit(_event(EventType.RUN_STARTED, {}))
        await collector.emit(_event(EventType.TEXT_MESSAGE_START, {}))
        await collector.emit(_event(EventType.TEXT_MESSAGE_CONTENT, {"delta": "before delegation"}))
        await collector.emit(_event(EventType.TEXT_MESSAGE_END, {}))
        await collector.emit(_event(EventType.RUN_STARTED, {}, run_context=_CHILD_CTX))
        await collector.emit(_event(EventType.RUN_FINISHED, {}, run_context=_CHILD_CTX))
        await collector.emit(_event(EventType.TEXT_MESSAGE_START, {}))
        await collector.emit(_event(EventType.TEXT_MESSAGE_CONTENT, {"delta": "after delegation"}))
        await collector.emit(_event(EventType.TEXT_MESSAGE_END, {}))

        assert [p["type"] for p in collector.parts] == ["text", "sub_agent", "text"]
        assert collector.parts[0]["content"] == "before delegation"
        assert collector.parts[2]["content"] == "after delegation"


class TestReplaceFinalText:
    def test_replaces_last_text_part_content(self) -> None:
        collector = TranscriptCollector()
        collector.parts = [
            {"type": "reasoning", "content": "thinking"},
            {"type": "text", "content": "draft answer"},
        ]

        collector.replace_final_text("final normalized answer")

        assert collector.parts[1]["content"] == "final normalized answer"
        assert collector.parts[0]["content"] == "thinking"

    def test_marks_replaced_text_part_as_final(self) -> None:
        collector = TranscriptCollector()
        collector.parts = [{"type": "text", "content": "draft answer"}]

        collector.replace_final_text("final normalized answer")

        assert collector.parts[0]["isFinal"] is True

    def test_appends_text_part_when_none_exists(self) -> None:
        collector = TranscriptCollector()
        collector.parts = [{"type": "tool_call", "toolCallId": "call-1"}]

        collector.replace_final_text("fallback answer")

        assert collector.parts[-1] == {"type": "text", "content": "fallback answer", "isFinal": True}

    def test_only_the_last_text_part_is_marked_final(self) -> None:
        """Earlier root `text` parts are narration turns (Cursor-Style
        Agent Transparency plan) and must stay unmarked so the frontend
        renders them, not the final answer."""
        collector = TranscriptCollector()
        collector.parts = [
            {"type": "text", "content": "Let me check the tests first."},
            {"type": "tool_call", "toolCallId": "call-1"},
            {"type": "text", "content": "draft answer"},
        ]

        collector.replace_final_text("final normalized answer")

        assert "isFinal" not in collector.parts[0]
        assert collector.parts[2]["isFinal"] is True


class TestNarrationSequence:
    """End-to-end shape from the "Cursor-Style Agent Transparency" plan: a
    preamble turn narrates before a tool call, then the terminal turn's
    text is corrected to the final answer via `replace_final_text` and
    marked `isFinal` — narration stays unmarked."""

    async def test_preamble_then_tool_call_then_final_answer(self) -> None:
        collector = TranscriptCollector()

        await collector.emit(_event(EventType.RUN_STARTED, {}))
        await collector.emit(_event(EventType.TEXT_MESSAGE_START, {}))
        await collector.emit(_event(EventType.TEXT_MESSAGE_CONTENT, {"delta": "Let me check the tests first."}))
        await collector.emit(_event(EventType.TEXT_MESSAGE_END, {}))
        await collector.emit(
            _event(EventType.TOOL_CALL_START, {"tool": "run_tests", "args": {}, "tool_call_id": "call-1"})
        )
        await collector.emit(
            _event(EventType.TOOL_CALL_END, {"tool": "run_tests", "content": "30 passed", "is_error": False, "tool_call_id": "call-1"})
        )
        await collector.emit(_event(EventType.TEXT_MESSAGE_START, {}))
        await collector.emit(_event(EventType.TEXT_MESSAGE_CONTENT, {"delta": "All tests pass."}))
        await collector.emit(_event(EventType.TEXT_MESSAGE_END, {}))
        collector.replace_final_text("All 30 tests pass.")

        assert [p["type"] for p in collector.parts] == ["text", "tool_call", "text"]
        narration, tool_call, final = collector.parts
        assert narration["content"] == "Let me check the tests first."
        assert "isFinal" not in narration
        assert tool_call["toolName"] == "run_tests"
        assert final["content"] == "All 30 tests pass."
        assert final["isFinal"] is True


class TestNoFullToolResultsLeak:
    async def test_result_preview_never_exceeds_bound_even_for_huge_payload(self) -> None:
        """Structural guarantee behind "don't persist full external tool
        results": no matter how large the raw tool output, the collected
        part's `resultPreview` is bounded — see module docstring."""
        collector = TranscriptCollector()
        huge_result = "y" * 1_000_000

        await collector.emit(_event(EventType.RUN_STARTED, {}))
        await collector.emit(_event(EventType.TOOL_CALL_START, {"tool": "big_tool", "args": {}, "tool_call_id": "call-5"}))
        await collector.emit(
            _event(EventType.TOOL_CALL_END, {"tool": "big_tool", "content": huge_result, "is_error": False, "tool_call_id": "call-5"})
        )

        assert len(collector.parts[0]["resultPreview"]) <= 500


class TestLargeToolArgsStayParseable:
    """The transcript's `args` are re-parsed with `JSON.parse` on the Node
    side (`enterprise_search/utils/utils.ts::toolResultsFromParts`) to rebuild
    a previous turn's tool calls. Truncating the serialized JSON at a byte
    offset leaves a document that cannot parse, and Node's `catch` then drops
    `args` ENTIRELY — so `run_code`'s program disappeared from history rather
    than arriving shortened, and the model rewrote from scratch instead of
    editing.
    """

    async def _tool_call_part(self, args: dict[str, Any]) -> dict[str, Any]:
        collector = TranscriptCollector()
        await collector.emit(_event(EventType.RUN_STARTED, {}))
        await collector.emit(_event(EventType.TOOL_CALL_START, {
            "tool_call_id": "call_1", "tool": "run_code", "args": args,
        }))
        return next(p for p in collector.parts if p["type"] == "tool_call")

    async def test_oversized_args_still_parse_as_json(self) -> None:
        # 3322 chars: the exact program size from the production failure.
        part = await self._tool_call_part({
            "code": "x" * 3322, "language": "typescript", "packages": ["pptxgenjs"],
        })
        parsed = json.loads(part["args"])  # must not raise
        assert isinstance(parsed, dict)

    async def test_oversized_args_keep_every_key(self) -> None:
        """Only the oversized VALUE is shortened. Dropping the small keys
        alongside it would lose the language and package list, which are
        exactly what a follow-up turn needs to reproduce the setup."""
        part = await self._tool_call_part({
            "code": "x" * 5000, "language": "typescript", "packages": ["pptxgenjs"],
        })
        parsed = json.loads(part["args"])
        assert parsed["language"] == "typescript"
        assert parsed["packages"] == ["pptxgenjs"]

    async def test_truncation_is_visible_not_silent(self) -> None:
        """A prefix presented as the whole program is worse than no program:
        the model would treat a half-written script as complete."""
        part = await self._tool_call_part({"code": "x" * 5000})
        code = json.loads(part["args"])["code"]
        assert len(code) < 5000
        assert "truncated" in code.lower()
        assert "5000" in code, "the true length should be recoverable"

    async def test_small_args_are_untouched(self) -> None:
        args = {"code": 'console.log("hi")', "language": "typescript"}
        part = await self._tool_call_part(args)
        assert json.loads(part["args"]) == args

    async def test_stays_within_the_size_budget(self) -> None:
        """The cap exists to bound the persisted Mongo document."""
        part = await self._tool_call_part({
            "code": "x" * 50_000, "notes": "y" * 50_000,
        })
        assert len(part["args"]) <= 4000

    async def test_non_string_values_survive(self) -> None:
        part = await self._tool_call_part({
            "code": "x" * 5000, "timeout": 30, "allow_network": True, "retries": None,
        })
        parsed = json.loads(part["args"])
        assert parsed["timeout"] == 30
        assert parsed["allow_network"] is True
        assert parsed["retries"] is None

    async def test_unserializable_args_do_not_explode(self) -> None:
        """`default=str` handled this before; it still has to."""
        part = await self._tool_call_part({"when": object(), "code": "x" * 5000})
        assert json.loads(part["args"])
