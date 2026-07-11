"""Domain-agent catalog: PipesHub's scoped sub-agents, exposed as tools.

Instead of granting the top-level agent every registered tool schema (30+
per turn), each capability domain becomes ONE `AgentTool` — an entire
ReAct `Agent` (same `agent_loop_lib.Agent` loop, just a narrower
`AgentSpec`) callable like any other tool. The top-level agent reasons
over a handful of high-level delegates plus whatever residual tools no
domain claimed; each child reasons over only its own domain's tools.

Design:

- `DomainAgentDefinition` is pure data (which tools a domain claims, how
  the delegate is described to the calling model, which OTHER domain
  agents it may itself call). Adding a domain = adding one entry to
  `DOMAIN_AGENT_DEFINITIONS`; nothing in `plan_domain_agents()`,
  `register_domain_agents()`, or `PipesHubAgentFactory` changes
  (open/closed).
- Claiming tools off the request's already-loaded `ToolRegistry` is split
  from actually building/registering agents (`plan_domain_agents()` vs.
  `register_domain_agents()`) so callers that need to know the FINAL
  top-level tool grant before a runtime/loop exists (the quick-mode
  planner, see `factory.py`) can do so without any registry mutation, and
  callers that already have a runtime (deep mode's spawn machinery, the
  eventual registration step) can build off that same decision instead of
  re-deriving it. Availability falls out naturally either way: a request
  with no web-search provider configured never loads `web_search`, so no
  `web_agent` is planned and the definition is silently skipped.
- Children run through `AgentTool.handle()` → `AgentRuntime.run_child()`
  on the SAME runtime — the shared hook kernel (citations, result
  accumulation, SSE status) and event emitter see child tool calls
  exactly as they see top-level ones, and `RunContext.spawn_depth`
  guards recursion (coding_agent → web_agent is depth 2 of 3).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.tools.builtin.coordination.agent_tool import AgentTool
from app.agent_loop_lib.tools.errors import DuplicateToolNameError, DuplicateToolPathError
from app.agents.agent_loop.sub_agent_prompt import build_sub_agent_prompt

if TYPE_CHECKING:
    from app.agent_loop_lib.runtime.runtime import AgentRuntime
    from app.agent_loop_lib.tools.registry import ToolRegistry
    from app.agents.agent_loop.context import AgentContext

logger = logging.getLogger(__name__)

# Matches OrchestratorLoop's sub-agent cap: enough turns for a multi-step
# task plus a retry or two, low enough that a flailing child can't dominate
# the request's latency budget.
_DEFAULT_CHILD_MAX_TURNS = 8


@dataclass(frozen=True)
class DomainAgentDefinition:
    """Declarative description of one domain agent. Pure data — claiming
    lives in `plan_domain_agents()`; spec construction and registration
    live in `register_domain_agents()`."""

    name: str
    """LLM-facing tool name of the delegate, e.g. ``"coding_agent"``."""

    domain: str
    """Human label used in the child's system prompt ("web", "coding", ...)."""

    description: str
    """Tool description shown to the CALLING agent — this is the only thing
    the parent model sees, so it must say what to delegate and what comes back."""

    app_names: frozenset[str] = frozenset()
    """Claim every registered tool whose adapter `app_name` is in this set."""

    tool_names: frozenset[str] = frozenset()
    """Claim these exact registered tool names (for tools whose `app_name`
    is shared with unrelated tools, e.g. the synthetic ``"dynamic"`` bucket)."""

    delegate_agents: tuple[str, ...] = ()
    """Names of OTHER domain agents this agent may call as tools — wired
    only when the referenced agent was actually built for this request."""

    extra_instructions: str | None = None
    max_turns: int = _DEFAULT_CHILD_MAX_TURNS


