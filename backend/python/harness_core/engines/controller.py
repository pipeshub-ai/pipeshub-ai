"""``EngineController``: one engine turn's lifecycle around an engine adapter (ARCHITECTURE §4.1).

``run_turn``: refuse what cannot run (version, grant size, image, isolation,
skills) before anything is created → mount the run's tool endpoint →
``acquire(idempotency_key=attempt)`` with an egress allowlist of exactly the two
gateways → materialize the workspace → seed the skills → restore the engine
state (or resume from the workspace, flagged) → drive the turn, autosaving every
``autosave_changes`` file changes, every ``autosave_interval_s`` and before every
gateway tool call, publishing ``out/`` files as drafts at each save → final
save → finalize the drafts on success → checkpoint → release.

``cancel``: interrupt → final save → drafts discarded → release (no checkpoint).

With a snapshot policy (P5.9b) the sandbox's caches are restored before the engine
opens and snapshotted at each autosave and at turn end (``engine.snapshots``).

Product-neutral: ports only, ids opaque, time and randomness injected (Loom).
The caller (a durable kernel, a Loom host) binds the run's tool endpoint, its
artifact publisher and the event sink to its own journal.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlsplit

from harness_core.access.types import ModelApi
from harness_core.artifacts.types import Derivation, OutputFile, PublishedArtifact
from harness_core.budget.run_budget import (
    Allowance,
    PersonWaits,
    RunBudget,
    SynthesisReason,
)
from harness_core.budget.types import MICRO_USD, LedgerUsage
from harness_core.context.user_compact import CompactReport
from harness_core.contracts.canonical_json import canonical_digest
from harness_core.engines.allowance import (
    WRITE_BY_KEY,
    left_words,
    write_by_instruction,
)
from harness_core.engines.attempt_labels import ATTEMPT_LABEL, RUN_LABEL, bound_labels
from harness_core.engines.background import process_table, stop_started, stopped_note
from harness_core.engines.cache_mounts import (
    MIRRORS_GITCONFIG,
    CacheMount,
    GitConfig,
    SharedCacheMounts,
    mirrors_gitconfig,
    mounts_git_config,
    with_mounts,
)
from harness_core.engines.chain import engine_session_chain
from harness_core.engines.durable.inputs import MAX_INPUT_BYTES
from harness_core.engines.environments import (
    EnvironmentDefinition,
    EnvironmentSnapshotter,
    prepare_environment,
)
from harness_core.engines.errors import (
    CompactFailed,
    CompactUnsupported,
    EgressUnavailable,
    EngineContractUnsupported,
    EngineCrashed,
    EngineImageChanged,
    EngineImageNotConfigured,
    EngineImageUnavailable,
    EngineRefusal,
    EngineStateIncompatible,
    EngineStateUnavailable,
    GrantTooLarge,
    ModelApiUnavailable,
    SkillNotAllowed,
)
from harness_core.engines.events import (
    ApprovalRequested,
    ContextCompacted,
    EngineError,
    EngineEventMeta,
    FileChanged,
    NativeUnknown,
    RepositoryReady,
    SignInProgress,
    TurnCompleted,
    UsageReported,
)
from harness_core.engines.git_checkout import (
    PUSH_ROUTE,
    SETUP_ROUTE,
    GitCheckout,
    GitSetupFailed,
    PushOutcome,
    agent_git_env,
    agent_policy,
    clone,
    git_config_env,
    gitmodules,
    phase_credential,
    push_session_branch,
    setup_policy,
    submodule_report,
    trust_proxy,
    verify_agent_phase,
)
from harness_core.engines.input_staging import (
    INPUT_FETCH_TOOL,
    STAGE_AT_START_MAX_BYTES,
    deferred_inputs,
    fetched,
    input_path,
    not_placed,
    not_waiting,
)
from harness_core.engines.isolation import isolation_refusal
from harness_core.engines.native import NativeTape, RecordsNative
from harness_core.engines.native_policy import POLICY_CHECK_TOOL
from harness_core.engines.outcome.faults import fault_of
from harness_core.engines.policy import (
    check_grant_size,
    check_profile_admits,
    model_api_for,
)
from harness_core.engines.ports import NativeTranscriptSink, WitnessesToolGateway
from harness_core.engines.profiles import profile_digest
from harness_core.engines.publish_tool import ARTIFACT_PUBLISH_TOOL, checked_output_path
from harness_core.engines.publish_tool import invalid_path as invalid_output_path
from harness_core.engines.publish_tool import missing as missing_output
from harness_core.engines.publish_tool import published as published_output
from harness_core.engines.publish_tool import refused as refused_output
from harness_core.engines.publish_tool import unavailable as publish_unavailable
from harness_core.engines.publish_tool import unchanged as unchanged_output
from harness_core.engines.rate import EngineEventFlood, EventRate, EventRateLimiter
from harness_core.engines.repository_checkpoint import read_commit, restore_commit
from harness_core.engines.repository_instructions import (
    read_repository_instructions,
    with_repository_instructions,
)
from harness_core.engines.signin import (
    KeptSignIn,
    PlanSignIn,
    SignInHosts,
    SignInKeep,
    SignInRefused,
    SignInRelay,
    SignsIn,
)
from harness_core.engines.sites import install_site_trust, site_env
from harness_core.engines.snapshots import SandboxSnapshots, SnapshotPolicy
from harness_core.engines.tool_folder import ToolFolder, granted_tools
from harness_core.engines.types import (
    SEALED_DIR,
    EngineTask,
    FollowUp,
    GatewayEndpoints,
    isolation_rank,
)
from harness_core.engines.usage import (
    RunUsageLedger,
    TurnUsage,
    UsageSource,
    turn_usage,
)
from harness_core.failpoints import afailpoint
from harness_core.gateway.errors import BindingMismatch
from harness_core.gateway.result_cap import ENGINE_RESULT_CAP_CHARS, run_result_cap
from harness_core.gateway.types import McpResult
from harness_core.interactions.ports import RunBinding
from harness_core.kernel.core.stall import RoundCall, StallState, next_stall, stalled
from harness_core.logs import HarnessLog
from harness_core.repos.fetch import HAND_OFF_SLACK_MB, MIB
from harness_core.sandbox.egress.sealed import new_placeholder
from harness_core.sandbox.errors import (
    IsolationUnavailable,
    SandboxOutOfMemory,
    memory_limit_text,
)
from harness_core.sandbox.pool import WarmShape, shape_of
from harness_core.sandbox.ports import hold_sandbox_flow, memory_exhausted
from harness_core.sandbox.streams import collect
from harness_core.sandbox.tar import put_files
from harness_core.sandbox.types import (
    FAREWELL_LABEL,
    LIFETIME_LABEL,
    BrokerHealth,
    EgressDestination,
    EgressPolicy,
    ImageResources,
    LeaseRef,
    MountSpec,
    ResourceCaps,
    Resources,
    RuntimeMode,
    SandboxSpec,
    TokenRoute,
    engine_image_digests,
    engines_unavailable,
)
from harness_core.sandbox.workspace_fs import WorkspacePathRefused
from harness_core.skills.materializer import skills_mount
from harness_core.skills.types import SkillBundleError
from harness_core.telemetry.carrier import trace_env
from harness_core.telemetry.memory import NOOP_TRACER
from harness_core.telemetry.ports import RunRef
from harness_core.workspace.changes import ChangeSet
from harness_core.workspace.forks import (
    WRITER_LEASE_MS,
    session_scope,
    session_workspace_id,
)
from harness_core.workspace.safe_names import UnsafeName, check_name_path
from harness_core.workspace.sync.saver import (
    PartialSave,
    Refused,
    Saved,
    SaveResult,
    WorkspaceSaver,
)
from harness_core.workspace.sync.storage_classes import reference_identity
from harness_core.workspace.sync.strategies import TAR
from harness_core.workspace.sync.types import ON_FIRST_READ
from harness_core.workspace.types import (
    SYMLINK_MODE,
    ObjectScope,
    WorkspaceFile,
    WorkspaceWriterBusy,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping

    from pydantic import JsonValue

    from harness_core.artifacts.ports import ArtifactPublisher
    from harness_core.artifacts.types import PublishReport
    from harness_core.clock import Clock
    from harness_core.engines.events import (
        EngineEvent,
        EngineState,
        EngineStateBlob,
        TurnEndReason,
    )
    from harness_core.engines.native import TranscriptPart
    from harness_core.engines.ports import (
        EngineAdapter,
        EngineCheckpointStore,
        EngineEventSink,
        EngineRegistry,
        EngineSession,
        SandboxSnapshotStore,
        SourceLedger,
        ToolGatewayHost,
    )
    from harness_core.engines.snapshots import SnapshotReason
    from harness_core.engines.types import (
        EngineCapabilities,
        EngineInput,
        EngineLaunch,
        EngineProfile,
        EngineVersion,
    )
    from harness_core.gateway.ports import Progress, ToolEndpoint
    from harness_core.gateway.types import (
        GatewayBinding,
        GatewayClaims,
        McpTool,
    )
    from harness_core.journal.types import CompactAsk
    from harness_core.knowledge.bulk_export import ShelfRelay
    from harness_core.sandbox.ports import SandboxBroker, SandboxHandle
    from harness_core.skills.ports import SkillBundleBuilder, SkillMaterializer
    from harness_core.skills.types import SkillBundleRef, SkillPin
    from harness_core.telemetry.ports import HarnessTracer
    from harness_core.workspace.changes import CommitReason
    from harness_core.workspace.ports import (
        WorkspaceStore,
        WorkspaceSync,
        WorkspaceWriters,
    )
    from harness_core.workspace.sync.storage_classes import Reference
    from harness_core.workspace.sync.strategies import SyncStrategies
    from harness_core.workspace.sync.types import Collected, SyncState
    from harness_core.workspace.types import ObjectRef, WorkspaceManifest

LOGGER = "harness_core.engines.controller"
MODEL_ROUTE = "gateway:model"
TOOLS_ROUTE = "gateway:tools"
# Strongest first: a run gets the strongest isolation the broker offers at or above its floor.
_MODE_PREFERENCE = (
    RuntimeMode.MICROVM,
    RuntimeMode.GVISOR,
    RuntimeMode.K8S,
    RuntimeMode.MANAGED,
    RuntimeMode.DOCKER_HARDENED,
)
# Rendered in a browser they run script in PipesHub's origin; refused until the sandboxed preview (#3596).
_ACTIVE_CONTENT_TYPES = frozenset({"text/html", "application/xhtml+xml", "image/svg+xml"})
_ACTIVE_CONTENT_SUFFIXES = (".html", ".htm", ".xhtml", ".svg")
OUTPUT_ACTIVE_CONTENT = "output_active_content"
OUTPUT_SYMLINK = "output_symlink"
# SEC-FS-02: a file name not every computer can hold as one file (``CON.txt``, ``a:b``, ``x.``) is not published.
OUTPUT_UNSAFE_NAME = "output_unsafe_name"
# The write lease could not be renewed before it could lapse; the turn stops rather than risk a second writer.
WRITER_LEASE_UNRENEWED = "writer_lease_unrenewed"
OUTPUTS_FOLDER = "."
# The image's ``ph`` (``harness_engines/pipeshub/install.sh``), which moves a legacy in-workspace checkout (ADR-RUN-02).
PH_BIN = "/usr/local/bin/ph"
RESUME_NOTE = (
    "The engine's earlier session could not be restored, so its conversation history is gone. "
    "Continue the task from the files in the workspace."
)
# The model gateway's refusal when the run's budget is spent (E3): no event follows it, the turn must stop.
BUDGET_EXHAUSTED = "budget_exhausted"
# P6.5: an insufficient budget means the next call cannot be paid either; both end the turn as a limit.
BUDGET_STOP_CODES = frozenset({BUDGET_EXHAUSTED, "budget_insufficient"})

RestoreSource = Literal["state", "workspace", "fresh"]
LimitName = Literal["wall_time", "tokens", "steps", "budget", "stall"]


def no_secrets() -> tuple[str, ...]:
    return ()


# How often a turn's ticker checks the wall limit, autosaves and renews the write lease, in a worker.
TICK_S = 5.0

@dataclass(frozen=True, slots=True)
class EngineControllerConfig:
    """Deployment configuration. URLs are as the sandbox sees them (through the egress proxy);
    which model gateway answers is the proxy's upstream, so swapping it is configuration."""

    images: Mapping[EngineVersion, str]
    # The digest the release pins for an engine's image (allowlist ``imageDigest``, C2.20); unset, the digest the
    # broker reports for it, and a run pinned at claim keeps its own.
    image_pins: Mapping[EngineVersion, str] = field(default_factory=lambda: {})
    model_base_url: str = "http://model-gateway:8097/v1"
    # What the model gateway serves at that address, in preference order (E3 chat completions, E3b
    # Responses, E3c Anthropic Messages, the Gemini wire); each run gets the first its engine speaks.
    model_apis: tuple[ModelApi, ...] = (
        ModelApi.CHAT_COMPLETIONS,
        ModelApi.RESPONSES,
        ModelApi.ANTHROPIC_MESSAGES,
        ModelApi.GEMINI_GENERATE_CONTENT,
    )
    tools_mcp_url: str = "http://tool-gateway:8097/mcp"
    workspace_root: str = "/workspace"
    state_dir: str = "/state"
    skills_dir: str = "/skills"
    outputs_dir: str = "out"
    # C5.01 (J-26): an input under in/ larger than this reaches the sandbox on its first read (``ph inputs get``).
    input_stage_max_bytes: int = STAGE_AT_START_MAX_BYTES
    # J-26: the largest input a run is given; a host binds its deployment's (PipesHub: HARNESS_INPUT_MAX_MB).
    input_max_bytes: int = MAX_INPUT_BYTES
    resources: Resources = field(default_factory=Resources)
    # Image -> what its sandboxes get over ``resources`` (an engine's own release setting).
    image_resources: Mapping[str, ImageResources] = field(default_factory=dict[str, ImageResources])
    autosave_changes: int = 20
    autosave_interval_s: float = 120.0
    tick_s: float = TICK_S
    keep_warm_s: int = 0
    # The workspace's write lease (P6.4), renewed by the ticker at a third of it.
    writer_lease_ms: int = WRITER_LEASE_MS
    # What a sandbox snapshot holds (P5.9b); unset, no snapshot is taken or restored.
    snapshot: SnapshotPolicy | None = None
    # How long a person's signed-in sandbox is kept between a conversation's turns (SIGNIN-5).
    signin_keep: SignInKeep = field(default_factory=SignInKeep)
    # The read-only folder a shell CLI in the image reads the run's tools from (R3: ``ph``); unset, none.
    tool_folder: ToolFolder | None = None
    # WP-63 part A: where repositories the worker fetched are handed over, read-only, and that folder's size in MB
    # (the per-repository cap); unset, nothing can be handed over.
    repos_mount: tuple[str, int] | None = None
    # WP98-FU-1 (ADR-RUN-02): where ``ph git clone`` checks repositories out, outside the synced workspace, and its
    # size in MB as a scratch mount; it follows the workspace's strategy (scratch beside a scratch workspace, the
    # session's own persistent folder beside a persistent one). Unset, checkouts stay in the workspace.
    references_mount: tuple[str, int] | None = None
    # How fast a turn's events may arrive before the turn is throttled, then failed (SEC-DOS-02).
    event_rate: EventRate = field(default_factory=EventRate)
    # C20.18: where what an environment's setup left is kept for the next session (an extension point); unset, an
    # environment's setup runs in every session.
    environment_snapshots: EnvironmentSnapshotter | None = None
    # Shared read-only caches a run's sandbox mounts (an extension point); unset, none is mounted.
    shared_caches: SharedCacheMounts | None = None

    def __post_init__(self) -> None:
        if self.snapshot is not None:
            folder = () if self.tool_folder is None else (self.tool_folder.target,)
            self.snapshot.check_reserved((self.workspace_root, self.state_dir, self.skills_dir, SEALED_DIR, *folder))

    def resources_for(self, image: str) -> Resources:
        own = self.image_resources.get(image)
        return self.resources if own is None else own.apply(self.resources)

    def endpoints(self, model_placeholder: str, tools_placeholder: str, api: ModelApi | None = None) -> GatewayEndpoints:
        return GatewayEndpoints(
            model_base_url=self.model_base_url,
            model_api=api or self.model_apis[0],
            tools_mcp_url=self.tools_mcp_url,
            token_placeholder=model_placeholder,
            tools_token_placeholder=tools_placeholder,
            policy_hook=None if self.tool_folder is None else self.tool_folder.policy_hook,
        )


