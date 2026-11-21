"""The real lane for durable engine runs (E1, gate G4): E0's rig with Node's real journal behind it.

* Node: the journal HTTP test server (``journal-http-server.ts``): its internal routes, execution and
  journal services, attempt tokens, sealed content, workspace store, writer leases, workspace keys and
  gateway-token authority, over in-memory stores. The egress proxy pulls every sandbox's gateway token
  from it; the tool gateway verifies them with its published keys.
* A worker is one process (``g4_worker.py``, or ``run_attempt`` in process): ``RunDriver`` with the
  ``EngineKernel`` registered for ``engine``, its own tool gateway (``ToolGatewayApp`` on its own port of
  the upstream bridge), its own ``DockerSandboxBroker`` on the lane's label namespace, the real opencode
  image. Killing it is ``SIGKILL`` (a failpoint ``kill9``, or a timer): nothing of it runs after.
* What outlives a worker, as a deployment's stores do: Node; the objects directory (the deployment's
  object store, sealed per session with the key Node leases each attempt); the artifact registry (a file);
  the SaaS the ``create`` tool writes to (an HTTP server here, counting each effect).

Everything the lane creates carries ``pipeshub.harness.owner=x-lead`` and the lane's own namespace and
suffix, and is removed by them at teardown.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import fcntl
import hashlib
import importlib
import json
import logging
import os
import pickle
import secrets
import signal
import sys
import time
import warnings
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import httpx
import uvicorn
from aiohttp import web
from fastapi import FastAPI

from app.harness.access.grants import NodeRunModelGrants
from app.harness.adapters.content_store import PipesHubContentStore
from app.harness.approvals import engine_guarded
from app.harness.engines.durable import TaskCardPublicationLog, attempt_binding
from app.harness.engines.presenter import PipesHubEngineRunPresenter, SessionRevisions
from app.harness.gateway.host import HttpToolGatewayHost
from app.harness.gateway.keys import ConfiguredGatewayKeys, parse_published
from app.harness.gateway.mcp import ToolGatewayApp
from app.harness.gateway.org_binding import OrgBoundGatewayTokenVerifier
from app.harness.journal_node import NodeJournal
from app.harness.labels import (
    SessionSourceLedger,
    call_provenance,
    inherited_grants,
    inherited_label,
)
from app.harness.repositories import (
    REPOSITORY_FETCH,
    RepositoryAsks,
    RepositoryCatalog,
    RepositoryFetchTools,
    repository_effect,
)
from app.harness.sandbox.docker_broker import DockerSandboxBroker
from app.harness.sandbox.docker_client import DockerClientProvider
from app.harness.sandbox.docker_egress import DockerEgressWiring
from app.harness.skills.bundles import SkillBundleService
from app.harness.unpinned import ConfiguredOrgAccessPolicies
from app.harness.workspace.node_store import (
    NodeKeyProvider,
    NodeWorkspaceStore,
    NodeWorkspaceWriters,
)
from app.model_gateway_main import guarded_access
from harness_core import failpoints
from harness_core.access.types import AccessRouteRef
from harness_core.artifacts.memory import InMemoryArtifactStore
from harness_core.artifacts.publisher import WorkspaceArtifactPublisher
from harness_core.budget.memory import InMemoryBudgetLedger
from harness_core.budget.types import Cap, PinnedBudget, RunAccountRef
from harness_core.clock import SystemClock
from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.driver import RunDriver
from harness_core.effects import EffectRunner, ToolExecution, args_digest
from harness_core.effects.flow import FlowRule, GrantingLedger
from harness_core.effects.policy import ENGINE_APPROVALS, LayeredApprovalPolicy
from harness_core.effects.types import ToolEffect, allow_all, tool_id_for
from harness_core.engines.checkpoints import ObjectStoreEngineCheckpoints
from harness_core.engines.controller import EngineController, EngineControllerConfig
from harness_core.engines.durable.inputs import StoreInputSource
from harness_core.engines.durable.kernel import ENGINE, EngineKernel
from harness_core.engines.durable.ports import AssembledEngineRun
from harness_core.engines.policy import approval_policy_for
from harness_core.engines.profiles import profile_digest, profile_json
from harness_core.engines.snapshots import ObjectStoreSandboxSnapshots
from harness_core.gateway import RunToolEndpoint
from harness_core.gateway.memory import InMemoryCitationSink, StaticGatewayKeySource
from harness_core.gateway.model_gateway import ModelGateway
from harness_core.gateway.model_server import create_model_gateway_app
from harness_core.ids import RandomIds
from harness_core.interactions.ports import RunBinding
from harness_core.invoker import CatalogTool, InMemoryToolCatalog, InvocationScope
from harness_core.journal.types import PROVENANCE_RECORDS, REPOSITORY_FETCHES
from harness_core.kernel.core.tool_schema import ToolSchema
from harness_core.knowledge.bulk_export import ShelfRelay
from harness_core.logs import HarnessLog
from harness_core.provenance.stamp import ProvenanceStamp
from harness_core.repos.fetch import FetchConfig, hand_off_mount
from harness_core.sandbox.auth import StaticBearerAuth
from harness_core.sandbox.egress.addresses import SubnetPool
from harness_core.sandbox.egress.client import RemoteEgressBindings
from harness_core.sandbox.types import Resources
from harness_core.skills.materializer import TarSkillMaterializer
from harness_core.skills.memory import InMemorySkillContentSource
from harness_core.skills.types import SkillPin, SkillRef
from harness_core.testing.engines.gateway import ConformanceGateway
from harness_core.testing.engines.scripted_wrap_up import ScriptedWrapUp
from harness_core.testing.run_spec import minimal_run_spec
from harness_core.workspace.forks import session_scope, session_workspace_id
from harness_core.workspace.sealing import SealedObjectStore
from harness_core.workspace.sync.strategies import SyncStrategies
from harness_core.workspace.sync.tar_sync import TarWorkspaceSync
from harness_core.workspace.types import ObjectRef, ObjectScope, ObjectUnreadable
from harness_engines.pipeshub import pipeshub_tool_folder
from harness_engines.release import (
    release_default_profile,
    release_registry,
    release_snapshot_policy,
)
from tests.support.sandbox_reaper import die_with_parent
from tests.unit.harness.access.support import StackConfig as GatewayConfig
from tests.unit.harness.engines.lane_releases import (
    lane_admits_candidates,
    lane_images,
    lane_release,
    lane_releases,
)
from tests.unit.harness.engines.real_stack import (
    OWNER,
    STATIC_MODEL_KEY,
    _free_port,  # pyright: ignore[reportPrivateUsage]
    lane_warm_pool,
    start_stack,
    stop_stack,
)
from tests.unit.harness.gateway.binding_keys import TEST_ATTESTER
from tests.unit.harness.sandbox_broker.conftest import cleanup, cleanup_networks
from tests.unit.harness.test_node_journal_contract import (
    _NodeServer,  # pyright: ignore[reportPrivateUsage]
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Mapping

    from app.harness.journal_node import NodeClaim
    from harness_core.effects.types import ApprovalPolicy, ToolContext
    from harness_core.engines.durable.ports import EngineAttempt
    from harness_core.engines.types import EngineVersion
    from harness_core.kernel.core.messages import ToolCall
    from harness_core.sandbox.ports import SandboxBroker

PYTHON_ROOT = Path(__file__).resolve().parents[4]
ORG = "org-contract"
GATEWAY_MOUNT = "/harness/tool-gateway"
EGRESS_POOL = "10.214.0.0/16"
# A1: an engine run pins a concrete configured model and its access route; the org's key pays.
CONFIGURED_MODEL = {"modelKey": "key-g4", "modelName": "local-model"}
ROUTE = {
    "provider": "openaicompatible",
    "model": "local-model",
    "access": {"route": "gateway", "accountKind": "org_api_key", "pays": "org"},
}
CREATE_ID = tool_id_for("create", source="g4", instance="inst-1")
LOOKUP_ID = tool_id_for("lookup", source="g4", instance="inst-1")
SKILL_MD = b"---\nname: report\ndescription: Use when asked for a report.\n---\nPut reports under out/.\n"
INSTRUCTIONS = "Work in the current directory. Write deliverables under out/."
_LOG = HarnessLog.named("tests.durable_stack")
# The repository cache's disk reserve for these runs (the default keeps up to 20 GB free; two fixture trees need MBs).
_GIT_CACHE_MIN_FREE = 1024 * 1024 * 1024


def engine_version(kind: str = "opencode") -> EngineVersion:
    return lane_release(kind).version


def opencode_version() -> EngineVersion:
    return engine_version("opencode")


def release_images() -> dict[EngineVersion, str]:
    """Every released engine on its release image (E0b: the allowlist chooses it), or a candidate under test (CC1)."""
    return lane_images()


def engine_spec(
    *,
    skills: tuple[tuple[str, str], ...] = (),
    kind: str = "opencode",
    route: dict[str, Any] | None = None,
    tools: tuple[str, ...] = (CREATE_ID, LOOKUP_ID),
    model: dict[str, str] | None = None,
    access: str = "openaicompatible:local-model",
    limits: dict[str, Any] | None = None,
    environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A v3 spec pinned with the engine's release profile and its access route (as Node's admission pins
    them), granting ``tools`` (``create`` and ``lookup`` by default) and pinning ``model`` (the org's
    configured model entry); ``limits``: ``RunLimits`` fields that narrow the profile's (WP-70's step allowance)."""
    profile = release_default_profile(engine_version(kind), AccessRouteRef(access, "d"))
    if limits:
        profile = dataclasses.replace(profile, limits=dataclasses.replace(profile.limits, **limits))
    spec = minimal_run_spec(max_turns=8).model_dump(mode="json", by_alias=True, exclude_none=True)
    spec["schemaVersion"] = 3
    spec["executor"] = {
        "kind": ENGINE,
        "engine": {
            "kind": profile.engine.kind,
            "version": profile.engine.version,
            "binaryDigest": profile.engine.binary_digest,
            "profileDigest": profile_digest(profile),
            "profile": profile_json(profile),
        },
    }
    spec["model"]["policy"] = [route or ROUTE]
    spec["grants"]["tools"] = [{"toolId": tool, "descriptorDigest": "sha256:" + "0" * 64} for tool in tools]
    spec["extensions"] = {
        "x.pipeshub": {
            "model": dict(model or CONFIGURED_MODEL),
            "knowledge": {"apps": [], "kb": [], "strictScope": False},
            "mcp": [],
            "skills": [{"name": n, "bundleDigest": d} for n, d in skills],
            "mounts": [],
            "via": "session",
            # WP100-FU-7: the run's environment pin (``x.pipeshub.environment``), as Node's admission pins it.
            **({"environment": environment} if environment is not None else {}),
        }
    }
    return spec


