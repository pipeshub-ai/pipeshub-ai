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
from app.agent_loop_lib.modules.providers.skills.base import SkillSource
from app.agent_loop_lib.modules.providers.skills.evaluator import RubricSkillEvaluator
from app.agent_loop_lib.modules.providers.skills.extractor import LLMSkillExtractor
from app.agent_loop_lib.modules.providers.skills.governor import (
    AutoApproveGovernor,
    ManualReviewGovernor,
    SkillGovernor,
)
from app.agent_loop_lib.modules.providers.skills.manager import SkillManager, SkillManagerConfig
from app.agent_loop_lib.modules.providers.skills.validator import SkillValidator
from app.agent_loop_lib.tools.builtin.data.skills import (
    LoadSkillResourceTool,
    LoadSkillTool,
    SkillManageTool,
    SkillSearchTool,
    SkillsListTool,
)
from app.agent_loop_lib.tools.errors import DuplicateToolNameError, DuplicateToolPathError
from app.agent_loop_lib.transport.registry import LazyTransport
from app.agents.agent_loop.skills.audit_governor import AuditGovernor
from app.agents.agent_loop.skills.builtin_seeder import SEED_IDENTITY, BuiltinSkillSeeder
from app.agents.agent_loop.skills.graph_store import GraphSkillStore
from app.agents.agent_loop.skills.graph_tracker import GraphUsageTracker
from app.agents.agent_loop.skills.semantic_index import SemanticSkillIndex

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
_BUILTIN_PACKS_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills", "builtin_packs")

_builtin_seeder: BuiltinSkillSeeder | None = None
_builtin_seeder_load_failed = False

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


def _get_builtin_seeder() -> BuiltinSkillSeeder | None:
    """Parses + validates `builtin_packs/` at most once per process — every
    org's `build_skill_manager()` call reuses the same in-memory packs
    (see `BuiltinSkillSeeder`'s docstring: "parsed once, reused across
    orgs"). A load/validation failure is logged once and cached as a
    permanent no-op for the process rather than retried every request."""
    global _builtin_seeder, _builtin_seeder_load_failed
    if _builtin_seeder is None and not _builtin_seeder_load_failed:
        try:
            _builtin_seeder = BuiltinSkillSeeder(_BUILTIN_PACKS_ROOT)
        except Exception:
            _builtin_seeder_load_failed = True
            logger.exception("skills_wiring: failed to load builtin_packs/ — builtin skills disabled")
    return _builtin_seeder


async def _sync_builtin_skills(context: "AgentContext", manager: SkillManager) -> None:
    """Seeds/upgrades this org's per-org copies of the in-repo builtin
    skill packs, gated by a cheap version check so a fully up-to-date org
    never pays for a sync round-trip. Failures are swallowed (logged) —
    builtin seeding is an enhancement, not a hard dependency for skills to
    work at all this turn."""
    seeder = _get_builtin_seeder()
    if seeder is None:
        return
    current = {
        m.name: m.pack_version for m in manager.catalog_snapshot() if m.source == SkillSource.BUILTIN
    }
    if current == seeder.pack_versions:
        return
    seed_store = GraphSkillStore(context.graph_provider, context.org_id, SEED_IDENTITY)
    try:
        await seeder.sync(seed_store)
    except Exception:
        logger.exception("skills_wiring: builtin skill seeding failed for org %s", context.org_id)
        return
    await manager.refresh()


def _env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() == "true"


def _governor(config: SkillManagerConfig, context: "AgentContext") -> SkillGovernor:
    """Mirrors `manager.py::_default_governor` (private there — Interface
    Segregation means the manager itself doesn't need to know which
    governor it got) and layers `AuditGovernor` on top when a graph
    provider is available, per the plan's Phase 4."""
    base: SkillGovernor = (
        ManualReviewGovernor() if config.write_approval
        else AutoApproveGovernor() if config.auto_approve
        else ManualReviewGovernor()
    )
    if context.graph_provider is None:
        return base
    return AuditGovernor(base, context.graph_provider, context.org_id, context.user_id)


async def build_skill_manager(
    context: "AgentContext", transport_registry: "TransportRegistry",
) -> SkillManager | None:
    """`None` when this request has no graph provider wired — the store
    hard-depends on `IGraphDBProvider` (unlike the filesystem store used
    for CLI/dev), so skills are unavailable rather than silently degraded
    to some other backend. Callers must check for `None` and skip the
    rest of this module's wiring."""
    if context.graph_provider is None:
        logger.warning(
            "skills_wiring: PIPESHUB_ENABLE_SKILLS is on but no graph_provider is set on "
            "this request's context — skills will not be available this turn"
        )
        return None

    config = SkillManagerConfig(
        auto_approve=_env_bool("PIPESHUB_SKILLS_AUTO_APPROVE", False),
        # Governance-safe default for an enterprise product: candidates
        # queue for review rather than auto-persisting (see manager.py's
        # `_default_governor` docstring for why write_approval always wins).
        write_approval=_env_bool("PIPESHUB_SKILLS_WRITE_APPROVAL", True),
        learning_enabled=_env_bool("PIPESHUB_SKILLS_LEARNING_ENABLED", True),
        catalog_render_limit=int(os.getenv("PIPESHUB_SKILLS_CATALOG_RENDER_LIMIT", "40")),
    )

    store = GraphSkillStore(context.graph_provider, context.org_id, context.user_id)
    index = SemanticSkillIndex(context.retrieval_service)
    tracker = GraphUsageTracker(context.graph_provider, context.org_id, context.user_id)
    extractor = (
        LLMSkillExtractor(LazyTransport(transport_registry, "langchain"))
        if config.learning_enabled else None
    )
    evaluator = RubricSkillEvaluator(index=index)

    manager = SkillManager(
        store=store, index=index, tracker=tracker, validator=SkillValidator(),
        extractor=extractor, evaluator=evaluator, governor=_governor(config, context), config=config,
    )
    await manager.start()
    await _sync_builtin_skills(context, manager)
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
