"""`create_plan.py`'s pure helpers: step validation and plan-text rendering
for the `boundaries`/`output_format` `PlanStep` fields (p3-delegation-quality)
— these feed BOTH `critique_plan` (via the rendered text) and
`_programmatic_dispatch` (via the `PlanStep` objects themselves, see
`test_orchestrator_step_goal_text.py`), so a field silently dropped at
either seam would defeat the whole point of adding it to the schema."""

from __future__ import annotations

from app.agent_loop_lib.tools.builtin.planning.create_plan import _steps_to_text, _validate_steps


class TestValidateSteps:
    def test_boundaries_and_output_format_are_optional(self) -> None:
        steps, error = _validate_steps([
            {"id": "a", "description": "fetch tickets", "domain": "jira"},
        ])
        assert error is None
        assert steps[0].boundaries == []
        assert steps[0].output_format is None

    def test_boundaries_and_output_format_parse_when_present(self) -> None:
        steps, error = _validate_steps([{
            "id": "a",
            "description": "fetch tickets",
            "domain": "jira",
            "boundaries": ["do not fetch epics", "do not analyze priority"],
            "output_format": "a table with columns Ticket, Assignee, Status",
        }])
        assert error is None
        assert steps[0].boundaries == ["do not fetch epics", "do not analyze priority"]
        assert steps[0].output_format == "a table with columns Ticket, Assignee, Status"

    def test_invalid_boundaries_type_is_rejected(self) -> None:
        steps, error = _validate_steps([{
            "id": "a", "description": "x", "domain": "jira", "boundaries": "not-a-list",
        }])
        assert steps == []
        assert error is not None
        assert "Step 0" in error


class TestStepsToText:
    def test_step_with_no_boundaries_or_format_renders_unchanged(self) -> None:
        steps, _ = _validate_steps([{"id": "a", "description": "fetch tickets", "domain": "jira"}])
        text = _steps_to_text(steps)
        assert text == "1. **a** — fetch tickets"

    def test_boundaries_and_output_format_appear_in_rendered_text(self) -> None:
        steps, _ = _validate_steps([{
            "id": "a",
            "description": "fetch tickets",
            "domain": "jira",
            "boundaries": ["do not fetch epics"],
            "output_format": "a JSON list of {id, title}",
        }])
        text = _steps_to_text(steps)
        assert "Boundaries: do not fetch epics" in text
        assert "Output format: a JSON list of {id, title}" in text

    def test_multiple_steps_each_render_their_own_boundaries(self) -> None:
        steps, _ = _validate_steps([
            {
                "id": "a", "description": "fetch tickets", "domain": "jira",
                "boundaries": ["do not analyze"],
            },
            {
                "id": "b", "description": "analyze tickets", "domain": "jira",
                "depends_on": ["a"], "boundaries": ["do not re-fetch — use step a's output"],
            },
        ])
        text = _steps_to_text(steps)
        assert "do not analyze" in text
        assert "do not re-fetch" in text
