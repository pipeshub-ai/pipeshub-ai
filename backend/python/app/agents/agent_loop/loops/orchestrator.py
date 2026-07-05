"""`OrchestratorLoop`: Phase 10 (deferred) of the agent-loop migration —
replaces the legacy LangGraph deep-agent graph (`app.modules.agents.deep`:
`orchestrator_node` -> `critic_node` -> `execute_sub_agents_node` ->
`aggregator_node`) with a `LoopStrategy` composed entirely over agent-loop's
own generic planning/coordination TOOLS, per the migration plan's mapping:

| Deep Component               | Agent-Loop Equivalent                          |
|-------------------------------|-------------------------------------------------|
| `orchestrator_node`           | `create_plan` tool call (Phase 1)                |
| `critic_node`                 | `critique_plan` tool call, one-shot-then-execute |
| `execute_sub_agents_node`     | `spawn_agent` tool calls, parallel batch (Phase 2)|
| `tool_router.group_tools_by_domain` | `_domain_overview()` + per-spawn `tools=[...]`|
| `aggregator_node`             | `verify_result` tool + retry loop (Phase 3)      |

Deliberate scope boundary vs. the legacy deep agent: this is NOT a port of
`sub_agent.py`'s batch/multi-step/phased execution machinery (~1900 lines of
bespoke pagination, budget-wrapping, and citation-buffer plumbing) — the
migration plan frames Phase 10 as replacing the EXECUTION ENGINE with
agent-loop's generic primitives, not reproducing every bespoke optimization
built on top of the old one. `spawn_agent`'s children are plain `ReActLoop`
agents (see `domain_spec_factory`) scoped to one domain's tools each; the
orchestrator's own `spec.tool_names` is deliberately restricted to the four
coordination tools below (never the raw connector tools) so it can only
plan/dispatch/verify, never execute domain work directly — same isolation
the legacy orchestrator enforced by simply never receiving connector tools.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.agent.loops import LoopStrategy, ReActLoop
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.tools.builtin.coordination.spawn_agent import SpawnAgentTool
from app.agent_loop_lib.tools.builtin.planning.create_plan import CreatePlanTool
from app.agent_loop_lib.tools.builtin.planning.critique_plan import CritiquePlanTool
from app.agent_loop_lib.tools.builtin.planning.verify_result import VerifyResultTool
from app.utils.time_conversion import build_llm_time_context

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.agent_loop_lib.agent import Agent
    from app.agent_loop_lib.core.types import AgentResult, Goal
    from app.agent_loop_lib.tools.registry import ToolRegistry
    from app.agents.agent_loop.context import AgentContext

logger = logging.getLogger(__name__)

# The only tools the top-level orchestrator agent itself is ever granted
# (`spec.tool_names`) — every other registered tool (connector actions,
# `retrieval`, dynamic tools) stays reachable ONLY through a spawned
# sub-agent's own, domain-scoped `tool_names`. Order matches the plan's
# Decompose -> Critique -> Dispatch -> Verify phases.
COORDINATION_TOOL_NAMES: tuple[str, ...] = (
    "create_plan",
    "critique_plan",
    "spawn_agent",
    "verify_result",
)

# Sub-agents are single-domain ReAct workers, not orchestrators themselves —
# enough turns for a multi-page fetch + a retry or two, but low enough that
# a flailing child can't silently dominate the whole request's latency
# budget (each turn is an LLM call + a tool call: ~5-15s).
_SUB_AGENT_MAX_TURNS = 8


class DomainSpawnAgentTool(SpawnAgentTool):
    """`SpawnAgentTool` with a description steering the LLM toward
    PipesHub's domain-scoped sub-agents (jira/slack/retrieval/...) instead
    of agent-loop's default researcher/writer/planner/critic/verifier
    roles. `role` stays a free-form string either way — resolution is
    entirely up to whatever `AgentRuntime.spec_factory` is wired (see
    `domain_spec_factory` below); only the LLM-facing description changes.
    `handle()`/`execute()`/`parameters` are inherited unchanged."""

    @property
    def description(self) -> str:
        return (
            "Spawn a sub-agent scoped to ONE domain (e.g. 'jira', 'slack', "
            "'retrieval', 'confluence' — see the 'Available Domains' section "
            "of your instructions) to execute one independent workstream in "
            "isolation. ALWAYS pass `tools` with the exact tool names for "
            "that domain — the sub-agent receives ONLY those tools, nothing "
            "else. To parallelize independent domains, call spawn_agent "
            "multiple times in the SAME turn (one call per domain)."
        )


def register_coordination_tools(registry: "ToolRegistry") -> None:
    """Registers the four builtin tools `OrchestratorLoop` composes over
    onto a fresh `ToolRegistry` (call once per request — `PipesHubToolLoader`
    already builds a new `ToolRegistry` per `AgentContext`, so there is no
    cross-request state to worry about; `ToolRegistry.register_tool` raises
    `DuplicateToolNameError`/`DuplicateToolPathError` if called twice on the
    same registry)."""
    for tool in (CreatePlanTool(), CritiquePlanTool(), DomainSpawnAgentTool(), VerifyResultTool()):
        registry.register_tool(tool)


def _build_sub_agent_prompt(domain: str, context: "AgentContext | None") -> str:
    """Builds the sub-agent's system prompt with essential PipesHub context.

    The legacy deep agent (`deep/sub_agent.py`) gives each sub-agent:
    user identity, time context, tool guidance, scoped instructions, and
    extensive data-completeness rules. Without at least user identity and
    time context, the child can't resolve "my tickets", "last 2 months",
    or any user-relative / time-relative query — causing it to flail with
    wrong parameters for all its turns and appear stuck.
    """
    parts: list[str] = [
        f"You are a focused sub-agent for the '{domain}' domain. "
        "Complete the assigned task using ONLY the tools you were given.",
        _SUB_AGENT_EXECUTION_RULES,
    ]

    if context is not None:
        user_block = _build_user_context_block(context)
        if user_block:
            parts.append(user_block)
        time_block = build_llm_time_context(
            current_time=context.current_time,
            time_zone=context.timezone,
        )
        if time_block:
            parts.append(time_block)

    return "\n\n".join(parts)


_SUB_AGENT_EXECUTION_RULES = """\
## Execution Rules

