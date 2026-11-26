"""``PipesHubRuntimeAssembler``: the durable run's composition root (REPORT 6.2.1).

One ``RunContainer`` (a ``dependency-injector`` container) per claimed attempt,
keyed by the pinned ``RunSpec``: the typed ``RunContext``, the explicit
bindings, the content store, and the ``ToolSource`` providers. ``build``
resolves them in order (model, trusted tool environment, sources, catalog)
and hands the driver an ``AssembledRuntime`` that exposes ports only.

The catalog enforces the pin. Every granted tool, MCP instance and skill must
be present (else ``CapabilityLost``); a tool the grant does not name is never
exposed, whatever the agent gained since; and the real descriptor, manifest
and bundle digests of what was built are journaled by the first attempt and
compared by every later one (``CapabilityCheck``).
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Final, cast

from dependency_injector import containers, providers

from app.agents.actions.util.sign_in_rejected import SIGN_IN_REJECTED_STATE
from app.agents.agent_loop.artifact_provenance import SANDBOX_TOOL_NAMES
from app.agents.agent_loop.connected_sources import ConnectedSources
from app.core.org_context import OrgContext, set_request_context
from app.harness.activity import journals_commands
from app.harness.adapters.content_store import LOGGER as CONTENT_LOGGER
from app.harness.adapters.content_store import PipesHubContentStore
from app.harness.approvals import flow_guarded, layer_ask_answers
from app.harness.artifacts.events import presented_names
from app.harness.assembly.bindings import durable_bindings
from app.harness.assembly.call_effects import call_effects_for
from app.harness.assembly.code_task import offers_code_task, with_code_task
from app.harness.assembly.context import RunContext
from app.harness.assembly.context_compiler import (
    auto_compact_enabled,
    build_compactor,
    build_context_compiler,
)
from app.harness.assembly.deep_route import (
    DeepRouteDirector,
    routes,
    thresholds_from_env,
)
from app.harness.assembly.descriptor_pins import (
    MCP_SOURCE,
    LiveMcpDescriptors,
    ToolDescriptors,
    ToolDescriptorSource,
)
from app.harness.assembly.disclosure import Disclosure, mcp_at_first_use
from app.harness.assembly.followups import followups_steps
from app.harness.assembly.grounding_gate import hand_off_request
from app.harness.assembly.history import LegacyHistory
from app.harness.assembly.hooks import build_hooks
from app.harness.assembly.items.credentials import PinnedBindingLeases
from app.harness.assembly.items.pins import offer
from app.harness.assembly.items.run import (
    SkillReader,
    item_credentials,
    lease_pinned_bindings,
    load_pinned_items,
    run_item_runtimes,
)
from app.harness.assembly.loop import (
    LATE_TOOLS,
    ClarificationGate,
    CompositionStep,
    GoalStep,
    PipesHubResponseReview,
    PipesHubTurnShaper,
    SubAgentGoalStep,
    build_composition,
    model_name,
    run_transports,
)
from app.harness.assembly.model import (
    HelperMeter,
    admitted,
    helper_meter,
    helper_model_scope,
    metered,
)
from app.harness.assembly.model_identity import bind_run_identity
from app.harness.assembly.model_policy import (
    ConfiguredRoutePorts,
    build_model_policy,
    compaction_model,
)
from app.harness.assembly.plan import PlanRoutes, plan_tool, step_asks
from app.harness.assembly.skill_learning import skill_learning_steps
from app.harness.assembly.steering import PipesHubInputRenderer
from app.harness.assembly.teardown import close_environment
from app.harness.assembly.tool_environment import (
    WEB_SOURCE,
    UnavailableItemsStep,
    build_tool_environment,
)
from app.harness.assembly.tool_sources import (
    ASKS_USER,
    PLATFORM,
    McpSource,
    SkillsSource,
    SourcedTool,
    default_sources,
    descriptor_digest,
    mcp_tool_pins,
)
from app.harness.assembly.trigger import trigger_note
from app.harness.assembly.workspace_view import (
    WorkspaceContextHost,
    WorkspaceContextShaper,
    WorkspaceViewStep,
    workspace_tools,
)
from app.harness.citations import CitationJournal
from app.harness.classification import (
    ModelGuard,
    classification_guarded,
    classification_port,
    level_source,
    record_ranking,
    tool_state_records,
)
from app.harness.credentials.connect import CatalogConnectGate
from app.harness.external_policy import external_policy_gate
from app.harness.flow_destination import pipeshub_destination
from app.harness.knowledge.access_gate import knowledge_access_gate
from app.harness.knowledge.provenance import untrusted_integrity
from app.harness.labels import (
    PipeshubReplay,
    SessionSourceLedger,
    call_provenance,
    inherited_fields,
    inherited_grants,
    inherited_label,
)
from app.harness.mcp_hub.settings import hub_enabled as mcp_hub_enabled
from app.harness.presentation import PipesHubPresenter
from app.harness.quarantine import bind_summarizer
from app.harness.reconcile import build_reconcile_hooks
from app.harness.run_gates import pipeshub_verify_ports
from app.harness.run_policy.resolved import bind_run_tuning
from app.harness.run_policy.select import RunPolicies
from app.harness.tool_provenance import platform_rule
from app.harness.web_addresses import run_addresses
from app.harness.worker.claimer import RUN_JOB_SOURCE
from app.harness.write_identity.ports import write_identity_port
from app.harness.write_identity.settings import use_write_identity
from app.services.featureflag.platform_settings import bind_pinned_stages
from app.services.rate_budget import priority_for_run_kind, rate_priority_for_class
from app.telemetry.modules.harness_gateway_metrics import READ_CACHE_METRICS
from app.utils.run_model_scope import hold_run_model_scope
from harness_core import bindings
from harness_core.activity import BoundedCommandActivity
from harness_core.budget.run_budget import RunBudget
from harness_core.budget.types import RunAccountRef
from harness_core.budget.wrap_up import WrapUpReserve, WrapUpSize
from harness_core.clock import SystemClock
from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.driver import AssembledRuntime, CapabilityLost
from harness_core.effects.flow import GrantingLedger
from harness_core.effects.types import ToolContext
from harness_core.engines.outcome.deliverable import pinned_deliverable
from harness_core.engines.outcome.gathered import loop_outcome_events, with_sources
from harness_core.engines.outcome.loop import loop_settled
from harness_core.gates.spec import dry_run, verify_gate
from harness_core.ids import RandomIds
from harness_core.journal.types import (
    ATTACHMENT_COUNT,
    OUTCOME_LEDGER,
    OUTCOME_WORDS,
    PROVENANCE_RECORDS,
    SKILL_LABELS,
    TASK_ASKS_FIRST,
)
from harness_core.kernel.budget.pricing import get_context_window
from harness_core.kernel.durable.capability import (
    NEEDS_CONNECT,
    WITHDRAWN,
    withdrawn_mark,
)
from harness_core.kernel.durable.effects import UNKNOWN_TOOL, classify_tool_effect
from harness_core.kernel.task_ledger import TaskLedger
from harness_core.logs import HarnessLog
from harness_core.memories import (
    MEMORY_SCOPES,
    REMEMBER_TOOL,
    MemoryContextHost,
    MemoryContextShaper,
    MemoryRecallStep,
    RememberTool,
)
from harness_core.provenance.ledger import (
    ProvenanceFeed,
    ProvenanceLedger,
    provenance_of,
)
from harness_core.provenance.rules import (
    DELEGATE_RULE,
    MCP_RULE,
    NO_CONTENT_RULE,
    ProvenanceRule,
)
from harness_core.provenance.stamp import ProvenanceStamp, ResultReview
from harness_core.tools.durable_port import RegistryToolPort
from harness_core.tools.read_cache import ReadScope, RunReadCache, cacheable_reads
from harness_core.tools.registry import ToolRegistry
from harness_core.tools.tags import TAG_EFFECT_READ

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from pydantic import JsonValue

    from app.agents.agent_loop.mcp_lazy import LazyMcpServers
    from app.control_plane.runtime.types import ItemLoad
    from app.harness.assembly.command_run import CommandDirector
    from app.harness.assembly.deep import DeepDelegation, DeepDirector, SubAgentResult
    from app.harness.assembly.model import ResolvedModel
    from app.harness.assembly.run_code import ReceiptedSandboxTools
    from app.harness.assembly.run_code_workspace import RunCodeWorkspace
    from app.harness.assembly.services import RunServices
    from app.harness.assembly.tool_environment import ToolEnvironment
    from app.harness.assembly.tool_sources import ToolSource
    from app.harness.journal_node import NodeClaim
    from app.harness.run_policy.ports import RunPolicy
    from app.harness.write_identity.disclosure import WriteIdentitySettings
    from harness_core.activity import CommandActivity
    from harness_core.engines.outcome.deliverable import Deliverable
    from harness_core.engines.outcome.loop import LoopSettled
    from harness_core.gateway.priority_gate import ProviderAdmission
    from harness_core.journal.ports import LiveSinkPort
    from harness_core.journal.types import JournalEvent
    from harness_core.kernel.durable.context_compiler import ContextHost
    from harness_core.kernel.durable.effects import ToolEffect
    from harness_core.kernel.durable.ports import (
        AfterRunStep,
        PreRunStep,
        RunEnding,
        RunRestore,
    )
    from harness_core.memories import MemoryScope, MemorySource
    from harness_core.tools.durable_port import Integrity

ModelFactory = Callable[[RunContext, "RunServices"], Awaitable["ResolvedModel"]]
RunCodeWorkspaceFactory = Callable[["NodeClaim", RunContext], Awaitable["RunCodeWorkspace | None"]]
EnvironmentFactory = Callable[
    [RunContext, "RunServices", "ResolvedModel"], Awaitable["ToolEnvironment"]
]


# Suggested next questions (UX5) come with the agent harness (CTL-5); tests that script no extra model call
# turn them off here (tests/conftest.py).
FOLLOWUP_SUGGESTIONS = True


async def _content_store(claim: NodeClaim) -> PipesHubContentStore:
    return PipesHubContentStore(claim.content, log=HarnessLog.for_claim(CONTENT_LOGGER, claim))



def _tool_descriptors(services: RunServices) -> ToolDescriptorSource:
    return ToolDescriptors(services.config_service())


def _run_context(claim: NodeClaim) -> RunContext:
    """The claim's run, granted what its authority holds: where the pin says the effective set decides (CPD-10
    R3), the spec's grants the set does not hold ready are gone before anything reads them."""
    run = RunContext.from_claim(claim)
    offered = offer(run)
    return run if offered is None else offered.narrowed(run)