@dataclass(frozen=True, slots=True)
class EngineRunContext:
    """One attempt of one engine run, as its caller bound it.

    ``tools`` is the run's tool endpoint (its grant, effect log and citations) and
    ``artifacts`` its publisher; ``restore`` is the last checkpoint the caller
    recorded, ``resume_summary`` what the caller tells the engine when that
    state cannot be used.
    """

    tenant: str
    session_id: str
    run_id: str
    attempt_id: str
    fence: int
    lease_holder: str
    profile: EngineProfile
    tools: ToolEndpoint
    tool_binding: GatewayBinding
    grant_size: int
    artifacts: ArtifactPublisher
    skills: tuple[SkillPin, ...] = ()
    restore: EngineStateBlob | None = None
    resume_summary: str | None = None
    labels: Mapping[str, str] = field(default_factory=lambda: dict[str, str]())
    # A child run's pinned fork (P6.4); unset, the run writes its session's workspace.
    workspace: str | None = None
    # Files the turn starts with under ``in/`` (E1b), seeded before the sandbox is materialized.
    inputs: tuple[EngineInput, ...] = ()
    # What the run's published files may hold (P2.5a); unset, their sources stay unknown and other
    # viewers of the session never see them (P3.5's rule).
    ledger: SourceLedger | None = None
    # The sandbox snapshot the turn starts from (P5.9b): the run's latest, or the carried run's.
    snapshot: ObjectRef | None = None
    # The run's token values as they are now: a snapshot holding one is refused, as one holding a placeholder is.
    secrets: Callable[[], tuple[str, ...]] = no_secrets
    # The run is pinned on its actor's own plan (SIGNIN): the engine signs in inside this turn's sandbox.
    own_login: PlanSignIn | None = None
    # The person's ``/compact [focus]`` (SLASH2 C2): the engine compacts its restored session before this turn.
    compact: CompactAsk | None = None
    # What the run's spec pins (C2.07): its engine image digest (``pins.sandboxImage``) and the ``/pipeshub``
    # contract its image must declare (``x.pipeshub.release``). Unset: whatever the deployment runs now.
    image_pin: str | None = None
    contract_pin: int | None = None
    # Where the turn's usage is read (OWD-8: the gateway ledger is the authority); unset, the engine's own report.
    usage: RunUsageLedger | None = None
    # The repository the run works on (GIT-FLOW): cloned in a setup phase when the workspace has none yet.
    checkout: GitCheckout | None = None
    # C20.18: the agent's or the repository's environment, set up (or restored from its post-setup snapshot) in each
    # turn's fresh sandbox before the workspace is materialised.
    environment: EnvironmentDefinition | None = None
    # The members of the run's pinned environment this build does not read (``PinnedEnvironment.extension``): the
    # ``shared_caches`` port reads which caches the run mounts from them.
    environment_extension: Mapping[str, JsonValue] = field(default_factory=dict[str, "JsonValue"])
    # WP-63: the allowed websites this turn's sandbox may reach through the egress proxy, each a ``SiteAccess``
    # destination; the engine's shell gets the proxy and its authority in its environment.
    sites: tuple[EgressDestination, ...] = ()
    # C4.10 ``toolResults.maxChars``: the inline tool-result cap the run's policy lowered to; ``None``: the release's.
    result_cap_chars: int | None = None
    # C4.10 ``sandbox.*``: caps on the template's resources for this run's sandboxes.
    resource_caps: ResourceCaps = ResourceCaps()
    # ADR-RUN-01 D4: the run's budget (its deadline, money and tokens), from which each turn's allowance and phase
    # plan come under the profile's limits; unset, the profile's limits alone bound the turn.
    budget: RunBudget = RunBudget(None, None)
    # The store's clock the budget's deadline is on (``Claim.store_time``, CH-14); unset, this process's wall clock.
    store_now_s: Callable[[], float] | None = None
    # D4: the seconds this attempt waited for a person (an approval held in the turn); the turn's working time is
    # paused while it waits. Unset: nothing pauses it.
    waits: PersonWaits | None = None
    # WP-70: the file the request asked for (``report.md``), named when the turn is told to stop gathering.
    deliverable: str | None = None
    # WP-70: the requested file the workspace still owes (missing, or only a placeholder); a completed turn leaves
    # such a draft unsettled for the run to write. ``None``: every draft settles.
    owed: Callable[[Mapping[str, WorkspaceFile]], str | None] | None = None

    @property
    def metered(self) -> bool:
        """The run's model calls are charged to its ledger account (not its actor's own plan)."""
        return self.usage is not None and self.own_login is None

    def allowance(self, now_s: float) -> Allowance:
        """What a turn of the run starting at ``now_s`` (epoch seconds) may use: the run budget's slice under the
        profile's limits (WP-70, WP-82, ADR-RUN-01 D4)."""
        return self.budget.turn(now_s, self.profile.limits, metered=self.metered)

    def derivation(self) -> Derivation:
        sources = self.ledger.source_records(self.run_id) if self.ledger is not None else None
        return Derivation(task_id=self.run_id, source_records=sources)

    @property
    def binding(self) -> RunBinding:
        return RunBinding(self.tenant, self.session_id, self.run_id)

    @property
    def workspace_id(self) -> str:
        return self.workspace or session_workspace_id(self.session_id)

    @property
    def object_scope(self) -> ObjectScope:
        """The session's partition: workspace objects (a fork's too), skill bundles and engine state share its key."""
        return session_scope(self.tenant, self.session_id)

    def correlation(self) -> dict[str, str]:
        return {
            **self.labels,
            "orgId": self.tenant,
            "sessionId": self.session_id,
            RUN_LABEL: self.run_id,
            ATTEMPT_LABEL: self.attempt_id,
        }


@dataclass(frozen=True, slots=True)
class OutputRefusal:
    path: str
    reason_code: str


@dataclass(frozen=True, slots=True)
class EngineTurnOutcome:
    reason: TurnEndReason
    final_text: str = ""
    cancelled: bool = False
    limit: LimitName | None = None
    workspace: WorkspaceManifest | None = None
    checkpoint: EngineStateBlob | None = None
    published: tuple[PublishedArtifact, ...] = ()
    refused_outputs: tuple[OutputRefusal, ...] = ()
    # Why the store refused the turn's saves (one entry per distinct reason): the outcome ledger's SaveRefused.
    save_refusals: tuple[str, ...] = ()
    restored: RestoreSource = "fresh"
    restore_issue: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    # Where the token counts come from: the gateway ledger (OWD-8), or the engine's own report when it is unread.
    usage_source: UsageSource = "engine"
    # The engine ended the turn itself (``TurnCompleted``); false: its process or sandbox went away.
    ended: bool = False
    # The last ``EngineError`` code the engine reported (a gateway refusal's own code, OC-18).
    error_code: str | None = None
    # What the turn's ``/compact`` did before the turn (``EngineRunContext.compact``); ``None``: none was asked.
    compacted: CompactReport | None = None
    # The engine's own compaction during the turn (``ContextCompacted``), the last one; ``None``: it did not.
    auto_compacted: CompactReport | None = None
    # Native messages of kinds the adapter does not know (EV-13), by kind: the turn's count of each.
    native_unknown: Mapping[str, int] = field(default_factory=lambda: dict[str, int]())


@dataclass(slots=True)
class _GitPhase:
    """A turn's repository phases: ``setup_spec`` is unset once the workspace holds the clone."""

    checkout: GitCheckout
    agent_spec: SandboxSpec
    setup_spec: SandboxSpec | None
    setup_placeholder: str
    # Unset when the repository has no push credential: the engine's git then sends none.
    push_placeholder: str | None
    lease: LeaseRef | None = None
    key: str | None = None

    @property
    def first_spec(self) -> SandboxSpec:
        return self.setup_spec or self.agent_spec

    @property
    def placeholders(self) -> tuple[str, ...]:
        return (self.setup_placeholder, *(() if self.push_placeholder is None else (self.push_placeholder,)))


def placeholder_for(key: bytes, attempt_id: str, route: str) -> str:
    """A placeholder bound to one attempt and route; unguessable without ``key``, never a vendor shape."""
    digest = hmac.new(key, f"{attempt_id}\x00{route}".encode(), hashlib.sha256).hexdigest()
    return f"pxh-{route.rsplit(':', 1)[-1]}-{digest[:32]}"


# Marks a sandbox kept for a conversation's turns (SIGNIN-5), for the kernel's crash-loop reap.
KEPT_LABEL = "pipeshub.signin.kept"
# The broker removes a kept sandbox this long past the sign-in's maximum, whoever holds it (a worker that died).
KEPT_LIFETIME_GRACE_S = 15 * 60
# A kept sandbox outlives the broker's own keep-warm by this much less than its record, so the parker's logout runs
# while the sandbox is still there.
KEPT_LOGOUT_GRACE_S = 2 * 60


def kept_sandbox_key(run: EngineRunContext) -> str:
    """The sandbox one person's sign-in is kept in (SIGNIN-5): their own, for this conversation's workspace and this
    engine profile only. Another person, conversation, fork or agent profile never names it."""
    if run.own_login is None:
        raise ValueError("only a run on its actor's own plan keeps a sandbox")
    parts = (run.tenant, run.session_id, run.workspace_id, run.own_login.user_id, profile_digest(run.profile))
    return "signin:" + hashlib.sha256("\x00".join(parts).encode()).hexdigest()[:40]


def kept_sandbox_labels(run: EngineRunContext, keep: SignInKeep) -> dict[str, str]:
    """A kept sandbox belongs to the conversation, not to one run: no run id (the kernel reaps a finished run's
    sandboxes by it), the broker's own lifetime backstop, and the engine's farewell (its logout) that the broker runs
    when it removes the sandbox itself, so a worker that died never leaves the login unrevoked (GAPS-2). The attempt
    it serves now goes under the bound keys, which its egress binding's token source reads (SIGNFIX: without them
    the proxy had no attempt to ask Node a token for and refused every tool call)."""
    labels = {k: v for k, v in run.correlation().items() if k not in (RUN_LABEL, ATTEMPT_LABEL)}
    return {
        **labels,
        **bound_labels(run.run_id, run.attempt_id),
        KEPT_LABEL: "1",
        LIFETIME_LABEL: str(keep.max_s + KEPT_LIFETIME_GRACE_S),
        FAREWELL_LABEL: run.profile.engine.kind,
    }


def is_active_content(path: str, mime_type: str) -> bool:
    return mime_type.lower() in _ACTIVE_CONTENT_TYPES or path.lower().endswith(_ACTIVE_CONTENT_SUFFIXES)


def _output_name(path: str, folder: str) -> str | None:
    if path == folder:
        return OUTPUTS_FOLDER
    return path[len(folder) + 1 :] if path.startswith(folder + "/") else None


def deliverables(
    saved: Saved, outputs_dir: str
) -> tuple[list[OutputFile], list[OutputRefusal]]:
    """Files written under ``outputs_dir`` by this save: publishable outputs and refusals."""
    return collected_deliverables(saved.collected, outputs_dir)


def collected_deliverables(
    collected: Collected, outputs_dir: str
) -> tuple[list[OutputFile], list[OutputRefusal]]:
    """Files a sandbox's collected changes write under ``outputs_dir``: publishable outputs and refusals.

    A symbolic link is refused, never published (its content is a path, not a deliverable), and so is
    every entry the sync left out under ``outputs_dir``, with the sync's reason; ``outputs_dir`` itself
    is named ``OUTPUTS_FOLDER``.
    """
    folder = outputs_dir.strip("/")
    refused: list[OutputRefusal] = []
    for path, reason in sorted(collected.skipped.items()):
        name = _output_name(path, folder)
        if name is not None:
            refused.append(OutputRefusal(name, reason))
    outputs, more = _deliverable_files(collected.changes.upserts, folder)
    return outputs, [*refused, *more]


def workspace_deliverables(manifest: WorkspaceManifest, outputs_dir: str) -> list[OutputFile]:
    """Every publishable file a workspace revision holds under ``outputs_dir`` (what a save of it would offer)."""
    outputs, _ = _deliverable_files(manifest.files, outputs_dir.strip("/"))
    return outputs


def _deliverable_files(
    files: Mapping[str, WorkspaceFile], folder: str
) -> tuple[list[OutputFile], list[OutputRefusal]]:
    outputs: list[OutputFile] = []
    refused: list[OutputRefusal] = []
    for path, entry in sorted(files.items()):
        name = _output_name(path, folder)
        if name is None:
            continue
        if entry.mode == SYMLINK_MODE:
            refused.append(OutputRefusal(name, OUTPUT_SYMLINK))
            continue
        if name == OUTPUTS_FOLDER:
            continue
        if not _publishable_name(name):
            refused.append(OutputRefusal(name, OUTPUT_UNSAFE_NAME))
            continue
        if is_active_content(name, entry.mime_type):
            refused.append(OutputRefusal(name, OUTPUT_ACTIVE_CONTENT))
            continue
        outputs.append(OutputFile(path=name, content=entry.content, mime_type=entry.mime_type))
    return outputs, refused


