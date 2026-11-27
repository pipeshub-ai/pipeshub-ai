"""PipesHub's composition of durable engine runs (E1): what ``EngineKernel`` asks its host for.

``PipesHubEngineAssembler`` builds one attempt of an engine run from the
pinned spec, as the loop's assembler does (the run's context, tool
environment, sources and catalog, its approvals), then narrows the catalog to
what an engine may call: the pinned profile's effect classes, without the
loop's own coordination tools. Around it:

* the run's tool endpoint: ``RunToolEndpoint`` over the kernel's
  ``JournalEffectLog`` and approval hold, ``ENGINE_APPROVALS`` under the
  profile's and the org's layers, citations journaled with each outcome;
* a controller bound to this attempt: Node's workspace store and writer lease
  through the attempt's routes, the session's objects sealed with the key Node
  leases to this attempt (engine state included, so erasing the session
  shreds it), the worker's tool gateway at an address the proxy reaches;
* artifacts published through the attempt's fenced commit (non-critical),
  each card shown on the task's card as it changes;
* the presenter (``PipesHubEngineRunPresenter``) reading this attempt's stores.
"""

from __future__ import annotations

import asyncio
import dataclasses
import functools
import os
from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import TYPE_CHECKING, Any, cast, override

from app.agents.agent_loop.artifact_provenance import SANDBOX_TOOL_NAMES
from app.agents.agent_loop.connected_sources import ConnectedSources
from app.harness.activity import journals_commands
from app.harness.approvals import engine_guarded, layer_ask_answers
from app.harness.artifacts.events import EventPublicationLog, presented_event
from app.harness.artifacts.registry_store import RegistryArtifactStore
from app.harness.assembly.assembler import (
    PipesHubRuntimeAssembler,
    first_use_preparer_of,
    late_effect,
    lazy_servers,
    load_run_tools,
    run_memory,
)
from app.harness.assembly.context import pinned_parent_asks
from app.harness.assembly.disclosure import mcp_at_first_use
from app.harness.assembly.loop import model_name
from app.harness.assembly.model import admitted, helper_model_scope, metered
from app.harness.assembly.teardown import close_environment
from app.harness.assembly.tool_environment import build_tool_environment
from app.harness.citations import EVENT as CITATIONS_EVENT
from app.harness.citations import CitationJournal
from app.harness.classification import (
    ModelGuard,
    classification_guarded,
    classification_port,
    level_source,
    record_ranking,
    tool_state_records,
)
from app.harness.engines.answer import EngineAnswer
from app.harness.engines.capability_limits import (
    InputsAccepted,
    limits_instructions,
    run_limits,
)
from app.harness.engines.child_result import EngineChildResult
from app.harness.engines.deliverable_saves import DeliverableSaves
from app.harness.engines.git_flow import (
    ref_links,
    run_checkout,
    run_repository,
    with_pull_request,
)
from app.harness.engines.inputs import run_input_source
from app.harness.engines.instructions import (
    engine_answer_language,
    engine_task_instructions,
    engine_time_line,
)
from app.harness.engines.org_references import attempt_references
from app.harness.engines.presenter import PipesHubEngineRunPresenter, SessionRevisions
from app.harness.engines.record_fetch import (
    EngineRecordFetchTool,
    resolve_record_label,
    surfaced_record_ids,
)
from app.harness.external_policy import external_policy_gate
from app.harness.gateway.citations import JournalCitationSink
from app.harness.grounding import card_answers
from app.harness.knowledge.access_gate import knowledge_access_gate
from app.harness.knowledge.bulk_export import (
    export_catalog,
    export_effects,
    export_provenance,
)
from app.harness.labels import (
    SessionSourceLedger,
    call_provenance,
    inherited_fields,
    inherited_grants,
    inherited_label,
)
from app.harness.memories import recalled_event_for, source_free
from app.harness.quarantine import bind_summarizer
from app.harness.repositories import (
    REPOSITORY_FETCH,
    REPOSITORY_PUSH,
    CacheStatusPublisher,
    EgressControl,
    RepositoryAsks,
    RepositoryCatalog,
    RepositoryFetchTools,
    RepositoryPushTools,
    SessionRepositories,
    deployment_fetcher,
    named_repositories,
    named_repositories_line,
    org_reachability,
    push_effect,
    repository_effect,
    repository_instructions,
)
from app.harness.run_gates import RunCitationSources
from app.harness.run_policy.resolved import bind_run_tuning
from app.harness.schedules import SCHEDULE_CREATE_TOOL, schedule_asks, schedule_reader
from app.harness.site_uploads import SiteUploads
from app.harness.skills.pinned_labels import (
    MountedSkills,
    mounted_skills,
    mounted_skills_instructions,
)
from app.harness.step_display import GatewaySteps
from app.harness.tool_provenance import added_rules
from app.harness.web_reachability import PageReview
from app.harness.websites import LiveFlowHold, RunSites, sites_instructions
from app.harness.workspace.node_store import (
    NodeKeyProvider,
    NodeWorkspaceStore,
    NodeWorkspaceWriters,
)
from app.harness.write_identity.ports import write_identity_port
from app.harness.write_identity.settings import use_write_identity
from app.services.artifact_registry.models import Actor
from app.services.featureflag.platform_settings import bind_pinned_stages
from app.telemetry.modules.harness_gateway_metrics import READ_CACHE_METRICS
from app.utils.citations import WEB_CITATION_STYLE, WebCitationStyle
from app.utils.run_model_scope import hold_run_model_scope
from harness_core import bindings
from harness_core.access.types import spec_route_id
from harness_core.activity import BoundedCommandActivity
from harness_core.artifacts.publisher import WorkspaceArtifactPublisher
from harness_core.budget.metered import MeteredTransport
from harness_core.budget.types import RunAccountRef
from harness_core.budget.wrap_up import WrapUpReserve, WrapUpSize
from harness_core.effects import EffectRunner
from harness_core.effects.flow import GrantingLedger
from harness_core.engines.checkpoints import ObjectStoreEngineCheckpoints
from harness_core.engines.controller import EngineController, KeptSandboxWatches
from harness_core.engines.durable.ports import AssembledEngineRun, SilentPresenter
from harness_core.engines.durable.tool_pin import EngineToolPin
from harness_core.engines.durable.wrap_up import ModelEngineWrapUp
from harness_core.engines.git_checkout import GitCheckout, GitSetupFailed
from harness_core.engines.native_policy import NativeCallPolicy
from harness_core.engines.outcome.deliverable import pinned_deliverable
from harness_core.engines.policy import (
    engine_default_approvals,
    tool_effect_allowed,
)
from harness_core.engines.signin import PlanSignIn, SignInRefused
from harness_core.engines.snapshots import ObjectStoreSandboxSnapshots
from harness_core.engines.usage import AccountUsageLedger
from harness_core.gates.gateway import gated_endpoint
from harness_core.gates.instructions import gated_instructions
from harness_core.gates.spec import dry_run, verify_gate
from harness_core.gates.verify import VerifyPorts
from harness_core.gateway import LazyDisclosure, Ranked, RunToolEndpoint
from harness_core.gateway.asking import asks_person
from harness_core.gateway.types import RunAttemptBinding, grant_digest
from harness_core.ids import RandomIds
from harness_core.invoker import InvocationScope
from harness_core.journal.types import (
    KNOWLEDGE_EXPORTS,
    PROVENANCE_RECORDS,
    REPOSITORY_FETCHES,
    REPOSITORY_NOT_CLONED,
    SITE_UPLOAD_ASKS,
    NewEvent,
)
from harness_core.kernel.budget.pricing import get_context_window
from harness_core.kernel.hooks.registry import HookRegistry
from harness_core.knowledge.bulk_export import ShelfRelay
from harness_core.logs import HarnessLog
from harness_core.memories import (
    MemoryUnavailable,
    RenderedMemories,
    render_memories,
    with_memory,
)
from harness_core.provenance.rules import MCP_RULE
from harness_core.provenance.stamp import ProvenanceStamp
from harness_core.security.classification import UNCONFIGURED, Classification
from harness_core.skills.materializer import TarSkillMaterializer
from harness_core.skills.types import SkillPin
from harness_core.telemetry.memory import NOOP_TRACER
from harness_core.telemetry.metrics import NOOP_GATEWAY_METRICS
from harness_core.tools.call_tool import CALL_TOOL
from harness_core.tools.durable_port import RegistryToolPort
from harness_core.tools.errors import ToolNotFoundError
from harness_core.tools.read_cache import ReadScope, RunReadCache, cacheable_reads
from harness_core.tools.tags import (
    TAG_LIFECYCLE_TERMINAL,
    TAG_SPAWN,
    TAG_SPAWN_BATCH,
    TAG_UI_ONLY,
)
from harness_core.workspace.forks import session_scope
from harness_core.workspace.sealing import SealedObjectStore
from harness_core.workspace.sync.strategies import SyncStrategies
from harness_core.workspace.sync.tar_sync import TarWorkspaceSync
from harness_core.workspace.sync.types import write_back_limits
from harness_core.workspace.types import ObjectScope
from harness_engines.pipeshub import knowledge_tool

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable, Mapping

    from pydantic import JsonValue

    from app.agents.agent_loop.mcp_lazy import LazyMcpServers
    from app.harness.assembly.assembler import EnvironmentFactory, ModelFactory
    from app.harness.assembly.context import RunContext
    from app.harness.assembly.model import ResolvedModel
    from app.harness.assembly.services import RunServices
    from app.harness.assembly.tool_sources import ToolSource
    from app.harness.journal_node import AttemptRoute, NodeClaim
    from app.harness.run_policy.select import RunPolicies
    from app.harness.write_identity.disclosure import WriteIdentitySettings
    from harness_core.activity import CommandActivity
    from harness_core.artifacts.types import ArtifactCard
    from harness_core.clock import Clock
    from harness_core.effects.flow import FlowRule
    from harness_core.effects.types import ToolEffect
    from harness_core.engines.controller import EngineControllerConfig
    from harness_core.engines.durable.ports import EngineAttempt, EngineRunPresenter
    from harness_core.engines.ports import EngineRegistry, ToolGatewayHost
    from harness_core.engines.signin import SignInHosts, SignInRelay
    from harness_core.engines.types import EngineProfile
    from harness_core.gateway.priority_gate import ProviderAdmission
    from harness_core.interactions.ports import RunBinding
    from harness_core.journal.ports import ContentStorePort, LiveSinkPort
    from harness_core.journal.types import AttemptToken
    from harness_core.kernel.durable.ports import ApprovalPolicy
    from harness_core.provenance.rules import ProvenanceRule
    from harness_core.sandbox.ports import SandboxBroker
    from harness_core.security.labels import SourceLedger
    from harness_core.security.reachability import PublicReachability
    from harness_core.skills.ports import SkillBundleBuilder
    from harness_core.telemetry.metrics import GatewayMetrics
    from harness_core.telemetry.ports import HarnessTracer
    from harness_core.tools.base import Tag
    from harness_core.tools.registry import ToolRegistry
    from harness_core.workspace.ports import BinaryObjectStore