class RunContainer(containers.DeclarativeContainer):
    """Per-attempt providers, built from the claim's pinned spec."""

    services = providers.Dependency()
    claim = providers.Dependency()
    run_context = providers.Singleton(_run_context, claim)
    bindings = providers.Singleton(durable_bindings)
    content_store = providers.Factory(_content_store, claim)
    tool_sources = providers.Factory(default_sources)
    tool_descriptors = providers.Factory(_tool_descriptors, services)


@dataclasses.dataclass(frozen=True)
class Catalog:
    registry: ToolRegistry
    tools: tuple[SourcedTool, ...]
    # Domain-agent delegate -> the tools (and delegates) its child agent is granted.
    delegates: dict[str, tuple[str, ...]] = dataclasses.field(default_factory=dict)
    # The attempt's live record of toolsets whose saved sign-in was rejected (``SIGN_IN_REJECTED_STATE``, WP-64).
    sign_in_rejected: Mapping[str, str] = dataclasses.field(default_factory=dict, compare=False)
    # Granted tools withheld from this attempt because their server's listing no longer offers them (WP-79), or
    # because the run's first attempt was built without them (``withhold``, WP79-FU-5), by tool id, with the
    # server's label.
    withheld: dict[str, str] = dataclasses.field(default_factory=dict)

    def withhold(self, marks: Mapping[str, JsonValue]) -> None:
        """Withhold the granted tools ``marks`` names (``tool:<id>`` with the withdrawal mark the run's first attempt
        recorded): taken out of the registry, so the model is not offered them and cannot call them, and observed
        with the same mark, so the run sees no change."""
        by_id = {tool.tool_id: tool for tool in self.tools if tool.grant is not None}
        for key, mark in marks.items():
            tool = by_id.get(key.removeprefix("tool:")) if key.startswith("tool:") else None
            label = cast("dict[str, JsonValue]", mark).get(WITHDRAWN) if isinstance(mark, dict) else None
            if tool is None or not isinstance(label, str):
                continue
            _ = self.registry.withdraw(tool.name)
            self.withheld[tool.tool_id] = label

    def with_delegates(self, delegates: dict[str, tuple[str, ...]]) -> Catalog:
        added = tuple(
            SourcedTool(
                name, f"{PLATFORM}@agents/{name}", None, _delegate_integrity(self, children, delegates),
                provenance=DELEGATE_RULE,
            )
            for name, children in delegates.items()
        )
        return dataclasses.replace(self, tools=self.tools + added, delegates=dict(delegates))

    def effects(self) -> dict[str, ToolEffect]:
        effects: dict[str, ToolEffect] = {}
        for tool in self.tools:
            if tool.name in self.delegates or tool.deferred:
                continue
            effect = classify_tool_effect(tool.name, self.registry.tags_for_name(tool.name))
            effects[tool.name] = dataclasses.replace(effect, tool_id=tool.tool_id)
        ids = {tool.name: tool.tool_id for tool in self.tools}
        for name in self.delegates:
            effects[name] = dataclasses.replace(
                _delegate_effect(name, self.delegates, effects), tool_id=ids[name]
            )
        return effects

    def integrity(self) -> dict[str, Integrity]:
        return {tool.name: tool.integrity for tool in self.tools}

    def provenance(self) -> dict[str, ProvenanceRule]:
        """Each tool's declared provenance rule (ADR-RUN-01 D3)."""
        return {tool.name: tool.provenance for tool in self.tools}

    def granted_digests(self) -> dict[str, JsonValue]:
        # A masked tool stands in for one its item would describe once connected: no digest to hold a later attempt
        # to, only the mark that it waits for a sign-in, which a later attempt may find made or withdrawn (WP-64).
        granted: dict[str, JsonValue] = {
            f"tool:{tool.tool_id}": NEEDS_CONNECT if tool.needs_authentication else descriptor_digest(self.registry, tool.name)
            for tool in self.tools
            if tool.grant is not None and tool.tool_id not in self.withheld and not tool.deferred
        }
        # A withheld tool is observed as withdrawn: a later attempt of a run that had it fails naming its server.
        granted.update({f"tool:{tool_id}": withdrawn_mark(server) for tool_id, server in self.withheld.items()})
        return granted

    def deferred(self) -> tuple[str, ...]:
        """The granted MCP tools not built until the run first uses their server (D-LAZY-3)."""
        return tuple(tool.name for tool in self.tools if tool.deferred)

    def needs_authentication(self) -> frozenset[str]:
        """The tools listed ``needs_authentication``: their item is not connected for the run's owner (WS-09)."""
        return frozenset(tool.name for tool in self.tools if tool.needs_authentication)

    def connect_gate(self) -> CatalogConnectGate:
        """What the run waits on before a call of one of those tools (G-12)."""
        return CatalogConnectGate(
            {tool.name: tool.connect for tool in self.tools if tool.connect is not None},
            {tool.name: tool.reconnect for tool in self.tools if tool.reconnect is not None},
            self.sign_in_rejected,
        )


# How far a call's arguments go, and the effect classes from the strongest down.
_EGRESS_REACH: Final[dict[str, int]] = {"none": 0, "self": 1, "internal_audience": 2, "external": 3}
_EFFECT_STRENGTH: Final = ("execute", "write", "workspace", "read")


def _delegate_effect(
    name: str, delegates: dict[str, tuple[str, ...]], effects: dict[str, ToolEffect]
) -> ToolEffect:
    """A child agent can do what the tools it may call can do (LONGTASK-F2): ``read`` only if every one of them is,
    else their strongest effect, sent as far as the furthest of their sinks (a read's own request counts only when
    it declares an external one), idempotent only if all are. A tool outside the catalog makes it an unknown write
    (fail closed). Code that only runs in the run's own sandbox is then an own-sandbox call, as ``run_code`` is."""
    seen: set[str] = set()
    pending = [name]
    reached: list[ToolEffect] = []
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        for child in delegates.get(current, ()):
            if child in delegates:
                pending.append(child)
            elif child not in effects:
                return dataclasses.replace(UNKNOWN_TOOL, effect="write")
            else:
                reached.append(effects[child])
    if all(e.effect == "read" for e in reached):
        return classify_tool_effect(name, [TAG_EFFECT_READ])
    sinks = [e.sink for e in reached if e.effect != "read" or e.sink.egress == "external"]
    egress = max((s.egress for s in sinks), key=_EGRESS_REACH.__getitem__)
    return dataclasses.replace(
        UNKNOWN_TOOL,
        effect=next(k for k in _EFFECT_STRENGTH if any(e.effect == k for e in reached)),
        idempotent=all(e.idempotent for e in reached),
        sink=SinkDescriptor(egress=egress, args_leave_boundary=any(s.args_leave_boundary for s in sinks)),
    )


def _provenance_events(ledger: object, run_id: str) -> Sequence[JournalEvent]:
    """The run's events as the session's provenance ledger was fed them (none without one)."""
    provenance = provenance_of(ledger)
    return provenance.events_of(run_id) if provenance is not None else ()


def cut_short_settle(
    fed: object, run_id: str, deliverable: Deliverable
) -> Callable[[RunEnding], LoopSettled | None]:
    """A loop run's settle step (WP-100): its cut-short answer's outcome, from what the session's provenance ledger
    ``fed`` saw it gather and the files it showed the person (R36COVER-FU-5, R50JFIX-1)."""

    def settle(ending: RunEnding) -> LoopSettled | None:
        return loop_settled(
            ending, run_id, with_sources(_provenance_events(fed, run_id), run_id, fed), deliverable,
            saved=presented_names(ending.events, run_id),
        )

    return settle


def _delegate_integrity(
    catalog: Catalog, children: tuple[str, ...], delegates: dict[str, tuple[str, ...]]
) -> Integrity:
    known = {tool.name: tool.integrity for tool in catalog.tools}
    names = [c for c in children if c not in delegates]
    return "external" if any(known.get(c) == "external" for c in names) else "internal"


def withdrawn_tools(run: RunContext, sourced: Sequence[SourcedTool], answered: Mapping[str, str]) -> dict[str, str]:
    """The run's granted MCP tools that a server whose listing answered does not offer at their pin (dropped,
    renamed, or held by the hub as changed since an admin approved them), by tool id, with the server's label."""
    present = {t.tool_id for t in sourced if t.grant == t.tool_id}
    withdrawn: dict[str, str] = {}
    for tool_id in run.tool_ids:
        source, _, rest = tool_id.partition("@")
        instance = rest.split("/", 1)[0]
        if source == MCP_SOURCE and tool_id not in present and instance in answered:
            withdrawn[tool_id] = answered[instance]
    return withdrawn


def without_unset_web_search(run: RunContext, *, unset: bool, log: HarnessLog | None = None) -> RunContext:
    """``run`` without its web search grant when the org has no web search provider (main #5722).

    Admission pins the grant from the chat mode alone; with no provider set up the tools never load, and failing
    the run as ``CapabilityLost`` would leave the person with no answer where legacy chat answers and says web
    search is not set up (the prompt's ``web_search_unset`` line, which the run keeps)."""
    if not unset:
        return run
    web = [t for t in run.tool_ids if t.partition("@")[0] == WEB_SOURCE]
    if not web:
        return run
    if log is not None:
        log.info("run.web_search_unset", tools=sorted(web))
    return run.model_copy(update={"tool_ids": tuple(t for t in run.tool_ids if t not in web)})


