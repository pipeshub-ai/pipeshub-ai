"""Single composition root for `SkillManager` construction (Dependency
Inversion — both the agent runtime, via `skills_wiring.py`, and the REST
management API, via `api/routes/skills.py`, depend on this module's
factory functions and their `SkillManager` return type; neither builds
`GraphSkillStore`/`SemanticSkillIndex`/`GraphUsageTracker`/`SkillGovernor`
itself).

Two profiles, one assembly path (`_build_manager`):

- `build_runtime_skill_manager` — the agent-loop profile: full visibility
  (no creator scope; an agent's own `ScopedSkillManager` layer, not this
  factory, narrows the catalog to an assignment — see `scoped_manager.py`),
  plus the learning-loop `LLMSkillExtractor` and builtin-pack seeding.
- `build_management_skill_manager` — the REST profile: creator-scoped
  reads (management endpoints only ever act on the caller's own skills;
  builtins stay visible), no extractor (`learn_from_execution` is a
  runtime-only concern the REST surface never calls), no builtin seeding
  (seeding is a per-turn runtime side effect, not something a management
  read/write call should trigger).

Both profiles share the exact same store/index/tracker/governor stack, so
governance, versioning, and audit behavior can never drift between "what
an agent sees this turn" and "what the management UI shows" beyond the
one deliberate difference (visibility scope) above.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

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
from app.agents.agent_loop.skills.audit_governor import AuditGovernor
from app.agents.agent_loop.skills.builtin_seeder import SEED_IDENTITY, BuiltinSkillSeeder
from app.agents.agent_loop.skills.graph_store import GraphSkillStore
from app.agents.agent_loop.skills.graph_tracker import GraphUsageTracker
from app.agents.agent_loop.skills.semantic_index import SemanticSkillIndex

if TYPE_CHECKING:
    from app.agent_loop_lib.transport.registry import TransportRegistry
    from app.agents.agent_loop.context import AgentContext
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

__all__ = [
    "skills_manager_config",
    "build_governor",
    "get_builtin_seeder",
    "sync_builtin_skills",
    "build_runtime_skill_manager",
    "build_management_skill_manager",
]

logger = logging.getLogger(__name__)

_BUILTIN_PACKS_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills", "builtin_packs",
)

_builtin_seeder: BuiltinSkillSeeder | None = None
_builtin_seeder_load_failed = False


def _env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() == "true"


def skills_manager_config() -> SkillManagerConfig:
    """Env-driven config, identical for both profiles — governance/limits
    are an org/deployment policy, not something that should vary between
    "the agent this turn" and "the management UI"."""
    return SkillManagerConfig(
        auto_approve=_env_bool("PIPESHUB_SKILLS_AUTO_APPROVE", False),
        write_approval=_env_bool("PIPESHUB_SKILLS_WRITE_APPROVAL", True),
        learning_enabled=_env_bool("PIPESHUB_SKILLS_LEARNING_ENABLED", True),
        catalog_render_limit=int(os.getenv("PIPESHUB_SKILLS_CATALOG_RENDER_LIMIT", "40")),
    )


def build_governor(
    config: SkillManagerConfig,
    graph_provider: "IGraphDBProvider | None",
    org_id: str,
    user_id: str,
) -> SkillGovernor:
    """Mirrors `manager.py::_default_governor` (kept private there — the
    manager itself doesn't need to know which concrete governor it got)
    and layers `AuditGovernor` on top whenever a graph provider is
    available, regardless of which profile is asking."""
    base: SkillGovernor = (
        ManualReviewGovernor() if config.write_approval
        else AutoApproveGovernor() if config.auto_approve
        else ManualReviewGovernor()
    )
    if graph_provider is None:
        return base
    return AuditGovernor(base, graph_provider, org_id, user_id)


def get_builtin_seeder() -> BuiltinSkillSeeder | None:
    """Parses + validates `builtin_packs/` at most once per process — every
    org's manager construction reuses the same in-memory packs. A load/
    validation failure is logged once and cached as a permanent no-op for
    the process rather than retried every request."""
    global _builtin_seeder, _builtin_seeder_load_failed
    if _builtin_seeder is None and not _builtin_seeder_load_failed:
        try:
            _builtin_seeder = BuiltinSkillSeeder(_BUILTIN_PACKS_ROOT)
        except Exception:
            _builtin_seeder_load_failed = True
            logger.exception("skills: failed to load builtin_packs/ — builtin skills disabled")
    return _builtin_seeder


async def sync_builtin_skills(
    graph_provider: "IGraphDBProvider", org_id: str, manager: SkillManager,
) -> None:
    """Seeds/upgrades this org's per-org copies of the in-repo builtin
    skill packs, gated by a cheap version check so a fully up-to-date org
    never pays for a sync round-trip. Failures are swallowed (logged) —
    builtin seeding is an enhancement, never a hard dependency for skills
    to work at all this turn."""
    seeder = get_builtin_seeder()
    if seeder is None:
        return
    current = {
        m.name: m.pack_version for m in manager.catalog_snapshot() if m.source == SkillSource.BUILTIN
    }
    if current == seeder.pack_versions:
        return
    seed_store = GraphSkillStore(graph_provider, org_id, SEED_IDENTITY)
    try:
        await seeder.sync(seed_store)
    except Exception:
        logger.exception("skills: builtin skill seeding failed for org %s", org_id)
        return
    await manager.refresh()


async def _build_manager(
    graph_provider: "IGraphDBProvider",
    org_id: str,
    user_id: str,
    config: SkillManagerConfig,
    *,
    retrieval_service: Any,
    extractor_transport: "TransportRegistry | None",
    visibility_scope: str | None = None,
) -> SkillManager:
    store = GraphSkillStore(graph_provider, org_id, user_id, visibility_scope=visibility_scope)
    index = SemanticSkillIndex(retrieval_service)
    tracker = GraphUsageTracker(graph_provider, org_id, user_id)
    extractor = None
    if extractor_transport is not None and config.learning_enabled:
        from app.agent_loop_lib.transport.registry import LazyTransport

        extractor = LLMSkillExtractor(LazyTransport(extractor_transport, "langchain"))
    evaluator = RubricSkillEvaluator(index=index)

    manager = SkillManager(
        store=store, index=index, tracker=tracker, validator=SkillValidator(),
        extractor=extractor, evaluator=evaluator,
        governor=build_governor(config, graph_provider, org_id, user_id), config=config,
    )
    await manager.start()
    return manager


async def build_runtime_skill_manager(
    context: "AgentContext", transport_registry: "TransportRegistry",
) -> SkillManager | None:
    """Full profile for the agent loop. `None` when this request has no
    graph provider wired — the store hard-depends on `IGraphDBProvider`,
    so skills are unavailable rather than silently degraded to some other
    backend. Callers must check for `None` and skip the rest of the
    skills wiring."""
    if context.graph_provider is None:
        logger.warning(
            "skills: PIPESHUB_ENABLE_SKILLS is on but no graph_provider is set on this "
            "request's context — skills will not be available this turn"
        )
        return None

    config = skills_manager_config()
    manager = await _build_manager(
        context.graph_provider, context.org_id, context.user_id, config,
        retrieval_service=context.retrieval_service,
        extractor_transport=transport_registry,
        visibility_scope=None,
    )
    await sync_builtin_skills(context.graph_provider, context.org_id, manager)
    return manager


async def build_management_skill_manager(
    graph_provider: "IGraphDBProvider",
    org_id: str,
    user_id: str,
    retrieval_service: Any,
) -> SkillManager:
    """Management profile for the REST API: creator-scoped reads (see
    `GraphSkillStore.visibility_scope`), no learning-loop extractor, no
    builtin-pack seeding side effect. Never returns `None` — routes call
    this only once `graph_provider` is already known to exist (same
    `get_services()` guard every other agent route uses)."""
    config = skills_manager_config()
    return await _build_manager(
        graph_provider, org_id, user_id, config,
        retrieval_service=retrieval_service,
        extractor_transport=None,
        visibility_scope=user_id,
    )
