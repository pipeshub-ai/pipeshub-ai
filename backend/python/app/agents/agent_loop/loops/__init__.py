"""Custom `LoopStrategy` implementations for the agent-loop adapter layer.

Only `OrchestratorLoop` (Phase 10, deferred) lives here today — every other
chat mode maps onto a `LoopStrategy` agent-loop already ships (`ReActLoop`,
`PlanExecuteLoop`; see `router.py`)."""

from app.agents.agent_loop.loops.orchestrator import (
    COORDINATION_TOOL_NAMES,
    OrchestratorLoop,
    domain_spec_factory,
    register_coordination_tools,
)

__all__ = [
    "COORDINATION_TOOL_NAMES",
    "OrchestratorLoop",
    "domain_spec_factory",
    "register_coordination_tools",
]