def _publishable_name(name: str) -> bool:
    try:
        _ = check_name_path(name)
    except UnsafeName:
        return False
    return True


def engine_mode(offered: Iterable[RuntimeMode], floor: RuntimeMode = RuntimeMode.DOCKER_HARDENED) -> RuntimeMode | None:
    """The strongest mode a broker offers at or above ``floor``: never a weaker one."""
    available = frozenset(offered)
    return next(
        (m for m in _MODE_PREFERENCE if m in available and isolation_rank(m) >= isolation_rank(floor)), None
    )


def _gateway_host(url: str) -> tuple[str, int]:
    parts = urlsplit(url)
    if parts.hostname is None:
        raise ValueError(f"gateway URL {url!r} has no host")
    return parts.hostname, parts.port or (443 if parts.scheme == "https" else 80)


async def adopt_references(sandbox: SandboxHandle, root: str, references: tuple[Reference, ...], log: HarnessLog) -> None:
    """ADR-RUN-02: a checkout an older session left inside the workspace moves onto the references mount at the
    turn's start (``ph git adopt``, a no-op for a link), so no later save streams it. One that cannot move stays where
    it is, kept and never saved, as before."""
    for reference in references:
        try:
            process = await sandbox.exec([PH_BIN, "git", "adopt", f"{root}/{reference.path}"], env={})
            _, err, code = await asyncio.gather(collect(process.stdout), collect(process.stderr), process.wait())
        except Exception as exc:
            log.warning("engine.reference_adopt_failed", errorType=type(exc).__name__)
            continue
        if code != 0:
            log.warning("engine.reference_adopt_failed", exitCode=code, stderrBytes=len(err))


def references_mount_for(workspace: MountSpec, target: str, size_mb: int) -> MountSpec:
    """The references mount beside ``workspace`` (ADR-RUN-02): a scratch one of ``size_mb`` beside a scratch workspace
    (a warm sandbox holds it), the workspace's own persistent folder's sibling beside a persistent one (on disk,
    measured by the broker's quota guard, erased with the session)."""
    if workspace.source is None:
        return MountSpec(target, size_mb=size_mb)
    return MountSpec(target, source=f"{workspace.source}-references")


def engine_sandbox_spec(
    config: EngineControllerConfig,
    *,
    image: str,
    mode: RuntimeMode,
    workspace: MountSpec,
    labels: Mapping[str, str],
    placeholders: tuple[str, str],
    tools_upstream: str,
    tunnels: tuple[str, ...] = (),
    image_pin: str | None = None,
    sites: tuple[EgressDestination, ...] = (),
    resource_caps: ResourceCaps | None = None,
) -> SandboxSpec:
    """The sandbox an engine turn acquires: egress to exactly the two gateways, one placeholder each.

    On a person's own sign-in (SIGNIN, ``tunnels`` given) the model gateway is not reachable at all, so nothing can
    spend the org's account from that sandbox, and the signed vendor hosts are opaque tunnels, port 443, no token."""
    model_host, model_port = _gateway_host(config.model_base_url)
    tools_host, tools_port = _gateway_host(config.tools_mcp_url)
    model_ph, tools_ph = placeholders
    model = () if tunnels else (EgressDestination(model_host, (model_port,)),)
    model_route = () if tunnels else (TokenRoute(model_ph, model_host, MODEL_ROUTE),)
    egress = EgressPolicy(
        destinations=(
            *model,
            EgressDestination(tools_host, (tools_port,), upstream=tools_upstream),
            *(EgressDestination(host, (443,)) for host in tunnels),
            *sites,
        ),
        token_routes=(*model_route, TokenRoute(tools_ph, tools_host, TOOLS_ROUTE)),
    )
    return SandboxSpec(
        image_digest=image,
        mode=mode,
        resources=(resource_caps or ResourceCaps()).apply(config.resources_for(image)),
        egress=egress,
        mounts=(
            workspace,
            MountSpec(config.state_dir),
            skills_mount(config.skills_dir),
            MountSpec(SEALED_DIR, read_only=True),
            *(() if config.tool_folder is None else (config.tool_folder.mount(),)),
            *(() if config.repos_mount is None else (MountSpec(config.repos_mount[0], read_only=True, size_mb=config.repos_mount[1]),)),
            *(() if config.references_mount is None else (references_mount_for(workspace, *config.references_mount),)),
        ),
        labels=labels,
        image_pin=image_pin,
    )


def engine_warm_shapes(config: EngineControllerConfig, images: Iterable[str], mode: RuntimeMode) -> tuple[WarmShape, ...]:
    """What a broker's warm pool pre-creates so engine turns find it: the shape of the spec ``run_turn``
    builds with a scratch workspace (``tar``; a pooled broker never offers ``volume``), per image."""
    shapes: list[WarmShape] = []
    for image in images:
        spec = engine_sandbox_spec(
            config,
            image=image,
            mode=mode,
            workspace=TAR.mount(config.workspace_root),
            labels={},
            placeholders=("pxh-model-shape", "pxh-tools-shape"),
            tools_upstream="http://tool-host/shape",
        )
        shape = shape_of(spec)
        if shape is not None and shape not in shapes:
            shapes.append(shape)
    return tuple(shapes)


def _witnessed(session: EngineSession, endpoint: _SavingEndpoint) -> EngineSession:
    """The opened session, wrapped; one that asks the gateway whether it holds the tools (E2E3-F6) is told what the
    gateway this attempt is served by saw."""
    if isinstance(session, WitnessesToolGateway):
        session.witness_tool_gateway(endpoint)
    return engine_session_chain(session)


# WP-70: how often a running turn reads its run's ledger for the writing reserve: each model call already settles there,
# and one read per turn per tick would put every engine's ticker on Node's ledger route.
MONEY_CHECK_S = 30.0

# What a call the engine makes after its turn began stopping is answered (a stall's last round, WP-62).
STOPPING_TEXT = (
    "Not run: this task is stopping because its recent steps made no progress. Write your answer now from what you "
    "have already gathered."
)


# What a call that reaches the gateway after the engine ended its turn is answered (WP-82: a background command's
# ``ph`` call, still waiting for the save before it when the turn completed).
TURN_ENDED_TEXT = "Not run: this turn has already ended."


class HandOffTooLarge(Exception):
    """A repository that would not fit in what the turn's repository folder has left; nothing was written."""

    def __init__(self, size_bytes: int, left_bytes: int) -> None:
        super().__init__(f"{size_bytes} bytes do not fit in the {left_bytes} the repository folder has left")
        self.size_bytes = size_bytes
        self.left_bytes = left_bytes


class _SavingEndpoint:
    """The run's tool endpoint, saving the workspace before each call (autosave before external actions)."""

    def __init__(
        self, inner: ToolEndpoint, binding: object = None, *, max_result_chars: int = ENGINE_RESULT_CAP_CHARS
    ) -> None:
        self._inner = inner
        self._binding = binding
        # Set while a turn runs: stages an input the sandbox gets on its first read (C5.01, ``INPUT_FETCH_TOOL``).
        self.inputs: Callable[[Mapping[str, JsonValue]], Awaitable[McpResult]] | None = None
        # The run's inline cap on a tool result (C4.10); the gateway cuts or shelves past it.
        self.max_result_chars = max_result_chars
        # Asked before each call: the refusal's text once the turn is stopping or writing (WP-70), and the call is
        # refused without running; ``None`` lets it run. Its argument is the call's tool: writing still publishes.
        self.opening: Callable[[str], Awaitable[str | None]] | None = None
        # The save before a call; the refusal's text when the turn ended while the call waited for it (WP-82).
        self.before: Callable[[], Awaitable[str | None]] | None = None
        self.after: Callable[[str, Mapping[str, JsonValue], McpResult], Awaitable[None]] | None = None
        # What became of a deliverable after the save before the call (``ph publish``, C3.12); None between turns.
        self.publication: Callable[[str], McpResult] | None = None
        # Set once the turn's sandbox holds the tool folder: over-cap results of the tools it shelves go there.
        self.shelf: tuple[ToolFolder, SandboxHandle, HarnessLog] | None = None
        # The turn's repository folder (WP-63 part A): its own read-only mount, sized to the per-repository cap, and
        # the bytes it can still take (a hand-off that would not fit is refused before anything is written).
        self.repos: str | None = None
        self.repos_left = 0
        # ADR-RUN-01 D2: the session's references as the turn's workspace revision records them (earlier turns'
        # fetches, re-materialised from the cache under the approval they were fetched with), and the repositories
        # the worker fetched for this turn (``reference_identity``), the only marks the sync records.
        self.references: tuple[Reference, ...] = ()
        self.vouched: set[str] = set()
        # The turn's pushable repository and the workspace root (GIT-FLOW): what a push the person allowed runs on.
        self.git: tuple[GitCheckout, str] | None = None
        self._listed = 0
        self._connects = 0

    def listed(self) -> int:
        """How many times this attempt's sandbox was served the tool list (``ToolListings``)."""
        return self._listed

    def connected(self) -> int:
        """How many MCP handshakes the gateway answered this attempt's sandbox (``ToolListings``)."""
        return self._connects

    def note_connect(self) -> None:
        self._connects += 1

    def shelves(self, tool: str) -> bool:
        return self.shelf is not None and self.shelf[0].shelves(tool)

    async def shelve(self, name: str, text: str, *, suffix: str = ".md") -> str | None:
        """The shelved file's path; None when it could not be written, and the result is then cut instead."""
        if self.shelf is None:
            return None
        folder, sandbox, log = self.shelf
        try:
            path = await folder.shelve(sandbox, name, text, suffix=suffix)
        except Exception as exc:  # the call's result still reaches the engine, cut
            log.warning("engine.result_shelf_failed", errorType=type(exc).__name__)
            return None
        log.info("engine.result_shelved", chars=len(text))
        return path

    def sandbox_id(self) -> str | None:
        """The turn's sandbox, once it holds the tool folder."""
        return None if self.shelf is None else self.shelf[1].id

    async def hold_flow(self, *, run_id: str, hold: str) -> bool | None:
        """A flow hold on the turn's sandbox through its broker (R38EGRESS-FU-1); ``None`` when it cannot."""
        return None if self.shelf is None else await hold_sandbox_flow(self.shelf[1], run_id=run_id, hold=hold)

    def reference_for(self, address: str) -> Reference | None:
        """The session's recorded reference of the repository at ``address``, if an earlier turn fetched it."""
        identity = reference_identity(address)
        return next((ref for ref in self.references if ref.identity == identity), None)

    def fetched(self, address: str) -> None:
        """The worker fetched the repository at ``address`` for this turn: its checkout is a reference to record."""
        self.vouched.add(reference_identity(address))

    async def hand_off(self, archive: AsyncIterator[bytes], size_bytes: int) -> str | None:
        """Seeds ``archive`` (a complete tar of ``size_bytes``) into the turn's read-only repository folder (WP-63
        part A); the folder's path in the sandbox, or ``None`` while no turn's sandbox holds one. One that would not
        fit in what the folder has left is refused before anything is written (``HandOffTooLarge``), so a refused
        hand-off leaves nothing in the folder."""
        if self.shelf is None or self.repos is None:
            return None
        if size_bytes > self.repos_left:
            raise HandOffTooLarge(size_bytes, self.repos_left)
        _, sandbox, log = self.shelf
        self.repos_left -= size_bytes
        await sandbox.seed_tar(self.repos, archive)
        log.info("engine.handed_off")
        return self.repos

    async def push_session_branch(self, grant: Callable[[str, str], Awaitable[None]]) -> PushOutcome | None:
        """The push a person allowed (``repository_push``): a fresh placeholder, which ``grant(sandbox_id,
        placeholder)`` makes good at the egress proxy for this one push, then the clone's commit to the session branch
        from the turn's sandbox; ``None`` while no turn's sandbox holds a pushable repository."""
        if self.git is None or self.shelf is None:
            return None
        checkout, root = self.git
        _, sandbox, log = self.shelf
        placeholder = new_placeholder()
        await grant(sandbox.id, placeholder)
        pushed = await push_session_branch(sandbox, checkout, workspace_root=root, placeholder=placeholder)
        log.info("engine.git.pushed", pushed=pushed.pushed)
        return pushed

    async def list_tools(self, claims: GatewayClaims) -> tuple[McpTool, ...]:
        tools = await self._inner.list_tools(claims)
        self._listed += 1
        return tools

    async def call_tool(
        self,
        claims: GatewayClaims,
        name: str,
        arguments: Mapping[str, JsonValue],
        *,
        progress: Progress | None = None,
    ) -> McpResult:
        # A policy check (G-25) asks about one of the engine's own calls; it is not an effect and no step.
        if name == POLICY_CHECK_TOOL:
            return await self._inner.call_tool(claims, name, arguments, progress=progress)
        if name == ARTIFACT_PUBLISH_TOOL:
            # Answered here, not by the grant: the binding is checked as the gateway would before anything saves.
            if claims.binding != self._binding:
                raise BindingMismatch("the gateway token is for another binding")
            return await self._publish(arguments)
        if name == INPUT_FETCH_TOOL:
            # The controller's own answer, not a tool of the grant: no effect, no step, no save before it.
            if claims.binding != self._binding:
                raise BindingMismatch("the gateway token is for another binding")
            if self.inputs is None:
                return not_waiting(input_path(arguments), ())
            return await self.inputs(arguments)
        refusal = await self.opening(name) if self.opening is not None else None
        if refusal is not None:
            # The turn is stopping (its last round showed a stall), or writing its deliverable: no new effect starts.
            return McpResult(refusal, is_error=True, disposition="refused", retryable=False)
        ended = await self.before() if self.before is not None else None
        if ended is not None:
            return McpResult(ended, is_error=True, disposition="refused", retryable=False)
        result = await self._inner.call_tool(claims, name, arguments, progress=progress)
        if self.after is not None:
            await self.after(name, arguments, result)
        return result

    async def _publish(self, arguments: Mapping[str, JsonValue]) -> McpResult:
        """``ph publish``: the save every call starts with publishes the file; the answer says what became of it."""
        path = checked_output_path(arguments)
        if isinstance(path, UnsafeName):
            return invalid_output_path(path)
        publication, before = self.publication, self.before
        if publication is None or before is None:
            return publish_unavailable()
        refusal = await self.opening(ARTIFACT_PUBLISH_TOOL) if self.opening is not None else None
        if refusal is not None:
            return McpResult(refusal, is_error=True, disposition="refused", retryable=False)
        ended = await before()
        if ended is not None:
            return McpResult(ended, is_error=True, disposition="refused", retryable=False)
        result = publication(path)
        if self.after is not None:
            await self.after(ARTIFACT_PUBLISH_TOOL, arguments, result)
        return result


