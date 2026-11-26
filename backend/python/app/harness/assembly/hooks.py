"""The legacy agent loop's hooks on the durable path (PA-3), bound explicitly.

The registry is the one ``PipesHubAgentFactory`` builds (``_build_hooks``,
the same middleware in the same order), plus what the factory and
``Agent.__init__`` add around it (skill and tool preloading, the turn
guards). It is built per attempt from the run's own environment and handed
to the parts of the durable runtime that dispatch each event; nothing is
read from process-wide state.

| Event | Where it runs on the durable path |
|---|---|
| PRE_AGENT (tool/skill preloading) | once per attempt, when the turn shaper opens the run scope |
| PRE_TURN (turn 0: follow-up note, attachment rehydration, artifact reminder, visible-tool seeding) | once per attempt, on the goal step's goal (its constraints are journaled with the goal) |
| PRE_MODEL (L0, L1 and L9 shapers, deadline warning) | every turn, in the turn shaper |
| PRE_TOOL_USE / POST_TOOL_USE (error tracker, citation tracking, artifact registration, result accumulation, the coding-sandbox hooks of an ``agent-code`` run) | every call, in the tool port's executor |
| POST_MODEL (completion gate, truncation recovery) | the response review, before a no-tool response is the answer |

Not ported, with the reason, in ``NOT_PORTED``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.agent_loop_lib.hooks.registry import HookRegistry
    from app.agents.agent_loop.hooks import RepeatedReadGuard, ToolErrorTracker
    from app.harness.assembly.context import RunContext
    from app.harness.assembly.model import ResolvedModel
    from app.harness.assembly.tool_environment import ToolEnvironment
    from harness_core.tools.registry import ToolRegistry

# Why the history reducers are left out: QA-FP12 run 8cbf6950 sent its sixth request at a fifth of the size, without
# the message the user had pasted, and nothing in the journal said so.
_UNJOURNALED_CUT = (
    "cuts history without a journal entry and can drop the user's own message (QA-FP12 B2); the durable loop "
    "reduces history only through its journaled ladder (context.compacted with x.pipeshub.context.compacted), "
    "which keeps the run's input after the summary"
)

NOT_PORTED: dict[str, str] = {
    "shape_artifact_compaction (L2)": f"above a share of the budget it swaps older large results for references: it {_UNJOURNALED_CUT}",
    "shape_tool_result_clearing (L3)": f"it clears older tool results to references: it {_UNJOURNALED_CUT}",
    "shape_synthesis_guard (L8)": (
        f"past the shaping budget it clears every older tool result and truncates the rest (LONGTASK-F4): it "
        f"{_UNJOURNALED_CUT}; an oversized request is compacted when the provider refuses it (QAFIX4 B2)"
    ),
    "shape_loop_compaction (L4)": f"every fifth turn it folds all but the last six messages into one-line notes: it {_UNJOURNALED_CUT}",
    "shape_sliding_window (L5)": f"it drops the oldest messages until the request fits: it {_UNJOURNALED_CUT}",
    "report_context_reduction (PRE_MODEL)": (
        "today's loop says when L4-L9 reduced a request; the durable loop does not run L4-L7 and journals its own "
        "reductions as x.pipeshub.context.compacted, which the chat shows (QAFIX4 B2)"
    ),
    "shape_deterministic_compact (L6)": f"it shortens older calls and results above 85% of the budget: it {_UNJOURNALED_CUT}",
    "shape_auto_compact (L7a/L7b)": (
        "an LLM summarization on every over-budget turn: unjournaled it would "
        "make a resumed request differ and bill twice; compaction is journaled "
        "as context.compacted by the P4 context compiler"
    ),
    "retry_with_status (PRE_MODEL_CALL)": (
        "the durable model port classifies provider errors and the kernel backs "
        "off or yields the run (REPORT 6.4); the status frames it emitted are not projected"
    ),
    "check_not_cancelled (PRE_TURN)": (
        "the kernel checks the journaled cancel at every boundary"
    ),
    "legacy_flow_guard (PRE_TOOL_USE)": (
        "today's loop cannot hold a call for a person, so it refuses an external write after a read; the "
        "durable kernel asks the user instead, through FlowGuardedPolicy (UX1, UX3)"
    ),
    "coding_sandbox_result_propagation (POST_AGENT)": (
        "the scratch rescue reads sandboxes at run end; a durable run's sandboxes do not outlive "
        "its attempt, so a rescue could not be repeated by a re-driven one; each run_code result "
        "already tells the model to write deliverables to $OUTPUT_DIR (P5.11)"
    ),
}


# Hooks the durable path adds that today's loop does not run, with the reason (the reverse of ``NOT_PORTED``).
DURABLE_ONLY: dict[str, str] = {
    "write_identity_middleware (PRE_TOOL_USE)": (
        "OWD-18's write identity and AI disclosure apply to the harness's runs, pinned at admission (WP-23); turning "
        "them on in today's loop would change every customer's sends outside the harness rollout"
    ),
}


@dataclass
class DurableHooks:
    registry: HookRegistry
    tracker: ToolErrorTracker
    repeated_reads: RepeatedReadGuard


def build_hooks(
    env: ToolEnvironment,
    run: RunContext,
    registry: ToolRegistry,
    model: ResolvedModel,
    *,
    artifact_store: Any = None,  # noqa: ANN401 - the conversation's artifact store
    skill_manager: Any = None,  # noqa: ANN401 - a SkillManager
) -> DurableHooks:
    from app.agents.agent_loop.factory import PipesHubAgentFactory
    from app.agents.agent_loop.hooks import RepeatedReadGuard, ToolErrorTracker
    from app.agents.agent_loop.lazy_tools_wiring import (
        lazy_tools_enabled,
        register_tool_preloading,
    )
    from app.agents.agent_loop.skills_wiring import register_skill_preloading
    from app.harness.assembly.loop import model_name
    from app.harness.assembly.tool_sources import code_network
    from harness_core.kernel.hooks.builtin.turn_guards import install_turn_guards

    tracker = ToolErrorTracker()
    repeated_reads = RepeatedReadGuard(env.context)
    hooks = PipesHubAgentFactory._build_hooks(
        env.context,
        # The sandbox source's manager (agent-code pins only): the legacy sandbox hooks, unchanged.
        env.context.sandbox_manager,
        allow_network=code_network(run),
        artifact_store=artifact_store,
        tool_registry=registry,
        transport_registry=None,
        model_name=model_name(run),
        error_tracker=tracker,
        repeated_reads=repeated_reads,
        reduce_history=False,
    )
    if skill_manager is not None:
        register_skill_preloading(hooks, skill_manager)
    if lazy_tools_enabled():
        register_tool_preloading(hooks)
    # What `Agent.__init__` installs: the deadline warning and truncation recovery.
    install_turn_guards(hooks)
    return DurableHooks(hooks, tracker, repeated_reads)


__all__ = ["DURABLE_ONLY", "NOT_PORTED", "DurableHooks", "build_hooks"]