LOGGER = "app.harness.engines.durable"
# Coordination and disclosure tools are the loop's own; an engine gets the grant's tools only. The loop's code
# sandbox too: an engine runs code in its own shell, so what a program writes lands in the workspace it
# publishes; ``run_code``'s files stay in another sandbox (EMPTYOUT).
_META_TOOLS = frozenset({"list_toolsets", "fetch_tools", "search_tools", CALL_TOOL, *SANDBOX_TOOL_NAMES})
# Loop tools an engine never gets: planning/coordination builtins and skills, wherever they are registered.
_LOOP_MODULES = (
    "app.agent_loop_lib.tools.builtin",
    "harness_core.tools.builtin",
    "app.agents.agent_loop.skills",
)
# Tools that talk to the person or steer the loop (ask the user, end the turn, spawn): the loop's own, which an engine
# cannot drive. An engine asks through its gateway's ``ask_user`` instead (C3.70). A tag, not a module:
# ``ask_user_question`` is a toolset tool tagged ``effect=read``, so the module rule alone granted it (S1c).
_LOOP_INTERACTIVE = frozenset({TAG_UI_ONLY, TAG_LIFECYCLE_TERMINAL, TAG_SPAWN, TAG_SPAWN_BATCH})


def is_loop_internal(tool: object, tags: Iterable[Tag] = ()) -> bool:
    return (
        hasattr(tool, "handle")
        or type(tool).__module__.startswith(_LOOP_MODULES)
        or not _LOOP_INTERACTIVE.isdisjoint(tags)
    )


@dataclass(frozen=True)
class DurableEngineServices:
    """What every engine attempt of a worker shares."""

    broker: SandboxBroker
    tool_host: ToolGatewayHost
    engines: EngineRegistry
    config: EngineControllerConfig
    # The deployment's object store (local, S3, Azure); each attempt seals over it with the session's key.
    objects: BinaryObjectStore
    route_for: Callable[[AttemptToken], AttemptRoute]
    clock: Clock
    placeholder_key: bytes = field(repr=False)
    # Skills a run pins, resolved and seeded by the controller; ``None``: the run gets none.
    skill_bundles_for: Callable[[ObjectScope, BinaryObjectStore, NodeWorkspaceStore, str], SkillBundleBuilder] | None = None
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    # P9: spans of sandbox acquires and tool calls (``HARNESS_OTEL``; none by default).
    tracer: HarnessTracer = NOOP_TRACER
    # C20.48: the Tool Gateway's tool-call metrics (none by default).
    gateway_metrics: GatewayMetrics = NOOP_GATEWAY_METRICS
    # The activity bus (UX5): where the engine's shell commands stream their output; ``None``: tails only.
    activity: LiveSinkPort | None = None
    # SIGNIN: where a person's sign-in code reaches this worker, and the signed hosts its sandbox may tunnel to;
    # either ``None`` and a run pinned on a person's own plan is refused (``signin_unavailable``).
    signin_relay: SignInRelay | None = None
    signin_hosts: SignInHosts | None = None
    # SIGNIN-5: the worker's watches over the signed-in sandboxes it kept between turns (one set for all attempts).
    parked: KeptSandboxWatches = field(default_factory=KeptSandboxWatches)