def without_tools(run: RunContext, withheld: Iterable[str]) -> RunContext:
    """``run`` granted none of ``withheld``. A server pin covers every tool the run was granted of the server, so the
    pin of a server that lost one goes, and its remaining tools are checked one by one at their own pins (as
    ``Offer.narrowed`` does); a server left with no granted tool is no longer one the run holds."""
    gone = set(withheld)
    if not gone:
        return run
    servers = {tool_id.partition("@")[2].split("/", 1)[0] for tool_id in gone}
    tool_ids = tuple(t for t in run.tool_ids if t not in gone)
    holding = {t.partition("@")[2].split("/", 1)[0] for t in tool_ids if t.startswith(f"{MCP_SOURCE}@")}
    return run.model_copy(
        update={
            "tool_ids": tool_ids,
            "mcp_instances": tuple(i for i in run.mcp_instances if i not in servers or i in holding),
            "descriptor_pins": tuple(
                pin
                for pin in run.descriptor_pins
                if not (pin.kind == "tool" and pin.ref in gone) and not (pin.kind == "mcp" and pin.ref in servers)
            ),
        }
    )


def build_catalog(
    run: RunContext,
    registry: ToolRegistry,
    sourced: Sequence[SourcedTool],
    missing_extra: Sequence[str] = (),
    sign_in_rejected: Mapping[str, str] | None = None,
    withheld: Mapping[str, str] | None = None,
) -> Catalog:
    """Exactly the pin: granted tools only if the grant names them, every
    pinned capability present. ``run`` is already narrowed of ``withheld``, which the catalog only records."""
    granted = set(run.tool_ids)
    whole = set(run.mcp_at_first_use)

    def held(grant: str | None) -> bool:
        if grant is None or grant in granted:
            return True
        instance = _instance_of(grant)
        if instance not in whole:
            return False
        # A server the parent listed is the run's at the parent's pin (D-LAZY-5): a tool added since is not.
        pin = run.mcp_carried_pins.get(instance)
        return pin is None or grant.split("/", 1)[1] in pin

    kept = tuple(t for t in sourced if held(t.grant))
    present = {t.tool_id for t in kept if t.grant == t.tool_id}
    groups = {t.group for t in kept if t.group is not None}
    missing = [f"tool:{tool_id}" for tool_id in run.tool_ids if tool_id not in present]
    # A server granted whole has no tool to miss before its first listing (D-LAZY-2): a cold one names none yet.
    missing += [f"mcp:{i}" for i in run.mcp_instances if f"mcp:{i}" not in groups and i not in whole]
    missing += list(missing_extra)
    if missing:
        raise CapabilityLost(missing=missing)
    return Catalog(
        registry,
        kept,
        sign_in_rejected=sign_in_rejected if sign_in_rejected is not None else {},
        withheld=dict(withheld or {}),
    )


def _instance_of(tool_id: str) -> str | None:
    source, _, rest = tool_id.partition("@")
    return rest.split("/", 1)[0] if source == MCP_SOURCE and "/" in rest else None


def run_org_context(run: RunContext) -> OrgContext:
    """The org and person a run's task acts as: what its knowledge tools pin their principal to
    (``bound_org_id``), so a run built without it searches as no org and is refused."""
    return OrgContext(run.org_id, run.actor_id or None, "user" if run.actor_id else "service", RUN_JOB_SOURCE, run.run_id)


def _log(run: RunContext, claim: NodeClaim) -> HarnessLog:
    return HarnessLog.named(
        "app.harness.assembly",
        orgId=run.org_id,
        sessionId=run.session_id,
        runId=run.run_id,
        attemptId=claim.attempt_id,
        fence=claim.fence,
    )


class _Observation:
    """The ``CapabilityCheck`` for one attempt: what its sources really built."""

    def __init__(
        self, catalog: Catalog, sources: Sequence[ToolSource]
    ) -> None:
        self._catalog = catalog
        self._sources = sources

    async def observe(self) -> Mapping[str, JsonValue]:
        observed = self._catalog.granted_digests()
        for source in self._sources:
            observed.update(await source.observe(self._catalog.registry))
        HarnessLog.named("app.harness.assembly").debug(
            "run.capabilities_observed", count=len(observed)
        )
        return observed

    async def withhold(self, marks: Mapping[str, JsonValue]) -> None:
        self._catalog.withhold(marks)


