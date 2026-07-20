from __future__ import annotations

from typing import Any

from app.agent_loop_lib.core.exceptions import FeasibilityError
from app.agent_loop_lib.core.types import Goal


class FeasibilityChecker:
    """
    Checks whether the tools required by a Goal are present in the
    scoped ToolRegistry before the agent enters its execution loop.
    """

    def __init__(self, tool_registry: Any = None) -> None:
        self._registry = tool_registry

    async def check(self, goal: Goal) -> None:
        """
        Raise FeasibilityError if any required tool is unavailable.
        If no registry is configured, always passes.
        """
        if self._registry is None or not goal.requirements:
            return

        try:
            available = set(self._registry.names())
        except Exception:
            return

        required_tools = {
            req[len("requires tool:"):].strip()
            for req in goal.requirements
            if req.lower().startswith("requires tool:")
        }
        missing = required_tools - available
        if missing:
            raise FeasibilityError(
                f"Goal requires tools not in registry: {', '.join(sorted(missing))}"
            )
