"""`child_result_content` (p4-output-schema): the shared tool-result shape
`SpawnAgentTool.handle()` and `orchestrator.py`'s `_programmatic_dispatch`
both build from a completed child's `AgentResult` — a completed child's
typed output contract fields (`confidence`/`record_ids`/`needs_input`)
must actually reach the CALLER's context through this, or they exist on
`AgentResult` for nothing."""

from __future__ import annotations

from app.agent_loop_lib.core.types import AgentResult, Artifact, Confidence, Goal
from app.agent_loop_lib.tools.builtin.coordination.spawn_agent import child_result_content

_GOAL = Goal(description="x")


class TestChildResultContent:
    def test_plain_success_has_only_output_and_success(self) -> None:
        result = AgentResult(goal=_GOAL, output="done", success=True)
        content = child_result_content(result)
        assert content == {"output": "done", "success": True}

    def test_artifacts_become_refs_without_full_content(self) -> None:
        result = AgentResult(
            goal=_GOAL, output="done", success=True,
            artifacts=[Artifact(name="tickets.json", type="json", content=[1, 2, 3], description="all tickets")],
        )
        content = child_result_content(result)
        assert content["artifacts"] == [
            {"name": "tickets.json", "type": "json", "description": "all tickets"},
        ]
        # The full artifact content must never leak into this dict — see
        # the module docstring on why (it reaches a DEPENDENT via staged
        # files instead, see `spawn_scheduler.py`).
        assert "content" not in content["artifacts"][0]

    def test_confidence_and_record_ids_pass_through(self) -> None:
        result = AgentResult(
            goal=_GOAL, output="done", success=True,
            confidence=Confidence.HIGH, record_ids=["JIRA-1", "JIRA-2"],
        )
        content = child_result_content(result)
        assert content["confidence"] == "high"
        assert content["record_ids"] == ["JIRA-1", "JIRA-2"]

    def test_needs_input_is_surfaced_with_an_explicit_prefix(self) -> None:
        result = AgentResult(
            goal=_GOAL, output="partial", success=True,
            needs_input="the target sprint name",
        )
        content = child_result_content(result)
        assert "needs_input" in content
        assert "the target sprint name" in content["needs_input"]
        # Not just the bare string — explicit enough that a model skimming
        # tool results would not mistake this for a normal successful result.
        assert "could not fully complete" in content["needs_input"]

    def test_absent_optional_fields_are_omitted_not_null(self) -> None:
        """Keeps the common case's payload small/back-compat — a dict key
        with a `None`/`[]` value would still be new surface for every
        existing consumer that does `if "confidence" in content`."""
        result = AgentResult(goal=_GOAL, output="done", success=True)
        content = child_result_content(result)
        assert "confidence" not in content
        assert "record_ids" not in content
        assert "needs_input" not in content
        assert "artifacts" not in content