class TaskCardPublicationLog(EventPublicationLog):
    """The run's publication events through the attempt's fenced commit; a card is shown on the task's card
    in the same commit, so drafts appear while the engine works (U1's open item)."""

    def __init__(self, attempt: EngineAttempt, presenter: EngineRunPresenter) -> None:
        super().__init__(lambda _binding, event: attempt.append([event]), lambda _binding: attempt.run_events())
        self._attempt = attempt
        self._presenter = presenter

    @override
    async def presented(self, binding: RunBinding, card: ArtifactCard) -> None:
        try:
            shown = self._presenter.artifact(self._attempt.stamp(), card)
        except RuntimeError:
            shown = []
        await self._attempt.append([presented_event(card), *shown])


def _tool_tags(registry: ToolRegistry, name: str) -> list[Tag]:
    """The tags of a tool the run's registry holds (its declared cost, C3.71); none for any other."""
    try:
        tool = registry.resolve_by_name(name)
    except ToolNotFoundError:
        return []
    tags = getattr(tool, "tags", None)
    return list(tags) if isinstance(tags, list | tuple) else []


def _tool_display_name(registry: ToolRegistry, name: str) -> str | None:
    """The display name of a tool the run's registry holds, for the chat's step; ``None`` for any other."""
    try:
        tool = registry.resolve_by_name(name)
    except ToolNotFoundError:
        return None
    display = getattr(tool, "display_name", None)
    return display if isinstance(display, str) and display else None


ENGINE_LAZY_TOOLS_THRESHOLD = "PIPESHUB_ENGINE_LAZY_TOOLS_THRESHOLD"


def run_repository_hosts(
    policy: object, *, web: bool
) -> Callable[[], Awaitable[list[Mapping[str, JsonValue]]]] | None:
    """The run's git hosts, read at each call: the policy's fold for a run with or without web access, which is what
    the release's default presets ride on (ADR-REPO-01). None for a policy that holds none."""
    hosts = getattr(policy, "allowed_repository_hosts", None)
    return None if hosts is None else functools.partial(hosts, web=web)


async def told_repository_hosts(
    hosts: Callable[[], Awaitable[list[Mapping[str, JsonValue]]]] | None, log: HarnessLog
) -> list[Mapping[str, JsonValue]]:
    """The git hosts the engine is told it may clone from as its run starts (WP-70); none when they cannot be read
    (each clone still checks the hosts at its call)."""
    if hosts is None:
        return []
    try:
        return list(await hosts())
    except Exception as exc:
        log.warning("engine.repository_hosts_unread", errorType=type(exc).__name__)
        return []


def engine_lazy_tools_threshold() -> int:
    """Higher than the loop's 20: an engine pays a turn per search, worth it once its list costs ~15 k tokens a
    request (MCP-E2E §9). Blank reads as unset; a bad value refuses rather than running on a guess."""
    raw = (os.getenv(ENGINE_LAZY_TOOLS_THRESHOLD) or "").strip()
    if not raw:
        return 50
    if not raw.isdigit():
        raise ValueError(f"{ENGINE_LAZY_TOOLS_THRESHOLD}={raw!r} must be a whole number of tools")
    return int(raw)


def engine_lazy_port(
    registry: ToolRegistry, catalog: Any, lazy: LazyMcpServers, allowed: Callable[[ToolEffect], bool],  # noqa: ANN401
) -> dict[str, Any]:
    """The engine port's arguments for MCP tools built at the run's first use (D-LAZY-3/4), as the loop's port takes
    them: named in the grant, built before a call runs, described from the live listing and held to the engine's
    profile then, ``external`` and pinned in ``mcp.pins`` with the outcome."""
    deferred = {t.name: t.tool_id for t in catalog.tools if t.deferred}

    def late(name: str) -> ToolEffect | None:
        effect = late_effect(registry, name, deferred, lazy)
        return effect if effect is not None and allowed(effect) else None

    return {
        "deferred": deferred,
        "prepare": first_use_preparer_of(lazy, registry),
        "late": late,
        "late_integrity": lambda name: "external" if lazy.owns(name) else None,
        "after_call": lazy.drain,
        "more_names": lazy.granted_names,
    }


def engine_disclosure(
    registry: ToolRegistry,
    granted: Iterable[str],
    essential: Iterable[str],
    mode: str | None = None,
    *,
    sources: ConnectedSources | None = None,
    lazy: LazyMcpServers | None = None,
) -> LazyDisclosure | None:
    """LZ1: above the engine threshold, the grant's connector and MCP tools (members of a toolset that is not
    essential, as the loop hides them) are found with ``search_tools`` instead of listed; ``None``: all listed.

    ``mode`` is the agent's choice (C5.01, the pin's ``toolDisclosure.mode``): ``eager`` lists every tool, ``lazy``
    defers those tools whatever the count, and ``filesystem`` defers every granted tool but the skills, so the
    engine lists the meta tools and reads each tool's schema under ``/pipeshub/tools``.

    ``sources`` (D-DISC-2): the grant's connected sources, which ``search_tools``' description names. The search ranks
    as the loop's does (``ToolsetAwareToolIndex``), by what a source covers as well as by tool names.

    ``lazy`` (D-LAZY-4): the run's MCP servers listed at first use. Their tools are always found through the search,
    whatever the count; the search lists a server it names that has no tool named yet, and builds what it found."""
    from app.agent_loop_lib.tools.index import ToolsetAwareToolIndex
    from app.agents.agent_loop.lazy_tools_wiring import lazy_tools_enabled

    names = set(granted)
    if mode == "eager" or not lazy_tools_enabled():
        return None
    first_use = lazy is not None and bool(lazy)
    # On-demand toolsets (ADR-SCH-03 D8) are always found through the search and never counted toward the threshold.
    on_demand = registry.on_demand_tool_names() & names
    small = mode is None and len(names - on_demand) <= engine_lazy_tools_threshold()
    if small and not first_use and not on_demand:
        return None
    kept = {"skills"} if mode == "filesystem" else {"skills", *essential}
    grouped = {n for g in registry.toolsets() for n in g.tool_names}
    deferred = frozenset(
        set()
        if small
        else {n for g in registry.toolsets() if g.name not in kept for n in g.tool_names if n in names}
        | ({n for n in names if n not in grouped} if mode == "filesystem" else set())
    ) | on_demand | (lazy.granted_names() if lazy is not None else frozenset())

    async def rank(query: str, limit: int) -> Ranked:
        """The grant's matches, best first. Each is built before it counts toward ``limit`` (D-LAZY-4): a match its
        server refused at first use is named with why, and never takes an allowed match's place."""
        searchable = names | (lazy.granted_names() if lazy is not None else set())
        if lazy is not None:
            await lazy.warm(query)
            searchable |= lazy.granted_names()
        # The index ranks every registered tool: ungranted ones are filtered out before any limit applies.
        found = await ToolsetAwareToolIndex().search(registry, query, limit=len(registry.discover()))
        ranked: list[str] = []
        held: dict[str, str] = {}
        for name in (m.summary.name for m in found if m.summary.name in searchable):
            if len(ranked) >= limit:
                break
            if lazy is not None:
                await registry.materialize_many([name])
                refused = registry.refusal(name)
                if refused is not None:
                    held[name] = refused.message
                    continue
            ranked.append(name)
        return Ranked(ranked, held)

    # The on-demand lines first: the description names at most SOURCES_LISTED_MAX lines.
    lines = (*_on_demand_lines(registry, on_demand), *(sources.lines() if sources is not None else ()))
    return LazyDisclosure(deferred, rank, lines, always=first_use) if deferred or first_use else None


