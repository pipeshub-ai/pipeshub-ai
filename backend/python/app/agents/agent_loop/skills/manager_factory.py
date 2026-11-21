"""Single composition root for `SkillManager` construction (Dependency
Inversion — both the agent runtime, via `skills_wiring.py`, and the REST
management API, via `api/routes/skills.py`, depend on this module's
factory functions and their `SkillManager` return type; neither builds
`GraphSkillStore`/`SemanticSkillIndex`/`GraphUsageTracker`/`SkillGovernor`
itself).

Two profiles, one assembly path (`_build_manager`):

- `build_runtime_skill_manager` — the agent-loop profile: the running
  user's own skills, the builtins, and the skills the agent being run
  assigns (`SkillVisibility`; an agent's `ScopedSkillManager` layer then
  narrows the catalog to that assignment — see `scoped_manager.py`),
  plus the learning-loop `LLMSkillExtractor`. Builtin packs are seeded at
  service startup (`reconcile_builtin_skills_for_orgs`); a request only
  schedules a background reconcile for an org that startup missed.
- `build_management_skill_manager` — the REST profile: creator-scoped
  reads (management endpoints only ever act on the caller's own skills;
  builtins stay visible), no extractor (`learn_from_execution` is a
  runtime-only concern the REST surface never calls), and no *automatic*
  builtin seeding baked into construction — `GET /api/v1/skills`
  (`list_skills`) is the one management route that opts back in by
  calling `sync_builtin_skills` itself, so a brand-new org sees builtin
  skills in the UI without needing its first chat turn first. Every other
  management route (create/update/delete/...) stays pure.

Both profiles share the exact same store/index/tracker/governor stack, so
governance, versioning, and audit behavior can never drift between "what
an agent sees this turn" and "what the management UI shows" beyond the
one deliberate difference (what is shared through an agent) above.
"""

from __future__ import annotations

import asyncio
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
from app.agent_loop_lib.modules.providers.skills.manager import (
    SkillManager,
    SkillManagerConfig,
)
from app.agent_loop_lib.modules.providers.skills.validator import SkillValidator
from app.agents.agent_loop.skills.audit_governor import AuditGovernor
from app.agents.agent_loop.skills.builtin_seeder import (
    SEED_IDENTITY,
    BuiltinSkillSeeder,
)
from app.agents.agent_loop.skills.graph_store import (
    SharedSkillRights,
    SkillVisibility,
)
from app.agents.agent_loop.skills.graph_tracker import GraphUsageTracker
from app.agents.agent_loop.skills.offer import skill_viewers
from app.agents.agent_loop.skills.semantic_index import SemanticSkillIndex
from app.core.org_jobs import for_each_org
from app.services.featureflag.platform_settings import is_skills_enabled
from app.utils.env_flags import (
    PIPESHUB_SKILLS_AUTO_APPROVE,
    PIPESHUB_SKILLS_LEARNING_ENABLED,
    PIPESHUB_SKILLS_WRITE_APPROVAL,
    env_flag,
)
from app.utils.logging_fields import log_event, log_once

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.agent_loop_lib.modules.providers.skills.base import SkillProvenance
    from app.agent_loop_lib.transport.registry import TransportRegistry
    from app.agents.agent_loop.context import AgentContext
    from app.agents.agent_loop.skills.ledger_manager import SkillOrigins
    from app.config.configuration_service import ConfigurationService
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

__all__ = [
    "skills_manager_config",
    "build_governor",
    "get_builtin_seeder",
    "sync_builtin_skills",
    "reconcile_builtin_skills",
    "reconcile_builtin_skills_for_orgs",
    "schedule_builtin_skill_reconcile",
    "reset_builtin_reconcile_state",
    "build_runtime_skill_manager",
    "build_management_skill_manager",
    "runtime_visibility",
    "run_user_key",
]

logger = logging.getLogger(__name__)

_BUILTIN_PACKS_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills", "builtin_packs",
)

_builtin_seeder: BuiltinSkillSeeder | None = None
_builtin_seeder_load_failed = False

# org_id -> pack-versions key this process last reconciled that org at.
_reconciled_orgs: dict[str, tuple[tuple[str, str], ...]] = {}
_reconcile_tasks: dict[str, asyncio.Task] = {}