class PipesHubRuntimeAssembler:
    def __init__(
        self,
        services: RunServices,
        *,
        model_factory: ModelFactory | None = None,
        environment_factory: EnvironmentFactory = build_tool_environment,
        sources: Callable[[], list[ToolSource]] | None = None,
        activity: LiveSinkPort | None = None,
        policies: RunPolicies | None = None,
        run_code_workspace: RunCodeWorkspaceFactory | None = None,
        admission: ProviderAdmission | None = None,
    ) -> None:
        """``admission``: where each of the run's own model calls takes its provider slot (G-45); unset, none."""
        self._services = services
        self.admission = admission
        # Where run_code's session workspace is read and saved: Node's store with the attempt, unless a test says.
        self._workspace_factory = run_code_workspace
        self.policies = policies if policies is not None else RunPolicies(services)
        # The activity bus (UX5): where a run's sandbox commands stream their output.
        self._activity = activity
        self._model_factory = model_factory
        self._environment_factory = environment_factory
        self._sources = sources

    def container(self, claim: NodeClaim) -> RunContainer:
        container = RunContainer(services=self._services, claim=claim)
        if self._sources is not None:
            container.tool_sources.override(providers.Factory(self._sources))
        return container

    async def build(self, claim: NodeClaim) -> AssembledRuntime:  # type: ignore[override]
        container = self.container(claim)
        run: RunContext = container.run_context()
        # Every org-scoped read of this attempt's task runs as the run's org and person (main #4703): the stores and
        # the vector filters fail closed without one, and a second org in the same task is refused.
        set_request_context(run_org_context(run))
        durable = container.bindings()
        log = _log(run, claim)
        _log_assembly(log, run)
        # Everything built for the run (transports, the prompt's runtime)
        # sees the durable bindings, never the process-wide ones.
        policy = self.policies.for_run(run, log)
        tuning = policy.tuning()
        # Every Labs flag read of this attempt's task answers from the run's pin (none for a run without one).
        bind_pinned_stages(policy.stages())
        # As legacy's producer task (main): record headers the tools render show dates in the user's zone.
        from app.utils.time_conversion import set_llm_time_zone

        _ = set_llm_time_zone(run.timezone)
        approval = await policy.approvals()
        governance = policy.live(approval.rules)
        activity: BoundedCommandActivity | None = None
        with bindings.scoped(durable):
            guard = classification_port().model_guard(log)
            model, child_budget, model_port, model_policy, wrap_up = await self._model(
                run, claim, log, policy, guard
            )
            env: ToolEnvironment = await self._environment_factory(run, self._services, model)
            env.helper_models = helper_model_scope(run, self.helper_meter(run, claim), guard)
            bind_run_tuning(env.context.tool_state, tuning)
            # What a tool call has retrieved, before the ledger has it, is what its helpers are checked at (WP-55).
            guard.reads_from(tool_state_records(env.context.tool_state))
            # Every model a helper of this attempt builds is the run's, a tool's or not (WP-49).
            hold_run_model_scope(env.helper_models)
            run = env.narrowed(run)
            # Before the first model call: the gateway is told whom this attempt's calls are for (main #4428).
            await bind_run_identity(
                run,
                email=getattr(env.context, "user_email", None)
                if getattr(env.context, "user_id", None) == run.actor_id
                else None,
                config_service=self._services.config_service(),
                container=getattr(self._services, "container", None),
            )
            # Before the tools load: an MCP server refused for scope at discovery is noted for the card.
            env.context.durable_web_app = run.web_app
            # Before the tools load too: the web tools take their allowance as they are built (WP-56).
            env.context.web_scope = run.web_scope
            # Before the tools load too: knowledgegraph__summarize is offered only with a summarizer to hand it to (G-58).
            _ = bind_summarizer(env.context.tool_state, model, model_id=model_name(run), guard=guard)
            # Sources open MCP sessions and sandboxes; a build that fails after
            # this point would otherwise leave them to the garbage collector.
            try:
                memory = run_memory(run, claim, log)
                workspace = await self._run_code_workspace(claim, run)
                # Before the tools load: who the run's writes are from (OWD-18, an extension point).
                await write_identity_port().prepare(run, env, policy, log)
                tools = await load_run_tools(
                    self._services, container, env, run, claim, memory=memory, workspace=workspace, log=log,
                    lazy_mcp=mcp_at_first_use(policy.tool_disclosure(), deep=run.deep),
                )
                built = await self._parts(
                    claim, run, env, model, tools, memory, child_budget, governance, log,
                    tool_disclosure=policy.tool_disclosure(),
                    write_identity=await write_identity_port().settings(run, env, policy, log),
                    review=page_review(policy, log),
                )
                activity = built.activity
                content = await container.content_store()
            except BaseException:
                await close_environment(env.context, log)
                raise

        async def close() -> None:
            if activity is not None:
                await activity.aclose()
            await tools.items.close()
            await close_environment(env.context, log)

        from app.harness.tool_rules import ToolRulesPolicy

        try:
            command = _command_run(run, tools.registry)
        except BaseException:
            await close()
            raise

        # The kernel feeds the ledger the session's stream only when the flow rule or a classification reads it.
        sessions = SessionSourceLedger(
            inherited=inherited_label(claim.spec), inherited_fields=inherited_fields(claim.spec)
        )
        ledger = (
            GrantingLedger(sessions, inherited_grants(claim.spec, enabled=tuning.conversation_grants))
            if approval.flow_rules or approval.classification.configured
            else None
        )
        # One ranking of records for the conversation's level and for what a tool call reads (WP-55), by the labels
        # the session's provenance ledger recorded and what the call has retrieved so far (OWD-15).
        ranking = record_ranking(approval.classification, ledger, env.context.tool_state)
        conversation = level_source(approval.classification, ledger, run.run_id, record_level=ranking)
        guard.bind(approval.classification, conversation, ranking)
        if ledger is not None and approval.classification.configured:
            # What a child this run asks for inherits beside its label.
            sessions.bind_label_fields(classification_port().label_fields(approval.classification, conversation))
        # ADR-RUN-01 D1 x D3: the outcome lists what the run gathered from the session's provenance ledger; a run whose
        # rules read no ledger is fed one that labels nothing and holds no grants.
        fed = (
            ledger
            if ledger is not None
            else ProvenanceFeed(ProvenanceLedger(PipeshubReplay())) if claim.journal_accepts(OUTCOME_LEDGER) else None
        )
        if fed is not None and claim.journal_accepts(OUTCOME_LEDGER):
            built.presenter.use_outcome(
                lambda: loop_outcome_events(_provenance_events(fed, run.run_id), run.run_id, fed),
                # WP-100: a cut-short answer's outcome, worded by the chat that advertises it.
                settle=cut_short_settle(fed, run.run_id, pinned_deliverable(claim.spec, run.query))
                if claim.journal_accepts(OUTCOME_WORDS)
                else None,
            )
        if ledger is not None and tools.skills is not None and tools.skills.provenance is not None:
            # A skill this run's model writes carries what the run had read by then; with no ledger, unknown.
            tools.skills.provenance.bind(ledger)
        auto_compact = auto_compact_enabled()
        external_policy = await external_policy_gate().prepare(self._services.config_service(), run.org_id, log)
        rules = classification_guarded(
            flow_guarded(
                ToolRulesPolicy(
                    layer_ask_answers(
                        governance, context=env.context, grants=ledger.grants if ledger is not None else None, log=log
                    ),
                    env.context, tools.registry,
                    grants=ledger.grants if ledger is not None else None, destination=pipeshub_destination,
                ),
                rules=approval.flow_rules,
                ledger=ledger,
                run_id=run.run_id,
                log=log,
                destination=pipeshub_destination,
                grants=ledger.grants if ledger is not None else None,
                addresses=run_addresses(env.context.tool_state, run, sessions),
            ),
            approval.classification,
            ledger=ledger,
            run_id=run.run_id,
            log=log,
        )
        # Whether the run's knowledge reads may go ahead (an edition's gate; this build's lets all through).
        rules = (
            knowledge_access_gate()
            .gate(rules, route=getattr(claim, "attempt_route", None), claim=claim, log=log)
            .policy
        )
        return AssembledRuntime(
            model=model_port,
            model_policy=model_policy,
            tools=built.port,
            content=content,
            bindings=durable,
            # An outside policy outermost, asked after every rule under it.
            approval=external_policy_gate().guard(
                rules,
                external_policy,
                org_id=run.org_id,
                run_id=run.run_id,
                log=log,
                dry_run=dry_run(claim.spec),
            ),
            ledger=fed,
            connect=built.catalog.connect_gate(),
            # What the conversation holds, as the port records it on the run for the journal that names its feature.
            level_events=classification_port().level_events(
                approval.classification,
                level_source(approval.classification, ledger, run.run_id, record_level=ranking),
                claim.journal_accepts,
            )
            if ledger is not None and approval.classification.configured
            else None,
            capability_check=_Observation(built.catalog, tools.sources),
            prepare_calls=first_use_preparer(tools.sources, tools.registry),
            mcp_pins=first_use_pins(tools.sources),
            presenter=built.presenter,
            # A /run asks no model: no goal step, no gate, nothing after the answer (SLASH3 SD9).
            pre_run=() if command else built.pre_run,
            post_run=() if command else built.post_run,
            after_run=() if command else built.after_run,
            pre_run_gate=None if command else ClarificationGate(),
            shaper=_viewed(
                MemoryContextShaper(built.shaper) if memory is not None and memory.recalled else built.shaper,
                tools.workspace,
            ),
            review=None if command else PipesHubResponseReview(built.shaper, built.hooks, log),
            # G-05: what the run's verify gate may look at; only a run whose spec pins one is checked.
            verify=pipeshub_verify_ports(
                context=env.context,
                workspace=tools.workspace,
                tools=built.port,
                has_run_code=tools.registry.has("run_code"),
                ctx=ToolContext(
                    run.run_id, run.org_id, "verify", claim.attempt_id, f"{run.org_id}:{run.run_id}:verify"
                ),
            )
            if verify_gate(claim.spec) is not None
            else None,
            restore=_restores(built.citations, lazy_servers(tools.sources)),
            inputs=PipesHubInputRenderer(
                env, built.presenter, log, counted=claim.journal_accepts(ATTACHMENT_COUNT)
            ),
            history=LegacyHistory(env, log),
            audience=run.history_audience,
            delegation=None
            if command
            else with_code_task(
                built.deep_mode.delegation,
                offered=offers_code_task(run),
                remember=RememberTool(memory.source, memory.write_scopes)
                if memory is not None and memory.write_scopes
                else None,
                citations=_adopting(built.citations, env.context, log),
                request=hand_off_request(run),
                deliverable=pinned_deliverable(claim.spec, run.query).name,
                budget=RunBudget.of_claim(claim),
                sources=ConnectedSources.of(
                    tools.registry, (t.name for t in built.catalog.tools),
                    essential=env.context.essential_toolset_names, state=env.context.tool_state,
                    known=lazy.sources() if (lazy := lazy_servers(tools.sources)) is not None else (),
                ),
                log=log,
                preloaded_skills=built.shaper.preloaded_skills,
            ),
            director=command or built.deep_mode.director,
            child_result=built.deep_mode.child_result,
            context=build_context_compiler(
                model,
                model_name(run),
                _context_host(built.shaper, memory, tools.workspace),
            ),
            # The person's /compact, and the ladder's compact rung, summarise even where requests are not
            # compiled (SLASH2 C2, LC2FIX-2).
            compactor=(
                build_compactor(model, model_name(run), _context_host(built.shaper, memory, tools.workspace))
                if claim.compact is not None or auto_compact
                else None
            ),
            auto_compact=auto_compact,
            wrap_up=wrap_up,
            reconcile_hooks=build_reconcile_hooks(tools.registry, built.effects, log),
            secrets=env.context,
            live=lambda inner: _cited_live(inner, env.context, log),
            close=close,
        )

    def helper_meter(self, run: RunContext, claim: NodeClaim) -> HelperMeter:
        """How a model a tool's helper builds is made the run's: at the claim's class, on the run's account."""
        return helper_meter(
            admission=self.admission,
            priority_class=claim.priority_class,
            run_kind=claim.spec.run_kind,
            accounts=claim.budget,
            account=RunAccountRef(run.org_id, run.run_id),
            ids=RandomIds,
        )

    async def _model(
        self, run: RunContext, claim: NodeClaim, log: HarnessLog, policy: RunPolicy, guard: ModelGuard
    ) -> tuple[ResolvedModel, Any, Any, Any, WrapUpReserve | None]:
        """The run's metered model, the model policy over its pinned routes, and the run's wrap-up reserve: the
        budget its answer-only turn after the spending limit draws on, shared by every route (BUDGETEND)."""
        model: ResolvedModel = admitted(
            await (self._model_factory or policy.model)(run, self._services),
            self.admission,
            claim.priority_class,
            claim.spec.run_kind,
            org=run.org_id,
        )
        window = int(model.context_length) if model.context_length else get_context_window(model_name(run))
        wrap_up = (
            WrapUpReserve(
                accounts=claim.budget,
                account=RunAccountRef(run.org_id, run.run_id),
                pricing_model=model_name(run),
                ids=RandomIds(),
                size=WrapUpSize.for_window(window),
                log=log,
            )
            if claim.budget is not None
            else None
        )
        model, child_budget = metered(
            model,
            accounts=claim.budget,
            account=RunAccountRef(run.org_id, run.run_id),
            pricing_model=model_name(run),
            ids=RandomIds(),
            wrap_up=wrap_up,
        )
        # Every route's port answers to the org's classification (OWD-15): the level is bound once the ledger exists.
        model = guard.wrap(model, model_name(run))
        if child_budget is None:
            # Not metered: no ledger refuses a call, so nothing is held back.
            wrap_up = None
        log.info("run.model", provider=model.provider, model=run.model.model, metered=child_budget is not None)

        def meter(resolved: ResolvedModel, routed: RunContext) -> ResolvedModel:
            return guard.wrap(
                metered(
                    admitted(resolved, self.admission, claim.priority_class, claim.spec.run_kind, org=run.org_id),
                    accounts=claim.budget,
                    account=RunAccountRef(run.org_id, run.run_id),
                    pricing_model=model_name(routed),
                    ids=RandomIds(),
                    wrap_up=wrap_up,
                )[0],
                model_name(routed),
            )

        compaction = await compaction_model(
            run,
            self._services,
            primary_account=bool(claim.spec.model.policy) and claim.spec.model.policy[0].access is not None,
            resolve=self._model_factory or policy.fallback_model,
            meter=meter,
            log=log,
        )
        if compaction is not None:
            model = dataclasses.replace(model, compaction=compaction)
        model_port, model_policy = build_model_policy(
            claim.spec.model,
            model,
            lambda: ConfiguredRoutePorts(
                run,
                self._services,
                tuple(claim.spec.model.policy),
                resolve=self._model_factory or policy.fallback_model,
                meter=meter,
            ),
        )
        return model, child_budget, model_port, model_policy, wrap_up

    async def _parts(
        self,
        claim: NodeClaim,
        run: RunContext,
        env: ToolEnvironment,
        model: ResolvedModel,
        tools: RunTools,
        memory: _RunMemory | None,
        child_budget: Any,  # noqa: ANN401 - the metered model's child budget, or None
        governance: Any,  # noqa: ANN401 - the run's live approval governance
        log: HarnessLog,
        *,
        tool_disclosure: str | None = None,
        write_identity: WriteIdentitySettings | None = None,
        review: ResultReview | None = None,
    ) -> _BuiltParts:
        """Everything the loop runs with beside the model: hooks, composition, shaping, deep mode, steps."""
        registry, skills = tools.registry, tools.skills
        skill_manager = skills.manager if skills is not None else None
        sandbox = await self._sandbox(claim, env, model, tools, skill_manager, log)
        if write_identity is not None:
            # OWD-18: last of the call hooks, so the disclosure joins the arguments exactly as they are sent.
            _ = use_write_identity(sandbox.hooks.registry, write_identity, registry)
        usage = env.context.usage_ledger.add if env.context.include_retrieval_context else None
        transports = run_transports(model, run, usage)
        if skills is not None and skill_manager is not None:
            from app.agents.agent_loop.factory import (
                DIRECT_TRANSPORT,
                LANGCHAIN_TRANSPORT,
            )

            skills.use_transports(transports, (LANGCHAIN_TRANSPORT, DIRECT_TRANSPORT))
        after_run = followups_steps(
            model.transport,
            claim=claim,
            question=run.query,
            enabled=FOLLOWUP_SUGGESTIONS,
            child=claim.spec.run_kind == "child" or run.sub_agent is not None,
            bus=self._activity,
            sink=claim.followups,
            log=log,
        )
        post_run = skill_learning_steps(
            skill_manager,  # type: ignore[arg-type]
            run,
            run_kind=claim.spec.run_kind,
            registry=registry,
            transports=transports,
            hooks=sandbox.hooks.registry,
            provider=_transport_provider(),
            context_window=env.context.configured_context_window,
        )
        composed = _compose(
            env, run, registry, transports, sandbox.hooks, tools.catalog, skill_manager, memory, log, tools.workspace
        )
        shaping = _shape(
            claim, run, env, registry, composed, sandbox, transports, skill_manager, tools, governance, log,
            tool_disclosure=tool_disclosure, review=review,
        )
        deep_mode, pre_run = await _deep_and_steps(
            claim, run, env, model, registry, composed, shaping, transports, child_budget, log,
            lazy=lazy_servers(tools.sources),
        )
        if env.unavailable:
            pre_run.append(UnavailableItemsStep(env.unavailable))
        return _BuiltParts(
            catalog=composed.catalog,
            hooks=sandbox.hooks,
            activity=sandbox.activity,
            port=shaping.port,
            effects=shaping.effects,
            presenter=shaping.presenter,
            citations=shaping.citations,
            shaper=shaping.shaper,
            deep_mode=deep_mode,
            pre_run=[*composed.pre_run, *pre_run],
            post_run=post_run,
            after_run=after_run,
        )

    async def _sandbox(
        self,
        claim: NodeClaim,
        env: ToolEnvironment,
        model: ResolvedModel,
        tools: RunTools,
        skill_manager: object | None,
        log: HarnessLog,
    ) -> _Sandbox:
        """The run's hooks, and its sandbox commands receipted and streamed when it has a sandbox."""
        registry = tools.registry
        artifacts = next((s for s in tools.sources if s.name == "artifacts"), None)
        hooks = build_hooks(
            env,
            tools.run,
            registry,
            model,
            artifact_store=getattr(artifacts, "store", None),
            skill_manager=skill_manager,
        )
        activity = (
            BoundedCommandActivity(
                self._activity,
                run_id=claim.run_id,
                attempt_id=claim.attempt_id,
                fence=claim.fence,
                clock=SystemClock(),
                content=await tools.content_store(),
                log=log,
            )
            if env.context.sandbox_manager is not None
            else None
        )
        workspace = tools.workspace
        receipted = _receipted_sandbox(
            env,
            registry,
            log,
            activity=activity,
            journal_commands=journals_commands(claim),
            typed_line=_typed_line(tools.run),
        )
        if receipted is not None:
            receipted.register(hooks.registry)
            _session_workspace(registry, workspace, env.context, log)
        return _Sandbox(hooks, activity, receipted)

    async def _run_code_workspace(self, claim: NodeClaim, run: RunContext) -> RunCodeWorkspace | None:
        """The workspace ``run_code`` reads and saves (SLASH3: what an engine's turn reads, at the same paths): the
        run's principal's own (OWD-16). A sub-agent writes no session workspace: its parent's run owns it."""
        if run.sub_agent is not None:
            return None
        if self._workspace_factory is not None:
            return await self._workspace_factory(claim, run)
        route = getattr(claim, "attempt_route", None)
        if route is None:
            return None
        from app.harness.adapters.pipeshub_object_store import PipesHubBinaryObjectStore
        from app.harness.assembly.run_code_workspace import RunCodeWorkspace
        from app.harness.workspace.node_store import NodeKeyProvider, NodeWorkspaceStore
        from harness_core.workspace.forks import session_scope
        from harness_core.workspace.sealing import SealedObjectStore

        objects = SealedObjectStore(PipesHubBinaryObjectStore(await self._services.blob_store()), NodeKeyProvider(route))
        return RunCodeWorkspace(
            store=NodeWorkspaceStore(route),
            objects=objects,
            scope=session_scope(run.org_id, run.session_id),
            workspace_id=run.workspace_id,
        )