@dataclass
class _Turn:
    """The mutable state of one running turn: the sandbox's sync state, counters, the stop reason."""

    run: EngineRunContext
    sandbox: SandboxHandle
    session: EngineSession
    saver: WorkspaceSaver
    sync: WorkspaceSync
    state: SyncState
    sink: EngineEventSink
    clock: Clock
    config: EngineControllerConfig
    log: HarnessLog
    started: float
    last_save: float
    # D4: what the attempt had waited for a person when the turn began (``EngineRunContext.waits``).
    waited_at_start: float = 0.0
    changes: int = 0
    steps: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    limit: LimitName | None = None
    cancelled: bool = False
    stopping: bool = False
    last_saved: WorkspaceManifest | None = None
    published: list[PublishedArtifact] = field(default_factory=list[PublishedArtifact])
    refused: list[OutputRefusal] = field(default_factory=list[OutputRefusal])
    # ADR-RUN-01 D1/D6: each distinct reason the store refused a save for, in order (the outcome's SaveRefused).
    save_refusals: list[str] = field(default_factory=list[str])
    _seen_refusals: set[OutputRefusal] = field(default_factory=set[OutputRefusal], init=False)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Renews the workspace's write lease; None when the controller holds none.
    renew: Callable[[], Awaitable[None]] | None = None
    last_renew: float = 0.0
    # When the lease was last confirmed (taken or renewed): it is ours until then plus the lease.
    renewed_at: float = 0.0
    writer_lost: bool = False
    error_code: str | None = None
    # The turn's PipesHub tool calls, by round (BU-06): a runaway or stalled engine is stopped as a limit stops it. A
    # round is the calls one model turn made, however many at once (a batch script's parallel ``ph`` calls are one);
    # ``round_mark`` is the model's progress when the open round began (``_model_mark``).
    stall: StallState = field(default_factory=StallState)
    round_calls: list[RoundCall] = field(default_factory=list[RoundCall])
    round_mark: object = None
    model_turns: int = 0
    auto_compacted: CompactReport | None = None
    snapshots: SandboxSnapshots | None = None
    # WP-70: the limit whose writing reserve the turn reached, and what it was told then; set once.
    synthesis: SynthesisReason | None = None
    synthesis_note: str = ""
    allowance: Allowance = field(default_factory=lambda: Allowance(None, None, None))
    # WP-81: when the engine's last model step was reported, and the longest step of the turn so far (the writing
    # reserve on the wall clock is sized to it).
    step_at: float | None = None
    longest_step_s: float = 0.0
    # When the run's money was last read for the writing reserve (``MONEY_CHECK_S`` apart, not every tick).
    money_read_at: float | None = None
    # The run's money could not be read for the writing reserve this turn (logged once).
    money_unread: bool = False
    # WP-82: the engine's turn stream ended; a call still waiting for its save is refused, and saves nothing.
    ended: bool = False
    # The repositories the worker fetched for this turn (the endpoint's ``vouched``); ``None``: none can be.
    vouched: set[str] | None = None

    async def stage_input(self, arguments: Mapping[str, JsonValue]) -> McpResult:
        """Writes an input the sandbox gets on its first read (C5.01, J-26); one already there answers at once."""
        path = input_path(arguments)
        async with self.lock:
            waiting = [p for p, why in self.state.hidden.items() if why == ON_FIRST_READ]
            entry = self.state.manifest.files.get(path) if path is not None else None
            if path is None or entry is None or entry.is_symlink:
                return not_waiting(path, waiting)
            if path not in waiting:
                return fetched(path, entry.content.size_bytes, staged=False)
            try:
                self.state = await self.sync.stage(self.sandbox, self.state, path)
            except WorkspacePathRefused as refused:
                # SEC-FS-02: the input's folder leads outside the workspace (a link the engine made); nothing is written.
                self.log.warning("engine.input.refused", reason=refused.reason_code)
                return not_placed(path)
        self.log.info("engine.input.staged", bytes=entry.content.size_bytes)
        return fetched(path, entry.content.size_bytes, staged=True)

    async def before_call(self) -> str | None:
        """The save before a gateway call; the refusal's text once the turn has ended (WP-82), even if it ended while
        the call waited for the save."""
        if self.ended:
            return TURN_ENDED_TEXT
        await self.save("before_effect")
        return TURN_ENDED_TEXT if self.ended else None

    async def save(self, reason: CommitReason) -> None:
        await self._save(reason)
        if reason == "autosave":
            await self.snapshot("autosave")

    async def snapshot(self, reason: SnapshotReason) -> None:
        """Store the sandbox's caches when they changed; never on a cancelled turn or after the writer was lost."""
        if self.snapshots is None or self.cancelled or self.writer_lost:
            return
        async with self.lock:
            ref = await self.snapshots.take(self.sandbox)
            if ref is None:
                return
            await self.sink.snapshotted(self.run.binding, ref, reason)
            await afailpoint("engine.turn.after_snapshot")

    async def _save(self, reason: CommitReason) -> None:
        if self.writer_lost:
            # Another run writes the workspace now; this sandbox's changes stay unsaved.
            return
        async with self.lock:
            if reason == "before_effect" and self.ended:
                # The turn's own last save follows; this call is refused without running.
                return
            prefix = self.config.outputs_dir.strip("/") + "/"
            if self.vouched is not None and self.state.vouched != frozenset(self.vouched):
                self.state = replace(self.state, vouched=frozenset(self.vouched))
            # WP-82, the degrade table's save row (ADR-RUN-01 D6) as a typed result (D2): a change set the store refused
            # as sent keeps the turn's last revision, and the deliverables are saved on their own.
            result = await self.saver.save(
                self.sandbox,
                self.state,
                reason=reason,
                before_commit=self._prepare,
                fallback=lambda path: path.startswith(prefix),
            )
            self.changes = 0
            self.last_save = self.clock.monotonic()
            saved = self._saved_of(result, reason)
            if saved is None:
                return
            self.state = saved.state
            # Before the no-commit return: a save whose only news is skipped entries still refuses them.
            outputs, refused = deliverables(saved, self.config.outputs_dir)
            self.record_refusals(refused, log=True)
            if saved.manifest is None:
                return
            self.last_saved = saved.manifest
            await afailpoint("engine.turn.after_autosave" if reason == "autosave" else f"engine.turn.after_save_{reason}")
            await self.sink.workspace_saved(self.run.binding, saved.manifest, reason)
            await self._publish(saved.manifest, outputs)

    def _saved_of(self, result: SaveResult, reason: CommitReason) -> Saved | None:
        """What of a save's result the turn goes on from; ``None``: nothing was saved and the last revision stands."""
        match result:
            case Saved():
                return result
            case PartialSave(reason="save_refused"):
                self.log.error("engine.turn.save_refused", reason=reason, reasonCode=result.refusal or "")
                self._save_refused(result.refusal or "save_refused")
                return result.saved
            case PartialSave():
                return result.saved
            case Refused():
                self.log.error("engine.turn.save_refused", reason=reason, reasonCode=result.reason, errorType=result.error)
                self._save_refused(result.reason)
                if result.attempted_fallback:
                    self.log.error(
                        "engine.turn.outputs_save_refused", reason=reason, reasonCode=result.reason, errorType=result.error
                    )
                return None

    def publication_of(self, path: str) -> McpResult:
        """What this turn's saves made of the deliverable ``path`` (relative to the outputs folder): the draft it
        published last, the refusal, or that the file is unchanged since an earlier turn published it."""
        refusal = next((r for r in reversed(self.refused) if r.path == path), None)
        if refusal is not None:
            return refused_output(path, refusal.reason_code)
        done = next((p for p in reversed(self.published) if p.path == path), None)
        if done is not None:
            return published_output(path, done.artifact_id, done.version, done.lifecycle)
        # The sync's base after the save: the workspace as the sandbox holds it, whichever turn committed the file.
        folder = self.config.outputs_dir.strip("/")
        if f"{folder}/{path}" in self.state.manifest.files:
            return unchanged_output(path)
        return missing_output(path)

    async def _prepare(self, collected: Collected) -> None:
        """The outputs' publication intents go to the run's log before the revision holding them exists, so
        a worker that dies after the commit leaves a save the next attempt can publish (EV2-F2)."""
        outputs, _ = collected_deliverables(collected, self.config.outputs_dir)
        if not outputs:
            return
        await self.run.artifacts.prepare(self.run.binding, outputs)
        await afailpoint("engine.turn.after_prepare")

    def _save_refused(self, reason: str) -> None:
        if reason not in self.save_refusals:
            self.save_refusals.append(reason)

    def record_refusals(self, refusals: Iterable[OutputRefusal], *, log: bool = False) -> None:
        """Once per (path, reason) per turn: the sync reports a skipped path again on every save."""
        for refusal in refusals:
            if refusal in self._seen_refusals:
                continue
            self._seen_refusals.add(refusal)
            self.refused.append(refusal)
            if log:
                self.log.warning("engine.output.refused", reasonCode=refusal.reason_code)

    async def _publish(self, manifest: WorkspaceManifest, outputs: list[OutputFile]) -> None:
        if not outputs:
            return
        report = await self.run.artifacts.publish_changes(
            self.run.binding,
            manifest,
            outputs,
            draft=True,
            derivation=self.run.derivation(),
        )
        self.published.extend(report.published)
        # The publisher logs its own refusals.
        self.record_refusals(OutputRefusal(r.path, r.reason_code) for r in report.refused)
        await afailpoint("engine.turn.after_publish")

    def reported_usage(self) -> LedgerUsage:
        return LedgerUsage(self.input_tokens, self.output_tokens, self.cache_read_tokens, self.cache_write_tokens)

    async def stop(self, limit: LimitName | None = None, *, cancelled: bool = False) -> None:
        if cancelled:
            self.cancelled = True
        if self.stopping:
            return
        self.stopping = True
        self.limit = limit
        if limit is not None:
            self.log.warning("engine.turn.limit", reasonCode=f"limit_{limit}")
        await self.session.interrupt()

    async def tool_called(self, name: str, arguments: Mapping[str, JsonValue], result: McpResult) -> None:
        """One gateway call came back. Rounds that repeat the same calls and results, or rounds whose calls all failed
        or returned nothing, stop the turn, and its answer is written from what it gathered (BU-06).

        A round is weighed only once the engine's model has made another call since it (WP-62), before that call's
        first tool runs (``call_opening``): its results reached the model, which went on calling tools. Counted per gateway call, a Codex run was stopped by one parallel
        batch of six refusals 0.06 s after the sixth, before its model had read any of them."""
        self.round_calls.append(RoundCall(name, canonical_digest(dict(arguments)), result.is_error, result.text))

    async def call_opening(self, tool: str) -> str | None:
        """Before a call: the round before it is weighed once a model call has read it; the refusal's text once the
        turn is stopping, or writing its deliverable (WP-70), else ``None``. Writing still publishes: ``ph publish``
        hands over the deliverable the window asks for."""
        mark = await self._model_mark()
        if self.round_calls and mark != self.round_mark:
            await self._close_round()
        if not self.round_calls:
            self.round_mark = mark
        if self.stopping:
            return STOPPING_TEXT
        if tool == ARTIFACT_PUBLISH_TOOL:
            return None
        return self.synthesis_note or None

    async def enter_synthesis(self, reason: SynthesisReason, *, usd_left: float | None = None) -> None:
        """WP-70: the turn reached the writing reserve of ``reason``'s limit. The engine is told to stop gathering and
        write the deliverable (steered where it can be; every engine reads it as each later call's refusal), once."""
        if self.synthesis is not None or self.stopping:
            return
        self.synthesis = reason
        left = left_words(
            reason, self.allowance, steps=self.steps, elapsed_s=self.worked_s(self.clock.monotonic()), usd_left=usd_left
        )
        # ADR-RUN-01 D5: the steer is the harness's typed instruction, worded once (``write_by_instruction``).
        instruction = write_by_instruction(reason, left, self.run.deliverable)
        self.synthesis_note = instruction.text
        steered = True
        try:
            await self.session.steer(instruction.text)
        except Exception as exc:  # an engine that cannot steer reads it as its next call's refusal
            steered = False
            self.log.info("engine.turn.synthesis_unsteered", errorType=type(exc).__name__)
        self.log.warning(
            "engine.turn.synthesis", reasonCode=f"synthesis_{reason}", steps=self.steps, steered=steered,
            deliverable=self.run.deliverable is not None, instruction=WRITE_BY_KEY,
        )

    def worked_s(self, now: float) -> float:
        """D4: the turn's working seconds at monotonic ``now``: its time since it began, less what the attempt waited
        for a person meanwhile (an approval held in the turn pauses its working time)."""
        waits = self.run.waits
        waited = waits.waited_s() - self.waited_at_start if waits is not None else 0.0
        return now - self.started - waited

    async def _model_mark(self) -> object:
        """What changes with each model call the engine completes. The run's ledger first: the model gateway settles a
        call there before the engine reads its last chunk, so before any tool call that reply asked for. Without it,
        the model calls the engine reported, which the turn's event stream may deliver after the next tool call."""
        usage = self.run.usage
        if usage is not None and self.run.own_login is None:
            try:
                used = (await usage.read()).used
            except Exception as exc:
                self.log.warning("engine.turn.round_unread", errorType=type(exc).__name__)
                return self.round_mark
            return (used.input_tokens, used.output_tokens)
        return self.model_turns

    async def _close_round(self) -> None:
        calls, self.round_calls = self.round_calls, []
        self.stall = next_stall(self.stall, calls)
        if stalled(self.stall):
            await self.stop("stall")

    async def observe(self, event: EngineEvent) -> None:
        match event:
            case FileChanged():
                self.changes += 1
                if self.changes >= self.config.autosave_changes:
                    await self.save("autosave")
            case UsageReported():
                self.steps += 1
                if event.sub_session_id is None:
                    self.model_turns += 1
                    # Measured in working time: a person's wait inside a step is not the model's step time.
                    now = self.worked_s(self.clock.monotonic())
                    since = self.step_at if self.step_at is not None else 0.0
                    self.longest_step_s = max(self.longest_step_s, now - since)
                    self.step_at = now
                self.input_tokens += event.input_tokens
                self.output_tokens += event.output_tokens
                self.cache_read_tokens += event.cache_read_tokens
                self.cache_write_tokens += event.cache_write_tokens
                steps = self.allowance.steps
                tokens = self.allowance.tokens
                if tokens is not None and self.input_tokens + self.output_tokens > tokens:
                    await self.stop("tokens")
                elif steps is not None and self.steps > steps:
                    await self.stop("steps")
                elif self.allowance.synthesis_due(steps=self.steps, elapsed_s=0) == "steps":
                    await self.enter_synthesis("steps")
            case ApprovalRequested():
                # Approvals belong to the tool gateway; an engine's own ask is refused, never left hanging.
                self.log.warning("engine.native_approval.denied", reasonCode="native_approval")
                await self.session.answer(event.request_id, "denied")
            case EngineError() if event.code in BUDGET_STOP_CODES:
                self.error_code = event.code
                await self.stop("budget")
            case EngineError():
                self.error_code = event.code
            case ContextCompacted():
                self.auto_compacted = CompactReport(
                    "compacted", event.before_tokens, event.after_tokens, user_messages_kept=event.user_messages_kept
                )
                self.log.info("engine.context.compacted", beforeTokens=event.before_tokens, afterTokens=event.after_tokens)
            case _:
                pass

    async def tick(self, sleep: Callable[[float], Awaitable[None]]) -> None:
        """Enforces the wall limit, autosaves and renews the write lease until the turn stops; one failed
        iteration is logged and the next one runs, so the limit and the lease never go unwatched."""
        while True:
            await sleep(self.config.tick_s)
            try:
                if not await self._tick_once():
                    return
            except Exception as exc:
                self.log.error("engine.turn.tick_failed", exc=exc, errorType=type(exc).__name__)

    async def _tick_once(self) -> bool:
        """One iteration; False once the turn is stopping."""
        now = self.clock.monotonic()
        wall = self.allowance.wall_s
        if wall is not None and self.worked_s(now) >= wall:
            await self.stop("wall_time")
            return False
        if self.synthesis is None:
            await self._synthesis_check(now)
        if now - self.last_save >= self.config.autosave_interval_s:
            try:
                await self.save("autosave")
            except Exception as exc:  # the next tick or the turn's end saves again
                self.log.warning("engine.turn.autosave_failed", errorType=type(exc).__name__)
        return await self._renew(now)

    async def _synthesis_check(self, now: float) -> None:
        """WP-70: the turn's wall time and the run's money, against their writing reserves (steps are checked as each
        model call is reported). Money is read from the run's ledger account, the authority (OWD-8)."""
        share = left = None
        if self.money_read_at is None or now - self.money_read_at >= MONEY_CHECK_S:
            self.money_read_at = now
            share, left = await self._usd_left()
        due = self.allowance.synthesis_due(
            steps=0, elapsed_s=self.worked_s(now), usd_left_share=share, longest_step_s=self.longest_step_s
        )
        if due is not None:
            await self.enter_synthesis(due, usd_left=left)

    async def _usd_left(self) -> tuple[float | None, float | None]:
        """The share of the run's money it may still spend, and how much that is in USD; ``(None, None)`` for an
        unmetered run or one nothing bounds."""
        usage = self.run.usage
        if usage is None or not self.run.metered:
            return None, None
        try:
            remaining = await usage.read()
        except Exception as exc:
            # Once a turn: the reserve keeps its step and time limits; the turn's own usage read says the same cause.
            if not self.money_unread:
                self.money_unread = True
                self.log.warning("engine.turn.money_unread", reasonCode="usage_unreadable", errorType=type(exc).__name__)
            return None, None
        available = remaining.available.usd_micros
        if available is None:
            return None, None
        total = available + remaining.committed.usd_micros + remaining.reserved.usd_micros
        if total <= 0:
            return None, None
        return available / total, available / MICRO_USD

    async def _renew(self, now: float) -> bool:
        if self.renew is None:
            return True
        lease_s = self.config.writer_lease_ms / 1000
        every_s = lease_s / 3
        if now - self.last_renew < every_s:
            return True
        self.last_renew = now
        try:
            await self.renew()
        except WorkspaceWriterBusy:
            # The lease lapsed and another writer took the workspace: this one must stop.
            return await self._writer_lost(WorkspaceWriterBusy.reason_code)
        except Exception as exc:
            # Unconfirmed is not lost yet; but a lease that could lapse before the next try may be taken
            # meanwhile, so the turn stops while the workspace is still its own.
            if now + every_s >= self.renewed_at + lease_s:
                return await self._writer_lost(WRITER_LEASE_UNRENEWED, errorType=type(exc).__name__)
            self.log.warning("engine.turn.renew_failed", errorType=type(exc).__name__)
            return True
        self.renewed_at = now
        return True

    async def _writer_lost(self, reason_code: str, **fields: str) -> bool:
        self.writer_lost = True
        self.log.warning("engine.turn.writer_lost", reasonCode=reason_code, **fields)
        await self.stop()
        return False