def _on_demand_lines(registry: ToolRegistry, on_demand: frozenset[str]) -> list[str]:
    """An on-demand toolset's one line for ``search_tools``' description, so the engine knows what it can find."""
    return [f"{g.name}: {g.description}" for g in registry.toolsets() if g.on_demand and set(g.tool_names) & on_demand]


def engine_checkout(claim: NodeClaim, context: Any) -> GitCheckout | None:  # noqa: ANN401 - the legacy AgentContext
    """The repository the run's spec names (GIT-FLOW), with the person's toolset credential sealed per phase."""
    from app.harness.sandbox.egress_credentials import credential_sealer

    repository = run_repository(claim.spec)
    if repository is None:
        return None
    owner = claim.run.actor_id if claim.run is not None else ""
    try:
        checkout = run_checkout(
            repository, session_id=claim.session_id, context=context, owner=owner, sealer=credential_sealer()
        )
        # G-03: a dry run's engine reads the repository and never pushes it.
        return dataclasses.replace(checkout, push=False) if dry_run(claim.spec) else checkout
    except ValueError as exc:
        raise GitSetupFailed(str(exc), reason="git_repository_invalid") from None


def engine_grant(registry: ToolRegistry, effects: dict[str, ToolEffect], allowed: Callable[[ToolEffect], bool]) -> dict[str, ToolEffect]:
    """What an engine may call of a run's catalog: the profile's classes, never the loop's own tools."""
    granted: dict[str, ToolEffect] = {}
    for name, effect in effects.items():
        if name in _META_TOOLS or not allowed(effect):
            continue
        if is_loop_internal(registry.resolve_by_name(name), registry.tags_for_name(name)):
            continue
        granted[name] = effect
    return granted


def _allowed(profile: EngineProfile, effect: ToolEffect) -> bool:
    return tool_effect_allowed(profile, effect.effect)


def with_record_fetch(
    registry: ToolRegistry,
    granted: dict[str, ToolEffect],
    context: Any,  # noqa: ANN401 - the legacy AgentContext
    allowed: Callable[[ToolEffect], bool],
    log: HarnessLog,
) -> dict[str, ToolEffect]:
    """S1c on the durable path: the run's full-record read is in an engine's grant from the start when the run has
    internal search (an engine lists its tools once, at connect, so a tool the loop's hooks would add later is never
    offered), limited to the records this run's own knowledge calls surfaced. The run's environment is its own (no
    parent request shares it), so what it surfaced is what this run was shown."""
    from app.agents.agent_loop.hooks.citations import full_record_reader

    reader = full_record_reader(context, registry)
    if reader is None or reader.name in granted:
        return granted
    state = context.tool_state
    fetch = EngineRecordFetchTool(
        reader,
        shown=lambda: surfaced_record_ids(state),
        resolve=lambda record_id: resolve_record_label(state, record_id),
        log=log,
    )
    registry.register_tool(fetch)
    effect = late_effect(registry, fetch.name)
    if effect is None or not allowed(effect):
        return granted
    return {**granted, fetch.name: effect}


def present_for_engines(context: Any) -> None:  # noqa: ANN401 - the legacy AgentContext
    """The run's tools present web results to an engine as it uses them (ENGCITE): the page's real link, which it
    may open, write into a file or pass to a tool, and the Citation ID its answer cites, as two fields. Its web tools
    draw on the run's own allowance, not a chat answer's: an engine run is the long, many-source work DEEPROUTE hands
    off, and its USD budget is what limits it (WP-62)."""
    context.tool_state[WEB_CITATION_STYLE] = WebCitationStyle.SEPARATE
    context.web_scope = "run"


def attempt_binding(claim: NodeClaim) -> RunAttemptBinding:
    """The binding Node's gateway token names for this attempt (its grant digests are the pin's).

    Node mints run tokens with no parent, a child's included: a child's spend nests under its parent through the
    ledger's own link (P6.5), not through the token."""
    return RunAttemptBinding(
        tenant=claim.org_id,
        scope=claim.session_id,
        run_id=claim.run_id,
        parent_run_id=None,
        attempt_id=claim.attempt_id,
        fence=claim.fence,
        tool_ids_digest=grant_digest(g.tool_id for g in claim.spec.grants.tools),
        # A pinned account is part of a route's id (Node ``modelRouteId``): without it a run on a user's own key
        # holds a token for another binding, and the Tool Gateway refuses every call it makes.
        routes_digest=grant_digest(spec_route_id(r) for r in claim.spec.model.policy),
    )