@dataclasses.dataclass(frozen=True)
class RunTools:
    """What a run's tools were built from: its sources, the catalog the pin allows, and its loaded pinned items."""

    run: RunContext
    registry: ToolRegistry
    sources: list[ToolSource]
    sourced: list[SourcedTool]
    by_source: dict[str, int]
    catalog: Catalog
    items: ItemLoad
    skills: SkillsSource | None
    content_store: Callable[[], Awaitable[Any]]
    # The chat's workspace the run reads and saves (``run_code``, the loop's view of it); ``None``: none.
    workspace: RunCodeWorkspace | None = None


async def load_run_tools(
    services: RunServices,
    container: RunContainer,
    env: ToolEnvironment,
    run: RunContext,
    claim: NodeClaim,
    *,
    memory: _RunMemory | None = None,
    workspace: RunCodeWorkspace | None = None,
    log: HarnessLog | None = None,
    check_skills: bool = True,
    plan: bool = True,
    lazy_mcp: bool = False,
) -> RunTools:
    """Sources, then the catalog the pin allows, then the pinned items at their pinned digests (CPD-7).

    Every granted capability must be present and every pinned digest still the one pinned, else
    ``CapabilityLost``. The loop and engine assemblers share it; an engine run passes ``check_skills=False``:
    it mounts its pinned skills rather than reading them through the loop's skill tools, so the skills source's
    catalog is not what it uses. The item load holds its pinned skills to their pins as it does the loop's.
    """
    registry = ToolRegistry()
    # Before the sources load: a connector toolset's calls spend the vendor budget at the run's priority (G-31): its
    # class as the claim named it (G-45), else as its run kind gives it.
    env.context.rate_priority = (
        rate_priority_for_class(claim.priority_class)
        if claim.priority_class is not None
        else priority_for_run_kind(claim.spec.run_kind)
    ).value
    credentials = item_credentials(run, services)
    leases = PinnedBindingLeases(credentials)
    # The attempt's tool clients read a bound installation's record from its lease, and the teardown that
    # revokes their handles closes the broker with them. Sources create clients as they load, so every pinned
    # binding is leased first.
    env.context.tool_credentials().route(leases)
    await lease_pinned_bindings(run, leases, credentials, claim.attempt_id)
    if claim.journal_accepts(SKILL_LABELS):
        from app.agents.agent_loop.skills.labelled_manager import SKILL_LABELS_STATE

        # Where the skill reads note what agent-written content they hand the model (FOLLOWUPS1-1); without it
        # such a skill is not handed over, since its label could not be journaled.
        env.context.tool_state.setdefault(SKILL_LABELS_STATE, [])
    sources: list[ToolSource] = container.tool_sources()
    for source in sources:
        if isinstance(source, McpSource):
            # D-LAZY-3: a server is listed at the run's first use of it, never at the claim.
            source.lazy = lazy_mcp
    from app.harness.mcp_hub.run_pinning import mcp_run_pinning

    pinning = await mcp_run_pinning(run.org_id, services.config_service(), hub=mcp_hub_enabled(harness=True))
    pinned_live = pinning.holds_claim_to_admission_listing
    for source in sources:
        if isinstance(source, McpSource) and pinned_live:
            source.pinned = mcp_tool_pins(run)
    sourced: list[SourcedTool] = []
    by_source: dict[str, int] = {}
    for source in sources:
        loaded = await source.load(env, run, registry)
        by_source[source.name] = len(loaded)
        sourced.extend(loaded)
    remember = remember_tool(memory, registry)
    if remember is not None:
        sourced.append(remember)
        by_source["memory"] = 1
    for viewing in workspace_tools(workspace):
        registry.register_tool(viewing)
        workspace_id = f"{PLATFORM}@workspace/{viewing.name}"
        sourced.append(SourcedTool(viewing.name, workspace_id, None, provenance=platform_rule(viewing.name, workspace_id)))
        by_source["workspace"] = by_source.get("workspace", 0) + 1
    # The engine executor has no plan route (UXR-B1 wires write_todos for the loop only).
    planned = plan_tool(run, registry) if plan else None
    if planned is not None:
        sourced.append(planned)
        by_source["plan"] = 1
    skills = cast("SkillsSource | None", next((s for s in sources if s.name == "skills"), None))
    missing_skills = skills.missing(run) if skills is not None and check_skills else []
    # The same dict the attempt's call scopes write: the gate sees a rejection the moment it is withdrawn.
    rejected: dict[str, str] = env.context.tool_state.setdefault(SIGN_IN_REJECTED_STATE, {})
    mcp = next((s for s in sources if isinstance(s, McpSource)), None)
    # A tool its third-party server stopped offering is withheld, not fatal (WP-79): the run never calls it, and
    # the capability check fails a run that had it on an earlier attempt.
    withheld = withdrawn_tools(run, sourced, mcp.answered) if mcp is not None else {}
    if withheld:
        run = without_tools(run, withheld)
        if log is not None:
            log.info("run.tools_withheld", tools=sorted(withheld), servers=sorted(set(withheld.values())))
    run = without_unset_web_search(run, unset=env.context.web_search_unset, log=log)
    catalog = build_catalog(run, registry, sourced, missing_skills, sign_in_rejected=rejected, withheld=withheld)
    descriptors: ToolDescriptorSource = container.tool_descriptors()
    if mcp is not None and pinned_live:
        # A lazy server is held to its pins at its first listing (D-LAZY-5), not here: the claim reads them as given.
        descriptors = LiveMcpDescriptors(descriptors, mcp_tool_pins(run) if mcp.lazy else mcp.live_digests)
    from app.agents.agent_loop.skills.offer import skill_viewers

    runtimes = run_item_runtimes(
        run,
        descriptors=descriptors,
        sourced=sourced,
        skills=SkillReader(
            services.graph_provider,
            run.agent.agent_key if run.agent is not None else None,
            services.config_service,
            viewers=skill_viewers(env.context),
        ),
        close_mcp_session=_mcp_session_closer(env),
        leases=leases,
    )
    items = await load_pinned_items(run, runtimes, claim.attempt_id, credentials)
    if log is not None:
        log.debug("run.items_loaded", tools=len(items.tools))
    return RunTools(
        run, registry, sources, sourced, by_source, catalog, items, skills, container.content_store, workspace
    )