class EngineController:
    def __init__(
        self,
        *,
        registry: EngineRegistry,
        broker: SandboxBroker,
        workspaces: WorkspaceStore,
        sync_for: Callable[[ObjectScope], WorkspaceSync],
        strategies: SyncStrategies,
        skill_bundles: SkillBundleBuilder,
        skill_materializer: SkillMaterializer,
        checkpoints: EngineCheckpointStore,
        sink: EngineEventSink,
        tool_host: ToolGatewayHost,
        clock: Clock,
        placeholder_key: bytes,
        config: EngineControllerConfig,
        writers: WorkspaceWriters,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        log: HarnessLog | None = None,
        snapshots: SandboxSnapshotStore | None = None,
        tracer: HarnessTracer = NOOP_TRACER,
        signin_relay: SignInRelay | None = None,
        signin_hosts: SignInHosts | None = None,
        parked: KeptSandboxWatches | None = None,
        shelf_relay: ShelfRelay | None = None,
    ) -> None:
        if len(placeholder_key) < 16:
            raise ValueError("the placeholder key must be at least 16 random bytes")
        # One writer per workspace (P6.4): a turn holds its workspace's lease while it runs,
        # through whatever binding the caller's scope provides (a run's attempt, a request's task).
        self._writers = writers
        self._registry = registry
        self._broker = broker
        self._workspaces = workspaces
        self._sync_for = sync_for
        self._strategies = strategies
        self._skill_bundles = skill_bundles
        self._skill_materializer = skill_materializer
        self._checkpoints = checkpoints
        self._snapshot_store = snapshots
        self._sink = sink
        self._tool_host = tool_host
        self._clock = clock
        self._placeholder_key = placeholder_key
        self._config = config
        self._sleep = sleep
        self._log = log or HarnessLog.named(LOGGER)
        self._tracer = tracer
        self._active: dict[str, _Turn] = {}
        # Turns whose engine is not open yet (a sandbox, files, a sign-in to wait for): cancel ends their wait.
        self._opening: dict[str, _Opening] = {}
        # SIGNIN: where a person's code reaches this worker, and the signed hosts; without both, no own sign-in.
        self._signin_relay = signin_relay
        self._signin_hosts = signin_hosts
        # Kept signed-in sandboxes this worker parked and watches (SIGNIN-5): shared by its controllers.
        self._parked = parked if parked is not None else KeptSandboxWatches()
        # G-35: the run's own tools (a bulk export) write onto the shelf of whichever turn's sandbox holds it.
        self._shelf_relay = shelf_relay

    @property
    def workspace_root(self) -> str:
        """Where the workspace is in the sandbox: a repository environment's setup runs there."""
        return self._config.workspace_root

    def capabilities_for(self, version: EngineVersion) -> EngineCapabilities:
        """What the caller passes to the run's ``RunToolEndpoint`` so ``GrantTooLarge`` refuses there too."""
        return self._registry.adapter_for(version).capabilities()

    async def cancel(self, run: EngineRunContext, *, reason: str) -> bool:
        """Stop the run's turn now; the turn finishes with a final save and its drafts discarded. A turn whose engine
        is not open yet ends in whatever it waits for (its sandbox, a person's sign-in), with nothing to save."""
        turn = self._active.get(run.run_id)
        if turn is None:
            opening = self._opening.get(run.run_id)
            if opening is None:
                return False
            if not opening.cancelled:
                self._log.bind(**run.correlation(), fence=run.fence).info(
                    "engine.turn.cancel", reason=reason, phase="opening"
                )
                opening.cancelled = True
                _ = opening.task.cancel()
            return True
        turn.log.info("engine.turn.cancel", reason=reason)
        await turn.stop(cancelled=True)
        return True

    async def interrupt(self, run: EngineRunContext, *, reason: str) -> bool:
        """Stop the run's turn without cancelling the task (drain, a lost lease): its drafts stay open,
        its state is still captured, and a later attempt continues it."""
        turn = self._active.get(run.run_id)
        if turn is None:
            return False
        turn.log.info("engine.turn.interrupt", reason=reason)
        await turn.stop()
        return True

    async def tell(self, run: EngineRunContext, text: str) -> bool:
        """Add ``text`` to the run's running turn (ADR-RUN-06 D2); ``False`` when no turn runs, it is stopping, or its
        engine cannot be steered: the caller then hands it over another way."""
        turn = self._active.get(run.run_id)
        if turn is None or turn.stopping:
            return False
        try:
            await turn.session.steer(text)
        except Exception as exc:  # SteeringUnsupported, or an engine that refused it mid-turn
            turn.log.info("engine.turn.tell_unsteered", errorType=type(exc).__name__)
            return False
        return True

    async def recover_saves(self, run: EngineRunContext, *, journaled_revision: int | None) -> PublishReport:
        """Before a re-driven turn: publish what an earlier attempt of the run saved and never published.

        Every output of the head whose intent the run logged without a publication was committed by an
        attempt that died before publishing it (intents precede the commit). ``journaled_revision`` is the
        last revision the run's journal holds for the workspace; a newer head that held such a save is
        journaled as the save it was.
        """
        log = self._log.bind(**run.correlation(), fence=run.fence)
        head = await self._workspaces.head(ObjectScope(run.tenant), run.workspace_id)
        report = await run.artifacts.publish_pending(
            run.binding,
            head,
            workspace_deliverables(head, self._config.outputs_dir),
            draft=True,
            derivation=run.derivation(),
        )
        if not report.published and not report.refused:
            return report
        if report.published and (journaled_revision is None or head.revision > journaled_revision):
            await self._sink.workspace_saved(run.binding, head, "autosave")
        log.info(
            "engine.turn.saves_recovered",
            published=len(report.published),
            refused=len(report.refused),
            revision=head.revision,
        )
        return report

    async def run_turn(self, run: EngineRunContext, task: EngineTask | FollowUp) -> EngineTurnOutcome:
        log = self._log.bind(**run.correlation(), fence=run.fence)
        adapter, image, mode = self._admit(run, log)
        health = await self._broker.health()
        self._admit_image(run, health, log)
        image_pin = self._image_pin(run, health, log)
        bundles = await self._resolve_skills(run, log)
        writer_taken_at, writer_fence = await self._take_writer(run, log)
        current = asyncio.current_task()
        assert current is not None
        opening = self._opening[run.run_id] = _Opening(current)
        try:
            return await self._turn(
                run, task, adapter, (image, image_pin), mode, bundles, log, health, writer_taken_at, writer_fence
            )
        except asyncio.CancelledError:
            # Only this controller's own cancel is an outcome; the worker cancelling the turn's task propagates.
            if not opening.cancelled or current.uncancel() > 0:
                raise
            log.info("engine.turn.finished", reason="interrupted", cancelled=True, phase="opening")
            return EngineTurnOutcome(reason="interrupted", cancelled=True)
        finally:
            if self._opening.get(run.run_id) is opening:
                del self._opening[run.run_id]
            await self._writers.release(run.object_scope, run.workspace_id, holder=run.run_id)

    def _admit_image(self, run: EngineRunContext, health: BrokerHealth, log: HarnessLog) -> None:
        """An engine whose image the broker can neither find nor pull (an admin-supplied image never built
        there) is refused before anything is created, not at sandbox acquisition."""
        missing = engines_unavailable(health)
        if missing is not None and run.profile.engine.ref in missing:
            raise self._refused(log, EngineImageUnavailable(f"no {run.profile.engine.ref} image on the sandbox broker"))

    def _image_pin(self, run: EngineRunContext, health: BrokerHealth, log: HarnessLog) -> str | None:
        """The digest the turn's sandbox must run (C2.20): the run's own pin, else the release's, else the one the
        broker reports now. A run pinned to an image the broker no longer has under that name is refused here,
        before anything is created; the broker holds the sandbox to the same digest whatever this sees."""
        reported = engine_image_digests(health) or {}
        current = reported.get(run.profile.engine.ref)
        if run.image_pin is not None:
            if current is not None and current != run.image_pin:
                log.warning("engine.image.changed", pinned=run.image_pin, current=current)
                raise self._refused(log, EngineImageChanged("the engine image changed after the run was pinned"))
            return run.image_pin
        return self._config.image_pins.get(run.profile.engine) or current

    def _renewal(self, run: EngineRunContext) -> Callable[[], Awaitable[None]]:
        async def renew() -> None:
            _ = await self._writers.acquire(
                run.object_scope, run.workspace_id, holder=run.run_id, ttl_ms=self._config.writer_lease_ms
            )

        return renew

    async def _take_writer(self, run: EngineRunContext, log: HarnessLog) -> tuple[float, int]:
        """The last refusal before anything is created: another run writes this workspace. Returns when the
        lease was asked for, the earliest it can lapse from, and its expiry: each later writer's is later, so it
        orders the runs that take a kept sandbox in turn (SIGNIN-5's lease fence)."""
        asked_at = self._clock.monotonic()
        try:
            lease = await self._writers.acquire(
                run.object_scope, run.workspace_id, holder=run.run_id, ttl_ms=self._config.writer_lease_ms
            )
        except WorkspaceWriterBusy as busy:
            log.warning("engine.run.refused", reasonCode=busy.reason_code, holder=busy.holder)
            raise
        return asked_at, lease.expires_at_ms

    async def _turn(
        self,
        run: EngineRunContext,
        task: EngineTask | FollowUp,
        adapter: EngineAdapter,
        pinned_image: tuple[str, str | None],
        mode: RuntimeMode,
        bundles: list[SkillBundleRef],
        log: HarnessLog,
        health: BrokerHealth,
        writer_taken_at: float,
        writer_fence: int,
    ) -> EngineTurnOutcome:
        image, image_pin = pinned_image
        model_ph = placeholder_for(self._placeholder_key, run.attempt_id, MODEL_ROUTE)
        tools_ph = placeholder_for(self._placeholder_key, run.attempt_id, TOOLS_ROUTE)
        strategy = self._strategies.for_broker(health)
        endpoint = _SavingEndpoint(
            run.tools, run.tool_binding, max_result_chars=run_result_cap(run.result_cap_chars)
        )
        async with self._tool_host.serve(endpoint, run.tool_binding) as upstream:
            workspace = strategy.mount(self._config.workspace_root, source=self._source(run))
            spec = self._spec(run, pinned_image, mode, workspace, model_ph, tools_ph, upstream)
            caches = await self._shared_caches(run)
            spec = with_mounts(spec, caches)
            git = await self._git_phase(run, spec)
            if git is not None:
                spec = git.first_spec
            with self._tracer.span(
                "pipeshub.sandbox.acquire",
                kind="client",
                run=RunRef(run.tenant, run.run_id, run.session_id, run.attempt_id, run.fence),
                attributes={
                    "pipeshub.sandbox.mode": mode,
                    "pipeshub.sandbox.image": image,
                    **({"pipeshub.sandbox.image_digest": image_pin} if image_pin is not None else {}),
                },
            ) as span:
                if run.own_login is None:
                    key, lease = run.attempt_id, LeaseRef(run.lease_holder, run.fence)
                else:
                    # The person's kept sandbox, if a turn of this conversation left one (SIGNIN-5).
                    key, lease = kept_sandbox_key(run), LeaseRef(run.lease_holder, writer_fence)
                    await self._parked.stop(key)
                sandbox = await _acquire_or_release(self._broker, spec, lease, key)
                span.set({"pipeshub.sandbox.id": sandbox.id})
            log = log.bind(sandboxId=sandbox.id)
            await afailpoint("engine.turn.after_acquire")
            parking = _Parking()
            try:
                if git is not None:
                    git.lease, git.key = lease, key
                outcome = await self._in_sandbox(
                    run, task, adapter, sandbox, bundles, endpoint, model_ph, tools_ph, log, writer_taken_at, parking,
                    lease.fence, git, caches,
                )
            except Exception as exc:
                lost = await self._out_of_memory(sandbox, spec, exc, log)
                if lost is None:
                    raise
                raise lost from exc
            else:
                lost = await self._out_of_memory(sandbox, spec, outcome, log)
                if lost is not None:
                    raise lost
                return outcome
            finally:
                if self._shelf_relay is not None:
                    self._shelf_relay.detach(endpoint)
                # A signed-in sandbox is kept only as ``_park`` decided: else signed out already, and destroyed here.
                keep = self._config.keep_warm_s if run.own_login is None else parking.keep_warm_s
                await self._broker.release(sandbox, keep_warm_s=keep)
                log.info("engine.sandbox.released", keptS=keep)
                if parking.kept is not None:
                    self._watch(key, parking, git.agent_spec if git is not None else spec, lease, adapter, run.binding, log)

    @staticmethod
    async def _out_of_memory(
        sandbox: SandboxHandle, spec: SandboxSpec, ended: Exception | EngineTurnOutcome, log: HarnessLog
    ) -> SandboxOutOfMemory | None:
        """A turn whose engine or sandbox went away (raised, or an outcome the engine never ended) is asked once why,
        before its sandbox is released: its memory limit (the container's OOM kill; under gVisor the whole sandbox
        ends) fails the run with the limit named, where a lost turn alone would be re-driven into the same limit
        (R31GVISO-FU-3). ``None``: not that."""
        if isinstance(ended, EngineTurnOutcome):
            lost = not (ended.ended or ended.cancelled or ended.limit is not None or ended.reason == "completed")
        else:
            fault = fault_of(ended)
            lost = fault is not None and fault.kind == "turn_lost"
        if not lost or not await memory_exhausted(sandbox):
            return None
        memory_mb = spec.resources.memory_mb
        log.warning("engine.sandbox.out_of_memory", memoryMb=memory_mb, mode=spec.mode.value)
        return SandboxOutOfMemory(
            f"the sandbox ran out of memory (limit {memory_limit_text(memory_mb)})", memory_mb=memory_mb
        )

    @property
    def input_max_bytes(self) -> int:
        """The largest input a run is given (J-26), before its pin lowers it."""
        return self._config.input_max_bytes

    @property
    def input_stage_max_bytes(self) -> int:
        """The largest input copied in at a turn's start; a larger one waits for its first read (C5.01)."""
        return self._config.input_stage_max_bytes

    def _admit(self, run: EngineRunContext, log: HarnessLog) -> tuple[EngineAdapter, str, RuntimeMode]:
        profile = run.profile
        adapter = self._registry.adapter_for(profile.engine)  # the registry logs its own refusal
        try:
            check_grant_size(adapter.capabilities(), run.grant_size)
            _ = model_api_for(adapter.capabilities(), self._config.model_apis)
            check_profile_admits(profile, tools=run.grant_size, skills=(pin.name for pin in run.skills))
        except (GrantTooLarge, ModelApiUnavailable, SkillNotAllowed, EgressUnavailable) as refused:
            raise self._refused(log, refused) from None
        if run.own_login is not None and (
            self._signin_relay is None or self._signin_hosts is None or not isinstance(adapter, SignsIn)
        ):
            raise self._refused(
                log, SignInRefused("this deployment cannot sign in to the engine", reason="signin_unavailable")
            )
        image = self._config.images.get(profile.engine)
        if image is None:
            raise self._refused(log, EngineImageNotConfigured("no sandbox image for this engine version"))
        mode = engine_mode(self._broker.modes(), profile.isolation_min)
        if mode is None:
            raise self._refused(log, isolation_refusal(profile.isolation_min))
        return adapter, image, mode

    async def _resolve_skills(self, run: EngineRunContext, log: HarnessLog) -> list[SkillBundleRef]:
        try:
            return [await self._skill_bundles.resolve(run.object_scope, pin) for pin in run.skills]
        except SkillBundleError as refused:
            log.warning("engine.run.refused", reasonCode=refused.reason)
            raise

    def _refused[E: EngineRefusal | IsolationUnavailable](self, log: HarnessLog, refusal: E) -> E:
        log.warning("engine.run.refused", reasonCode=refusal.reason_code)
        return refusal

    def _source(self, run: EngineRunContext) -> str:
        if run.workspace_id == session_workspace_id(run.session_id):
            return f"{run.tenant}/{run.session_id}/workspace"
        # A fork's persistent copy sits beside the session's, never inside it (it would be collected).
        digest = hashlib.sha256(run.workspace_id.encode()).hexdigest()[:24]
        return f"{run.tenant}/{run.session_id}/fork-{digest}"

    def _spec(
        self,
        run: EngineRunContext,
        pinned_image: tuple[str, str | None],
        mode: RuntimeMode,
        workspace: MountSpec,
        model_ph: str,
        tools_ph: str,
        upstream: str,
    ) -> SandboxSpec:
        image, image_pin = pinned_image
        return engine_sandbox_spec(
            self._config,
            image=image,
            image_pin=image_pin,
            mode=mode,
            workspace=workspace,
            labels=run.correlation() if run.own_login is None else kept_sandbox_labels(run, self._config.signin_keep),
            placeholders=(model_ph, tools_ph),
            tools_upstream=upstream,
            tunnels=self._signin_hosts.sandbox if run.own_login is not None and self._signin_hosts else (),
            sites=run.sites,
            resource_caps=run.resource_caps,
        )

    async def _in_sandbox(
        self,
        run: EngineRunContext,
        task: EngineTask | FollowUp,
        adapter: EngineAdapter,
        sandbox: SandboxHandle,
        bundles: list[SkillBundleRef],
        endpoint: _SavingEndpoint,
        model_ph: str,
        tools_ph: str,
        log: HarnessLog,
        writer_taken_at: float,
        parking: _Parking,
        fence: int,
        git: _GitPhase | None = None,
        caches: tuple[CacheMount, ...] = (),
    ) -> EngineTurnOutcome:
        if self._config.tool_folder is not None:
            try:
                declared = await self._config.tool_folder.admit(sandbox)
            except EngineContractUnsupported as refused:
                raise self._refused(log, refused) from None
            if run.contract_pin is not None and declared is not None and declared != str(run.contract_pin):
                log.warning("engine.contract.changed", pinned=run.contract_pin, declared=declared)
                raise self._refused(
                    log, EngineImageChanged(f"the run is pinned to contract {run.contract_pin}, the image declares {declared}")
                )
        environment = {
            **(await self._verified_caches(sandbox, caches, log)),
            **(await self._environment(run, sandbox, (model_ph, tools_ph), log)),
        }
        sync = self._sync_for(run.object_scope)
        head = await self._workspaces.head(ObjectScope(run.tenant), run.workspace_id)
        if run.inputs:
            head = await self._seed(run, head, log)
        deferred = deferred_inputs(head.files, self._config.input_stage_max_bytes)
        state = await sync.materialize(sandbox, head, root=self._config.workspace_root, defer=deferred)
        endpoint.references, endpoint.vouched = state.references, set()
        if self._config.references_mount is not None and self._config.tool_folder is not None and state.references:
            await adopt_references(sandbox, self._config.workspace_root, state.references, log)
        await afailpoint("engine.turn.after_materialize")
        if git is not None:
            await self._git_setup(run, sandbox, git, log, task.turn_id)
            task = await self._restore_commit(run, sandbox, git, task, log)
        if bundles:
            await self._skill_materializer.materialize(
                run.object_scope, sandbox, bundles, skills_dir=self._config.skills_dir
            )
            await afailpoint("engine.turn.after_skills")
        # C3.10: where the repository's own instruction files are, once the turn's tree (and any clone) is in place.
        clones = (git.checkout.directory,) if git is not None else ()
        api = model_api_for(adapter.capabilities(), self._config.model_apis)
        endpoints = self._config.endpoints(model_ph, tools_ph, api)
        if self._config.tool_folder is not None:
            listed = await self._config.tool_folder.write(
                sandbox, granted_tools(run.tools), endpoints, tuple(pin.name for pin in run.skills)
            )
            log.info("engine.tool_folder.written", tools=listed, target=self._config.tool_folder.target)
            if self._config.tool_folder.shelf is not None:
                endpoint.shelf = (self._config.tool_folder, sandbox, log)
                if self._config.repos_mount is not None:
                    endpoint.repos = self._config.repos_mount[0]
                    endpoint.repos_left = max(0, self._config.repos_mount[1] - HAND_OFF_SLACK_MB) * MIB
                if git is not None and git.checkout.push and git.checkout.credentials is not None:
                    endpoint.git = (git.checkout, self._config.workspace_root)
                if self._shelf_relay is not None:
                    self._shelf_relay.attach(endpoint)
        # A signed-in sandbox is never snapshotted or restored from one (SIGNIN): its config folder holds the login.
        held = (model_ph, tools_ph, *(git.placeholders if git is not None else ()))
        snapshots = self._snapshots(run, held, log) if run.own_login is None else None
        if snapshots is not None and run.snapshot is not None:
            _ = await snapshots.restore_into(sandbox, run.snapshot)
        launch = adapter.render_launch(run.profile, endpoints, self._config.skills_dir)
        if self._config.tool_folder is not None:
            # WP-70: the worker's own CLI first on the engine's PATH, in its shells and its login shells.
            path = await self._config.tool_folder.path_env(sandbox, launch.env)
            launch = replace(launch, env={**launch.env, **path})
        traced = trace_env(self._tracer, RunRef(run.tenant, run.run_id, run.session_id, run.attempt_id, run.fence))
        if traced:
            launch = replace(launch, env={**traced, **launch.env})
        if environment:
            # The harness's own variables (its proxy, its gateways) always win over the environment's.
            launch = replace(launch, env={**environment, **launch.env})
        mirrors = await self._mirror_rules(sandbox, mounts_git_config(caches))
        if git is not None:
            # WP-63 rule: the engine's git fetches with the read credential and cannot push; a push goes
            # through repository_push (``ph git push``), which asks first and pushes with a one-push grant.
            launch = replace(
                launch, env={**launch.env, **agent_git_env(git.checkout, git.push_placeholder, mirrors)}
            )
        elif mirrors:
            launch = replace(launch, env={**launch.env, **git_config_env(mirrors)})
        if run.sites:
            await install_site_trust(sandbox)
            launch = replace(launch, env={**site_env(), **launch.env})
            log.info("engine.sites", sites=len(run.sites))
        if run.own_login is None:
            return await self._opened(
                run, task, adapter, sandbox, launch, endpoint, snapshots, state, sync, log, writer_taken_at,
                clones=clones,
            )
        assert isinstance(adapter, SignsIn) and self._signin_relay is not None and self._signin_hosts is not None
        launch = parking.launch = adapter.own_login(launch, run.own_login.model)
        relay, hosts, owner = self._signin_relay, self._signin_hosts, run.own_login.user_id
        progress = _SignInProgress(self._sink, run.binding, task.turn_id, f"sin_{run.attempt_id}")
        async with relay.revoked(run.binding, owner) as ended:
            signing: asyncio.Future[object] | None = None
            try:
                parking.spare = await adapter.processes(sandbox)
                kept = await self._kept(run, sandbox, log)
                account = await adapter.signed_in(sandbox, launch) if kept is not None else None
                if kept is not None and account is not None:
                    signed_in_at = kept.signed_in_at
                    await progress("signed_in", None, account.plan)
                    log.info("engine.signin.kept", attemptId=progress.attempt_id)
                else:
                    # Whatever an earlier turn left (its worker died, the login lapsed) is signed out and wiped first.
                    await adapter.sign_out(sandbox, launch)
                    signed_in_at = self._clock.now().timestamp()
                    signing = asyncio.ensure_future(
                        adapter.sign_in(
                            sandbox, launch, hosts, lambda: relay.attempt(run.binding, owner, progress.attempt_id),
                            progress,
                        )
                    )
                    await _first(signing, ended)
                    if not signing.done():
                        raise SignInRefused("the sign-in was ended", reason="account_disconnected")
                    _ = signing.result()
                    log.info("engine.signin.ready", attemptId=progress.attempt_id)
                outcome = await self._opened(
                    run, task, adapter, sandbox, launch, endpoint, snapshots, state, sync, log, writer_taken_at,
                    ended=ended, clones=clones,
                )
                if ended.is_set():
                    raise SignInRefused("the sign-in was ended during the turn", reason="account_disconnected")
                await self._park(run, adapter, sandbox, signed_in_at, fence, ended, parking, log)
                return outcome
            finally:
                if signing is not None and not signing.done():
                    _ = signing.cancel()
                    with contextlib.suppress(BaseException):
                        _ = await signing
                if parking.kept is None:
                    await adapter.sign_out(sandbox, launch)

    async def _kept(self, run: EngineRunContext, sandbox: SandboxHandle, log: HarnessLog) -> KeptSignIn | None:
        """The sign-in an earlier turn of this conversation left in ``sandbox``, if it may be used now: parked cleanly
        (a turn whose worker died parked nothing), the same sandbox and person, within its maximum lifetime and
        not revoked since (a revocation its worker missed). Taken either way: nobody else ends it now."""
        assert run.own_login is not None and self._signin_relay is not None
        relay, owner = self._signin_relay, run.own_login.user_id
        try:
            kept = await relay.take(kept_sandbox_key(run))
            if kept is None:
                reason = "not_parked"
            elif kept.sandbox_id != sandbox.id or kept.owner_id != owner:
                reason = "other_sandbox"
            elif self._clock.now().timestamp() - kept.signed_in_at >= self._config.signin_keep.max_s:
                reason = "max_lifetime"
            elif await relay.ended_since(run.binding, owner, kept.signed_in_at):
                reason = "revoked"
            else:
                return kept
        except Exception as exc:
            log.warning("engine.signin.kept_unreadable", errorType=type(exc).__name__)
            return None
        log.info("engine.signin.not_kept", reasonCode=reason)
        return None

    async def _park(
        self,
        run: EngineRunContext,
        adapter: SignsIn,
        sandbox: SandboxHandle,
        signed_in_at: float,
        fence: int,
        ended: asyncio.Event,
        parking: _Parking,
        log: HarnessLog,
    ) -> None:
        """Keeps the signed-in sandbox for the conversation's next turn (SIGNIN-5), for the idle timeout or what is
        left of its maximum lifetime, whichever is shorter; on any doubt it is not kept and the caller signs out."""
        assert run.own_login is not None and self._signin_relay is not None
        relay, keep = self._signin_relay, self._config.signin_keep
        left = keep.max_s - (self._clock.now().timestamp() - signed_in_at)
        idle_s = int(min(keep.idle_s, left))
        if idle_s <= 0 or ended.is_set():
            return
        if not parking.spare or not await adapter.park(sandbox, parking.spare):
            log.warning("engine.signin.park_failed", reasonCode="processes_left")
            return
        kept = KeptSignIn(sandbox.id, run.own_login.user_id, signed_in_at, fence)
        key = kept_sandbox_key(run)
        try:
            await relay.keep(key, kept, idle_s + KEPT_LOGOUT_GRACE_S)
            if ended.is_set():
                # Revoked while parking: taken back, and the caller signs out.
                _ = await relay.take(key, only=kept)
                return
        except Exception as exc:
            log.warning("engine.signin.park_failed", errorType=type(exc).__name__)
            with contextlib.suppress(Exception):
                _ = await relay.take(key, only=kept)
            return
        parking.kept, parking.idle_s = kept, idle_s
        parking.keep_warm_s = idle_s + KEPT_LOGOUT_GRACE_S
        log.info("engine.signin.parked", idleS=idle_s)

    def _watch(
        self,
        key: str,
        parking: _Parking,
        spec: SandboxSpec,
        lease: LeaseRef,
        adapter: EngineAdapter,
        binding: RunBinding,
        log: HarnessLog,
    ) -> None:
        """While the sandbox is parked this worker hears revocations for it and ends it at the idle timeout:
        ``claude auth logout`` and the wipe, as at a turn's end. A worker that dies leaves the broker's keep-warm
        to destroy it, and the next turn's ``_kept`` to refuse it."""
        assert isinstance(adapter, SignsIn) and parking.kept is not None and parking.launch is not None
        watched = _Watched()
        kept, launch, idle_s = parking.kept, parking.launch, parking.idle_s

        async def watch() -> None:
            assert self._signin_relay is not None
            relay = self._signin_relay
            try:
                async with relay.revoked(binding, kept.owner_id) as ended:
                    sleeping = asyncio.ensure_future(self._sleep(idle_s))
                    try:
                        await _first(sleeping, ended)
                    finally:
                        _ = sleeping.cancel()
                reason = "revoked" if ended.is_set() else "idle"
                watched.ending = True
                if await relay.take(key, only=kept) is None:
                    return  # a later turn took it, and ends it itself
                sandbox = await self._broker.acquire(spec, lease=lease, idempotency_key=key)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("engine.signin.kept_end_failed", errorType=type(exc).__name__)
                return
            try:
                await adapter.sign_out(sandbox, launch)
            finally:
                await self._broker.release(sandbox, keep_warm_s=0)
                log.info("engine.signin.kept_ended", reasonCode=reason)

        watched.task = asyncio.ensure_future(watch())
        self._parked.add(key, watched)

    async def _mirror_rules(self, sandbox: SandboxHandle, pairs: GitConfig) -> GitConfig:
        """The mounts' git rules as the session's git reads them: anchored (WP100-FU-8), in a file
        written to the sandbox's state folder, and the one ``include.path`` that reads it; none without a mirror."""
        if not pairs:
            return ()
        path = f"{self._config.state_dir.rstrip('/')}/{MIRRORS_GITCONFIG}"
        await put_files(sandbox, {path: mirrors_gitconfig(pairs)})
        return (("include.path", path),)

    async def _opened(
        self,
        run: EngineRunContext,
        task: EngineTask | FollowUp,
        adapter: EngineAdapter,
        sandbox: SandboxHandle,
        launch: EngineLaunch,
        endpoint: _SavingEndpoint,
        snapshots: SandboxSnapshots | None,
        state: SyncState,
        sync: WorkspaceSync,
        log: HarnessLog,
        writer_taken_at: float,
        *,
        ended: asyncio.Event | None = None,
        clones: tuple[str, ...] = (),
    ) -> EngineTurnOutcome:
        restore, restored, issue = await self._load_state(run, log)
        try:
            session = _witnessed(await adapter.open(sandbox, launch, restore, correlation=run.correlation()), endpoint)
        except EngineStateIncompatible as refused:
            restored, issue = "workspace", refused.reason_code
            log.warning("engine.state.unusable", reasonCode=issue)
            session = _witnessed(await adapter.open(sandbox, launch, None, correlation=run.correlation()), endpoint)
        await afailpoint("engine.turn.after_restore")
        if issue is not None:
            task = self._resumed(task, run.resume_summary)
        # A session's first message carries the repository's instructions, as quoted content after the request; a
        # follow-up goes to a session that already has them, so they are read only for a first message.
        if isinstance(task, EngineTask):
            repository = await read_repository_instructions(sandbox, self._config.workspace_root, clones)
            if repository:
                log.info("engine.repository_instructions", files=[f.provenance_line() for f in repository])
                task = replace(task, prompt=with_repository_instructions(task.prompt, repository))
        now = self._clock.monotonic()
        turn = _Turn(
            run=run,
            sandbox=sandbox,
            session=session,
            saver=WorkspaceSaver(sync=sync, store=self._workspaces, scope=ObjectScope(run.tenant), log=log),
            sync=sync,
            state=state,
            sink=self._sink,
            clock=self._clock,
            config=self._config,
            log=log,
            started=now,
            last_save=now,
            renew=self._renewal(run),
            last_renew=now,
            renewed_at=writer_taken_at,
            snapshots=snapshots,
            allowance=run.allowance(run.store_now_s() if run.store_now_s is not None else self._clock.now().timestamp()),
            waited_at_start=run.waits.waited_s() if run.waits is not None else 0.0,
            vouched=endpoint.vouched,
        )
        self._active[run.run_id] = turn
        # From here a cancel stops the engine (``_Turn.stop``): the turn saves and ends as the engine leaves it.
        _ = self._opening.pop(run.run_id, None)
        endpoint.opening = turn.call_opening
        endpoint.before = turn.before_call
        endpoint.after = turn.tool_called
        endpoint.publication = turn.publication_of
        endpoint.inputs = turn.stage_input
        # The person's sign-in ended mid-turn (SIGNIN): the turn stops at once, and its outcome says why.
        watcher = asyncio.ensure_future(_stop_when(ended, turn)) if ended is not None else None
        try:
            return await self._drive(turn, task, restored, issue)
        finally:
            if watcher is not None:
                _ = watcher.cancel()
            endpoint.opening = None
            endpoint.before = endpoint.after = None
            endpoint.publication = None
            endpoint.inputs = None
            self._active.pop(run.run_id, None)
            await session.close()

    async def _git_phase(self, run: EngineRunContext, spec: SandboxSpec) -> _GitPhase | None:
        """The run's repository phases (GIT-FLOW): the agent phase's spec, and the setup phase's first when the
        workspace holds no clone yet (SEC-EG-11: the sandbox starts under the setup policy, never the other way)."""
        checkout = run.checkout
        if checkout is None:
            return None
        setup_ph = placeholder_for(self._placeholder_key, run.attempt_id, SETUP_ROUTE)
        push_ph = placeholder_for(self._placeholder_key, run.attempt_id, PUSH_ROUTE)
        # The agent phase fetches with the read credential: the push one reaches the proxy only in a one-push grant.
        push = await phase_credential(checkout, "setup", push_ph)
        agent = replace(spec, egress=agent_policy(spec.egress, checkout, push_ph, push))
        head = await self._workspaces.head(ObjectScope(run.tenant), run.workspace_id)
        setup = None
        if f"{checkout.directory}/.git/HEAD" not in head.files:
            read = await phase_credential(checkout, "setup", setup_ph)
            setup = replace(spec, egress=setup_policy(spec.egress, checkout, setup_ph, read))
        return _GitPhase(checkout, agent, setup, setup_ph, push_ph if push is not None else None)

    async def _git_setup(
        self, run: EngineRunContext, sandbox: SandboxHandle, git: _GitPhase, log: HarnessLog, turn_id: str
    ) -> None:
        """Clone under the setup policy, switch the sandbox to the agent policy (a re-acquire of the same sandbox
        rebinds its egress), then check nothing of the setup credential is left; before the engine starts."""
        if git.setup_spec is None:
            await trust_proxy(sandbox, git.checkout)
            return
        assert git.lease is not None and git.key is not None
        root = self._config.workspace_root
        head = await clone(sandbox, git.checkout, workspace_root=root, placeholder=git.setup_placeholder)
        switched = await self._broker.acquire(git.agent_spec, lease=git.lease, idempotency_key=git.key)
        if switched.id != sandbox.id:
            raise GitSetupFailed("the agent phase did not keep the setup phase's sandbox", reason="git_phase_switch")
        await verify_agent_phase(sandbox, git.checkout, workspace_root=root, setup_placeholder=git.setup_placeholder)
        modules = submodule_report(await gitmodules(sandbox, git.checkout, workspace_root=root), git.agent_spec.egress)
        log.info(
            "engine.git.ready",
            head=head,
            branch=git.checkout.session_branch,
            submodules=len(modules),
            submodulesUnreachable=sum(1 for m in modules if not m.reachable),
        )
        ready = RepositoryReady(
            EngineEventMeta(turn_id, _REPOSITORY_SEQ), git.checkout.repository, git.checkout.session_branch
        )
        await self._sink.event(run.binding, ready)
        await afailpoint("engine.turn.after_git_setup")

    async def _shared_caches(self, run: EngineRunContext) -> tuple[CacheMount, ...]:
        caches = self._config.shared_caches
        if caches is None or not run.environment_extension:
            return ()
        return tuple(await caches.mounts(run.tenant, run.environment_extension))

    async def _verified_caches(
        self, sandbox: SandboxHandle, caches: tuple[CacheMount, ...], log: HarnessLog
    ) -> dict[str, str]:
        """Each mounted cache checked before anything else runs; the variables that point the tools at them."""
        port = self._config.shared_caches
        if not caches or port is None:
            return {}
        return dict(await port.verify(sandbox, caches, log))

    async def _environment(
        self, run: EngineRunContext, sandbox: SandboxHandle, held: tuple[str, ...], log: HarnessLog
    ) -> dict[str, str]:
        """C20.18: the run's environment in this fresh sandbox (its setup run, or its kept state restored);
        the variables the engine is launched with."""
        definition = run.environment
        if definition is None:
            return {}
        config = self._config
        folder = () if config.tool_folder is None else (config.tool_folder.target,)
        prepared = await prepare_environment(
            sandbox,
            definition,
            tenant=run.tenant,
            image_digest=run.image_pin or config.images.get(run.profile.engine, ""),
            workspace_root=config.workspace_root,
            snapshots=config.environment_snapshots,
            secrets=lambda: held,
            log=log,
            reserved=(config.state_dir, config.skills_dir, SEALED_DIR, *folder),
        )
        return dict(prepared.env)

    async def _restore_commit(
        self, run: EngineRunContext, sandbox: SandboxHandle, git: _GitPhase, task: EngineTask | FollowUp, log: HarnessLog
    ) -> EngineTask | FollowUp:
        """C2.10: the checkpoint's repository commit, reachable again in the materialised repository before the
        engine starts (``repository_checkpoint.restore_commit``); the engine is told what differs from it."""
        saved = run.restore.repository if run.restore is not None else None
        if saved is None or saved.directory != git.checkout.directory:
            return task
        restored = await restore_commit(
            sandbox, git.checkout, saved, workspace_root=self._config.workspace_root, placeholder=git.push_placeholder
        )
        log.info("engine.git.checkpoint_restored", status=restored.status, commit=saved.short)
        note = restored.note
        if note is None:
            return task
        if isinstance(task, EngineTask):
            return replace(task, prompt=f"{task.prompt}\n\n{note}")
        return replace(task, text=f"{task.text}\n\n{note}")

    def _snapshots(self, run: EngineRunContext, placeholders: tuple[str, ...], log: HarnessLog) -> SandboxSnapshots | None:
        policy = self._config.snapshot
        if policy is None or self._snapshot_store is None:
            return None
        return SandboxSnapshots(
            store=self._snapshot_store,
            policy=policy,
            scope=run.object_scope,
            secrets=lambda: (*placeholders, *run.secrets()),
            log=log,
        )

    async def _seed(self, run: EngineRunContext, head: WorkspaceManifest, log: HarnessLog) -> WorkspaceManifest:
        """The turn's input files under ``in/``, as one revision, under the run's writer lease; only files whose
        content the head lacks, so seeding again commits nothing."""
        files = [WorkspaceFile(item.path, item.content, item.mime_type) for item in run.inputs]
        changed = [f for f in files if not f.same_content(head.files.get(f.path))]
        if not changed:
            return head
        manifest = await self._workspaces.commit(
            ObjectScope(run.tenant), run.workspace_id, base_revision=head.revision, changes=ChangeSet.of(*changed),
            reason="seed",
        )
        await self._sink.workspace_saved(run.binding, manifest, "seed")
        log.info("engine.turn.inputs_seeded", inputs=len(changed), revision=manifest.revision)
        return manifest

    async def _load_state(
        self, run: EngineRunContext, log: HarnessLog
    ) -> tuple[EngineState | None, RestoreSource, str | None]:
        if run.restore is None:
            return None, "fresh", None
        try:
            state = await self._checkpoints.load(run.object_scope, run.restore)
        except EngineStateUnavailable as unavailable:
            log.warning("engine.state.unusable", reasonCode=unavailable.reason_code)
            return None, "workspace", unavailable.reason_code
        return state, "state", None

    @staticmethod
    def _resumed(task: EngineTask | FollowUp, summary: str | None) -> EngineTask:
        note = RESUME_NOTE if not summary else f"{RESUME_NOTE}\n\nWhat was done so far:\n{summary}"
        if isinstance(task, FollowUp):
            return EngineTask(turn_id=task.turn_id, prompt=task.text, instructions=note)
        return replace(task, instructions=f"{task.instructions}\n\n{note}" if task.instructions else note)

    async def _drive(
        self, turn: _Turn, task: EngineTask | FollowUp, restored: RestoreSource, issue: str | None
    ) -> EngineTurnOutcome:
        run = turn.run
        before = await process_table(turn.sandbox)
        ticker = asyncio.get_running_loop().create_task(turn.tick(self._sleep))
        ended: TurnCompleted | None = None
        compacted: CompactReport | None = None
        tape = self._tape(turn, task.turn_id)
        limiter = EventRateLimiter(self._config.event_rate, clock=self._clock.monotonic, sleep=self._sleep)
        unknown: dict[str, int] = {}
        ledger_before = await self._ledger_used(turn)
        try:
            if run.compact is not None:
                compacted = await self._compact(turn, run.compact, task, restored)
            async for event in turn.session.run_turn(task):
                await self._admit_event(turn, limiter)
                await self._sink.event(run.binding, event)
                if isinstance(event, TurnCompleted):
                    ended = event
                    turn.ended = True
                elif isinstance(event, NativeUnknown):
                    unknown[event.kind] = unknown.get(event.kind, 0) + 1
                else:
                    await turn.observe(event)
                if tape is not None:
                    await self._transcript(turn, tape.take())
        finally:
            turn.ended = True
            ticker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await ticker
            if tape is not None:
                await self._transcript(turn, tape.finish(), final=True)
            if unknown:
                # The counter EV-13 asks for: one line per turn, its total and each kind's (never the bytes).
                turn.log.warning(
                    "engine.native.unknown", reasonCode="native_unknown", count=sum(unknown.values()), kinds=dict(unknown)
                )
        if ended is None and not turn.stopping:
            # The engine went away mid-turn (LCFIX): its sandbox's files are a partial turn, never its end; the next
            # attempt resumes from the last save, the same for every adapter.
            turn.log.warning("engine.turn.lost", reasonCode=EngineCrashed.reason_code)
            raise EngineCrashed("the engine's turn stream ended before the turn completed")
        # The gateway settled each call before the engine saw it end, so the ledger already holds the whole turn.
        usage = turn_usage(ledger_before, await self._ledger_used(turn), turn.reported_usage())
        self._log_usage(turn, usage)
        # Before the final save: what a cut-off command wrote is the turn's, and the answer says it was cut off.
        stopped = (
            await stop_started(turn.sandbox, before, self.capabilities_for(run.profile.engine).command_parents)
            if before is not None and not turn.cancelled
            else None
        )
        if stopped:
            turn.log.warning("engine.turn.background_stopped", commands=stopped)
        await turn.save("turn_end")
        await turn.snapshot("turn_end")
        reason: TurnEndReason = ended.reason if ended is not None else "failed"
        if turn.limit is None and reason == "limit":
            # The engine's own step bound (opencode's and ACP agents' ``steps``, rendered from the profile) ended the
            # turn: a step limit, cut short as the controller's own (WP-70), never a lost turn driven again.
            turn.limit = "steps"
            turn.log.warning("engine.turn.limit", reasonCode="limit_steps_engine")
        if turn.limit is not None:
            reason = "limit"
        checkpoint: EngineStateBlob | None = None
        if turn.cancelled:
            settled = await run.artifacts.discard_drafts(run.binding)
        else:
            settled = (
                await run.artifacts.finalize(run.binding, keep_drafts=_owed(run, turn.state.manifest))
                if reason == "completed"
                else None
            )
            checkpoint = await self._checkpoint(turn)
        if settled is not None:
            turn.record_refusals(OutputRefusal(r.path, r.reason_code) for r in settled.refused)
        turn.log.info(
            "engine.turn.finished",
            reason=reason,
            cancelled=turn.cancelled,
            limit=turn.limit,
            published=len(turn.published),
            refusedOutputs=len(turn.refused),
            revision=turn.state.revision,
            restored=restored,
        )
        return EngineTurnOutcome(
            reason=reason,
            final_text=_with_note(ended.final_text if ended is not None else "", stopped_note(stopped) if stopped else ""),
            cancelled=turn.cancelled,
            limit=turn.limit,
            workspace=turn.state.manifest,
            checkpoint=checkpoint,
            published=tuple(turn.published),
            refused_outputs=tuple(turn.refused),
            save_refusals=tuple(turn.save_refusals),
            restored=restored,
            restore_issue=issue,
            input_tokens=usage.usage.input_tokens,
            output_tokens=usage.usage.output_tokens,
            cache_read_tokens=usage.usage.cache_read_tokens,
            cache_write_tokens=usage.usage.cache_write_tokens,
            usage_source=usage.source,
            ended=ended is not None,
            error_code=turn.error_code,
            compacted=compacted,
            auto_compacted=turn.auto_compacted,
            native_unknown=unknown,
        )

    @staticmethod
    async def _ledger_used(turn: _Turn) -> LedgerUsage | None:
        # A run on its actor's own plan (SIGNIN, OWD-8's exception) calls the vendor directly: no ledger sees it.
        if turn.run.usage is None or turn.run.own_login is not None:
            return None
        try:
            return (await turn.run.usage.read()).used
        except Exception as exc:
            turn.log.warning("engine.turn.usage_unreadable", reasonCode="usage_unreadable", errorType=type(exc).__name__)
            return None

    @staticmethod
    def _log_usage(turn: _Turn, usage: TurnUsage) -> None:
        reported = turn.reported_usage()
        fields = {
            "source": usage.source,
            "inputTokens": usage.usage.input_tokens,
            "outputTokens": usage.usage.output_tokens,
            "cacheReadTokens": usage.usage.cache_read_tokens,
            "reportedInputTokens": reported.input_tokens,
            "reportedOutputTokens": reported.output_tokens,
        }
        if usage.over_ledger:
            turn.log.warning("engine.turn.usage", reasonCode="usage_over_ledger", **fields)
        else:
            turn.log.info("engine.turn.usage", **fields)

    def _tape(self, turn: _Turn, turn_id: str) -> NativeTape | None:
        """The turn's native transcript (C2.39), when both the session can record one and the sink keeps it."""
        if not isinstance(turn.session, RecordsNative) or not isinstance(self._sink, NativeTranscriptSink):
            return None
        tape = NativeTape()
        tape.begin(turn_id)
        turn.session.tap_native(tape)
        return tape

    async def _transcript(self, turn: _Turn, parts: list[TranscriptPart], *, final: bool = False) -> None:
        if not parts:
            return
        assert isinstance(self._sink, NativeTranscriptSink)
        try:
            for part in parts:
                await self._sink.native_transcript(turn.run.binding, part)
        except Exception as exc:
            if not final:
                raise
            # At a turn's end an earlier failure is what ends it: a lost transcript tail never hides it.
            turn.log.warning("engine.native.transcript_failed", errorType=type(exc).__name__)
        if final and parts[-1].truncated:
            turn.log.warning("engine.native.transcript_truncated", reasonCode="transcript_truncated")

    async def _admit_event(self, turn: _Turn, limiter: EventRateLimiter) -> None:
        """SEC-DOS-02: the turn's events at no more than the run's rate; a flood ends the attempt."""
        try:
            await limiter.take()
        except EngineEventFlood:
            turn.log.warning(
                "engine.turn.event_flood",
                reasonCode=EngineEventFlood.reason_code,
                events=limiter.events,
                throttledS=round(limiter.throttled_s, 3),
            )
            raise

    async def _compact(
        self, turn: _Turn, ask: CompactAsk, task: EngineTask | FollowUp, restored: RestoreSource
    ) -> CompactReport:
        """The person's ``/compact`` before the turn, under the turn's lease renewals: the engine's own compaction of
        the session it restored. A session with no restored state has nothing to compact; an engine that cannot, or
        that does not finish, leaves the turn to run on the full context."""
        if not self.capabilities_for(turn.run.profile.engine).can_compact:
            report = CompactReport("unsupported")
        elif restored != "state":
            report = CompactReport("nothing_to_compact")
        else:
            instructions = task.instructions if isinstance(task, EngineTask) else ""
            try:
                report = await turn.session.compact(ask.focus, instructions=instructions)
            except CompactUnsupported:
                report = CompactReport("unsupported")
            except CompactFailed as failed:
                turn.log.warning("engine.compact.failed", reasonCode=failed.reason_code, detail=str(failed)[:200])
                report = CompactReport("failed")
        turn.log.info(
            "engine.compact", outcome=report.outcome, beforeTokens=report.before_tokens,
            afterTokens=report.after_tokens, focus=ask.focus is not None, restored=restored,
        )
        return report

    async def _checkpoint(self, turn: _Turn) -> EngineStateBlob:
        await afailpoint("engine.turn.before_checkpoint")
        state = await turn.session.capture_state()
        blob = await self._checkpoints.save(turn.run.object_scope, state)
        checkout = turn.run.checkout
        if checkout is not None:
            # C2.10: the turn-end save just kept the repository as a durable folder; the checkpoint names its commit.
            commit = await read_commit(turn.sandbox, checkout, workspace_root=self._config.workspace_root)
            blob = replace(blob, repository=commit)
        # What the broker gave this attempt (the strongest at or above the floor), so the run says where it ran.
        blob = replace(blob, isolation=turn.sandbox.mode)
        await afailpoint("engine.turn.after_checkpoint")
        await self._sink.checkpointed(turn.run.binding, blob)
        turn.log.info(
            "engine.checkpointed", schemaVersion=blob.schema_version, bytes=blob.content.size_bytes,
            isolation=turn.sandbox.mode.value,
        )
        return blob