DOMAIN_AGENT_DEFINITIONS: tuple[DomainAgentDefinition, ...] = (
    DomainAgentDefinition(
        name="web_agent",
        domain="web research",
        description=(
            "Delegate public-web research to a focused sub-agent that can search "
            "the web and fetch/read specific URLs. Give it ONE self-contained "
            "research question (include all context it needs — it cannot see this "
            "conversation). Returns a synthesized answer with source links."
        ),
        tool_names=frozenset({"dynamic_web_search", "dynamic_fetch_url"}),
    ),
    DomainAgentDefinition(
        name="coding_agent",
        domain="coding",
        description=(
            "Delegate a coding task to a focused sub-agent that writes and runs "
            "code in a sandbox, installs packages, and reads files it produced. "
            "Use for computation, data analysis/transformation, chart or file "
            "generation. Give it ONE self-contained goal stating exactly what to "
            "compute or produce and what the expected output looks like; include "
            "any input data inline. Returns the final result and paths of files "
            "it generated."
        ),
        tool_names=frozenset({"run_code", "install_packages", "read_sandbox_file"}),
        delegate_agents=("web_agent",),
    ),
    DomainAgentDefinition(
        name="internal_search_agent",
        domain="internal knowledge",
        description=(
            "Delegate a search over the organization's internal knowledge to a "
            "focused sub-agent that can run semantic retrieval over connected "
            "sources, browse the knowledge hub, and fetch full records. Give it "
            "ONE self-contained question (include names, dates, and identifiers "
            "it needs). Returns findings with record citations."
        ),
        app_names=frozenset({"retrieval", "knowledgehub"}),
        tool_names=frozenset({"dynamic_fetch_full_record"}),
    ),
    DomainAgentDefinition(
        name="calculator_agent",
        domain="calculation",
        description=(
            "Delegate arithmetic, unit, and date calculations to a focused "
            "sub-agent. Give it ONE self-contained calculation request with all "
            "numbers/dates inline. Returns the computed result."
        ),
        app_names=frozenset({"calculator", "date_calculator"}),
        max_turns=5,
    ),
    DomainAgentDefinition(
        name="calendar_agent",
        domain="calendar",
        description=(
            "Delegate calendar work to a focused sub-agent that can list, "
            "create, and update events and check availability on the user's "
            "connected calendars. Give it ONE self-contained request with "
            "explicit dates/times and attendees. Returns the outcome with event "
            "links."
        ),
        app_names=frozenset({"calendar", "google_calendar"}),
    ),
)


@dataclass(frozen=True)
class DomainAgentPlan:
    """The pure OUTCOME of claiming tools off a `ToolRegistry`: which
    domain agents WOULD be built and what the resulting top-level tool
    grant would be, computed with zero side effects — no `AgentSpec`, no
    `AgentTool`, no registry mutation. Callers that need to know the final
    top-level names before a runtime/loop exists (`factory.py`'s
    quick-mode planner, which must be steered with the SAME names the
    executing agent will end up with) can use `top_level_names` directly;
    `register_domain_agents()` later replays `claims`/`agent_names`
    unchanged to actually build things."""

    definitions: tuple[DomainAgentDefinition, ...]
    registered_names: tuple[str, ...]
    """Every tool name registered on the `ToolRegistry` this plan was
    computed from, at planning time — the residual grant is derived from
    this snapshot, not a live registry read, so it stays valid even after
    `register_domain_agents()` has added `AgentTool`s to that same registry."""
    claims: dict[str, list[str]] = field(default_factory=dict)
    """`{definition.name: [claimed tool names]}` for domains that claimed
    at least one tool — catalog order, first definition wins a tool."""

    @property
    def agent_names(self) -> list[str]:
        """Names of every domain agent that WOULD be built, catalog order."""
        return [d.name for d in self.definitions if d.name in self.claims]

    @property
    def top_level_names(self) -> list[str]:
        """The tool grant a top-level agent composed from this plan would
        end up with: domain-agent names + every tool no domain claimed."""
        claimed = {name for names in self.claims.values() for name in names}
        residual = [n for n in self.registered_names if n not in claimed]
        return [*self.agent_names, *residual]