class PipesHubEngineAssembler:
    def __init__(
        self,
        services: RunServices,
        engines: DurableEngineServices,
        *,
        model_factory: ModelFactory | None = None,
        environment_factory: EnvironmentFactory = build_tool_environment,
        sources: Callable[[], list[ToolSource]] | None = None,
        policies: RunPolicies | None = None,
        admission: ProviderAdmission | None = None,
    ) -> None:
        """``admission``: where the run's own in-process model calls (its wrap-up answer) take their provider slot,
        as its engine's calls do at the model gateway (G-45)."""
        self._services = services
        self._engines = engines
        self._model_factory = model_factory
        self._environment_factory = environment_factory
        self._loop = PipesHubRuntimeAssembler(services, sources=sources, policies=policies, admission=admission)
        # One control-plane client for every run: it reads the proxy's address per call and keeps no run's state, so
        # a client per run only left a connection pool open for each (R46LANE-FU-3).
        self._egress_control = EgressControl(services.config_service())

    async def aclose(self) -> None:
        """Close the egress proxy's control-plane client; with the worker, after its runs."""
        await self._egress_control.aclose()

    async def build(self, attempt: EngineAttempt) -> AssembledEngineRun:
        claim: NodeClaim = attempt.claim  # type: ignore[assignment]
        container = self._loop.container(claim)
        run = container.run_context()
        durable = container.bindings()
        log = HarnessLog.for_claim(LOGGER, claim)
        policy = self._loop.policies.for_run(run, log)
        bind_pinned_stages(policy.stages())
        approvals = await policy.approvals()
        org_policy = policy.live(await policy.engine_governance(approvals, attempt.profile))
        engine_default = engine_default_approvals()
        tuning = policy.tuning()
        # What the attempt's close ends before its tools close (a running bulk export).
        closers: list[Callable[[], Awaitable[None]]] = []
        # G-35 and WP-63: the turn whose sandbox holds the shelf now (exports, hand-offs, pushes, upload releases).
        shelf = ShelfRelay()
        with bindings.scoped(durable):
            resolved = await (self._model_factory or policy.model)(run, self._services)
            model = admitted(resolved, self._loop.admission, claim.priority_class, claim.spec.run_kind, org=run.org_id)
            meter = self._loop.helper_meter(run, claim)
            # Bound to the conversation's level with the run's ledger (``_runtime``); the engine's own calls are
            # checked at the model gateway, this process's under this guard (WP-55).
            guard = classification_port().model_guard(log)
            # The engine's own calls are metered at the model gateway; its tools' helpers call in this process,
            # through the run's model admitted and metered here on the same account (WP-49).
            env = await self._environment_factory(
                run, self._services, guard.wrap(meter(resolved, model_name(run)), model_name(run))
            )
            env.helper_models = helper_model_scope(run, meter, guard)
            bind_run_tuning(env.context.tool_state, tuning)
            # What a tool call has retrieved, before the ledger has it, is what its helpers are checked at (WP-55).
            guard.reads_from(tool_state_records(env.context.tool_state))
            hold_run_model_scope(env.helper_models)
            present_for_engines(env.context)
            run = env.narrowed(run)
            # G-58: an engine's knowledgegraph__summarize reaches the run's own model, metered as its gateway calls are,
            # and is checked by the run's one guard (bound with the run's ledger and ranking below).
            summarizing, _ = metered(
                model, accounts=claim.budget, account=RunAccountRef(run.org_id, run.run_id),
                pricing_model=model_name(run), ids=RandomIds(),
            )
            _ = bind_summarizer(env.context.tool_state, summarizing, model_id=model_name(run), guard=guard)
            try:
                # Before the tools load: who the run's writes are from (OWD-18, an extension point).
                await write_identity_port().prepare(run, env, policy, log)
                # QA6-F7: a run whose pin grants a memory write scope proposes through the gateway, whatever runs it.
                tools = await load_run_tools(
                    self._services,
                    container,
                    env,
                    run,
                    claim,
                    memory=run_memory(run, claim, log),
                    check_skills=False,
                    plan=False,
                    log=log,
                    lazy_mcp=mcp_at_first_use(policy.tool_disclosure(), deep=False),
                )
                registry, catalog = tools.registry, tools.catalog
                allowed = functools.partial(_allowed, attempt.profile)
                granted = with_record_fetch(registry, engine_grant(registry, catalog.effects(), allowed), env.context, allowed, log)
                write_identity = await write_identity_port().settings(run, env, policy, log)
                checkout = engine_checkout(claim, env.context)
                granted = with_pull_request(
                    registry, granted, checkout, ref_links(env.context), write_identity, allowed, log,
                    tool_state=env.context.tool_state,
                )
                content = await container.content_store()
                citations = CitationJournal(env.context, registry=registry, log=log)
                await citations.restore(await attempt.run_events())
                memory = await engine_memory(run, claim, log)
                mounted = await mounted_skills(
                    _pins(claim) if self._engines.skill_bundles_for is not None else (),
                    env.context.graph_provider,
                    claim,
                    log,
                )
                commands = BoundedCommandActivity(
                    self._engines.activity,
                    run_id=claim.run_id,
                    attempt_id=claim.attempt_id,
                    fence=claim.fence,
                    clock=self._engines.clock,
                    content=content,
                    log=log,
                )
                external_policy = await external_policy_gate().prepare(self._services.config_service(), claim.org_id, log)
                repository_hosts = run_repository_hosts(policy, web=env.web_search_provider is not None)
                runtime = self._runtime(
                    attempt, claim, log, env, registry, catalog, granted, citations, (engine_default, org_policy),
                    content, memory, commands, mounted, flow_rules=approvals.flow_rules,
                    external_policy=external_policy,
                    closers=closers,
                    tool_disclosure=policy.tool_disclosure(), classification=approvals.classification,
                    write_identity=write_identity, model_guard=guard,
                    repository_hosts=repository_hosts,
                    reachability=org_reachability(policy),
                    checkout=checkout,
                    shelf=shelf,
                    lazy=lazy_servers(tools.sources),
                    allowed=allowed,
                    run=run,
                    conversation_grants=tuning.conversation_grants,
                    input_max_mb=tuning.input_max_mb,
                )
            except BaseException:
                await close_environment(env.context, log)
                raise
        runtime = dataclasses.replace(
            runtime,
            wrap_up=engine_wrap_up(model, run, claim, log, guard),
            usage=AccountUsageLedger(claim.budget, RunAccountRef(run.org_id, run.run_id)) if claim.budget else None,
            snapshot_ttl=timedelta(days=policy.snapshot_ttl_days()),
            checkout=checkout,
            result_cap_chars=tuning.result_cap_chars,
            resource_caps=tuning.resources,
        )
        # WP-63: the run's allowed websites, folded again at each turn; the engine is told the ones it starts with.
        # The upload card: a read-write site held after a private read asks the person once per run and site.
        egress_control = self._egress_control
        uploads = SiteUploads(
            run_id=claim.run_id,
            append=attempt.append,
            run_events=attempt.run_events,
            stamp=attempt.stamp,
            presenter=runtime.presenter or SilentPresenter(),
            ledger=runtime.ledger,
            control=egress_control,
            sandbox=shelf,
            journal_asks=claim.journal_accepts(SITE_UPLOAD_ASKS),
            log=log,
        )
        closers.append(uploads.close)
        sites = RunSites(
            policy, approvals.classification, runtime.ledger, claim.run_id, tuple(approvals.flow_rules), log,
            uploads=uploads,
        )
        # What the engine is told it starts with; the turns' own reads ask the person, not this one.
        told = await dataclasses.replace(sites, uploads=None)()
        hosts = await told_repository_hosts(repository_hosts, log)
        # ADR-RUN-03 D5: what the run cannot do is said in its brief and carried in its hand-back.
        limits = run_limits(web=env.web_search_provider is not None, repository_hosts=hosts, unavailable=env.unavailable)
        # R41-CLONE: the repositories the task names, each told as its clone command and, never cloned, said so.
        named = named_repositories(claim.input_text, hosts)
        typed = claim.journal_accepts(REPOSITORY_NOT_CLONED)
        accepted = InputsAccepted.of(claim.journal_accepts)
        if named:
            log.info("engine.repositories_named", named=len(named), allowed=sum(r.allowed for r in named))
        if isinstance(runtime.presenter, PipesHubEngineRunPresenter):
            runtime.presenter.expect_repositories(named, typed=typed)
            runtime.presenter.expect_inputs(accepted)
        # SEC-A-02: a gateway result that makes the conversation hold more narrows the live sandbox before it is read.
        live = LiveFlowHold(sites, egress_control, shelf, log)
        runtime = dataclasses.replace(
            runtime,
            sites=live.sites,
            egress_flow=live.narrow,
            instructions=runtime.instructions
            + sites_instructions(told)
            + repository_instructions(hosts)
            + limits_instructions(limits),
            child_result=(
                runtime.child_result.with_limits(limits, named=named, typed=typed, accepted=accepted)
                if isinstance(runtime.child_result, EngineChildResult)
                else runtime.child_result
            ),
            task_facts="\n\n".join(
                line for line in (named_repositories_line(named), engine_time_line(run.prompt_time, run.timezone)) if line
            ),
        )
        log.info(
            "engine.assembly",
            granted=len(granted),
            withheld=len(catalog.effects()) - len(granted),
            skills=len(runtime.skills),
            engine=attempt.profile.engine.kind,
        )

        async def close() -> None:
            for closer in closers:
                await closer()
            await commands.aclose()
            await tools.items.close()
            await close_environment(env.context, log)

        return dataclasses.replace(runtime, close=close)

    def _runtime(
        self,
        attempt: EngineAttempt,
        claim: NodeClaim,
        log: HarnessLog,
        env: Any,  # noqa: ANN401 - the legacy ToolEnvironment
        registry: ToolRegistry,
        catalog: Any,  # noqa: ANN401 - the loop assembler's Catalog
        granted: dict[str, ToolEffect],
        citations: CitationJournal,
        approvals: tuple[ApprovalPolicy, ApprovalPolicy],
        content: ContentStorePort,
        memory: RenderedMemories,
        commands: CommandActivity,
        mounted: MountedSkills,
        *,
        flow_rules: tuple[FlowRule, ...] = (),
        external_policy: object | None = None,
        tool_disclosure: str | None = None,
        classification: Classification = UNCONFIGURED,
        write_identity: WriteIdentitySettings | None = None,
        closers: list[Callable[[], Awaitable[None]]] | None = None,
        repository_hosts: Callable[[], Awaitable[list[Mapping[str, JsonValue]]]] | None = None,
        reachability: Callable[[], Awaitable[PublicReachability]] | None = None,
        checkout: GitCheckout | None = None,
        model_guard: ModelGuard | None = None,
        shelf: ShelfRelay | None = None,
        conversation_grants: bool = True,
        input_max_mb: int | None = None,
        lazy: LazyMcpServers | None = None,
        allowed: Callable[[ToolEffect], bool] = lambda _effect: True,
        run: RunContext,
    ) -> AssembledEngineRun:
        engines = self._engines
        route = engines.route_for(claim.token)
        # G-35: the run's bulk exports write onto the shelf of whichever turn's sandbox holds it.
        shelf = shelf if shelf is not None else ShelfRelay()
        workspaces = NodeWorkspaceStore(route)
        objects = SealedObjectStore(engines.objects, NodeKeyProvider(route))
        # C20.18 / C20.20: the org's references, through this attempt's own token (an extension point; none here).
        references = attempt_references(route, engines.objects)
        config = replace(
            engines.config,
            environment_snapshots=references.environment_snapshots,
            shared_caches=references.shared_caches,
        )
        controller = EngineController(
            registry=engines.engines,
            broker=engines.broker,
            workspaces=workspaces,
            # One number for the largest file in and out (J-26): what a run may be given, it may write back changed.
            sync_for=lambda scope: TarWorkspaceSync(
                objects=objects,
                scope=scope,
                clock=engines.clock,
                limits=write_back_limits(engines.config.input_max_bytes),
            ),
            strategies=SyncStrategies(),
            skill_bundles=self._skills(objects, workspaces, claim),
            skill_materializer=TarSkillMaterializer(objects=objects, workspaces=workspaces),
            checkpoints=ObjectStoreEngineCheckpoints(objects),
            snapshots=ObjectStoreSandboxSnapshots(objects),
            sink=attempt.sink,
            tool_host=engines.tool_host,
            clock=engines.clock,
            placeholder_key=engines.placeholder_key,
            config=config,
            writers=NodeWorkspaceWriters(route),
            sleep=engines.sleep,
            tracer=engines.tracer,
            signin_relay=engines.signin_relay,
            signin_hosts=engines.signin_hosts,
            parked=engines.parked,
            shelf_relay=shelf,
        )

        def citations_for(invocation_id: str) -> NewEvent | None:
            delta = citations.delta(invocation_id)
            return NewEvent(CITATIONS_EVENT, delta, critical=False) if delta is not None else None

        binding = attempt_binding(claim)
        cited = JournalCitationSink(env.context.tool_state, citations_for)
        # The kernel feeds this ledger the session's stream: the flow rule reads what the run has read from it, and the
        # conversation's grants come from it and from what the child's admission pinned of its parent's.
        sessions = SessionSourceLedger(
            tool_names={effect.tool_id: name for name, effect in granted.items()},
            inherited=inherited_label(claim.spec),
            inherited_fields=inherited_fields(claim.spec),
        )
        ledger = GrantingLedger(sessions, inherited_grants(claim.spec, enabled=conversation_grants))

        # D3: links into a repository the session fetched publicly are cited for paths its fetched tree holds.
        fetched_repositories = SessionRepositories(sessions.provenance, lambda: deployment_fetcher().cache)
        ranking = record_ranking(classification, ledger, env.context.tool_state)
        conversation = level_source(classification, ledger, claim.run_id, record_level=ranking)
        if model_guard is not None:
            model_guard.bind(classification, conversation, ranking)
        if classification.configured:
            # What a child this run asks for inherits beside its label.
            sessions.bind_label_fields(classification_port().label_fields(classification, conversation))
        engine_default, org_policy = approvals
        from app.harness.flow_destination import pipeshub_destination
        from app.harness.tool_rules import ToolRulesPolicy
        from app.harness.web_addresses import run_addresses

        # Main's per-tool rules hold an engine's gateway calls as they hold a loop run's (SYNC3 open item): an admin's
        # Block refuses, an Ask is asked through the run's approval hold rather than refused as unattended.
        guarded = classification_guarded(
            engine_guarded(
                engine_default,
                ToolRulesPolicy(
                    layer_ask_answers(org_policy, context=env.context, grants=ledger.grants, log=log),
                    env.context, registry, grants=ledger.grants, destination=pipeshub_destination,
                ),
                rules=flow_rules,
                ledger=ledger,
                run_id=claim.run_id,
                log=log,
                addresses=run_addresses(env.context.tool_state, run, sessions),
            ),
            classification,
            ledger=ledger,
            run_id=claim.run_id,
            log=log,
        )
        # WP-63 part A: repository_fetch, the engine's one way to a git repository, asks the person every time.
        repositories = (
            self._repository_tools(claim, shelf, repository_hosts, classification, ledger, log, reachability)
            if repository_hosts is not None
            else None
        )
        # GIT-FLOW under the same rule: a push of the session branch asks too, and only this tool can push.
        pushes = (
            RepositoryPushTools(checkout, shelf, self._egress_control, log)
            if checkout is not None and checkout.push and checkout.credentials is not None
            else None
        )
        if repositories is not None:
            guarded = RepositoryAsks(
                guarded, ledger, claim.run_id, ledger.grants, size_of=repositories.size_of(claim.org_id),
                cap_bytes=repositories.fetcher.config.repo_max_bytes, push=checkout if pushes is not None else None,
                log=log, references=shelf, hosts=repositories.hosts,
            )
        # ADR-SCH-01 D2: a schedule the engine proposes asks the person every time, whatever the grants under it say.
        guarded = schedule_asks(guarded, offered=registry.has(SCHEDULE_CREATE_TOOL), read=schedule_reader(registry))
        # Whether the run's knowledge reads and exports may go ahead (an edition's gate; this build's lets all through).
        knowledge = knowledge_access_gate().gate(guarded, route=route, claim=claim, log=log)
        guarded = knowledge.policy
        # An outside policy outermost of the run's rules, asked after every rule under it.
        guarded = external_policy_gate().guard(
            guarded,
            external_policy,
            org_id=claim.org_id,
            run_id=claim.run_id,
            log=log,
            dry_run=dry_run(claim.spec),
        )
        # WP-112: each gateway call's summaries, the tool's view of its result and its facts, for the engine's step.
        steps = GatewaySteps(registry, log=log)
        tool_port = RegistryToolPort(
            registry,
            granted,
            integrity=catalog.integrity(),
            present=steps.present,
            log=log,
            hooks=use_write_identity(HookRegistry(), write_identity, registry) if write_identity is not None else None,
            around=env.tool_call_scope,
            # D-LAZY-3/4: an MCP server's tools are built at the run's first use of them, through the gateway too;
            # one the engine's profile does not allow is never granted, built or not.
            **engine_lazy_port(registry, catalog, lazy, allowed) if lazy is not None else {},
        )
        # What the gateway serves: the grant, and the export tools beside it when the run has them.
        served: dict[str, ToolEffect] = dict(granted)
        # The grant's tools by their catalog's rules; the record fetch and the pull-request tool, added after, by kind.
        rules: dict[str, ProvenanceRule] = added_rules(granted, catalog.provenance())
        if (
            claim.accepts is not None
            and KNOWLEDGE_EXPORTS in claim.accepts
            and any(knowledge_tool(name) for name in granted)
        ):
            # G-35: a run that may read knowledge may export it in bulk, as far as the gate allows.
            tool_port, exports = export_catalog(
                tool_port,
                state=env.context.tool_state,
                shelf=shelf,
                allowance=knowledge.export_allowance,
                log=log,
            )
            if closers is not None:
                closers.append(exports.close)
            served |= export_effects()
            rules |= export_provenance()
        if repositories is not None:
            tool_port = RepositoryCatalog(tool_port, repositories, *((pushes,) if pushes is not None else ()))
            rules |= tool_port.provenance()
            served[REPOSITORY_FETCH] = repository_effect()
            if pushes is not None:
                served[REPOSITORY_PUSH] = push_effect()
        # WP-97: a save of the run's own deliverable is the harness's to version, never a second artifact or an ask.
        tool_port = DeliverableSaves(tool_port, pinned_deliverable(claim.spec, claim.input_text), log)
        # G-03, G-04: the Tool Gateway is an engine's only way to a tool, so the run's gates are enforced here for it.
        gated = gated_endpoint(
            claim.spec,
            # OWD-18: the Tool Gateway applies the run's write identity to an engine's writes as the loop does.
            tool_port,
            guarded,
            attempt.run_state,
        )
        endpoint = RunToolEndpoint(
            binding=binding,
            catalog=gated.catalog,
            effects=EffectRunner(
                attempt.effect_log,
                gated.approvals,
                hold=attempt.hold,
                guard=attempt.guard,
                propagate=attempt.propagate,
                secrets=env.context,
                dry_run=gated.dry_run,
            ),
            scope=InvocationScope(claim.run_id, claim.org_id, claim.attempt_id),
            ids=attempt.ids,
            citations=cited,
            capabilities=controller.capabilities_for(attempt.profile.engine),
            tracer=engines.tracer,
            metrics=engines.gateway_metrics,
            disclosure=engine_disclosure(
                registry, granted, env.context.essential_toolset_names, tool_disclosure,
                sources=ConnectedSources.of(
                    registry, [*granted, *catalog.deferred()], essential=env.context.essential_toolset_names,
                    state=env.context.tool_state, known=lazy.sources() if lazy is not None else (),
                ),
                lazy=lazy,
            ),
            prepare=first_use_preparer_of(lazy, registry) if lazy is not None else None,
            needs_authentication=catalog.needs_authentication() & granted.keys(),
            # ANSWER-CONTRACT-FU-3: a job asks only for a parent that can ask a person (pinned at its admission).
            asks=asks_person(claim.spec, parent_asks=pinned_parent_asks(claim.spec)),
            # ADR-RUN-03 D2: a job handed the person's answer with its request answers its question from it.
            answered=card_answers(str(env.context.query or "")),
            connect=attempt.connect_wait(catalog.connect_gate()) if attempt.connect_wait is not None else None,
            # G-25: what the engine's managed policy hook asks before each of its own tool calls.
            native=NativeCallPolicy.for_profile(attempt.profile),
            # ADR-RUN-01 D3: every result served recorded with its provenance; a served tool without a rule is refused.
            provenance=ProvenanceStamp.for_tools(
                [*served, *catalog.deferred()], rules, journal=claim.journal_accepts(PROVENANCE_RECORDS),
                sources_of=call_provenance, log=log,
                late_rule=(lambda name: MCP_RULE if lazy.owns(name) else None) if lazy is not None else None,
                # WP83-FU-3: a page from an address only the org reaches is not public.
                review=PageReview(reachability, log=log) if reachability is not None else None,
            ),
            # C7.14: the same connector read asked again in this run is answered from the first answer.
            read_cache=RunReadCache(
                ReadScope(claim.org_id, claim.run.actor_id if claim.run is not None else "", claim.run_id),
                cacheable_reads(served, rules),
                log=log,
                metrics=READ_CACHE_METRICS,
                executor="engine",
            ),
        )
        presenter = PipesHubEngineRunPresenter(
            SessionRevisions(workspaces, objects, claim.org_id, claim.session_id),
            log=log,
            label=lambda: sessions.answer_label(claim.run_id),
            recalled=recalled_event_for(memory.shown, memory.chars, truncated=memory.truncated),
            mounted=mounted.labels,
            answer=EngineAnswer(env.context, lambda: cited.read, fetched_repositories),
            commands=commands,
            journal_commands=journals_commands(claim),
            # The engine names a tool as the gateway listed it (OWD-7 spec names, or today's); the registry by its own.
            display_name=lambda name: _tool_display_name(registry, endpoint.internal_name(name)),
            tool_named=lambda name: (lazy.named(endpoint.internal_name(name)) if lazy is not None else None)
            or endpoint.internal_name(name),
            tags_of=lambda name: _tool_tags(registry, endpoint.internal_name(name)),
            steps=steps,
        )
        actor = Actor(org_id=claim.org_id, user_id=claim.run.actor_id if claim.run is not None else "")
        publisher = WorkspaceArtifactPublisher(
            RegistryArtifactStore(env.context.artifact_registry, actor),
            TaskCardPublicationLog(attempt, presenter),
            objects,
            object_scope=lambda b: session_scope(b.tenant, claim.session_id),
            harness_log=log,
        )
        return AssembledEngineRun(
            controller=controller,
            tools=endpoint,
            tool_binding=binding,
            grant_size=len(served),
            artifacts=publisher,
            content=content,
            idempotent={effect.tool_id: effect.idempotent for effect in served.values()},
            skills=mounted.pins,
            instructions=gated_instructions(
                with_memory(engine_task_instructions(claim.spec.run_kind) + mounted_skills_instructions(mounted), memory),
                claim.spec,
            ),
            workspaces=workspaces,
            presenter=presenter,
            inputs=run_input_source(
                env.context.artifact_registry, actor, objects,
                object_scope=lambda b: session_scope(b.tenant, claim.session_id),
                deployment_max_bytes=controller.input_max_bytes, pinned_max_mb=input_max_mb,
            ),
            ledger=ledger,
            secrets=route.secret_values,
            objects=objects,
            child_result=EngineChildResult(env.context, lambda: cited.read, fetched_repositories),
            own_login=own_login_of(claim),
            # What the conversation holds, for the model gateway (GA-16): only a Node that names the port's feature.
            level_events=classification_port().level_events(
                classification,
                level_source(classification, ledger, claim.run_id, record_level=ranking),
                claim.journal_accepts,
            )
            if classification.configured
            else None,
            # One gateway tool list per conversation and engine kind (OWD-7, TG-14).
            tool_pin=EngineToolPin(attempt.profile.engine.kind, log),
            # WP-84: what the attempt was granted, so a resumed run that had a tool its server withdrew stops (D3).
            capabilities=granted_capabilities(catalog),
            withhold=withholding(catalog),
            # G-05: the sources the run read; the kernel reads the workspace its turns saved.
            verify=VerifyPorts(citations=RunCitationSources(env.context)) if verify_gate(claim.spec) is not None else None,
            # G-52: the person's language, from the run's pinned locale, at the first task's tail.
            answer_language=engine_answer_language(claim.run),
        )

    def _repository_tools(
        self,
        claim: NodeClaim,
        shelf: ShelfRelay,
        hosts: Callable[[], Awaitable[list[Mapping[str, JsonValue]]]],
        classification: Classification,
        ledger: SourceLedger,
        log: HarnessLog,
        reachability: Callable[[], Awaitable[PublicReachability]] | None = None,
    ) -> RepositoryFetchTools:
        """The run's ``repository_fetch``: the deployment's fetcher, the org's hosts read at each call, the turn's
        session folder for the hand-off, the egress proxy's cap, and the conversation's level."""
        level = level_source(classification, ledger, claim.run_id)

        def egress() -> str:
            return classification_port().egress(classification, level()) if classification.configured else "allow"

        fetcher = deployment_fetcher()
        return RepositoryFetchTools(
            fetcher=fetcher,
            status=CacheStatusPublisher(fetcher, self._services.config_service()),
            hosts=hosts,
            shelf=shelf,
            volume=self._egress_control,
            egress=egress,
            journal_events=claim.journal_accepts(REPOSITORY_FETCHES),
            log=log,
            references=shelf,
            reachability=reachability,
        )

    def _skills(self, objects: BinaryObjectStore, workspaces: NodeWorkspaceStore, claim: NodeClaim) -> SkillBundleBuilder:
        factory = self._engines.skill_bundles_for
        if factory is None:
            from harness_core.skills.builder import StoredSkillBundleBuilder
            from harness_core.skills.memory import InMemorySkillContentSource

            return StoredSkillBundleBuilder(source=InMemorySkillContentSource(), objects=objects, workspaces=workspaces)
        return factory(ObjectScope(claim.org_id), objects, workspaces, claim.session_id)