def skills_manager_config() -> SkillManagerConfig:
    """Env-driven config, identical for both profiles — governance/limits
    are an org/deployment policy, not something that should vary between
    "the agent this turn" and "the management UI"."""
    return SkillManagerConfig(
        auto_approve=env_flag(PIPESHUB_SKILLS_AUTO_APPROVE),
        write_approval=env_flag(PIPESHUB_SKILLS_WRITE_APPROVAL),
        learning_enabled=env_flag(PIPESHUB_SKILLS_LEARNING_ENABLED),
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


def _versions_key(seeder: BuiltinSkillSeeder) -> tuple[tuple[str, str], ...]:
    return tuple(sorted(seeder.pack_versions.items()))


def _is_reconciled(org_id: str, seeder: BuiltinSkillSeeder) -> bool:
    return _reconciled_orgs.get(org_id) == _versions_key(seeder)


def reset_builtin_reconcile_state() -> None:
    """Test hook: forget which orgs this process has reconciled."""
    _reconciled_orgs.clear()
    _reconcile_tasks.clear()


async def _reconcile(graph_provider: "IGraphDBProvider", org_id: str) -> None:
    seeder = get_builtin_seeder()
    if seeder is None or _is_reconciled(org_id, seeder):
        return
    from app.edition_services import skill_store

    seed_store = skill_store(
        graph_provider, org_id, SEED_IDENTITY, visibility=SkillVisibility.everyone(), allow_builtin_writes=True,
    )
    try:
        settled = await seeder.sync(seed_store)
    except Exception:
        logger.exception("skills: builtin skill seeding failed for org %s", org_id)
        return
    if settled:
        _reconciled_orgs[org_id] = _versions_key(seeder)
    else:
        logger.warning("skills: builtin skill seeding incomplete for org %s; the next request retries", org_id)


def _reconcile_task(graph_provider: "IGraphDBProvider", org_id: str) -> asyncio.Task:
    """The org's one in-flight reconcile, started if none is running, so startup,
    agent requests and the skills listing never sync the same org twice at once."""
    task = _reconcile_tasks.get(org_id)
    if task is None:
        task = asyncio.get_running_loop().create_task(_reconcile(graph_provider, org_id))
        _reconcile_tasks[org_id] = task

        def _forget(done: asyncio.Task) -> None:
            if _reconcile_tasks.get(org_id) is done:
                del _reconcile_tasks[org_id]

        task.add_done_callback(_forget)
    return task


async def reconcile_builtin_skills(graph_provider: "IGraphDBProvider", org_id: str) -> None:
    """Seeds/upgrades this org's copies of the in-repo builtin packs, at most
    once per (org, pack versions) per process. Idempotent (the seeder upserts),
    so concurrent workers converge; never raises — a failure, or a pack the
    seeder could not write, is logged and retried on the next call."""
    seeder = get_builtin_seeder()
    if seeder is None or _is_reconciled(org_id, seeder):
        return
    # Shielded: a caller that is cancelled must not cancel the reconcile others share.
    await asyncio.shield(_reconcile_task(graph_provider, org_id))


async def reconcile_builtin_skills_for_orgs(
    graph_provider: "IGraphDBProvider", config_service: "ConfigurationService",
) -> None:
    """Service-startup reconcile for every active org with skills switched on
    (sequential: this is background work and should not compete with request
    traffic), each in its own org context, where its switch is read too. A
    deleted org is inactive and keeps no data, so it is not seeded. Never raises: a failure is logged, and requests retry per org."""
    async def reconcile(org_id: str) -> None:
        if await is_skills_enabled(config_service):
            await reconcile_builtin_skills(graph_provider, org_id)

    try:
        await for_each_org(graph_provider, "builtin_skills", reconcile, logger, active=True)
    except Exception as e:
        logger.warning("skills: builtin skill seeding did not run at startup (requests will retry): %s", type(e).__name__)


def schedule_builtin_skill_reconcile(graph_provider: "IGraphDBProvider", org_id: str) -> None:
    """Request-path hook for orgs the startup reconcile did not cover (created
    since, or startup failed): starts one background reconcile per org and
    returns immediately."""
    seeder = get_builtin_seeder()
    if seeder is None or _is_reconciled(org_id, seeder):
        return
    _reconcile_task(graph_provider, org_id)


async def sync_builtin_skills(
    graph_provider: "IGraphDBProvider", org_id: str, manager: SkillManager,
) -> None:
    """Awaited variant for `GET /api/v1/skills`, so a fresh org sees builtin
    skills in the UI immediately; refreshes `manager` after a sync. Free once
    the org is reconciled for the current pack versions."""
    seeder = get_builtin_seeder()
    if seeder is None or _is_reconciled(org_id, seeder):
        return
    current = {
        m.name: m.pack_version for m in manager.catalog_snapshot() if m.source == SkillSource.BUILTIN
    }
    if current == seeder.pack_versions:
        _reconciled_orgs[org_id] = _versions_key(seeder)
        return
    await reconcile_builtin_skills(graph_provider, org_id)
    if _is_reconciled(org_id, seeder):
        await manager.refresh()


async def _build_manager(
    graph_provider: "IGraphDBProvider",
    org_id: str,
    user_id: str,
    config: SkillManagerConfig,
    *,
    retrieval_service: Any,
    extractor_transport: "TransportRegistry | None",
    visibility: SkillVisibility,
    rights: SharedSkillRights | None = None,
    provenance: Callable[[], SkillProvenance] | None = None,
    origins: "SkillOrigins | None" = None,
    own_skills: bool = True,
    **store_options: object,
) -> SkillManager:
    from app.edition_services import skill_store

    store = skill_store(
        graph_provider, org_id, user_id, visibility=visibility, rights=rights, provenance=provenance,
        also_visible=visibility.shared, origins=origins, own_skills=own_skills,
        **store_options,
    )
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


def runtime_visibility(context: "AgentContext") -> SkillVisibility:
    """The same on both loops: the durable worker fills `AgentContext` as the query service does."""
    return SkillVisibility.of(skill_viewers(context), shared=context.agent_skills or ())


async def run_user_key(context: "AgentContext") -> str | None:
    """The asker's graph user key, the kind of id a skill's `createdBy` holds: the one the route read
    with the user's details, else read from the user record (and kept on the context). None when there
    is no such user or the read failed. `context.user_id` is the account id, never a stand-in for it."""
    key = getattr(context, "user_key", "") or ""
    if key:
        return key
    from app.edition_config import user_lookup_scope
    from app.utils.user_lookup import get_caller_user

    try:
        user = await get_caller_user(
            context.graph_provider, context.user_id, **user_lookup_scope(context.org_id or None),
        )
    except Exception as e:
        log_event(logger, "skills.user_key_failed", level=logging.WARNING, error_class=type(e).__name__)
        return None
    key = user.get("_key") if isinstance(user, dict) else None
    if not isinstance(key, str) or not key:
        return None
    context.user_key = key
    return key


async def build_runtime_skill_manager(
    context: "AgentContext", transport_registry: "TransportRegistry",
    *, provenance: "Callable[[], SkillProvenance] | None" = None,
    origins: "SkillOrigins | None" = None, own_skills: bool = True,
) -> SkillManager | None:
    """Full profile for the agent loop. `None` when this request has no
    graph provider wired — the store hard-depends on `IGraphDBProvider`,
    so skills are unavailable rather than silently degraded to some other
    backend. Callers must check for `None` and skip the rest of the
    skills wiring. `provenance`: the run every
    write is stamped with (a durable run's label and conversation,
    FOLLOWUPS1-1). `origins` is told where each skill the store reads came
    from, for the answer's label. `own_skills=False` leaves the person's own
    skills out of a run whose answer others read."""
    if context.graph_provider is None:
        if log_once("agent_wiring.skills_no_graph_provider"):
            log_event(logger, "skills.unavailable", level=logging.WARNING, reason="no_graph_provider")
        return None

    config = skills_manager_config()
    manager = await _build_manager(
        context.graph_provider, context.org_id, context.user_id, config,
        retrieval_service=context.retrieval_service,
        extractor_transport=transport_registry,
        visibility=runtime_visibility(context),
        provenance=provenance,
        origins=origins,
        own_skills=own_skills,
    )
    schedule_builtin_skill_reconcile(context.graph_provider, context.org_id)
    return manager


async def build_management_skill_manager(
    graph_provider: "IGraphDBProvider",
    org_id: str,
    user_id: str,
    retrieval_service: Any,
    *,
    visibility: SkillVisibility | None = None,
    rights: SharedSkillRights | None = None,
    **store_options: object,
) -> SkillManager:
    """Management profile for the REST API: creator-scoped reads (see
    `SkillVisibility`; the routes widen them to the caller's team and org
    commands), no learning-loop extractor, no
    builtin-pack seeding side effect. Never returns `None` — routes call
    this only once `graph_provider` is already known to exist (same
    `get_services()` guard every other agent route uses).

    `store_options` are what the edition's store takes beyond these
    (`app.edition_services.skill_store`)."""
    config = skills_manager_config()
    return await _build_manager(
        graph_provider, org_id, user_id, config,
        retrieval_service=retrieval_service,
        extractor_transport=None,
        visibility=visibility or SkillVisibility.of([user_id]),
        rights=rights,
        **store_options,
    )