def _owed(run: EngineRunContext, manifest: WorkspaceManifest | None) -> frozenset[str]:
    """The drafts a completed turn leaves unsettled: the requested file while the workspace still owes it (WP-70)."""
    if run.owed is None or manifest is None:
        return frozenset()
    name = run.owed(manifest.files)
    return frozenset() if name is None else frozenset({name})


def _with_note(text: str, note: str) -> str:
    return f"{text}\n\n{note}" if text and note else text or note


@dataclass(slots=True)
class _Opening:
    """A turn before its engine opens: ``cancel`` cancels its task, and ``run_turn`` makes that its outcome."""

    task: asyncio.Task[object]
    cancelled: bool = False


async def _acquire_or_release(broker: SandboxBroker, spec: SandboxSpec, lease: LeaseRef, key: str) -> SandboxHandle:
    """The broker's acquire, which a cancel does not cut: a sandbox it is still starting would be left running with
    nobody to release it. The cancel waits for the sandbox, releases it, and goes on."""
    acquiring = asyncio.ensure_future(broker.acquire(spec, lease=lease, idempotency_key=key))
    try:
        return await asyncio.shield(acquiring)
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            await broker.release(await acquiring, keep_warm_s=0)
        raise


@dataclass
class _Parking:
    """What ``_park`` decided for a signed-in sandbox at a turn's end (SIGNIN-5); unset, it is not kept."""

    launch: EngineLaunch | None = None
    # The sandbox's processes before the turn started any: the only ones that outlive it.
    spare: frozenset[int] = frozenset()
    kept: KeptSignIn | None = None
    idle_s: int = 0
    keep_warm_s: int = 0


