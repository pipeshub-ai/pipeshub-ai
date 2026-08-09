"""IWorkflowAgentRunner port — what `ctx.agent(id).run(goal=...)` needs.

Kept as a port rather than a direct dependency on the agent runtime so the
workflow layer never imports the agent loop, and so tests can substitute a
stub without constructing an LLM-backed agent.
"""
from __future__ import annotations

from typing import Any, Protocol


class IWorkflowAgentRunner(Protocol):
    async def run(
        self,
        *,
        agent_id: str,
        org_id: str,
        user_id: str,
        goal: str,
        arguments: dict[str, Any],
    ) -> Any:
        """Run the agent identified by `agent_id` WITHIN `org_id` and return
        its output. Implementations must resolve the agent inside the org --
        the org is the isolation boundary, not the caller-supplied id."""
        ...