def _mcp_session_closer(env: ToolEnvironment) -> Callable[[str], Awaitable[None]]:
    async def close(instance_id: str) -> None:
        from app.agents.agent_loop.mcp_session import MCPSessionManager

        await MCPSessionManager(env.context).aclose(instance_id)

    return close


def _log_assembly(log: HarnessLog, run: RunContext) -> None:
    log.info(
        "run.assembly",
        specDigest=run.spec_digest,
        agentKey=run.agent.agent_key if run.agent is not None else None,
        chatMode=run.chat_mode,
        via=run.via,
        grantedTools=len(run.tool_ids),
        mcpInstances=len(run.mcp_instances),
        skills=len(run.skills),
        sandboxProfile=run.sandbox_profile,
        knowledgeScoped=not run.knowledge.empty,
    )


@dataclasses.dataclass(frozen=True)
class _Sandbox:
    hooks: Any
    activity: BoundedCommandActivity | None
    receipted: ReceiptedSandboxTools | None


@dataclasses.dataclass(frozen=True)
class _Composed:
    catalog: Catalog
    composition: Any
    flat_names: list[str]
    # ``RunContext.coordinates``: the catalog holds the coordination tools and the run routes the planning ones.
    coordinates: bool
    pre_run: list[PreRunStep]


def _compose(
    env: ToolEnvironment,
    run: RunContext,
    registry: ToolRegistry,
    transports: Any,  # noqa: ANN401 - the run's transport registry
    hooks: Any,  # noqa: ANN401 - the run's hooks
    catalog: Catalog,
    skill_manager: object | None,
    memory: _RunMemory | None,
    log: HarnessLog,
    workspace: RunCodeWorkspace | None = None,
) -> _Composed:
    """The run's composition (domain agents as delegates) and the catalog the model is shown."""
    composed = build_composition(
        env,
        run,
        registry,
        transports,
        hooks=hooks.registry,
        allowed={t.name for t in catalog.tools},
        skills=skill_manager is not None,
    )
    flat_names = [t.name for t in catalog.tools]
    pre_run: list[PreRunStep] = []
    if memory is not None and memory.recalled:
        pre_run.append(MemoryRecallStep(memory.source, log=log))
    if workspace is not None:
        pre_run.append(WorkspaceViewStep(workspace, log))
    composition = None
    if composed is not None:
        composition, _plan = composed
        children = {
            name: tuple(registry.resolve_by_name(name).spec.tool_names)  # type: ignore[attr-defined]
            for name in composition.delegates
        }
        catalog = catalog.with_delegates(children)
        pre_run.append(CompositionStep(composition, log))
    _describe_run_code(registry, list(composition.top_level) if composition is not None else flat_names)
    if run.coordinates:
        catalog = _with_coordination_tools(registry, catalog)
    return _Composed(catalog, composition, flat_names, run.coordinates, pre_run)


def _adopting(journal: CitationJournal, context: Any, log: HarnessLog) -> Any:  # noqa: ANN401 - AgentContext, DeepCitations
    """Where a code_task child's citation sources join this run's (ENGCITE2), as a spawn_agent child's do."""
    from app.harness.assembly.deep import DeepCitations

    return DeepCitations(journal, context, log)


def _cited_live(inner: LiveSinkPort, context: Any, log: HarnessLog) -> LiveSinkPort:  # noqa: ANN401 - the legacy AgentContext
    """The run's live sink with the streamed answer's citations (CITESTREAM)."""
    # Imported per run: the citation stack is the legacy query stack an idle worker never loads (D7).
    from app.harness.live_citations import CitedLiveSink

    return CitedLiveSink(inner, context, log=log)


@dataclasses.dataclass(frozen=True)
class _Shaping:
    citations: CitationJournal
    presenter: PipesHubPresenter
    shaper: PipesHubTurnShaper
    sub_agent: Any
    domain_tool: Any
    effects: dict[str, ToolEffect]
    port: RegistryToolPort
    plan: TaskLedger


def page_review(policy: RunPolicy, log: HarnessLog) -> ResultReview:
    """WP83-FU-3: the run's review of each page fetch, so a page from an address only the org reaches is not public."""
    from app.harness.repositories import org_reachability
    from app.harness.web_reachability import PageReview

    return PageReview(org_reachability(policy), log=log)


def _shape(
    claim: NodeClaim,
    run: RunContext,
    env: ToolEnvironment,
    registry: ToolRegistry,
    composed: _Composed,
    sandbox: _Sandbox,
    transports: Any,  # noqa: ANN401 - the run's transport registry
    skill_manager: object | None,
    tools: RunTools,
    governance: Any,  # noqa: ANN401 - the run's live approval governance
    log: HarnessLog,
    *,
    tool_disclosure: str | None = None,
    review: ResultReview | None = None,
) -> _Shaping:
    """How turns are presented and shaped, and the tool port the kernel calls."""
    from app.harness.assembly.deep import DeepRoutes, withheld_from_sub_agent

    catalog = composed.catalog
    log.info(
        "run.catalog",
        tools=len(catalog.tools),
        granted=sum(1 for t in catalog.tools if t.grant is not None),
        platform=sum(1 for t in catalog.tools if t.grant is None),
        delegates=len(catalog.delegates),
        withheld=len(tools.sourced) - len(composed.flat_names),
        **{f"from_{name}": count for name, count in sorted(tools.by_source.items())},
    )
    citations = CitationJournal(env.context, registry=registry, log=log)
    presenter = PipesHubPresenter(
        env.context,
        registry,
        log=log,
        citations=citations,
        report_confidence=run.chat is None,
        max_turns=claim.spec.budgets.max_turns,
        model=run.model.model_name,
    )
    lazy = lazy_servers(tools.sources)
    disclosure = Disclosure(
        env,
        registry,
        lazy=lazy,
        essential=(["skills"] if skill_manager is not None else []) + list(env.context.essential_toolset_names),
        log=log,
        # CTL-6: one way to run code. With code_task offered the loop is not shown the sandbox tools (still granted).
        withheld=SANDBOX_TOOL_NAMES if offers_code_task(run) else frozenset(),
        mode=tool_disclosure,
    )
    sub_agent, domain_tool = _sub_agent_spec(env, run, registry, claim)
    plan_ledger = TaskLedger(asks_first=claim.journal_accepts(TASK_ASKS_FIRST))
    shaper = PipesHubTurnShaper(
        env,
        run,
        registry,
        disclosure,
        sandbox.hooks,
        flat_names=composed.flat_names,
        composition=composed.composition,
        provider=_transport_provider(),
        max_turns=claim.spec.budgets.max_turns,
        skills=skill_manager,
        transports=transports if run.deep or run.sub_agent is not None else None,
        sub_agent=sub_agent,
        deep=run.deep,
        plan=plan_ledger,
        log=log,
    )
    routes = disclosure.routes()
    effects = catalog.effects()
    if composed.coordinates:
        routes.update(
            DeepRoutes(registry, shaper, log, plan=plan_ledger, asks=step_asks(effects, governance)).routes()
        )
    if "plan" in tools.by_source:
        routes.update(PlanRoutes(plan_ledger, step_asks(effects, governance)).routes())
    if sandbox.receipted is not None:
        routes.update(sandbox.receipted.routes())
        effects = sandbox.receipted.effects(effects)
    # ADR-RUN-01 D3: every result recorded with its provenance; a granted tool without a rule is refused here.
    deferred_ids = {t.name: t.tool_id for t in catalog.tools if t.deferred}
    stamp = ProvenanceStamp.for_tools(
        [*effects, *deferred_ids], catalog.provenance(), journal=claim.journal_accepts(PROVENANCE_RECORDS),
        sources_of=call_provenance,
        late_rule=lambda name: MCP_RULE if lazy is not None and lazy.owns(name) else None,
        log=log, review=review,
    )
    port = RegistryToolPort(
        registry,
        effects,
        integrity=catalog.integrity(),
        provenance=stamp,
        asks_user=ASKS_USER,
        present=presenter.result,
        routes=routes,
        hooks=sandbox.hooks.registry,
        scope=shaper.tool_scope,
        late=lambda name: late_effect(registry, name, deferred_ids, lazy),
        late_integrity=lambda name: "external" if lazy is not None and lazy.owns(name) else None,
        call_effects=call_effects_for(registry, (t.name for t in catalog.tools)),
        after_call=lazy.drain if lazy is not None else None,
        deferred=deferred_ids,
        prepare=first_use_preparer(tools.sources, registry),
        more_names=lazy.granted_names if lazy is not None else None,
        withheld=withheld_from_sub_agent() if run.sub_agent is not None else None,
        log=log,
        rate=untrusted_integrity,
        around=env.tool_call_scope,
        # C7.14: the same connector read asked again in this run is answered from the first answer.
        read_cache=RunReadCache(
            ReadScope(run.org_id, run.actor_id or "", run.run_id),
            cacheable_reads(effects, catalog.provenance()),
            log=log,
            metrics=READ_CACHE_METRICS,
            executor="loop",
        ),
    )
    return _Shaping(citations, presenter, shaper, sub_agent, domain_tool, effects, port, plan_ledger)


