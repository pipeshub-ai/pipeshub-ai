"""Unit tests for `domain.dag_validation.validate_steps` -- the pure
DAG-shape gate `TaskEngine.create()` runs before persisting a multi-step
task (Phase 7 -- see that module's own docstring on why this duplicates
`agent_loop_lib`'s cycle-detection algorithm rather than importing it)."""
from __future__ import annotations

import pytest

from app.services.tasks.domain.dag_validation import validate_steps
from app.services.tasks.domain.errors import TaskDAGError
from app.services.tasks.domain.models import TaskStep


def _step(step_id: str, depends_on: list[str] | None = None) -> TaskStep:
    return TaskStep(id=step_id, description=f"do {step_id}", depends_on=depends_on or [])


class TestValidateSteps:
    def test_none_is_a_noop(self) -> None:
        validate_steps(None)

    def test_empty_list_is_a_noop(self) -> None:
        validate_steps([])

    def test_single_step_with_no_dependencies_is_valid(self) -> None:
        validate_steps([_step("a")])

    def test_linear_chain_is_valid(self) -> None:
        validate_steps([_step("a"), _step("b", ["a"]), _step("c", ["b"])])

    def test_diamond_shape_is_valid(self) -> None:
        validate_steps([_step("a"), _step("b", ["a"]), _step("c", ["a"]), _step("d", ["b", "c"])])

    def test_duplicate_step_id_raises(self) -> None:
        with pytest.raises(TaskDAGError, match="Duplicate TaskStep id"):
            validate_steps([_step("a"), _step("a")])

    def test_unknown_dependency_raises(self) -> None:
        with pytest.raises(TaskDAGError, match="unknown step id"):
            validate_steps([_step("a", ["missing"])])

    def test_self_referencing_cycle_raises(self) -> None:
        with pytest.raises(TaskDAGError, match="Circular dependency"):
            validate_steps([_step("a", ["a"])])

    def test_two_node_cycle_raises(self) -> None:
        with pytest.raises(TaskDAGError, match="Circular dependency"):
            validate_steps([_step("a", ["b"]), _step("b", ["a"])])

    def test_longer_cycle_raises(self) -> None:
        with pytest.raises(TaskDAGError, match="Circular dependency"):
            validate_steps([_step("a", ["c"]), _step("b", ["a"]), _step("c", ["b"])])

    def test_duplicate_id_checked_before_cycle(self) -> None:
        """Duplicate ids make the adjacency map ambiguous -- must be caught
        first so the cycle detector never runs on a malformed graph."""
        with pytest.raises(TaskDAGError, match="Duplicate TaskStep id"):
            validate_steps([_step("a", ["a"]), _step("a")])