### Tool Usage
- **Parallelise independent calls**: issue ALL independent tool calls in a SINGLE turn.
- **Maximise page size**: use the largest supported `maxResults`/`limit`/`pageSize`.
- **Retry differently**: if a tool returns empty results, try a DIFFERENT query phrasing
  or broader filter — do not repeat the same call.

### Response Format
- **Present ALL data**: every item returned by tools MUST appear in your response.
- **Include ALL fields**: IDs, keys, URLs, names, dates, statuses, priorities.
- **Date formatting**: render dates in human-readable form using the time zone from
  the Time context below (e.g. "April 28, 2026 at 3:45 PM IST"). Never output raw
  epoch numbers or ISO strings.
- **Links are mandatory**: include `[Title](url)` for every item.
- **Use tables** for lists of items.

### Completion
Once you have enough information, reply with your answer in plain text (no further
tool calls). That text becomes your result for the orchestrator to synthesise."""


def _build_user_context_block(context: "AgentContext") -> str:
    """Mirrors `deep/sub_agent.py::_build_sub_agent_instructions` — the
    child needs user identity to resolve 'my tickets', 'assigned to me'."""
    user_info = context.user_info or {}
    email = context.user_email or user_info.get("userEmail") or user_info.get("email") or ""
    name = (
        user_info.get("fullName")
        or user_info.get("name")
        or user_info.get("displayName")
        or f"{user_info.get('firstName', '')} {user_info.get('lastName', '')}".strip()
    )
    if not name and not email:
        return ""
    lines = ["## Current User"]
    if name:
        lines.append(f"- Name: {name}")
    if email:
        lines.append(f"- Email: {email}")
    lines.append('When the query says "my", "me", or "I", it refers to this user.')
    return "\n".join(lines)


def domain_spec_factory(
    *,
    provider: str,
    model_name: str,
    default_tool_names: list[str],
    context: "AgentContext | None" = None,
    max_turns: int = _SUB_AGENT_MAX_TURNS,
) -> "Callable[..., AgentSpec]":
    """Builds the `AgentRuntime.spec_factory` callable `spawn_agent` needs
    to resolve a domain "role" string into a concrete `AgentSpec` (see
    `AgentRuntime.spec_for_role`). `role_name` only labels the child's
    `AgentSpec.name` (tracing/timeline) — actual tool scoping always comes
    from `spawn_agent`'s `tools` argument (`overrides["tool_names"]`, see
    `tools/builtin/coordination/spawn_agent.py::build_spawn_child`), falling
    back to `default_tool_names` (every tool this request loaded, minus the
    four coordination tools) if the LLM omitted it."""

    def _factory(role_name: str, **overrides: Any) -> AgentSpec:  # noqa: ANN401
        tool_names = overrides.get("tool_names") or default_tool_names
        model = overrides.get("model") or model_name
        return AgentSpec(
            name=f"pipeshub-subagent-{role_name}",
            system_prompt=_build_sub_agent_prompt(role_name, context),
            tool_names=list(tool_names),
            model=ModelSpec(provider=provider, model=model),
            loop=ReActLoop(),
            max_turns=max_turns,
        )

    return _factory


def _domain_overview(agent: "Agent") -> str:
    """Builds the "## Available Domains" block used in Phase 2's dispatch
    instructions, by grouping every tool in the SHARED registry (minus the
    four coordination tools this loop itself owns) by `Tool.app_name` — the
    replacement for the legacy `tool_router.build_domain_description()`,
    computed at RUN time (not wiring time) directly off
    `agent.runtime.tool_registry` so it always reflects exactly what
    `PipesHubToolLoader` loaded for this request, with no separate state to
    keep in sync."""
    registry = agent.runtime.tool_registry
    if registry is None:
        return ""

    max_shown_per_domain = 12
    groups: dict[str, list[str]] = {}
    for name in registry.names():
        if name in COORDINATION_TOOL_NAMES:
            continue
        tool = registry.resolve_by_name(name)
        domain = getattr(tool, "app_name", None) or "utility"
        groups.setdefault(domain, []).append(name)

    if not groups:
        return "No external tool domains are configured for this request."

    lines = ["## Available Domains (pass these EXACT tool names as spawn_agent's `tools`)"]
    for domain, names in sorted(groups.items()):
        shown = ", ".join(f"`{n}`" for n in names[:max_shown_per_domain])
        if len(names) > max_shown_per_domain:
            shown += f", ... ({len(names) - max_shown_per_domain} more)"
        lines.append(f"- **{domain}**: {shown}")
    return "\n".join(lines)


class OrchestratorLoop(LoopStrategy):
    """Decompose -> Critique -> Dispatch -> Verify, composed purely over
    `create_plan` / `critique_plan` / `spawn_agent` / `verify_result` TOOLS
    — mirrors `PlanCritiqueExecuteLoop`'s style (phase transitions via
    `agent.inject_user_message()` + `agent.last_tool_result()`, never a
    planner/critic module called directly by this class) with an explicit
    DISPATCH phase in between critique and verify, since the deep agent's
    whole point is domain-isolated sub-agent execution rather than the
    orchestrator calling tools itself.
    """

    name = "orchestrator"

    def __init__(self, *, max_planning_rounds: int = 2, max_verify_rounds: int = 2) -> None:
        self._max_planning_rounds = max_planning_rounds
        self._max_verify_rounds = max_verify_rounds

    async def run(self, agent: "Agent", goal: "Goal") -> "AgentResult":
        turn_index = agent.start_turn_index

        # --- Phase 1: PLAN + CRITIQUE (orchestrator_node + critic_node) ---
        await agent.inject_user_message(
            "Phase 1 -- PLAN: decompose this goal into single-domain phases "
            "(one phase per independent domain/workstream — never mix "
            "domains in one phase) by calling create_plan. Then call "
            "critique_plan with those exact phases. If critique_plan "
            "returns passed=false, revise the phases and call critique_plan "
            "again. Do not dispatch any sub-agents until critique_plan "
            "passes.\n\n" + _domain_overview(agent)
        )
        planning_rounds = 0
        while planning_rounds < self._max_planning_rounds and turn_index < agent.max_turns:
            outcome = await agent.step(goal, turn_index)
            turn_index += 1
            if outcome.status == "stop":
                return outcome.result
            verdict = agent.last_tool_result("critique_plan")
            if verdict is not None:
                planning_rounds += 1
                if isinstance(verdict, dict) and verdict.get("passed"):
                    break
                await agent.inject_user_message(
                    "Phase 1 -- REPLAN: critique_plan did not pass. Revise "
                    "the plan to address the issues raised, then call "
                    "critique_plan again."
                )

        # --- Phase 2: DISPATCH (execute_sub_agents_node) ---
        await agent.inject_user_message(
            "Phase 2 -- DISPATCH: the plan is approved. For EACH phase, "
            "call spawn_agent with `role` set to that phase's domain and "
            "`tools` set to the exact tool names for that domain (see "
            "'Available Domains' above). Call every independent phase's "
            "spawn_agent IN THE SAME TURN so they run in parallel — only "
            "sequence spawn_agent calls across turns for phases that "
            "genuinely depend on an earlier phase's result."
        )
        dispatched = False
        while turn_index < agent.max_turns:
            outcome = await agent.step(goal, turn_index)
            turn_index += 1
            if outcome.status == "stop":
                return outcome.result
            if agent.last_tool_result("spawn_agent") is not None:
                dispatched = True
                break

        if not dispatched:
            return await agent.fail(
                goal,
                f"Exceeded max_turns={agent.max_turns} without dispatching any sub-agent",
                event="agent_failed",
            )

        # --- Phase 3: VERIFY + retry (aggregator_node) ---
        await agent.inject_user_message(
            "Phase 3 -- VERIFY: synthesize the sub-agents' results into a "
            "candidate final answer, then call verify_result with it before "
            "replying. If verify_result fails, spawn additional sub-agents "
            "or revise your synthesis and call verify_result again."
        )
        verify_rounds = 0
        while turn_index < agent.max_turns:
            outcome = await agent.step(goal, turn_index)
            turn_index += 1
            if outcome.status == "stop":
                return outcome.result
            verdict = agent.last_tool_result("verify_result")
            if verdict is not None and not (isinstance(verdict, dict) and verdict.get("passed")):
                verify_rounds += 1
                if verify_rounds >= self._max_verify_rounds:
                    await agent.inject_user_message(
                        "Phase 3 -- FINISH: verification still failing after "
                        "retries. Reply now with your best current answer "
                        "(no more tool calls)."
                    )
                else:
                    await agent.inject_user_message(
                        "Phase 3 -- REVISE: verify_result did not pass. "
                        "Spawn more sub-agents or revise your synthesis, "
                        "then call verify_result again."
                    )

        return await agent.fail(goal, f"Exceeded max_turns={agent.max_turns}", event="agent_failed")


__all__ = [
    "COORDINATION_TOOL_NAMES",
    "DomainSpawnAgentTool",
    "OrchestratorLoop",
    "domain_spec_factory",
    "register_coordination_tools",
]
