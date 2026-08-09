"""Pure DAG validation for `TaskDefinition.steps`.

Deliberately duplicates `agent_loop_lib.tools.builtin.coordination
.graph_utils.find_cycle`'s DFS rather than importing it:
`app.services.tasks.domain` may not import `agent_loop_lib` at all (see
`models.py`'s module docstring for the boundary rule -- only
`runtime/spec_assembler.py` may import both), and this algorithm is a dozen
lines of generic graph theory with no natural shared home either side could
depend on without inverting that boundary. Kept here (not in
`application/engine.py`) so `TaskEngine.create()`/`update()` and any future
non-HTTP caller (e.g. a REST route) get the identical, single-sourced
check.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.services.tasks.domain.errors import TaskDAGError

if TYPE_CHECKING:
    from app.services.tasks.domain.models import TaskStep

__all__ = ["validate_steps"]


def _find_cycle(adjacency: dict[str, list[str]]) -> list[str] | None:
    white, gray, black = 0, 1, 2
    color: dict[str, int] = dict.fromkeys(adjacency, white)
    stack: list[str] = []

    def _visit(node: str) -> list[str] | None:
        color[node] = gray
        stack.append(node)
        for successor in adjacency.get(node, []):
            if successor not in color:
                continue
            if color[successor] == gray:
                idx = stack.index(successor)
                return [*stack[idx:], successor]
            if color[successor] == white:
                found = _visit(successor)
                if found is not None:
                    return found
        stack.pop()
        color[node] = black
        return None

    for node in adjacency:
        if color[node] == white:
            found = _visit(node)
            if found is not None:
                return found
    return None


def validate_steps(steps: "list[TaskStep] | None") -> None:
    """Raises `TaskDAGError` for a duplicate step id, an edge to an
    unknown step id, or a dependency cycle. A no-op for `None`/empty."""
    if not steps:
        return
    seen: set[str] = set()
    for step in steps:
        if step.id in seen:
            raise TaskDAGError(f"Duplicate TaskStep id: {step.id!r}")
        seen.add(step.id)

    adjacency = {step.id: list(step.depends_on) for step in steps}
    for step in steps:
        for dep in step.depends_on:
            if dep not in adjacency:
                raise TaskDAGError(f"TaskStep {step.id!r} has depends_on unknown step id: {dep!r}")

    cycle = _find_cycle(adjacency)
    if cycle is not None:
        raise TaskDAGError("Circular dependency among TaskSteps: " + " -> ".join(cycle))
