"""`_step_goal_text` (p3-delegation-quality): folds a `PlanStep`'s
`boundaries`/`output_format` into the actual goal text a spawned child
sees — the ONLY place those fields have any effect on execution (a spawned
child never sees the `PlanStep` object itself, or its sibling steps, only
this string). A regression here would make the `create_plan` schema fields
purely decorative: visible to `critique_plan`'s review of the rendered
plan text, but invisible to the agent that has to actually honor them.

Imported directly from `orchestrator.py` rather than via
`test_orchestrator_loop.py`, which has an unrelated, pre-existing broken
import (`app.agents.tools.models`) unconnected to this module.
"""

from __future__ import annotations

from app.agent_loop_lib.modules.pipeline.planner.base import PlanStep
from app.agents.agent_loop.loops.orchestrator import _step_goal_text


class TestStepGoalText:
    def test_plain_step_returns_description_unchanged(self) -> None:
        step = PlanStep(id="a", description="fetch tickets", domain="jira")
        assert _step_goal_text(step) == "fetch tickets"

    def test_boundaries_are_appended_as_explicit_exclusions(self) -> None:
        step = PlanStep(
            id="a", description="fetch tickets", domain="jira",
            boundaries=["do not fetch epics", "do not analyze priority"],
        )
        text = _step_goal_text(step)
        assert text.startswith("fetch tickets\n\n")
        assert "do not do the following" in text.lower()
        assert "do not fetch epics" in text
        assert "do not analyze priority" in text

    def test_output_format_is_appended(self) -> None:
        step = PlanStep(
            id="a", description="fetch tickets", domain="jira",
            output_format="a table with columns Ticket, Assignee, Status",
        )
        text = _step_goal_text(step)
        assert "Required output format: a table with columns Ticket, Assignee, Status" in text

    def test_boundaries_and_output_format_both_present(self) -> None:
        step = PlanStep(
            id="a", description="fetch tickets", domain="jira",
            boundaries=["do not fetch epics"],
            output_format="a JSON list of {id, title}",
        )
        text = _step_goal_text(step)
        assert "fetch tickets" in text
        assert "do not fetch epics" in text
        assert "a JSON list of {id, title}" in text
        # description comes first, then boundaries, then output format
        assert text.index("fetch tickets") < text.index("do not fetch epics") < text.index("JSON list")