async def _deep_and_steps(
    claim: NodeClaim,
    run: RunContext,
    env: ToolEnvironment,
    model: ResolvedModel,
    registry: ToolRegistry,
    composed: _Composed,
    shaping: _Shaping,
    transports: Any,  # noqa: ANN401 - the run's transport registry
    child_budget: Any,  # noqa: ANN401 - the metered model's child budget, or None
    log: HarnessLog,
    lazy: LazyMcpServers | None = None,
) -> tuple[_DeepMode, list[PreRunStep]]:
    """Deep mode's delegation and director, or a sub-agent's result; and the step that states the goal."""
    from app.harness.assembly.deep import (
        DeepCitations,
        DeepDelegation,
        DeepDirector,
        SubAgentResult,
        planning_message,
        sub_agent_pool,
    )

    catalog, composition, flat_names = composed.catalog, composed.composition, composed.flat_names
    pre_run: list[PreRunStep] = []
    deep_mode = _DeepMode()
    if run.coordinates:
        pool = sub_agent_pool(list(composition.top_level) if composition is not None else flat_names)
        deep_mode = _DeepMode(
            delegation=DeepDelegation(
                registry,
                pool=pool,
                grants={t.name: t.grant for t in catalog.tools if t.grant is not None},
                model_name=model_name(run),
                max_turns=claim.spec.budgets.max_turns,
                max_tokens=claim.spec.budgets.max_tokens,
                citations=DeepCitations(shaping.citations, env.context, log),
                log=log,
                budget=child_budget,
            ),
            director=DeepDirector(
                planning_message=planning_message(registry),
                max_turns=claim.spec.budgets.max_turns,
                log=log,
                plan=shaping.plan,
            ),
        )
    routing = shaping.sub_agent is None and routes(run)
    if routing or (shaping.sub_agent is None and not run.deep and offers_code_task(run)):
        # Without routing, the director still presents a cut-short code_task's partial answer (SPENDCONT).
        deep_mode = _DeepMode(director=_route_director(
            claim, run, env, log, routing=routing,
            offered=list(composition.top_level) if composition is not None else flat_names,
        ))
    if shaping.sub_agent is not None:
        deep_mode = _DeepMode(child_result=SubAgentResult(context=env.context, domain_tool=shaping.domain_tool))
        pre_run.append(SubAgentGoalStep(env, run, shaping.shaper, log))
    else:
        attachment_text = "\n\n".join(
            t
            for t in (
                await _attachments(env, run, claim, log),
                await _input_files(env, run, claim),
                trigger_note(claim.trigger_context),
            )
            if t
        )
        pre_run.append(
            GoalStep(
                env,
                run,
                model,
                transports,
                shaping.shaper,
                tool_names=list(composition.top_level) if composition is not None else flat_names,
                attachment_text=attachment_text,
                registry=registry,
                deep=run.deep,
                routing=routing,
                lazy=lazy,
                log=log,
            )
        )
    return deep_mode, pre_run


def job_ask_tool(run: RunContext, env: ToolEnvironment, offered: Sequence[str]) -> str | None:
    """The question tool a job's question is asked with (ADR-RUN-03 D2): the run's own card, when its model is
    offered one and the turn may still ask."""
    from app.harness.assembly.grounding_gate import can_ask

    if not can_ask(run, has_ui_client=bool(env.context.has_ui_client)):
        return None
    return next((name for name in sorted(ASKS_USER) if name in offered), None)


def _route_director(
    claim: NodeClaim,
    run: RunContext,
    env: ToolEnvironment,
    log: HarnessLog,
    *,
    routing: bool = True,
    offered: Sequence[str] = (),
) -> DeepRouteDirector:
    """DEEPROUTE's director over the run's own limits: its model's window and its token and turn budgets."""
    from harness_core.contracts.generated.policy_data import RELEASE_DEFAULTS
    from harness_core.kernel.budget.pricing import get_context_window

    window = env.context.configured_context_window or get_context_window(model_name(run))
    return DeepRouteDirector(
        query=hand_off_request(run),
        thresholds=thresholds_from_env(log),
        window_tokens=window,
        # A run that may hand off pins the engine backstop (SPENDCONT); its own loop's spend share is still measured
        # against the loop's, or the spend threshold would never fire.
        max_tokens=min(claim.spec.budgets.max_tokens, RELEASE_DEFAULTS.budgets.loop_max_tokens),
        max_turns=claim.spec.budgets.max_turns,
        log=log,
        routing=routing,
        worded=claim.journal_accepts(OUTCOME_LEDGER) and claim.journal_accepts(OUTCOME_WORDS),
        ask_tool=job_ask_tool(run, env, offered),
    )


@dataclasses.dataclass(frozen=True)
class _BuiltParts:
    catalog: Catalog
    hooks: Any
    activity: BoundedCommandActivity | None
    port: RegistryToolPort
    effects: dict[str, ToolEffect]
    presenter: PipesHubPresenter
    citations: CitationJournal
    shaper: PipesHubTurnShaper
    deep_mode: _DeepMode
    pre_run: list[PreRunStep]
    post_run: Any
    after_run: list[AfterRunStep]


@dataclasses.dataclass(frozen=True)
class _RunMemory:
    source: MemorySource
    recalled: bool
    write_scopes: tuple[MemoryScope, ...]


def remember_tool(memory: _RunMemory | None, registry: ToolRegistry) -> SourcedTool | None:
    """Registers ``remember`` for a run whose pin grants a write scope (P7.3) and names it for the run's catalog; the
    loop and an engine run are offered it the same way, through the run's tool gateway (QA6-F7). ``None``: no pin,
    no scope or no memory route, and nothing is registered."""
    if memory is None or not memory.write_scopes:
        return None
    registry.register_tool(RememberTool(memory.source, memory.write_scopes))
    return SourcedTool(REMEMBER_TOOL, f"{PLATFORM}@memory/{REMEMBER_TOOL}", None, provenance=NO_CONTENT_RULE)


def run_memory(run: RunContext, claim: NodeClaim, log: HarnessLog) -> _RunMemory | None:
    """What the run's admission pinned (P7.3): nothing without a pin, so a flag-off run is unchanged."""
    pin = run.memory
    if pin is None or (not pin.recalled and not pin.write_scopes):
        return None
    if claim.memories is None:
        log.warning("memory.unavailable", reason="memory_unavailable", detail="no_memory_route")
        return None
    scopes = tuple(cast("MemoryScope", scope) for scope in pin.write_scopes if scope in MEMORY_SCOPES)
    return _RunMemory(claim.memories, bool(pin.recalled), scopes)


def _context_host(
    shaper: ContextHost, memory: _RunMemory | None, workspace: RunCodeWorkspace | None = None
) -> ContextHost:
    """The compiler's host (P4, ``HARNESS_CONTEXT_COMPILER``): a run given memories gets them in the tail, and a
    run with a workspace its index (LONGTASK-F6)."""
    host = MemoryContextHost(shaper) if memory is not None and memory.recalled else shaper
    return WorkspaceContextHost(host) if workspace is not None else host


def _viewed(shaper: Any, workspace: RunCodeWorkspace | None) -> Any:  # noqa: ANN401 - the run's turn shaper
    """The turn shaper, with the workspace's index before the input when the run has a workspace (LONGTASK-F6)."""
    return WorkspaceContextShaper(shaper) if workspace is not None else shaper


@dataclasses.dataclass(frozen=True)
class _DeepMode:
    """What deep mode adds to a run (K-L): a root's delegation and director, a child's result."""

    delegation: DeepDelegation | None = None
    director: DeepDirector | DeepRouteDirector | None = None
    child_result: SubAgentResult | None = None


def _with_coordination_tools(registry: ToolRegistry, catalog: Catalog) -> Catalog:
    """The coordinating run's coordination tools (``RunContext.coordinates``); legacy's tree shares one registry,
    but a sub-agent here holds none, so an empty tool list (the whole registry) cannot offer them (QAFINAL-FU-PW113).
    The planning tools are routed and read nothing outside the run; ``spawn_agent``
    is never a tool call here (it delegates), so the catalog never grants it."""
    from app.agent_loop_lib.tools.builtin.planning.create_plan import CreatePlanTool
    from app.agent_loop_lib.tools.builtin.planning.critique_plan import CritiquePlanTool
    from app.agent_loop_lib.tools.builtin.planning.verify_result import VerifyResultTool
    from app.agents.agent_loop.loops.orchestrator import DomainSpawnAgentTool
    from app.harness.assembly.deep import ROUTED_TOOLS

    for tool in (CreatePlanTool(), CritiquePlanTool(), DomainSpawnAgentTool(), VerifyResultTool()):
        if tool.name in ROUTED_TOOLS:
            _ = registry.register_tool_if_absent(tool, extra_tags=(TAG_EFFECT_READ,))
        else:
            _ = registry.register_tool_if_absent(tool)
    routed = tuple(
        SourcedTool(name, f"{PLATFORM}@builtin/{name}", None, provenance=NO_CONTENT_RULE) for name in ROUTED_TOOLS
    )
    return dataclasses.replace(catalog, tools=catalog.tools + routed)


