"""`TaskCompleteTool.extract_outcome`'s typed output contract fields
(p4-output-schema): `confidence`/`record_ids`/`needs_input`, additive on
top of the always-present `output`/`artifacts` — see `AgentResult`
(`core/types.py`) for what each means and why they're optional."""

from __future__ import annotations

from app.agent_loop_lib.core.types import Confidence, ToolCall
from app.agent_loop_lib.core.types import ToolResult as CoreToolResult
from app.agent_loop_lib.tools.builtin.planning.task_complete import TaskCompleteTool

_CALL = ToolCall(id="c1", name="task_complete", arguments={})


def _tr(content: object) -> CoreToolResult:
    return CoreToolResult(tool_call_id="c1", name="task_complete", content=content)


class TestExtractOutcomeTypedFields:
    def test_defaults_are_empty_when_none_of_the_new_fields_are_set(self) -> None:
        outcome = TaskCompleteTool.extract_outcome(_tr({"output": "done"}), _CALL, "")
        assert outcome.confidence is None
        assert outcome.record_ids == []
        assert outcome.needs_input is None

    def test_confidence_parses_valid_enum_value(self) -> None:
        outcome = TaskCompleteTool.extract_outcome(
            _tr({"output": "done", "confidence": "high"}), _CALL, "",
        )
        assert outcome.confidence is Confidence.HIGH

    def test_confidence_is_case_insensitive(self) -> None:
        outcome = TaskCompleteTool.extract_outcome(
            _tr({"output": "done", "confidence": "LOW"}), _CALL, "",
        )
        assert outcome.confidence is Confidence.LOW

    def test_invalid_confidence_value_is_dropped_not_fatal(self) -> None:
        outcome = TaskCompleteTool.extract_outcome(
            _tr({"output": "done", "confidence": "very-sure"}), _CALL, "",
        )
        assert outcome.task_done is True
        assert outcome.confidence is None

    def test_record_ids_are_collected_as_strings(self) -> None:
        outcome = TaskCompleteTool.extract_outcome(
            _tr({"output": "done", "record_ids": ["JIRA-1", "JIRA-2"]}), _CALL, "",
        )
        assert outcome.record_ids == ["JIRA-1", "JIRA-2"]

    def test_non_list_record_ids_is_ignored(self) -> None:
        outcome = TaskCompleteTool.extract_outcome(
            _tr({"output": "done", "record_ids": "JIRA-1"}), _CALL, "",
        )
        assert outcome.record_ids == []

    def test_blank_entries_in_record_ids_are_dropped(self) -> None:
        outcome = TaskCompleteTool.extract_outcome(
            _tr({"output": "done", "record_ids": ["JIRA-1", "  ", ""]}), _CALL, "",
        )
        assert outcome.record_ids == ["JIRA-1"]

    def test_needs_input_is_captured(self) -> None:
        outcome = TaskCompleteTool.extract_outcome(
            _tr({"output": "partial answer", "needs_input": "the target Jira project key"}),
            _CALL, "",
        )
        assert outcome.needs_input == "the target Jira project key"
        # needs_input never overrides task_done/final_output — still a
        # normal completion, just flagged for escalation upward.
        assert outcome.task_done is True
        assert outcome.final_output == "partial answer"

    def test_blank_needs_input_normalizes_to_none(self) -> None:
        outcome = TaskCompleteTool.extract_outcome(
            _tr({"output": "done", "needs_input": "   "}), _CALL, "",
        )
        assert outcome.needs_input is None

    def test_all_fields_together(self) -> None:
        outcome = TaskCompleteTool.extract_outcome(
            _tr({
                "output": "Found 2 tickets but could not confirm the sprint",
                "confidence": "medium",
                "record_ids": ["JIRA-1", "JIRA-2"],
                "needs_input": "which sprint to filter by",
            }),
            _CALL, "",
        )
        assert outcome.confidence is Confidence.MEDIUM
        assert outcome.record_ids == ["JIRA-1", "JIRA-2"]
        assert outcome.needs_input == "which sprint to filter by"