@dataclass
class _Watched:
    task: asyncio.Future[None] | None = None
    # Past the wait: it has taken the record (or is about to) and will sign out; a turn waits for it.
    ending: bool = False


class KeptSandboxWatches:
    """A worker's watches over the signed-in sandboxes it parked (SIGNIN-5), shared by every controller it makes
    (one per attempt), so a later turn on this worker stops the watch before it takes the sandbox."""

    def __init__(self) -> None:
        self._by_key: dict[str, _Watched] = {}

    def __len__(self) -> int:
        return len(self._by_key)

    def add(self, key: str, watched: _Watched) -> None:
        assert watched.task is not None
        self._by_key[key] = watched
        watched.task.add_done_callback(lambda _t: self._forget(key, watched))

    def _forget(self, key: str, watched: _Watched) -> None:
        if self._by_key.get(key) is watched:
            del self._by_key[key]

    async def stop(self, key: str) -> None:
        """Before a turn takes the sandbox: a watch still waiting stops; one already ending it finishes first (the
        turn then finds no record and signs in afresh)."""
        watched = self._by_key.pop(key, None)
        if watched is None or watched.task is None:
            return
        if not watched.ending:
            _ = watched.task.cancel()
        with contextlib.suppress(BaseException):
            await watched.task

    async def aclose(self) -> None:
        """A worker shutting down: the waiting watches stop and the sandboxes stay kept for the next turn's worker
        (``_kept`` checks them; the broker's keep-warm ends each one no turn takes). Ending ones finish."""
        for key in list(self._by_key):
            await self.stop(key)