def _sub_agent_spec(
    env: ToolEnvironment, run: RunContext, registry: ToolRegistry, claim: NodeClaim
) -> tuple[Any, Any]:  # (AgentSpec | None, AgentTool | None)
    """The ``AgentSpec`` a deep-mode child runs as: the domain agent it delegated
    to, or legacy ``domain_spec_factory``'s role sub-agent; UI-only tools never
    reach a child (``AgentRuntime.run_child``)."""
    sub = run.sub_agent
    if sub is None:
        return None, None
    from app.agent_loop_lib.agent.loops import ReActLoop
    from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
    from app.agent_loop_lib.tools.builtin.coordination.agent_tool import AgentTool
    from app.agents.agent_loop.sub_agent_prompt import build_sub_agent_prompt
    from harness_core.tools.tags import TAG_UI_ONLY

    domain = registry.resolve_by_name(sub.domain_agent) if sub.domain_agent and registry.has(sub.domain_agent) else None
    if isinstance(domain, AgentTool):
        spec = domain.spec
    else:
        domain = None
        spec = AgentSpec(
            name=f"pipeshub-subagent-{sub.role}",
            system_prompt=build_sub_agent_prompt(sub.role, env.context),
            tool_names=list(sub.tool_names),
            model=ModelSpec(
                provider=_transport_provider(), model=model_name(run),
                context_window=env.context.configured_context_window,
            ),
            loop=ReActLoop(),
        )
    from app.agents.agent_loop.factory import session_tool_grant

    names = [n for n in spec.tool_names if TAG_UI_ONLY not in registry.tags_for_name(n)]
    # The child's session list is fixed at its first request (R4): what the loop granted it mid-run is in it now.
    names = session_tool_grant(names, env.context, registry) if names else names
    return spec.model_copy(update={"tool_names": names, "max_turns": claim.spec.budgets.max_turns}), domain


def _command_run(run: RunContext, registry: ToolRegistry) -> CommandDirector | None:
    """A person's ``/run`` (SLASH3): its director, or ``None`` for a turn the model takes. It runs only where the
    chat's agent may run code: without ``run_code`` in the run's grant it is refused, never run some other way."""
    if run.sandbox_command_timeout_s is None:
        return None
    from app.harness.assembly.command_run import CommandDirector, command_line
    from harness_core.driver.refusal import RunRefused

    if not registry.has("run_code"):
        raise RunRefused("tool_not_granted", "This chat can't run code")
    return CommandDirector(line=command_line(run.query), timeout_s=run.sandbox_command_timeout_s)


def _typed_line(run: RunContext) -> str | None:
    """A person's ``/run`` line, which its activity shows instead of the launcher program that runs it."""
    if run.sandbox_command_timeout_s is None:
        return None
    from app.harness.assembly.command_run import command_line

    return command_line(run.query)


def _receipted_sandbox(
    env: ToolEnvironment,
    registry: ToolRegistry,
    log: HarnessLog,
    *,
    activity: CommandActivity | None = None,
    journal_commands: bool = False,
    typed_line: str | None = None,
) -> ReceiptedSandboxTools | None:
    """``run_code`` as a receipted effect (P5.11) when the sandbox source registered it.

    Without the conversation's artifact registry there is nowhere durable to keep a
    receipt: the tools keep their plain ``execute`` effect (a lost outcome goes to a person)."""
    from app.harness.assembly.run_code import RECEIPTED_TOOLS, ReceiptedSandboxTools
    from app.harness.sandbox.receipts import RegistryExecutionReceipts
    context = env.context
    if context.sandbox_manager is None or not any(registry.has(n) for n in RECEIPTED_TOOLS):
        return None
    if context.artifact_registry is None or not context.conversation_id:
        log.warning("run_code.receipts_unavailable", reason="artifact_registry_missing")
        return None
    receipts = RegistryExecutionReceipts(
        # A service-account agent's receipts belong to the person chatting, as its other artifacts (main #4433).
        context.artifact_registry, context.artifact_actor, context.conversation_id
    )
    return ReceiptedSandboxTools(
        registry,
        context,
        receipts,
        log=log,
        activity=activity,
        journal_commands=journal_commands,
        typed_line=typed_line,
    )


def _session_workspace(
    registry: ToolRegistry, workspace: RunCodeWorkspace | None, context: Any, log: HarnessLog  # noqa: ANN401 - the legacy AgentContext
) -> None:
    """``run_code`` keeps what a call leaves under ``$WORKSPACE_DIR`` when the run has a session workspace, and its
    description says so (QAFIX4 B1), for every caller of the tool: the run's loop and its delegates (LONGTASK-B1)."""
    from app.agent_loop_lib.tools.builtin.sandbox.coding_sandbox import (
        CodingSandboxTool,
    )
    from app.harness.assembly.run_code import SessionWorkspaceCalls

    tool = registry.resolve_by_name("run_code") if registry.has("run_code") else None
    if isinstance(tool, CodingSandboxTool):
        calls = SessionWorkspaceCalls(workspace, context, log) if workspace is not None else None
        tool.set_session_workspace(workspace is not None, calls)


def _describe_run_code(registry: ToolRegistry, top_level: list[str]) -> None:
    """``run_code``'s description names the web tools the run's top level has and whether parent
    results are staged, as ``PipesHubAgentFactory.create`` sets them once composition is decided."""
    from app.agent_loop_lib.tools.builtin.sandbox.coding_sandbox import (
        CodingSandboxTool,
    )
    from app.modules.agents.context.tool_names import granted, granted_any

    if not registry.has("run_code"):
        return
    tool = registry.resolve_by_name("run_code")
    if not isinstance(tool, CodingSandboxTool):
        return
    tool.set_advertise_parent_results("coding_agent" in top_level)
    web_agent = granted("web_agent", top_level)
    tool.set_web_tool_names((web_agent,) if web_agent else granted_any(["web_search", "fetch_url"], top_level))


def late_effect(
    registry: ToolRegistry, name: str, deferred: Mapping[str, str] | None = None, lazy: LazyMcpServers | None = None,
) -> ToolEffect | None:
    """The effect of a tool the run registers mid-run: one it registers once records exist (``LATE_TOOLS``), or a
    granted MCP tool built at its server's first use (D-LAZY-3, a server granted whole naming it then), classified
    from the live listing; ``None`` for any other."""
    tool_id = (deferred or {}).get(name) or (lazy.tool_id(name) if lazy is not None else None)
    if tool_id is not None:
        effect = classify_tool_effect(name, registry.tags_for_name(name))
        return dataclasses.replace(effect, tool_id=tool_id)
    if name not in LATE_TOOLS:
        return None
    effect = classify_tool_effect(name, registry.tags_for_name(name))
    return dataclasses.replace(effect, tool_id=f"{PLATFORM}@dynamic/{name}")


def first_use_preparer(
    sources: Iterable[ToolSource], registry: ToolRegistry
) -> Callable[[Sequence[str]], Awaitable[Mapping[str, str] | None]] | None:
    """The step before a call is planned (the kernel's) or resolved (the gateway's): the servers it names listed at
    their first use."""
    lazy = lazy_servers(sources)
    return first_use_preparer_of(lazy, registry) if lazy is not None else None


def first_use_pins(sources: Iterable[ToolSource]) -> Callable[[], Mapping[str, Mapping[str, str]]] | None:
    """The run's MCP pins as they stand when it spawns a child, which inherits them (D-LAZY-5, R31HOT2): its first-use
    pins, or what its claim listed when it lists there (R38IDENT-FU-13, ``McpSource.pins``)."""
    mcp = next((s for s in sources if isinstance(s, McpSource)), None)
    return mcp.pins if mcp is not None else None


def first_use_preparer_of(
    lazy: LazyMcpServers, registry: ToolRegistry
) -> Callable[[Sequence[str]], Awaitable[Mapping[str, str] | None]]:
    async def prepare(names: Sequence[str]) -> Mapping[str, str] | None:
        return await lazy.prepare(registry, names)

    return prepare


class _Restores:
    """Every in-process state a resumed attempt rebuilds from its run's journal, in order."""

    def __init__(self, *restores: RunRestore) -> None:
        self._restores = restores

    async def restore(self, events: Sequence[JournalEvent]) -> None:
        for restore in self._restores:
            await restore.restore(events)


def _restores(citations: RunRestore, lazy: LazyMcpServers | None) -> RunRestore:
    if lazy is None:
        return citations
    from app.agents.agent_loop.mcp_lazy import PinRestore

    return _Restores(citations, PinRestore(lazy))


def lazy_servers(sources: Iterable[ToolSource]) -> LazyMcpServers | None:
    """The attempt's MCP servers listed at first use, when its MCP source loads them lazily."""
    return next((s.servers for s in sources if isinstance(s, McpSource) and s.servers is not None), None)


async def _attachments(env: ToolEnvironment, run: RunContext, claim: NodeClaim, log: HarnessLog) -> str:
    """The legacy attachment resolution: its text joins the goal once (the goal
    step journals it); its images and citation maps are rebuilt on every attempt,
    and so is the list of the turn's files the model was not given, which the
    answer names (R33JGAPS-FU-6)."""
    from app.agents.agent_loop.hooks import resolve_attachments_for_goal
    from app.harness.assembly.attachment_limits import record_unread, unread_attachments
    from harness_core.journal.types import ATTACHMENT_LIMITS

    context = env.context
    kept = list(context.tool_state.get("attachments") or [])
    text, images = (
        await resolve_attachments_for_goal(context, logging.getLogger("app.harness.assembly.attachments"))
        if kept
        else ("", [])
    )
    if images:
        context.attachment_image_blocks = images
    unread = unread_attachments(
        run.attachments, kept, context.tool_state.get("virtual_record_id_to_result") or {},
        multimodal=bool(context.is_multimodal_llm),
        counted=(counted := claim.journal_accepts(ATTACHMENT_COUNT)),
    )
    record_unread(context.tool_state, unread, typed=claim.journal_accepts(ATTACHMENT_LIMITS), counted=counted)
    if run.attachments:
        log.info("run.attachments", resolved=bool(text), images=len(images), unread=len(unread))
    return text


async def _input_files(env: ToolEnvironment, run: RunContext, claim: NodeClaim) -> str:
    """The files the turn names (``inputArtifacts``), listed for the loop that may hand them to ``code_task`` (CTL-6)."""
    if not claim.input_artifacts:
        return ""
    from app.harness.engines.inputs import input_listing
    from app.services.artifact_registry.models import Actor

    return await input_listing(
        env.context.artifact_registry, Actor(org_id=run.org_id, user_id=run.actor_id), claim.input_artifacts, run.session_id
    )


def _transport_provider() -> str:
    from app.agents.agent_loop.factory import _transport_provider as provider

    return provider()


__all__ = ["Catalog", "PipesHubRuntimeAssembler", "RunContainer", "build_catalog", "late_effect", "run_org_context"]