def granted_capabilities(catalog: Any) -> Callable[[], Awaitable[Mapping[str, JsonValue]]]:  # noqa: ANN401 - Catalog
    """The attempt's granted tools as the loop observes them (``Catalog.granted_digests``: a withheld one marked
    withdrawn by its server), for the engine kernel's check across attempts (WP-84)."""

    async def observe() -> Mapping[str, JsonValue]:
        return catalog.granted_digests()

    return observe


def withholding(catalog: Any) -> Callable[[Mapping[str, JsonValue]], Awaitable[None]]:  # noqa: ANN401 - Catalog
    """``Catalog.withhold`` for the engine kernel: the tools the run's first attempt was built without stay out of
    this attempt's registry, so the gateway does not serve them (WP79-FU-5)."""

    async def withhold(marks: Mapping[str, JsonValue]) -> None:
        catalog.withhold(marks)

    return withhold


def engine_wrap_up(
    model: ResolvedModel,
    run: RunContext,
    claim: NodeClaim,
    log: HarnessLog,
    guard: ModelGuard | None = None,
) -> ModelEngineWrapUp | None:
    """The engine run's wrap-up (ENGBUDGET): a reserve on its ledger account, sized as a loop run's (BUDGETEND), and
    the run's own model, metered on that account, to answer on it. ``None`` when the run is not metered (no ledger
    route, the ledger off, a model without a transport): no ledger refuses its calls, so nothing is held back."""
    if claim.budget is None:
        return None
    account = RunAccountRef(run.org_id, run.run_id)
    window = int(model.context_length) if model.context_length else get_context_window(model_name(run))
    reserve = WrapUpReserve(
        accounts=claim.budget,
        account=account,
        pricing_model=model_name(run),
        ids=RandomIds(),
        size=WrapUpSize.for_window(window),
        log=log,
    )
    answering, child_budget = metered(
        model, accounts=claim.budget, account=account, pricing_model=model_name(run), ids=RandomIds(), wrap_up=reserve
    )
    if child_budget is None or not isinstance(answering.transport, MeteredTransport):
        return None
    if guard is not None:
        # The wrap-up answer reads what the engine gathered: the conversation's level binds it too (WP-55).
        answering = guard.wrap(answering, model_name(run))
    return ModelEngineWrapUp(reserve=reserve, transport=answering.transport, log=log)