def plan_domain_agents(
    tool_registry: "ToolRegistry",
    definitions: tuple[DomainAgentDefinition, ...] = DOMAIN_AGENT_DEFINITIONS,
) -> DomainAgentPlan:
    """Pure claim computation: assigns each registered tool to at most one
    domain (first definition wins, catalog order). No registration, no
    `AgentSpec` construction — safe to call before a runtime/loop exists
    (see `DomainAgentPlan`'s docstring) or purely to preview what
    `register_domain_agents()` would do."""
    claims: dict[str, list[str]] = {}
    claimed: set[str] = set()
    registered = tool_registry.names()
    for definition in definitions:
        names: list[str] = []
        for name in registered:
            if name in claimed:
                continue
            tool = tool_registry.resolve_by_name(name)
            app_name = getattr(tool, "app_name", None)
            if name in definition.tool_names or (app_name and app_name in definition.app_names):
                names.append(name)
                claimed.add(name)
        if names:
            claims[definition.name] = names
    return DomainAgentPlan(definitions=definitions, registered_names=tuple(registered), claims=claims)


def register_domain_agents(
    plan: DomainAgentPlan,
    tool_registry: "ToolRegistry",
    runtime: "AgentRuntime",
    context: "AgentContext | None" = None,
    *,
    provider: str,
    model_name: str,
) -> list[str]:
    """Materializes `plan`: builds one ReAct child `AgentSpec` per claimed
    domain and registers each as an `AgentTool` on `tool_registry`. Returns
    the tool names the TOP-LEVEL agent should actually be granted —
    ordinarily identical to `plan.top_level_names`, except a domain whose
    name collides with an already-registered tool is skipped (logged), and
    its claimed tools fall back to the residual grant instead of being
    silently dropped.
    """
    claims = plan.claims
    built: list[str] = []
    for definition in plan.definitions:
        claimed_names = claims.get(definition.name)
        if not claimed_names:
            continue
        delegate_names = [d for d in definition.delegate_agents if d in claims]
        spec = AgentSpec(
            name=definition.name,
            description=definition.description,
            system_prompt=build_sub_agent_prompt(
                definition.domain, context,
                extra_instructions=definition.extra_instructions,
            ),
            tool_names=[*claimed_names, *delegate_names],
            model=ModelSpec(provider=provider, model=model_name),
            loop=ReActLoop(),
            max_turns=definition.max_turns,
        )
        try:
            tool_registry.register_tool(
                AgentTool(spec, runtime, name=definition.name, description=definition.description)
            )
        except (DuplicateToolNameError, DuplicateToolPathError):
            logger.warning(
                "register_domain_agents: name %r already registered — skipping this domain agent",
                definition.name,
            )
            continue
        built.append(definition.name)
        logger.info(
            "register_domain_agents: built %s with %d tool(s) %s + delegates %s",
            definition.name, len(claimed_names), claimed_names, delegate_names,
        )

    # Only tools claimed by an agent that actually got built leave the
    # top level — a skipped registration must not orphan its tools. Uses
    # `plan.registered_names` (the pre-registration snapshot), not a fresh
    # `tool_registry.names()` read, so the newly-added AgentTool names
    # themselves can never leak into the residual list.
    all_claimed = {name for agent_name in built for name in claims[agent_name]}
    residual = [n for n in plan.registered_names if n not in all_claimed]
    return [*built, *residual]


def compose_domain_agents(
    tool_registry: "ToolRegistry",
    runtime: "AgentRuntime",
    context: "AgentContext | None" = None,
    *,
    provider: str,
    model_name: str,
    definitions: tuple[DomainAgentDefinition, ...] = DOMAIN_AGENT_DEFINITIONS,
) -> list[str]:
    """Convenience: plan + register in one call, for callers that don't
    need the plan/register split (e.g. tests, or a caller with no need to
    steer anything on the plan before registration happens)."""
    plan = plan_domain_agents(tool_registry, definitions)
    return register_domain_agents(
        plan, tool_registry, runtime, context, provider=provider, model_name=model_name,
    )


__all__ = [
    "DOMAIN_AGENT_DEFINITIONS",
    "DomainAgentDefinition",
    "DomainAgentPlan",
    "compose_domain_agents",
    "plan_domain_agents",
    "register_domain_agents",
]
