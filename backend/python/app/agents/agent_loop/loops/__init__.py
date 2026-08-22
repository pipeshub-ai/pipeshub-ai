"""Custom `LoopStrategy` implementations for the agent-loop adapter layer.

`OrchestratorLoop` (deep mode, multi-agent) and `PlanCritiqueExecuteLoop`
(planExecute mode, single-agent) both live here, sharing `PhaseDriver`
(`app/agent_loop_lib/agent/phase_driver.py`) for their Plan/Critique and
Verify phases — see `orchestrator.py`/`plan_execute.py`'s module docstrings
for what actually differs between them (Phase 2's shape). Every other chat
mode maps onto a `LoopStrategy` agent-loop already ships (`ReActLoop`; see
`router.py`)."""

from app.agents.agent_loop.loops.orchestrator import (
    COORDINATION_TOOL_NAMES,
    OrchestratorLoop,
    domain_spec_factory,
    register_coordination_tools,
)
from app.agents.agent_loop.loops.plan_execute import (
    PLANNING_TOOL_NAMES,
    PlanCritiqueExecuteLoop,
    register_planning_tools,
)

__all__ = [
    "COORDINATION_TOOL_NAMES",
    "PLANNING_TOOL_NAMES",
    "OrchestratorLoop",
    "PlanCritiqueExecuteLoop",
    "domain_spec_factory",
    "register_coordination_tools",
    "register_planning_tools",
]
