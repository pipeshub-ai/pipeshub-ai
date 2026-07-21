"""Phase 3 of the plan: wires agent_loop_lib's Skills subsystem (Phase 1's
library extensions + Phase 2's `app/agents/agent_loop/skills/` adapters)
into `PipesHubAgentFactory.create()`. Kept in its own module (rather than
inlined into `factory.py`) so the already-long factory stays readable and
this feature's env gate/wiring is one grep away.

Entirely gated by `PIPESHUB_ENABLE_SKILLS` (default ON) — same rollout
convention as `PIPESHUB_USE_COMPOSED_AGENTS`/`PIPESHUB_USE_AGENT_LOOP`
elsewhere in this adapter layer: a deployment-level opt-OUT, not an
opt-in, so it stays on unless explicitly disabled (e.g. for a deployment
whose graph DB hasn't provisioned the `agentSkills*` collections/indexes
yet — see `app/schema/arango/documents.py`, `node_schema_registry.py`).
`build_skill_manager()` below still requires a `graph_provider` on the
request regardless of this flag; the flag alone never turns the feature
on for a request that has none.

Four call sites in `factory.py`, in this order:

1. `skills_enabled()` — the gate itself.
2. `build_skill_manager()` — right after `tool_registry` is built, before
   `plan_domain_agents()` runs (see below for why ordering matters).
3. `register_skill_tools()` — same spot, so the 5 skill tools land in
   `plan_domain_agents()`'s registered-tool snapshot. They claim no
   `app_name`/`tool_name` any `DomainAgentDefinition` claims (see
   `domain_agents.py`), so they always fall into the RESIDUAL grant and
   stay on the top-level agent under composition — no `domain_agents.py`
   change needed for THAT; they are meta-capabilities, not a domain.
   The four read-only ones (`DOMAIN_SHARED_SKILL_TOOL_NAMES`, everything
   but the `skill_manage` write surface) are ADDITIONALLY passed as
   `register_domain_agents()`'s `shared_tool_names` so every domain child
   gets them too, not just the top level — otherwise a domain-scoped
   child's own `skill_preloading` pass (see that middleware's docstring)
   could only ever inject a skill's full body, never a "call load_skill
   if relevant" pointer, since it would have no `load_skill` to call.
4. `register_skill_preloading()` — added to `hooks` inside `_build_hooks`
   (PRE_AGENT, deterministic intent-match preload).
5. `register_skill_learning()` — added AFTER `AgentRuntime` is
   constructed (needs `runtime.run_child()` for the skill_writer
   sub-agent) — same `HookRegistry` instance already passed into that
   `AgentRuntime`, so appending to it post-construction is equivalent to
   having registered it earlier.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.hooks.events import HookEvent
from app.agent_loop_lib.hooks.middleware.builtin.skill_learning import SkillLearning
from app.agent_loop_lib.hooks.middleware.builtin.skill_preloading import skill_preloading
from app.agent_loop_lib.modules.providers.skills.manager import SkillManager
from app.agent_loop_lib.tools.builtin.data.skills import (
    LoadSkillResourceTool,
    LoadSkillTool,
    SkillManageTool,
    SkillSearchTool,
    SkillsListTool,
)
from app.agent_loop_lib.tools.errors import DuplicateToolNameError, DuplicateToolPathError
from app.agents.agent_loop.skills.manager_factory import build_runtime_skill_manager

if TYPE_CHECKING:
    from app.agent_loop_lib.hooks.registry import HookRegistry
    from app.agent_loop_lib.runtime.runtime import AgentRuntime
    from app.agent_loop_lib.tools.registry import ToolRegistry
    from app.agent_loop_lib.transport.registry import TransportRegistry
    from app.agents.agent_loop.context import AgentContext

__all__ = [
    "skills_enabled",
    "build_skill_manager",
    "register_skill_tools",
    "register_skill_preloading",
    "register_skill_learning",
    "DOMAIN_SHARED_SKILL_TOOL_NAMES",
]

logger = logging.getLogger(__name__)

_SKILL_TOOL_NAMES = ("skills_list", "load_skill", "load_skill_resource", "skill_search", "skill_manage")

# Granted to EVERY domain agent (see `domain_agents.py::register_domain_agents`'s
# `shared_tool_names`), not just kept in the top level's residual grant —
# read-only discovery/fetch only, so a domain-scoped child (e.g.
# `coding_agent`) can act on its own `skill_preloading` pointer instead of
# that pointer being a dead end (see `skill_preloading.py`'s
# `_can_load_skills_on_demand`). `skill_manage` deliberately stays OUT:
# it is the write surface (create/edit/patch/delete a skill), which is a
# governance decision (`SkillManagerConfig.write_approval`), not something
# every scoped sub-agent should be able to trigger just by writing PDF
# code.
DOMAIN_SHARED_SKILL_TOOL_NAMES = frozenset({"skills_list", "load_skill", "load_skill_resource", "skill_search"})
_SKILL_WRITER_MAX_TURNS = 8

# Adapted from `roles/builtin/skill_writer.py::SKILL_WRITER_ROLE` — cannot
# reuse it verbatim because that role's prompt ends with "call
# task_complete(...)" and PipesHubToolLoader never registers that tool;
# on this path a run ends the same way every other PipesHub agent turn
# does (a text-only, no-tool-call response — see `agent/loops.py::
# ReActLoop`), so the last rule is rewritten instead of pointing at a
# nonexistent tool.
_SKILL_WRITER_SYSTEM_PROMPT = (
    "You are a skill-distillation agent. You will be given a proposed skill — a name, "
    "a description, and an instructions body — already extracted from a real agent run "
    "that used it successfully. Your job is to refine and persist it, not invent one from "
    "scratch.\n\n"
    "- Pick (or keep) a concise, specific, lowercase kebab-case name (letters, digits, "
    "single hyphens only) — e.g. 'summarize-pdf-report', not 'helper' or 'skill-1'.\n"
    "- The description states WHEN to use this skill (what kind of request it matches), "
    "not what it does internally.\n"
    "- The body is step-by-step instructions that generalize the pattern — name the "
    "actual tools involved, but phrase it for any future goal of the same shape, not the "
    "one specific run it came from.\n"
    "- Before creating, check whether a similar skill already exists — call "
    "skill_search(query=<the proposed description>) once. If a near-duplicate exists, "
    "prefer skill_manage(action='edit', ...) or skill_manage(action='patch', ...) against "
    "it instead of creating a redundant new skill.\n"
    "- Do not invent tools that were not in the observed pattern. Be concise.\n\n"
    "Call skill_manage(action='create', name=..., description=..., body=..., category=..., "
    "subcategory=..., tags=...) exactly once (or an 'edit'/'patch' action against an "
    "existing near-duplicate) to persist the skill, then reply with a one-line summary of "
    "what you did — do not call any other tool afterward."
)


def skills_enabled() -> bool:
    """Kill-switch for the whole subsystem."""
    return os.getenv("PIPESHUB_ENABLE_SKILLS", "true").strip().lower() == "true"


async def build_skill_manager(
    context: "AgentContext", transport_registry: "TransportRegistry",
) -> SkillManager | None:
    """Composition (store/index/tracker/governor assembly, builtin-pack
    seeding) lives in `manager_factory.py`'s `build_runtime_skill_manager`,
    shared with the REST management API's `build_management_skill_manager`
    so the two profiles can never drift apart on anything but the one
    deliberate difference (creator visibility scope) documented there.

    When this request's agent has an explicit skill assignment
    (`context.agent_skills` — see `AgentContext`/`_parse_skills` in
    `api/routes/agent.py`), the returned manager is additionally wrapped in
    `ScopedSkillManager` (Phase 4 of the plan) so the prompt overview and
    all 5 skill tools only ever see/load that allowlist. An agent with NO
    explicit assignment (empty/None) keeps today's behavior — the full
    creator+builtin catalog, unfiltered."""
    manager = await build_runtime_skill_manager(context, transport_registry)
    if manager is None:
        return None
    agent_skill_names = getattr(context, "agent_skills", None)
    if agent_skill_names:
        from app.agents.agent_loop.skills.scoped_manager import ScopedSkillManager

        return ScopedSkillManager(manager, set(agent_skill_names))
    return manager


def register_skill_tools(tool_registry: "ToolRegistry", manager: SkillManager) -> None:
    """Registers the 5 skill tools + a `"skills"` toolset group. Swallows
    a name/path collision (should never happen — none of PipesHub's own
    tools use these names) rather than failing request construction over
    an optional feature."""
    try:
        tool_registry.register_tool(SkillsListTool(manager))
        tool_registry.register_tool(LoadSkillTool(manager))
        tool_registry.register_tool(LoadSkillResourceTool(manager))
        tool_registry.register_tool(SkillSearchTool(manager))
        tool_registry.register_tool(SkillManageTool(manager))
    except (DuplicateToolNameError, DuplicateToolPathError):
        logger.exception("skills_wiring: skill tool name/path collision — skills tools not registered")
        return
    tool_registry.register_toolset(
        "skills", "Search, load, and manage the skill library.", list(_SKILL_TOOL_NAMES),
    )


def register_skill_preloading(hooks: "HookRegistry", manager: SkillManager) -> None:
    """PRE_AGENT — deterministic intent-based preload, see
    `skill_preloading.py`'s module docstring for the tools-vs-middleware
    tradeoff this resolves."""
    hooks.on(HookEvent.PRE_AGENT).use(skill_preloading(
        manager,
        preload_body_threshold=float(os.getenv("PIPESHUB_SKILLS_PRELOAD_BODY_THRESHOLD", "0.75")),
        mention_threshold=float(os.getenv("PIPESHUB_SKILLS_PRELOAD_MENTION_THRESHOLD", "0.4")),
    ))


def register_skill_learning(
    hooks: "HookRegistry", manager: SkillManager, runtime: "AgentRuntime",
    *, provider: str, model_name: str,
) -> None:
    """POST_AGENT — outcome tracking always runs; candidate extraction
    only actually does anything when `SkillManagerConfig.learning_enabled`
    (checked inside `SkillManager.learn_from_execution`), so this is safe
    to register unconditionally once `manager` exists — turning learning
    on/off is a config change, not a wiring one. Must be called AFTER
    `runtime` is constructed (`spawn_skill_writer` closes over it for
    `run_child()`), but the same `HookRegistry` instance `runtime.hooks`
    already holds, so appending here is equivalent to having registered
    it earlier."""

    async def _spawn_skill_writer(goal_description: str):
        from app.agent_loop_lib.core.types import Goal

        registered = runtime.tool_registry.names() if runtime.tool_registry is not None else []
        spec = AgentSpec(
            name="skill_writer",
            description="Distills a candidate skill from a finished run and persists it via skill_manage.",
            system_prompt=_SKILL_WRITER_SYSTEM_PROMPT,
            tool_names=[n for n in ("skill_search", "skill_manage") if n in registered],
            model=ModelSpec(provider=provider, model=model_name),
            loop=ReActLoop(),
            max_turns=_SKILL_WRITER_MAX_TURNS,
        )
        return await runtime.run_child(spec, Goal(description=goal_description), parent_run_ctx=None)

    hooks.on(HookEvent.POST_AGENT).use(SkillLearning(manager=manager, spawn_skill_writer=_spawn_skill_writer))