class DirectoryObjectStore:
    """The deployment's object store as a directory: content-addressed, verified on read."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def _path(self, scope: ObjectScope, object_id: str) -> Path:
        return self._root / scope.tenant_id / object_id.replace(":", "_")

    async def put(self, scope: ObjectScope, content: bytes) -> ObjectRef:
        digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
        path = self._path(scope, digest)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            temporary = path.with_suffix(f".{secrets.token_hex(4)}")
            temporary.write_bytes(content)
            temporary.replace(path)
        return ObjectRef(digest, digest, len(content))

    async def content_hash(self, scope: ObjectScope, content: bytes) -> str:
        return f"sha256:{hashlib.sha256(content).hexdigest()}"

    async def get(self, scope: ObjectScope, ref: ObjectRef) -> bytes:
        path = self._path(scope, ref.object_id)
        if not path.exists():
            raise FileNotFoundError(ref.object_id)
        content = path.read_bytes()
        if f"sha256:{hashlib.sha256(content).hexdigest()}" != ref.content_hash or len(content) != ref.size_bytes:
            raise ObjectUnreadable("object does not match its reference")
        return content


class FileArtifactStore:
    """The artifact registry as one file shared by every worker: ``InMemoryArtifactStore`` loaded and
    saved under a lock around each call."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401 - forwards ArtifactStore's methods
        async def call(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
            with self._path.with_suffix(".lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                store = InMemoryArtifactStore()
                if self._path.exists():
                    store._scopes = pickle.loads(self._path.read_bytes())  # pyright: ignore[reportPrivateUsage]
                result = await getattr(store, name)(*args, **kwargs)
                # Written aside and renamed: a worker SIGKILLed mid-write must not truncate the shared registry
                # (a deployment's registry is a database; G4 run 4 lost 29 runs to this rig bug).
                temporary = self._path.with_suffix(f".{os.getpid()}.tmp")
                _ = temporary.write_bytes(pickle.dumps(store._scopes))  # pyright: ignore[reportPrivateUsage]
                temporary.replace(self._path)
                return result

        return call


@dataclass
class Saas:
    """The external system ``create`` writes to: each effect counted by (tool, arguments)."""

    effects: Counter[str] = field(default_factory=Counter[str])

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/saas/create", self._create)
        return app

    async def _create(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.effects[str(body["digest"])] += 1
        return web.json_response({"id": f"T-{len(self.effects)}"})

    def duplicates(self) -> dict[str, int]:
        return {key: n for key, n in self.effects.items() if n > 1}


@dataclass(frozen=True)
class WorkerConfig:
    run_id: str
    session_id: str
    node_url: str
    node_control: str
    credential: str
    host_ip: str
    gateway_port: int
    control_url: str
    control_token: str
    proxy: str
    namespace: str
    prefix: str
    objects_dir: str
    artifacts_path: str
    saas_url: str
    labels: dict[str, str]
    lease_ms: int = 30_000
    failpoints_file: str | None = None
    skills: bool = True
    # The run's tools: G4's ``create``/``lookup`` over the lane's SaaS, ``research`` (RUN01's public web), ``none``,
    # or ``module:function``, a factory ``(cfg, read_tree) -> (catalog, idempotent)`` the worker process imports, so a
    # suite that brings its own connector needs no change here.
    catalog: str = "g4"
    instructions: str = INSTRUCTIONS
    # WP94-FU-2: set, the worker serves ``repository_fetch`` for this one git host as production's worker does (its
    # fetcher on the worker, the hand-off onto the turn's shelf, the asks over the session's source ledger, every
    # result stamped with its provenance). The host is dialled on this machine's loopback at ``git_port``, its
    # certificate checked with ``git_ca_file``; fetched trees are cached in ``git_cache_dir``.
    git_host: str | None = None
    git_port: int = 0
    git_ca_file: str = ""
    git_cache_dir: str = ""
    # WP100-FU-7: ``module:function``, a factory ``(cfg) -> SharedCacheMounts`` the worker process imports for the
    # caches its controller mounts (it may read ``shared_caches_path``); empty, none.
    shared_cache_mounts: str = ""
    shared_caches_path: str = ""
    # The broker's data volume, where those caches' persistent mounts live.
    data_volume: str = ""
    # ``allow`` (G4: every call runs) or ``engine`` (``ENGINE_APPROVALS`` with the profile's, as production asks).
    approvals: str = "allow"
    # P9: the org (an object id on Node's Mongo mode), the port the sandbox reaches this worker's tool gateway
    # on (a forwarder in front of ``gateway_port``), the sandbox broker as a service, held approvals woken over
    # Redis, and claim-next polling instead of a claim by run id (as a worker whose broker hints are lost).
    org_id: str = "org-contract"
    gateway_public_port: int | None = None
    broker_url: str | None = None
    broker_token: str = ""
    wakeup_redis: str | None = None
    claim_next: bool = False
    # WP-70: the run's wrap-up on a scripted model (``ScriptedWrapUp``); off, the run has none (unmetered).
    wrap_up: bool = False

    def to_json(self) -> str:
        return json.dumps(self.__dict__)

    @classmethod
    def from_json(cls, raw: str) -> WorkerConfig:
        return cls(**json.loads(raw))


class _Credential:
    def __init__(self, value: str) -> None:
        self._value = value

    async def worker_credential(self) -> str:
        return self._value


class _NoLoop:
    async def build(self, claim: object) -> object:
        raise AssertionError("this lane runs engine runs only")


# P5.9b: set, every attempt takes and restores sandbox snapshots (the release policy, capped small).
SNAPSHOTS_ENV = "HARNESS_TEST_SNAPSHOTS"
SNAPSHOT_EVENTS = ("engine.snapshot.saved", "engine.snapshot.restored", "engine.snapshot.refused", "engine.snapshot.unusable")


def snapshots_on() -> bool:
    return bool(os.environ.get(SNAPSHOTS_ENV))


class SnapshotCounter(logging.Handler):
    """Counts the controller's snapshot lines in this worker, for the G4 report."""

    def __init__(self) -> None:
        super().__init__()
        self.counts: dict[str, int] = dict.fromkeys(SNAPSHOT_EVENTS, 0)

    def emit(self, record: logging.LogRecord) -> None:
        name = getattr(record, "harness_event", "")
        if name in self.counts:
            self.counts[name] += 1


@dataclass
class RigAssembler:
    """``EngineRuntimeAssembler`` of the lane: PipesHub's bindings where durability lives (Node's routes for
    the workspace, writer lease, key and content; the presenter and the publication log; the run's
    ``RunToolEndpoint``), a two-tool catalog over the lane's SaaS."""

    cfg: WorkerConfig
    journal: NodeJournal
    broker: SandboxBroker
    gateway: ToolGatewayApp

    async def build(self, attempt: EngineAttempt) -> AssembledEngineRun:
        cfg = self.cfg
        claim = cast("NodeClaim", attempt.claim)
        route = self.journal.attempt_route(claim.token)
        workspaces = NodeWorkspaceStore(route)
        objects = SealedObjectStore(DirectoryObjectStore(Path(cfg.objects_dir)), NodeKeyProvider(route))
        source = InMemorySkillContentSource()
        source.publish(claim.org_id, "report", {"SKILL.md": SKILL_MD})
        skills = SkillBundleService(source=source, objects=objects, workspaces=workspaces, session_id=claim.session_id)
        pins: tuple[SkillPin, ...] = ()
        if cfg.skills:
            bundle = await skills.build(session_scope(claim.org_id, claim.session_id), SkillRef("report"))
            pins = (SkillPin("report", bundle.digest),)
        clock = SystemClock()
        repositories = _worker_repositories(cfg, claim) if cfg.git_host else None
        controller = EngineController(
            registry=release_registry(releases=lane_releases()),
            broker=self.broker,
            workspaces=workspaces,
            sync_for=lambda scope: TarWorkspaceSync(objects=objects, scope=scope, clock=clock),
            strategies=SyncStrategies(),
            skill_bundles=skills,
            skill_materializer=TarSkillMaterializer(objects=objects, workspaces=workspaces),
            checkpoints=ObjectStoreEngineCheckpoints(objects),
            sink=attempt.sink,
            tool_host=HttpToolGatewayHost(
                self.gateway, base_url=f"http://{cfg.host_ip}:{cfg.gateway_public_port or cfg.gateway_port}{GATEWAY_MOUNT}"
            ),
            clock=clock,
            placeholder_key=secrets.token_bytes(32),
            config=EngineControllerConfig(
                images=release_images(),
                resources=Resources(cpus=1.0, memory_mb=1024, pids=256, scratch_mb=256),
                # Small, so a short scripted turn saves mid-turn (G4's autosave and publish points).
                autosave_changes=2,
                tick_s=1.0,
                snapshot=release_snapshot_policy(max_bytes=8 * 1024 * 1024) if snapshots_on() else None,
                shared_caches=_shared_cache_mounts(cfg),
                tool_folder=pipeshub_tool_folder() if repositories is not None else None,
                repos_mount=hand_off_mount(repositories.tools.fetcher.config) if repositories is not None else None,
            ),
            writers=NodeWorkspaceWriters(route),
            snapshots=ObjectStoreSandboxSnapshots(objects),
            shelf_relay=repositories.shelf if repositories is not None else None,
        )
        async def read_tree() -> dict[str, bytes]:
            head = await workspaces.head(ObjectScope(claim.org_id), session_workspace_id(claim.session_id))
            scope = session_scope(claim.org_id, claim.session_id)
            return {path: await objects.get(scope, entry.content) for path, entry in head.files.items()}

        catalog, idempotent = _catalog_for(cfg, read_tree)
        approvals = (
            LayeredApprovalPolicy((*ENGINE_APPROVALS.layers, *approval_policy_for(attempt.profile).layers))
            if cfg.approvals == "engine"
            else allow_all
        )
        provenance = None
        if repositories is not None:
            catalog, approvals, provenance = repositories.serve(catalog, approvals, idempotent)
        endpoint = RunToolEndpoint(
            binding=attempt_binding(claim),
            catalog=catalog,
            effects=EffectRunner(
                attempt.effect_log, approvals, hold=attempt.hold, guard=attempt.guard, propagate=attempt.propagate
            ),
            scope=InvocationScope(claim.run_id, claim.org_id, claim.attempt_id),
            ids=attempt.ids,
            citations=InMemoryCitationSink(),
            capabilities=controller.capabilities_for(attempt.profile.engine),
            provenance=provenance,
        )
        presenter = PipesHubEngineRunPresenter(SessionRevisions(workspaces, objects, claim.org_id, claim.session_id))
        publisher = WorkspaceArtifactPublisher(
            cast("Any", FileArtifactStore(Path(cfg.artifacts_path))),
            TaskCardPublicationLog(attempt, presenter),
            objects,
            object_scope=lambda b: session_scope(b.tenant, claim.session_id),
        )
        return AssembledEngineRun(
            controller=controller,
            tools=endpoint,
            tool_binding=attempt_binding(claim),
            grant_size=len(idempotent),
            artifacts=publisher,
            content=PipesHubContentStore(claim.content),
            idempotent=idempotent,
            skills=pins,
            instructions=cfg.instructions,
            labels=dict(cfg.labels),
            workspaces=workspaces,
            presenter=presenter,
            inputs=StoreInputSource(
                cast("Any", FileArtifactStore(Path(cfg.artifacts_path))),
                objects,
                object_scope=lambda b: session_scope(b.tenant, claim.session_id),
            ),
            secrets=route.secret_values,
            objects=objects,
            wrap_up=ScriptedWrapUp() if cfg.wrap_up else None,
            ledger=repositories.ledger if repositories is not None else None,
        )


@dataclass
class _WorkerRepositories:
    """``repository_fetch`` on the lane's worker, composed as ``PipesHubEngineRuntimeAssembler._runtime`` composes it
    (WP94-FU-2): the worker fetches, hands the copy onto the turn's shelf for ``ph git clone``, records the checkout
    as a reference so no save carries it, and asks the person over the session's source ledger. Only the fetcher's
    address check differs: the host is this machine's loopback, standing for the public host it names."""

    claim: NodeClaim
    shelf: ShelfRelay
    tools: RepositoryFetchTools
    ledger: GrantingLedger

    def serve(
        self, catalog: Any, approvals: ApprovalPolicy, idempotent: dict[str, bool]  # noqa: ANN401 - a ToolCatalog
    ) -> tuple[Any, ApprovalPolicy, ProvenanceStamp]:
        """The run's catalog with ``repository_fetch`` beside it, its approvals with the repository asks over the
        flow rule, and the stamp that records every result's provenance (ADR-RUN-01 D3)."""
        from tests.support.research_run import web_rules

        claim = self.claim
        served = RepositoryCatalog(catalog, self.tools)
        idempotent[repository_effect().tool_id] = repository_effect().idempotent
        guarded = engine_guarded(approvals, allow_all, rules=(FlowRule("ask"),), ledger=self.ledger, run_id=claim.run_id, log=_LOG)
        asks = RepositoryAsks(
            guarded, self.ledger, claim.run_id, self.ledger.grants, size_of=self.tools.size_of(claim.org_id),
            cap_bytes=self.tools.fetcher.config.repo_max_bytes, log=_LOG, references=self.shelf,
        )
        names = [schema.name for schema in served.schemas() or ()]
        stamp = ProvenanceStamp.for_tools(
            names, {**web_rules(), **served.provenance()}, journal=claim.journal_accepts(PROVENANCE_RECORDS),
            sources_of=call_provenance, log=_LOG,
        )
        return served, asks, stamp


def _worker_repositories(cfg: WorkerConfig, claim: NodeClaim) -> _WorkerRepositories:
    from tests.support import research_run as rr

    assert cfg.catalog == "research", "the stamp holds rules for the research catalog's tools only"
    shelf = ShelfRelay()
    config = FetchConfig(
        cache_dir=Path(cfg.git_cache_dir), git_bin="git", port=cfg.git_port, ca_file=cfg.git_ca_file,
        min_free_bytes=_GIT_CACHE_MIN_FREE,
    )
    fetcher = rr.LoopbackFetcher(config, rr.NoResolver())
    assert cfg.git_host == rr.GIT_HOST, cfg.git_host

    async def hosts() -> list[Mapping[str, Any]]:
        return rr.git_hosts()

    tools = RepositoryFetchTools(
        fetcher=fetcher, hosts=hosts, shelf=shelf, journal_events=claim.journal_accepts(REPOSITORY_FETCHES),
        log=_LOG, references=shelf,
    )
    sessions = SessionSourceLedger(
        tool_names={repository_effect().tool_id: REPOSITORY_FETCH, rr.FETCH_URL_ID: rr.FETCH_URL, rr.WEB_SEARCH_ID: rr.WEB_SEARCH},
        inherited=inherited_label(claim.spec),
    )
    return _WorkerRepositories(claim, shelf, tools, GrantingLedger(sessions, inherited_grants(claim.spec)))


def _shared_cache_mounts(cfg: WorkerConfig) -> Any:  # noqa: ANN401 - the factory's SharedCacheMounts
    if not cfg.shared_cache_mounts:
        return None
    module, _, factory = cfg.shared_cache_mounts.partition(":")
    return getattr(importlib.import_module(module), factory)(cfg)


def _catalog_for(
    cfg: WorkerConfig, read_tree: Callable[[], Awaitable[dict[str, bytes]]]
) -> tuple[InMemoryToolCatalog, dict[str, bool]]:
    if cfg.catalog == "none":
        return InMemoryToolCatalog([], []), {}
    if cfg.catalog == "research":
        from tests.support.research_run import research_catalog

        return research_catalog()
    if ":" in cfg.catalog:
        module, _, factory = cfg.catalog.partition(":")
        return getattr(importlib.import_module(module), factory)(cfg, read_tree)
    return _catalog(cfg.saas_url), {CREATE_ID: False, LOOKUP_ID: True}


def _catalog(saas_url: str) -> InMemoryToolCatalog:
    async def create(call: ToolCall, ctx: ToolContext) -> ToolExecution:
        async with httpx.AsyncClient(timeout=30) as client:
            answer = await client.post(f"{saas_url}/saas/create", json={"digest": args_digest(call.arguments)})
        answer.raise_for_status()
        return ToolExecution(status="ok", content=f"created {answer.json()['id']}")

    async def lookup(call: ToolCall, ctx: ToolContext) -> ToolExecution:
        return ToolExecution(status="ok", content=f"found {dict(call.arguments)}")

    def effect(tool_id: str, kind: str, idempotent: bool) -> ToolEffect:
        return ToolEffect(tool_id, kind, idempotent, SinkDescriptor(egress="external", args_leave_boundary=True))  # type: ignore[arg-type]

    tools = [
        CatalogTool(ToolSchema(name="create", description="Create a ticket.", input_schema={"type": "object"}), effect(CREATE_ID, "write", False), create),
        CatalogTool(ToolSchema(name="lookup", description="Look something up.", input_schema={"type": "object"}), effect(LOOKUP_ID, "read", True), lookup),
    ]
    return InMemoryToolCatalog(tools, [CREATE_ID, LOOKUP_ID])


@contextlib.asynccontextmanager
async def _gateway_server(gateway: ToolGatewayApp, host: str, port: int) -> AsyncIterator[None]:
    api = FastAPI()
    api.mount(GATEWAY_MOUNT, gateway)
    server = uvicorn.Server(uvicorn.Config(api, host=host, port=port, log_level="warning", lifespan="off"))
    task = asyncio.ensure_future(server.serve())
    while not server.started:  # noqa: ASYNC110 - uvicorn exposes a flag
        await asyncio.sleep(0.01)
    try:
        yield
    finally:
        server.should_exit = True
        with contextlib.suppress(Exception):
            await task


async def run_attempt(cfg: WorkerConfig) -> dict[str, Any]:
    """One worker: claim the run, run one attempt on the engine kernel, report its disposition."""
    if cfg.failpoints_file is not None:
        failpoints.registry.install_file(Path(cfg.failpoints_file))
    clock = SystemClock()
    counter = SnapshotCounter()
    logging.getLogger("harness_core.engines.controller").addHandler(counter)
    journal = NodeJournal(base_url=cfg.node_url, credentials=_Credential(cfg.credential), lease_ms=cfg.lease_ms)
    keys = StaticGatewayKeySource(parse_published(httpx.get(f"{cfg.node_control}/gateway-public-keys").json()))  # type: ignore[arg-type]
    gateway = ToolGatewayApp(OrgBoundGatewayTokenVerifier(keys, clock))
    provider = DockerClientProvider(max_workers=16)
    broker: SandboxBroker = await _remote_broker(cfg) if cfg.broker_url else await DockerSandboxBroker.create(
        provider=provider,
        clock=clock,
        ids=RandomIds(),
        allowed_images=frozenset(release_images().values()),
        admit_candidate_images=lane_admits_candidates(),
        label_namespace=cfg.namespace,
        volume_prefix=cfg.prefix,
        data_volume=cfg.data_volume or None,
        egress=DockerEgressWiring(
            provider=provider,
            clock=clock,
            proxy_container=cfg.proxy,
            bindings=RemoteEgressBindings(cfg.control_url, StaticBearerAuth(cfg.control_token)),
            pool=SubnetPool(EGRESS_POOL),
            # Signed as the production broker signs, under the key journal-http-server.ts verifies (R38IDENT-FU-7).
            attester=TEST_ATTESTER,
            network_prefix=f"{cfg.prefix}-net-",
        ),
        # Every attempt is a fresh broker process: the pool lives on the daemon, loaded at create (P5.9a).
        warm_pool=lane_warm_pool(release_images().values(), cfg.labels),
    )
    wakeup = _wakeup(cfg.wakeup_redis)
    kernel = EngineKernel(
        journal=journal,
        assembler=RigAssembler(cfg, journal, broker, gateway),
        clock=clock,
        broker=broker,
        interactions=wakeup[0] if wakeup is not None else None,
        renew_interval_s=cfg.lease_ms / 3000,
        poll_s=1.0,
    )
    driver = RunDriver(
        journal=journal,
        assembler=_NoLoop(),  # type: ignore[arg-type]
        clock=clock,
        renew_interval_s=cfg.lease_ms / 3000,
        kernels={ENGINE: kernel},
    )
    try:
        async with _gateway_server(gateway, cfg.host_ip, cfg.gateway_port):
            claim = await _claim(journal, cfg)
            if claim is None:
                return {"status": "not_claimed"}
            disposition = await driver.run_claimed(claim)
            return {
                "status": disposition.status,
                "reason": disposition.reason,
                "attempt": claim.attempt_id,
                "warmServed": broker.pool.served if isinstance(broker, DockerSandboxBroker) else 0,
                "snapshots": counter.counts,
            }
    finally:
        await broker.aclose()
        await journal.aclose()
        provider.close()
        if wakeup is not None:
            await wakeup[1].aclose()


async def _remote_broker(cfg: WorkerConfig) -> SandboxBroker:
    """The broker as D1 deploys it: its own process behind the broker HTTP API (P9 FM-2)."""
    from harness_core.sandbox.remote.client import RemoteSandboxBroker

    assert cfg.broker_url is not None
    return await RemoteSandboxBroker.open(cfg.broker_url, StaticBearerAuth(cfg.broker_token))


def _wakeup(url: str | None) -> tuple[Any, Any] | None:
    """Held approvals woken over Redis, as ``harness_main`` wires them (P9 FM-7); the client to close after."""
    if url is None:
        return None
    import redis.asyncio as aioredis

    from app.harness.interaction_wakeup import RedisInteractionWakeup

    client = aioredis.from_url(url, socket_timeout=2, socket_connect_timeout=2)
    return RedisInteractionWakeup(client), client


async def _claim(journal: NodeJournal, cfg: WorkerConfig) -> NodeClaim | None:
    """By run id, or by polling claim-next until the run is handed out (a worker without broker hints)."""
    if not cfg.claim_next:
        return await journal.claim(cfg.run_id, worker_id=f"g4-{os.getpid()}", org_id=cfg.org_id)
    for _ in range(120):
        claim = await journal.claim(None, worker_id=f"g4-{os.getpid()}", org_id=cfg.org_id)
        if claim is not None:
            return claim
        await asyncio.sleep(1)
    return None


@dataclass
class Worker:
    process: asyncio.subprocess.Process
    result_path: Path
    log_path: Path

    async def wait(self, timeout_s: float) -> dict[str, Any] | None:
        """The attempt's disposition; ``None`` when the worker died without one."""
        try:
            _ = await asyncio.wait_for(self.process.wait(), timeout_s)
        except TimeoutError:
            kill(self.process)
            _ = await self.process.wait()
            return {"status": "timeout"}
        if not self.result_path.exists():
            return None
        return cast("dict[str, Any]", json.loads(self.result_path.read_text()))

    def log(self) -> str:
        return self.log_path.read_text(errors="replace") if self.log_path.exists() else ""


async def spawn_worker(cfg: WorkerConfig, workdir: Path) -> Worker:
    config = workdir / f"{cfg.run_id}-{secrets.token_hex(3)}.json"
    config.write_text(cfg.to_json())
    log_path = config.with_suffix(".log")
    with log_path.open("wb") as log:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "tests.unit.harness.engines.g4_worker",
            str(config),
            cwd=str(PYTHON_ROOT),
            env={**os.environ, "PYTHONPATH": str(PYTHON_ROOT)},
            stdout=log,
            stderr=asyncio.subprocess.STDOUT,
            preexec_fn=die_with_parent,
        )
    return Worker(process, config.with_suffix(".result.json"), log_path)


def kill(process: asyncio.subprocess.Process) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.kill(process.pid, signal.SIGKILL)


class NodeOutage:
    """A TCP forwarder in front of Node for the workers: ``down`` drops every connection and refuses new
    ones until ``up`` (Node unreachable, as during its restart; its stores outlive it, as Mongo's do).
    P9 puts the same forwarder in front of the model gateway and a worker's tool gateway (``port`` fixed when
    the address must be known before it listens)."""

    def __init__(self, host: str, target: tuple[str, int], port: int = 0) -> None:
        self._host = host
        self._target = target
        self._port = port
        self._server: asyncio.Server | None = None
        self._open: set[asyncio.StreamWriter] = set()
        self.is_down = False
        self._flowing = asyncio.Event()
        self._flowing.set()

    @property
    def url(self) -> str:
        assert self._server is not None
        return f"http://{self._host}:{self._server.sockets[0].getsockname()[1]}"

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._accept, self._host, self._port, reuse_address=True)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
        self.down()

    def down(self) -> None:
        self.is_down = True
        for writer in list(self._open):
            writer.transport.abort()
        self._open.clear()

    def up(self) -> None:
        self.is_down = False

    def partition(self) -> None:
        """Holds every byte both ways, on open connections and new ones, closing nothing: a partition drops packets
        silently, so neither side gets a reset and each learns only from its own timeouts (CH-12)."""
        self._flowing.clear()

    def heal(self) -> None:
        """Delivers what was held, as TCP's retransmission does once the path is back."""
        self._flowing.set()

    async def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self.is_down:
            writer.transport.abort()
            return
        await self._flowing.wait()
        try:
            up_reader, up_writer = await asyncio.open_connection(*self._target)
        except OSError:
            writer.transport.abort()
            return
        self._open.update((writer, up_writer))

        async def pipe(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
            try:
                while data := await src.read(65536):
                    await self._flowing.wait()
                    dst.write(data)
                    await dst.drain()
            except (OSError, asyncio.IncompleteReadError):
                pass
            finally:
                self._open.discard(dst)
                dst.transport.abort()

        _ = await asyncio.gather(pipe(reader, up_writer), pipe(up_reader, writer))


LEASE_MS = 10_000


@dataclass
class DurableLane:
    """Node, the proxy, the scripted model, the SaaS: everything but the workers, for one module."""

    stack: Any  # RealStack
    node: Any  # _NodeServer
    outage: NodeOutage
    saas: Saas
    saas_url: str
    model: ConformanceGateway
    workdir: Path
    namespace: str
    prefix: str
    http: httpx.AsyncClient
    # Read once: a Docker call on this loop would stall the Node forwarder for every worker.
    control_url: str = ""
    engine: str = "opencode"
    # The model gateway's budget ledger (P6.5's reference; every run pinned uncapped at admission).
    ledger: InMemoryBudgetLedger = field(default_factory=lambda: InMemoryBudgetLedger(SystemClock()))
    # What the gateway's guarded resolver reads: the org's configured models (and no narrowing).
    gateway_config: GatewayConfig | None = None
    # Run -> its session (default ``ses-<run>``) and what its workers bind (EV2: runs of one session).
    sessions: dict[str, str] = field(default_factory=dict[str, str])
    workers: dict[str, dict[str, Any]] = field(default_factory=dict[str, dict[str, Any]])
    # P9: the org runs are admitted to, and a run's default session (object ids on Node's Mongo mode).
    org: str = ORG
    session_for: Callable[[str], str] = field(default=lambda run_id: f"ses-{run_id}")
    # P9: a forwarder in front of the model gateway, when the lane was asked for one.
    e3_outage: NodeOutage | None = None
    # Every worker process this lane started: the lane's teardown kills those still running before it removes the
    # namespace's sandboxes, or one acquires a sandbox after the removal (SBXLEAK).
    processes: list[asyncio.subprocess.Process] = field(default_factory=list[asyncio.subprocess.Process])

    async def admit(
        self,
        run_id: str,
        prompt: str,
        *,
        route: dict[str, Any] | None = None,
        session: str | None = None,
        tools: tuple[str, ...] | None = None,
        model: dict[str, str] | None = None,
        access: str | None = None,
        worker: dict[str, Any] | None = None,
        inputs: list[dict[str, Any]] | None = None,
        limits: dict[str, Any] | None = None,
        environment: dict[str, Any] | None = None,
    ) -> None:
        """``worker``: ``WorkerConfig`` fields every worker of this run gets (``catalog``, ``instructions``, ``skills``);
        ``inputs``: the run input's ``inputArtifacts`` (E1b), artifacts ``upload`` put in the session; ``limits``: the
        profile's narrowed ``RunLimits`` (WP-70)."""
        extra: dict[str, Any] = {"tools": tools} if tools is not None else {}
        if limits is not None:
            extra["limits"] = limits
        if access is not None:
            extra["access"] = access
        if environment is not None:
            extra["environment"] = environment
        spec = engine_spec(kind=self.engine, route=route, model=model, **extra)
        self.sessions[run_id] = session or self.session_for(run_id)
        self.workers[run_id] = dict(worker or {})
        body: dict[str, Any] = {
            "runId": run_id, "sessionId": self.sessions[run_id], "inputText": prompt, "spec": spec, "orgId": self.org,
        }
        if inputs:
            body["inputArtifacts"] = inputs
        _ = (await self.http.post("/admit", json=body)).raise_for_status()
        self.ledger.pin(RunAccountRef(self.org, run_id), PinnedBudget(Cap()))

    async def upload(self, session: str, name: str, content: bytes, mime_type: str) -> str:
        """A conversation artifact of ``session`` (v1), as a user's upload is: what a run's input can name."""
        store = cast("Any", FileArtifactStore(self.workdir / "artifacts.pickle"))
        stored, _ = await store.register_output(
            RunBinding(self.org, session, "upload"), artifact_id=f"up-{session}-{name}", name=name, mime_type=mime_type,
            content=content, lifecycle="final",
        )
        return str(stored.artifact_id)

    async def resolve(self, run_id: str, interaction_id: str, decision: str, *, scope: str | None = None) -> None:
        """A person decides an approval, through Node's ``InteractionService`` (P2.6c); ``scope="conversation"``
        allows it for the conversation (UX1)."""
        body = {"runId": run_id, "interactionId": interaction_id, "decision": decision}
        if scope is not None:
            body["scope"] = scope
        _ = (await self.http.post("/resolve", json=body)).raise_for_status()

    async def steer(self, run_id: str, text: str) -> None:
        """Queue ``text`` on the run's session: the kernel takes it as its next turn."""
        session = self.sessions.get(run_id, self.session_for(run_id))
        _ = (await self.http.post("/steer", json={"sessionId": session, "text": text})).raise_for_status()

    def config(self, run_id: str, *, failpoints_file: Path | None = None, skills: bool = True) -> WorkerConfig:
        stack = self.stack
        settings: dict[str, Any] = {"skills": skills, **self.workers.get(run_id, {})}
        return WorkerConfig(
            run_id=run_id,
            session_id=self.sessions.get(run_id, self.session_for(run_id)),
            org_id=self.org,
            node_url=self.outage.url,
            node_control=f"{self.node.base_url}/__control",
            credential=self.node.credential,
            host_ip=stack.host_ip,
            gateway_port=_free_port(stack.host_ip),
            control_url=self.control_url,
            control_token=stack.control_token,
            proxy=stack.proxy,
            namespace=self.namespace,
            prefix=self.prefix,
            objects_dir=str(self.workdir / "objects"),
            artifacts_path=str(self.workdir / "artifacts.pickle"),
            saas_url=self.saas_url,
            labels={**OWNER, "pipeshub.agent": "E1", "pxh.lane": stack.suffix},
            lease_ms=LEASE_MS,
            failpoints_file=str(failpoints_file) if failpoints_file is not None else None,
            **settings,
        )

    async def worker(self, run_id: str, *, arms: dict[str, Any] | None = None, **overrides: Any) -> Worker:  # noqa: ANN401
        """``overrides``: ``WorkerConfig`` fields for this worker only (P9: ``gateway_public_port``, ``claim_next``)."""
        armed = None
        if arms:
            armed = self.workdir / f"{run_id}-{secrets.token_hex(3)}.failpoints.json"
            armed.write_text(json.dumps(arms))
        cfg = self.config(run_id, failpoints_file=armed)
        if overrides:
            cfg = WorkerConfig(**{**cfg.__dict__, **overrides})
        worker = await spawn_worker(cfg, self.workdir)
        self.processes.append(worker.process)
        return worker

    async def stop_workers(self) -> None:
        """Kills every worker of this lane still running and waits for it."""
        for process in self.processes:
            if process.returncode is None:
                kill(process)
                with contextlib.suppress(Exception):
                    _ = await asyncio.wait_for(process.wait(), 30)

    async def run(self, run_id: str) -> dict[str, Any]:
        response = await self.http.get("/run", params={"runId": run_id})
        return cast("dict[str, Any]", response.raise_for_status().json())

    async def events(self, run_id: str) -> list[dict[str, Any]]:
        response = await self.http.get("/events", params={"runId": run_id})
        return cast("list[dict[str, Any]]", response.raise_for_status().json()["events"])

    async def until_claimable(self, run_id: str, timeout_s: float = LEASE_MS / 1000 + 10) -> dict[str, Any]:
        """Wait out a dead worker's lease (Node's clock follows the wall's), then let the sweeper requeue."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        while True:
            _ = (await self.http.post("/sweep")).raise_for_status()
            run = await self.run(run_id)
            if run["status"] != "running" or loop.time() > deadline:
                return run
            await asyncio.sleep(0.5)

    def artifacts(self, session: str) -> dict[str, tuple[bytes, ...]]:
        """Every artifact of ``session`` with its versions' bytes, as the lane's shared registry file holds them."""
        path = self.workdir / "artifacts.pickle"
        if not path.exists():
            return {}
        store = InMemoryArtifactStore()
        store._scopes = pickle.loads(path.read_bytes())  # pyright: ignore[reportPrivateUsage]
        binding = RunBinding(self.org, session, "snapshot")
        return {
            a.name: tuple(store._scopes[(self.org, session)][a.artifact_id].versions[v][0] for v in range(a.version))  # pyright: ignore[reportPrivateUsage]
            for a in store.listing(binding)
        }

    def sandboxes(self, run_id: str) -> list[Any]:
        """The run's live sandboxes: cold ones by their labels, warm-bound ones (P5.9a) through their binding marker."""
        client = self.stack.client
        lane = [f"runId={run_id}", f"pxh.lane={self.stack.suffix}"]
        found = cast("list[Any]", client.containers.list(filters={"label": lane}))
        markers = (client.api.volumes(filters={"label": [*lane, f"{self.namespace}.role=binding"]}) or {}).get("Volumes") or []
        for marker in markers:
            sandbox_id = marker["Labels"].get(f"{self.namespace}.id", "")
            found += client.containers.list(filters={"label": [f"{self.namespace}.id={sandbox_id}"]})
        return found

    def kill_sandboxes(self, run_id: str) -> int:
        killed = 0
        for container in self.sandboxes(run_id):
            with contextlib.suppress(Exception):
                container.kill()
                killed += 1
        return killed


async def _sync_clock(http: httpx.AsyncClient) -> None:
    now = int((await http.get("/now")).json()["now"])
    wall = int(time.time() * 1000)
    if wall > now:
        _ = await http.post("/advance", json={"seconds": (wall - now) / 1000})


async def _tick(http: httpx.AsyncClient) -> None:
    """Node's journal server keeps a manual clock; keep it on the wall's so leases and tokens lapse."""
    while True:
        with contextlib.suppress(httpx.HTTPError):
            await _sync_clock(http)
        await asyncio.sleep(0.25)


@contextlib.asynccontextmanager
async def durable_lane(
    workdir: Path,
    *,
    engine: str = "opencode",
    models: tuple[dict[str, Any], ...] = (),
    node_factory: Callable[[str], Any] | None = None,
    org: str = ORG,
    session_for: Callable[[str], str] | None = None,
    e3_forwarder: bool = False,
    egress_state: bool = False,
    e3_middlewares: tuple[Any, ...] = (),
    proxy_env: Callable[[Any], Mapping[str, str]] | None = None,
) -> AsyncIterator[DurableLane]:
    """The proxy's model upstream is the real model gateway (E3, E3b's Responses route for Codex) as
    ``app.model_gateway_main`` wires it: Node's grants and published keys, A1's guarded resolver over
    the org's configured model (the scripted model, an OpenAI-compatible provider) with the shipped
    signed vendor rules.

    P9: ``node_factory`` starts Node (given the host address; default the in-memory journal server), ``org`` and
    ``session_for`` name what runs are admitted to, and ``e3_forwarder`` puts a forwarder between the proxy and
    the model gateway (``lane.e3_outage``). P5.4b: ``egress_state`` keeps the proxy's bindings in a Redis of the
    lane's own, so a restarted proxy keeps serving. ``e3_middlewares``: aiohttp middlewares in front of the model
    gateway's routes (the long-task rig records each request an engine sent)."""
    started: dict[str, Any] = {}

    def upstreams(stack: Any) -> dict[str, str]:  # noqa: ANN401 - RealStack
        server = started["node"] = (node_factory or (lambda host: _NodeServer(host=host)))(stack.host_ip)
        started["e3"] = _free_port(stack.host_ip)
        upstream = started["e3"]
        if e3_forwarder:
            upstream = started["e3_front"] = _free_port(stack.host_ip)
        return {
            "NODE_URL": server.base_url,
            "WORKER_CREDENTIAL": server.proxy_credential,
            "EGRESS_UPSTREAMS": json.dumps({"model-gateway": f"http://{stack.host_ip}:{upstream}"}),
            # WP100-FU-7: what a rig host on the upstream bridge needs of the proxy (``EGRESS_TEST_HOSTS``, its CA).
            **(proxy_env(stack) if proxy_env is not None else {}),
        }

    try:
        stack = start_stack(upstreams=upstreams, egress_state=egress_state)
    except BaseException:
        if "node" in started:
            started["node"].stop()
        raise
    node = started["node"]
    namespace = f"io.pipeshub.g4.{stack.suffix}"
    prefix = f"pxh-g4-{stack.suffix}"
    model = ConformanceGateway(endpoints=EngineControllerConfig(images={}).endpoints("x" * 16, "y" * 16), grant=())
    _ = stack.models.register(model, STATIC_MODEL_KEY)
    saas = Saas()
    host, port = node.base_url.removeprefix("http://").rsplit(":", 1)
    outage = NodeOutage(stack.host_ip, (host, int(port)))
    runners: list[web.AppRunner] = []
    e3_outage: NodeOutage | None = None
    http = httpx.AsyncClient(base_url=f"{node.base_url}/__control", timeout=30)
    ticker: asyncio.Task[None] | None = None
    lane: DurableLane | None = None
    try:
        for app, host_ip, at in ((stack.models.app(), stack.host_ip, stack.model_port), (saas.app(), "127.0.0.1", 0)):
            runner = web.AppRunner(app, access_log=None, handler_cancellation=True)
            await runner.setup()
            await web.TCPSite(runner, host_ip, at, reuse_address=True).start()
            runners.append(runner)
        saas_url = f"http://127.0.0.1:{runners[1].addresses[0][1]}"
        await outage.start()
        _ = (await http.post("/reset")).raise_for_status()
        ledger = InMemoryBudgetLedger(SystemClock())
        config = GatewayConfig(
            {
                "llm": [
                    {
                        "provider": "openAICompatible",
                        "modelKey": CONFIGURED_MODEL["modelKey"],
                        "isDefault": True,
                        "configuration": {
                            "model": CONFIGURED_MODEL["modelName"],
                            "apiKey": "g4-org-compatible-key-not-real",
                            "endpoint": f"{stack.model_base}/m/{STATIC_MODEL_KEY}/v1",
                        },
                    },
                    # More of the org's models a run may pin (EV2's live lane: the Azure entry).
                    *({**m, "isDefault": False} for m in models),
                ]
            },
            (await http.get("/gateway-public-keys")).json(),
        )
        runners.append(await _serve_e3(stack, node, ledger, config, started["e3"], e3_middlewares))
        if e3_forwarder:
            e3_outage = NodeOutage(stack.host_ip, (stack.host_ip, started["e3"]), started["e3_front"])
            await e3_outage.start()
        await _sync_clock(http)
        ticker = asyncio.ensure_future(_tick(http))
        workdir.mkdir(parents=True, exist_ok=True)
        lane = DurableLane(
            stack, node, outage, saas, saas_url, model, workdir, namespace, prefix, http, stack.control_url,
            engine, ledger, config, org=org, session_for=session_for or (lambda run_id: f"ses-{run_id}"),
            e3_outage=e3_outage,
        )
        yield lane
    finally:
        try:
            if lane is not None:
                await lane.stop_workers()
            if e3_outage is not None:
                await e3_outage.stop()
            if ticker is not None:
                _ = ticker.cancel()
                with contextlib.suppress(BaseException):
                    await ticker
            await outage.stop()
            for runner in runners:
                await runner.cleanup()
            await http.aclose()
        finally:
            # Whatever stopping the servers raised, the namespace's sandboxes and networks go (SBXLEAK).
            provider = DockerClientProvider(max_workers=4)
            try:
                for remove, arg in ((cleanup, prefix), (cleanup_networks, stack.proxy)):
                    try:
                        await provider.run_blocking(remove, provider, namespace, arg)
                    except Exception as exc:
                        warnings.warn(f"the G4 lane {namespace} left Docker objects: {exc!r}", stacklevel=1)
            finally:
                provider.close()
                stop_stack(stack)
                node.control.close()
                node.stop()


async def _serve_e3(
    stack: Any, node: Any, ledger: InMemoryBudgetLedger, config: GatewayConfig, port: int,  # noqa: ANN401
    middlewares: tuple[Any, ...] = (),
) -> web.AppRunner:
    """``create_gateway``'s wiring with its guard (A1), Node's grants and keys; the budget ledger is
    P6.5's reference in process (the journal test server's ledger routes serve a separate budget world)."""
    clock = SystemClock()
    gateway = ModelGateway(
        verifier=OrgBoundGatewayTokenVerifier(ConfiguredGatewayKeys(config), clock),  # type: ignore[arg-type]
        grants=NodeRunModelGrants(base_url=node.base_url),
        resolver=guarded_access(config, ConfiguredOrgAccessPolicies(config)),  # type: ignore[arg-type]
        budget=ledger,
        clock=clock,
        ids=RandomIds(),
    )
    app = create_model_gateway_app(gateway)
    app.middlewares.extend(middlewares)
    runner = web.AppRunner(app, access_log=None, handler_cancellation=True)
    await runner.setup()
    await web.TCPSite(runner, stack.host_ip, port, reuse_address=True).start()
    return runner


__all__ = [
    "CREATE_ID",
    "LEASE_MS",
    "LOOKUP_ID",
    "ORG",
    "DirectoryObjectStore",
    "DurableLane",
    "FileArtifactStore",
    "NodeOutage",
    "RigAssembler",
    "Saas",
    "Worker",
    "WorkerConfig",
    "durable_lane",
    "engine_spec",
    "kill",
    "run_attempt",
    "spawn_worker",
]