# Sign-in lines sit apart from the engine's own events of the turn (which count from 0), so neither drops the other.
_SIGNIN_SEQ = 1_000_000
# The turn's one repository line (GIT-FLOW), apart from both.
_REPOSITORY_SEQ = 900_000


@dataclass
class _SignInProgress:
    """The sign-in card's lines for one turn (SIGNIN): ids, state, reason and plan kind only."""

    sink: EngineEventSink
    binding: RunBinding
    turn_id: str
    attempt_id: str
    seq: int = _SIGNIN_SEQ

    async def __call__(self, state: str, reason_code: str | None, plan: str | None) -> None:
        self.seq += 1
        event = SignInProgress(
            EngineEventMeta(self.turn_id, self.seq),
            self.attempt_id,
            state,  # type: ignore[arg-type]
            reason_code,
            plan,
        )
        await self.sink.event(self.binding, event)


async def _first[T](task: asyncio.Future[T], event: asyncio.Event) -> None:
    """Returns once ``task`` is done or ``event`` is set, whichever comes first."""
    waiter = asyncio.ensure_future(event.wait())
    try:
        _ = await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        _ = waiter.cancel()


async def _stop_when(event: asyncio.Event, turn: _Turn) -> None:
    await event.wait()
    turn.log.warning("engine.turn.signin_ended", reasonCode="account_disconnected")
    await turn.stop()


__all__ = [
    "BUDGET_EXHAUSTED",
    "BUDGET_STOP_CODES",
    "OUTPUTS_FOLDER",
    "OUTPUT_ACTIVE_CONTENT",
    "OUTPUT_SYMLINK",
    "OUTPUT_UNSAFE_NAME",
    "WRITER_LEASE_UNRENEWED",
    "KEPT_LABEL",
    "KEPT_LIFETIME_GRACE_S",
    "KEPT_LOGOUT_GRACE_S",
    "RESUME_NOTE",
    "TICK_S",
    "EngineController",
    "EngineControllerConfig",
    "EngineRunContext",
    "EngineTurnOutcome",
    "KeptSandboxWatches",
    "OutputRefusal",
    "collected_deliverables",
    "deliverables",
    "engine_mode",
    "engine_sandbox_spec",
    "engine_warm_shapes",
    "is_active_content",
    "kept_sandbox_key",
    "kept_sandbox_labels",
    "no_secrets",
    "placeholder_for",
    "workspace_deliverables",
]