# What a plan pin named before Node resolved a configured Claude model for it (ADR-MA-01): Claude Code's own default.
LEGACY_PLAN_MODEL = "plan-default"


def own_login_of(claim: NodeClaim) -> PlanSignIn | None:
    """The run's own-plan pin (SIGNIN): route ``vendor_sandbox_login`` on the actor's own account, else ``None``.

    Node pins it only for the actor's own interactive web turn; the account id names the actor, checked again here."""
    policy = claim.spec.model.policy
    access = policy[0].access if policy else None
    if access is None or access.route != "vendor_sandbox_login":
        return None
    actor = claim.run.actor_id if claim.run is not None else ""
    account = access.account_id or ""
    if not actor or account != f"users/{actor}/claude-plan":
        raise SignInRefused("the run's plan account is not its actor's", reason="signin_unavailable")
    model = policy[0].model
    return PlanSignIn(user_id=actor, account_id=account, model=None if model == LEGACY_PLAN_MODEL else model)


async def engine_memory(run: RunContext, claim: NodeClaim, log: HarnessLog) -> RenderedMemories:
    """The memories an engine run is given (P7.3): its pinned ones derived from no record, rendered as the loop's
    are, for its instructions. Nothing pinned, no route or an unavailable store: none."""
    if run.memory is None or not run.memory.recalled or claim.memories is None:
        return render_memories(())
    try:
        recalled = await claim.memories.recall()
    except MemoryUnavailable as exc:
        log.warning("memory.recall_unavailable", reason=MemoryUnavailable.reason_code, errorType=type(exc).__name__)
        return render_memories(())
    given = source_free(recalled)
    rendered = render_memories(given)
    log.info(
        "memory.recalled",
        pinned=len(recalled),
        withheld=len(recalled) - len(given),
        shown=len(rendered.shown),
        chars=rendered.chars,
        truncated=rendered.truncated,
    )
    return rendered


def _pins(claim: NodeClaim) -> tuple[SkillPin, ...]:
    """The skills the run pinned (``x.pipeshub.skills``: name and bundle digest)."""
    extension = cast("dict[str, JsonValue]", (claim.spec.extensions or {}).get("x.pipeshub") or {})
    skills = cast("list[JsonValue]", extension.get("skills") or [])
    return tuple(
        SkillPin(str(skill["name"]), str(skill["bundleDigest"]))
        for skill in (cast("dict[str, JsonValue]", item) for item in skills if isinstance(item, dict))
        if isinstance(skill.get("name"), str) and isinstance(skill.get("bundleDigest"), str)
    )


__all__ = [
    "DurableEngineServices",
    "PipesHubEngineAssembler",
    "TaskCardPublicationLog",
    "attempt_binding",
    "engine_grant",
    "present_for_engines",
    "with_record_fetch",
]
