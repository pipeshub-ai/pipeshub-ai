"""CT-1..CT-16 (E0), CT-19 (E8): ``EngineController`` on the fake broker, the fake egress proxy and ``FakeEngine``.

Everything is in memory: the workspace store and objects, the sync, skill
bundles, the artifact publisher, the run's ``RunToolEndpoint`` (mounted on the
in-memory tool-gateway host), and the scripted model of the conformance
gateway behind the real egress proxy core.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, override

import pytest

from harness_core.access.types import AccessRouteRef, ModelApi
from harness_core.artifacts.memory import InMemoryArtifactStore, InMemoryPublicationLog
from harness_core.artifacts.publisher import WorkspaceArtifactPublisher
from harness_core.engines.checkpoints import ObjectStoreEngineCheckpoints
from harness_core.engines.controller import (
    BUDGET_EXHAUSTED,
    OUTPUT_ACTIVE_CONTENT,
    OUTPUT_SYMLINK,
    RESUME_NOTE,
    EngineController,
    EngineControllerConfig,
    EngineRunContext,
    deliverables,
    placeholder_for,
)
from harness_core.engines.durable.fold import checkpoint_data, checkpoint_of
from harness_core.engines.errors import (
    CompactUnsupported,
    EgressUnavailable,
    EngineImageNotConfigured,
    EngineVersionNotAllowed,
    GrantTooLarge,
    ModelApiUnavailable,
    SkillNotAllowed,
)
from harness_core.engines.events import (
    ApprovalRequested,
    EngineError,
    EngineEventMeta,
    EngineState,
    EngineStateBlob,
    FileChanged,
    TurnCompleted,
    turn_id_for,
)
from harness_core.engines.memory import InMemoryEngineEventSink
from harness_core.engines.policy import model_api_for
from harness_core.engines.registry import AllowlistEngineRegistry
from harness_core.engines.types import (
    EngineCapabilities,
    EngineLaunch,
    EngineMode,
    EngineProfile,
    EngineTask,
    FollowUp,
    RunLimits,
    SessionHealth,
    ToolCeiling,
)
from harness_core.sandbox.errors import IsolationUnavailable
from harness_core.sandbox.types import RuntimeMode
from harness_core.skills.builder import StoredSkillBundleBuilder
from harness_core.skills.materializer import TarSkillMaterializer
from harness_core.skills.memory import InMemorySkillContentSource
from harness_core.skills.types import SkillBundleUnavailable, SkillPin, SkillRef
from harness_core.testing.clock import ManualClock
from harness_core.testing.contracts.tool_endpoint import (
    BINDING,
    GRANTED,
    build_endpoint,
)
from harness_core.testing.engines.fake_engine import (
    FAKE_VERSION,
    FakeEngineAdapter,
    FakeEngineSession,
)
from harness_core.testing.engines.gateway import ConformanceGateway, GrantedTool
from harness_core.testing.engines.loop import ToolOutcome
from harness_core.testing.engines.tool_host import InMemoryToolGatewayHost
from harness_core.testing.ids import SeededIds
from harness_core.testing.sandbox.egress import AttemptTokenSource, FakeEgressProxy
from harness_core.testing.sandbox.fake import FakeSandboxBroker, FakeSandboxHandle
from harness_core.workspace.changes import ChangeSet
from harness_core.workspace.memory import (
    InMemoryBinaryObjectStore,
    InMemoryWorkspaceStore,
    InMemoryWorkspaceWriters,
)
from harness_core.workspace.sync.saver import Saved
from harness_core.workspace.sync.strategies import SyncStrategies
from harness_core.workspace.sync.tar_sync import TarWorkspaceSync
from harness_core.workspace.sync.types import (
    ENGINE_CONFIG,
    FILE_TOO_LARGE,
    HARD_LINK,
    SPECIAL_FILE,
    SYMLINK_ESCAPES,
    Collected,
    SyncState,
)
from harness_core.workspace.types import (
    SYMLINK_MODE,
    ObjectRef,
    ObjectScope,
    WorkspaceFile,
    WorkspaceStoreUnavailable,
    empty_manifest,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Mapping

    from harness_core.context.user_compact import CompactReport
    from harness_core.engines.events import EngineEvent
    from harness_core.engines.ports import EngineRegistry, SandboxSnapshotStore
    from harness_core.engines.types import EngineKind, EngineVersion, GatewayEndpoints
    from harness_core.interactions.ports import InteractionDecision
    from harness_core.sandbox import SandboxHandle
    from harness_core.sandbox.types import LeaseRef, SandboxSpec
    from harness_core.workspace.ports import WorkspaceWriters

ORG, SESSION, RUN = BINDING.tenant, BINDING.scope, BINDING.run_id
SCOPE = ObjectScope(ORG, f"ws:{SESSION}")
KEY = b"k" * 32
IMAGE = "fake-engine-image"
SECRET_PROMPT = "PROMPT-CONTENT-MARKER-58c1"


class RecordingBroker(FakeSandboxBroker):
    """The fake broker, recording each acquire's spec and key, and the files a sandbox held at engine start."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.acquired: list[tuple[SandboxSpec, str]] = []
        self.released: list[str] = []

    async def acquire(self, spec: SandboxSpec, *, lease: LeaseRef, idempotency_key: str):  # noqa: ANN201
        self.acquired.append((spec, idempotency_key))
        return await super().acquire(spec, lease=lease, idempotency_key=idempotency_key)

    async def release(self, handle: SandboxHandle, *, keep_warm_s: int = 0) -> None:
        self.released.append(handle.id)
        await super().release(handle, keep_warm_s=keep_warm_s)


class WatchingAdapter(FakeEngineAdapter):
    """``FakeEngine``, keeping a copy of the sandbox's files and the restored state at ``open``."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.files_at_open: list[dict[str, bytes]] = []
        self.restored: list[EngineState | None] = []

    async def open(self, sandbox, launch, restore, *, correlation=None):  # type: ignore[no-untyped-def] # noqa: ANN201
        self.files_at_open.append(dict(sandbox.files))
        self.restored.append(restore)
        return await super().open(sandbox, launch, restore, correlation=correlation)


@dataclass
class World:
    clock: ManualClock = field(default_factory=ManualClock)
    objects: InMemoryBinaryObjectStore = field(default_factory=InMemoryBinaryObjectStore)
    sink: InMemoryEngineEventSink = field(default_factory=InMemoryEngineEventSink)
    host: InMemoryToolGatewayHost = field(default_factory=InMemoryToolGatewayHost)
    skills: InMemorySkillContentSource = field(default_factory=InMemorySkillContentSource)
    artifacts: InMemoryArtifactStore = field(default_factory=InMemoryArtifactStore)
    publications: InMemoryPublicationLog = field(default_factory=InMemoryPublicationLog)
    adapter: FakeEngineAdapter = field(default_factory=WatchingAdapter)
    modes: frozenset[RuntimeMode] = frozenset({RuntimeMode.DOCKER_HARDENED, RuntimeMode.GVISOR})
    images: dict[EngineVersion, str] = field(default_factory=lambda: {FAKE_VERSION: IMAGE})
    config_overrides: dict[str, object] = field(default_factory=dict[str, object])
    # The workspace's writer lease (P6.4); unset, an in-memory one on the world's clock.
    writers: WorkspaceWriters | None = None
    versions: tuple[EngineVersion, ...] = (FAKE_VERSION,)
    # A registry other than one over ``adapter`` and ``versions`` (CC1: the release registry).
    registry: EngineRegistry | None = None
    # Where sandbox snapshots go (P5.9b); taken only with a ``snapshot`` policy in ``config_overrides``.
    snapshot_store: SandboxSnapshotStore | None = None
    # The workspace store; unset, an in-memory one on the world's clock.
    store: InMemoryWorkspaceStore | None = None
    # The turn ticker's sleep; unset, a short real sleep while time moves only on the clock.
    sleep: Callable[[float], Awaitable[None]] | None = None
    # Files each image carries (image -> absolute path -> content), e.g. the /pipeshub contract's VERSION.
    image_files: dict[str, dict[str, bytes]] = field(default_factory=dict[str, dict[str, bytes]])

    def __post_init__(self) -> None:
        if self.store is None:
            self.store = InMemoryWorkspaceStore(clock=self.clock)
        self.gateway = ConformanceGateway(
            endpoints=EngineControllerConfig(images={}).endpoints("unused-0123456789", "unused-0123456789"),
            grant=[GrantedTool(t.name) for t in GRANTED],
        )
        # Tokens only for the attempt a binding names, as Node issues them (SIGNFIX).
        self.tokens = AttemptTokenSource(b"world-tokens", self.clock)
        self.proxy = FakeEgressProxy(
            clock=self.clock,
            gateways={"model-gateway": self.gateway._model},  # pyright: ignore[reportPrivateUsage]
            tokens=self.tokens,
        )
        self.proxy.route_upstream(self.host.base, self.host.handle)
        self.broker = RecordingBroker(
            clock=self.clock, ids=SeededIds(), modes=self.modes, egress_proxy=self.proxy, image_files=self.image_files
        )
        self.builder = StoredSkillBundleBuilder(source=self.skills, objects=self.objects, workspaces=self.store)
        self.config = EngineControllerConfig(images=self.images, tick_s=0.001, **self.config_overrides)  # type: ignore[arg-type]
        self.controller = self.make_controller()
        self.endpoint_world = build_endpoint()

    def make_controller(self, key: bytes = KEY) -> EngineController:
        return EngineController(
            registry=self.registry or AllowlistEngineRegistry([self.adapter], self.versions),
            broker=self.broker,
            workspaces=self.store,
            sync_for=lambda scope: TarWorkspaceSync(objects=self.objects, scope=scope, clock=self.clock),
            strategies=SyncStrategies(),
            skill_bundles=self.builder,
            skill_materializer=TarSkillMaterializer(objects=self.objects, workspaces=self.store),
            checkpoints=ObjectStoreEngineCheckpoints(self.objects),
            sink=self.sink,
            tool_host=self.host,
            clock=self.clock,
            placeholder_key=key,
            config=self.config,
            writers=self.writers or InMemoryWorkspaceWriters(clock=self.clock),
            sleep=self.sleep or (lambda _s: asyncio.sleep(0.001)),
            snapshots=self.snapshot_store,
        )

    def publisher(self) -> WorkspaceArtifactPublisher:
        return WorkspaceArtifactPublisher(
            self.artifacts, self.publications, self.objects, object_scope=lambda b: ObjectScope(b.tenant, f"ws:{b.scope}")
        )

    def run(self, attempt: int = 1, **changes: object) -> EngineRunContext:
        context = EngineRunContext(
            tenant=ORG,
            session_id=SESSION,
            run_id=RUN,
            attempt_id=f"{RUN}.a{attempt}",
            fence=attempt,
            lease_holder="worker-1",
            profile=EngineProfile(
                engine=FAKE_VERSION,
                mode=EngineMode.BUILD,
                access=AccessRouteRef("route-1", "d1"),
                isolation_min=RuntimeMode.DOCKER_HARDENED,
            ),
            tools=self.endpoint_world.endpoint,
            tool_binding=BINDING,
            grant_size=len(GRANTED),
            artifacts=self.publisher(),
            labels={"pipeshub.agent": "E0"},
        )
        return dataclasses.replace(context, **changes)  # type: ignore[arg-type]

    async def seed(self, files: Mapping[str, bytes]) -> None:
        entries = [WorkspaceFile(path, await self.objects.put(SCOPE, data)) for path, data in files.items()]
        await self.store.commit(
            ObjectScope(ORG), f"ws:{SESSION}", base_revision=0, changes=ChangeSet.of(*entries), reason="seed"
        )

    async def head_files(self) -> dict[str, bytes]:
        head = await self.store.head(ObjectScope(ORG), f"ws:{SESSION}")
        return {p: await self.objects.get(SCOPE, e.content) for p, e in head.files.items()}


class Ticks:
    """The turn ticker's sleep on the world's ``ManualClock``, so a test runs it one iteration at a time."""

    def __init__(self, clock: ManualClock) -> None:
        self.clock = clock
        self._parked: asyncio.Queue[None] = asyncio.Queue()

    async def sleep(self, delay_s: float) -> None:
        self._parked.put_nowait(None)
        await self.clock.sleep(delay_s)

    async def parked(self) -> None:
        """Until the ticker waits for its next tick: every iteration before it has finished."""
        await asyncio.wait_for(self._parked.get(), 5)

    async def advance(self, seconds: float) -> None:
        """Moves the clock and lets exactly one iteration run to its end."""
        _ = self.clock.advance(seconds)
        await self.parked()


class FlakyWorkspaceStore(InMemoryWorkspaceStore):
    """The workspace store, unreachable for its next ``failures`` commits (Node restarting)."""

    def __init__(self, *, clock: ManualClock, failures: int) -> None:
        super().__init__(clock=clock)
        self.failures = failures

    @override
    async def commit(self, *args: Any, **kwargs: Any) -> Any:
        if self.failures > 0:
            self.failures -= 1
            raise WorkspaceStoreUnavailable("workspace store unreachable")
        return await super().commit(*args, **kwargs)


def task(index: int, prompt: str, instructions: str = "") -> EngineTask:
    return EngineTask(turn_id=turn_id_for(RUN, index), prompt=prompt, instructions=instructions)


@pytest.fixture
def world() -> World:
    return World()


async def test_ct1_happy_path_spec_and_outcome(world: World) -> None:
    outcome = await world.controller.run_turn(world.run(), task(0, f"{SECRET_PROMPT}\nsay done"))
    assert (outcome.reason, outcome.final_text, outcome.cancelled) == ("completed", "done", False)
    [(spec, key)] = world.broker.acquired
    assert key == f"{RUN}.a1"
    assert spec.image_digest == IMAGE
    assert spec.mode is RuntimeMode.GVISOR
    assert [(m.target, m.read_only, m.source) for m in spec.mounts] == [
        ("/workspace", False, None),
        ("/state", False, None),
        ("/skills", True, None),
        ("/pxh-sealed", True, None),
    ]
    assert {k: spec.labels[k] for k in ("orgId", "sessionId", "runId", "attemptId")} == {
        "orgId": ORG,
        "sessionId": SESSION,
        "runId": RUN,
        "attemptId": f"{RUN}.a1",
    }
    destinations = {(d.host, d.ports) for d in spec.egress.destinations}
    assert destinations == {("model-gateway", (8097,)), ("tool-gateway", (8097,))}
    tools = next(d for d in spec.egress.destinations if d.host == "tool-gateway")
    assert tools.upstream is not None and tools.upstream.startswith(world.host.base + "/")
    routes = {r.destination: r.placeholder for r in spec.egress.token_routes}
    assert set(routes) == {"model-gateway", "tool-gateway"}
    assert len(set(routes.values())) == 2 and all(len(p) >= 16 for p in routes.values())
    assert len(world.broker.released) == 1
    assert world.host.mounted == 0
    assert world.broker.sandboxes == {}


async def test_ct2_the_head_is_in_the_sandbox_before_the_engine_starts_without_engine_config(world: World) -> None:
    await world.seed({"data/q3.csv": b"a,b\n", ".opencode/plugin/x.ts": b"evil()"})
    _ = await world.controller.run_turn(world.run(), task(0, "say ok"))
    assert isinstance(world.adapter, WatchingAdapter)
    [files] = world.adapter.files_at_open
    assert files["/workspace/data/q3.csv"] == b"a,b\n"
    assert not any(".opencode" in path for path in files)


async def test_ct3_pinned_skills_are_seeded_before_the_engine_starts(world: World) -> None:
    world.skills.publish(ORG, "report", {"SKILL.md": b"---\nname: report\ndescription: d\n---\nbody\n"})
    bundle = await world.builder.build(SCOPE, SkillRef("report"))
    run = world.run(skills=(SkillPin("report", bundle.digest),))
    _ = await world.controller.run_turn(run, task(0, "say ok"))
    assert isinstance(world.adapter, WatchingAdapter)
    [files] = world.adapter.files_at_open
    assert files["/skills/report/SKILL.md"].endswith(b"body\n")


async def test_ct3_an_unresolvable_skill_is_refused_before_anything_is_acquired(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    run = world.run(skills=(SkillPin("ghost", "sha256:" + "9" * 64),))
    with caplog.at_level(logging.WARNING), pytest.raises(SkillBundleUnavailable):
        await world.controller.run_turn(run, task(0, "say ok"))
    assert world.broker.acquired == [] and world.host.mounted == 0
    refusals = [r for r in caplog.records if getattr(r, "harness_event", "") == "engine.run.refused"]
    assert [r.harness["reasonCode"] for r in refusals] == ["skill_not_found"]  # type: ignore[attr-defined]


async def test_ct4_events_reach_the_sink_in_order_with_the_attempt_independent_turn_id(world: World) -> None:
    _ = await world.controller.run_turn(world.run(), task(0, 'tool lookup {"q": "x"}\nwrite notes.md hi\nsay ok'))
    events = world.sink.of_run(RUN)
    assert [e.meta.seq for e in events] == list(range(len(events)))
    assert {e.meta.turn_id for e in events} == {turn_id_for(RUN, 0)}
    assert isinstance(events[-1], TurnCompleted)


async def test_ct5_twenty_file_changes_autosave(world: World) -> None:
    writes = "\n".join(f"write f{i:02d}.txt {i}" for i in range(20))
    _ = await world.controller.run_turn(world.run(), task(0, f"{writes}\nsay ok"))
    assert [s.reason for s in world.sink.saves] == ["autosave"]
    assert len(world.sink.saves[0].manifest.files) == 20


async def test_ct5_a_gateway_tool_call_is_preceded_by_a_save(world: World) -> None:
    _ = await world.controller.run_turn(world.run(), task(0, 'write a.md x\ntool lookup {"q": "y"}\nsay ok'))
    assert [s.reason for s in world.sink.saves] == ["before_effect"]
    assert "a.md" in world.sink.saves[0].manifest.files
    assert world.endpoint_world.runs == ["lookup"]


async def test_ct5_two_minutes_without_a_save_autosave_on_the_injected_clock(world: World) -> None:
    runner = asyncio.ensure_future(world.controller.run_turn(world.run(), task(0, "write a.md x\nhold\nsay ok")))
    await asyncio.wait_for(world.gateway.holding.wait(), 5)
    assert world.sink.saves == []
    world.clock.advance(121)
    for _ in range(500):
        if world.sink.saves:
            break
        await asyncio.sleep(0.002)
    world.gateway.release()
    outcome = await asyncio.wait_for(runner, 5)
    assert outcome.reason == "completed"
    assert [s.reason for s in world.sink.saves] == ["autosave"]


async def test_ct5_turn_end_saves_what_is_left(world: World) -> None:
    outcome = await world.controller.run_turn(world.run(), task(0, "write notes.md hi\nsay ok"))
    assert [s.reason for s in world.sink.saves] == ["turn_end"]
    assert outcome.workspace is not None and outcome.workspace.revision == 1
    assert (await world.head_files()) == {"notes.md": b"hi"}


async def test_ct6_outputs_are_drafts_during_the_turn_and_final_on_success(world: World) -> None:
    prompt = 'write out/report.md # Q3\ntool lookup {"q": "z"}\nwrite out/page.html <p>x</p>\nwrite scratch.txt s\nsay ok'
    outcome = await world.controller.run_turn(world.run(), task(0, prompt))
    assert [(p.path, p.lifecycle) for p in outcome.published] == [("report.md", "draft")]
    assert [(r.path, r.reason_code) for r in outcome.refused_outputs] == [("page.html", OUTPUT_ACTIVE_CONTENT)]
    history = await world.publications.history(world.run().binding)
    assert [c.lifecycle for c in history.cards] == ["draft", "final"]
    assert all(c.name == "report.md" for c in history.cards)


async def _bash(sandbox: FakeSandboxHandle, workdir: str, command: str) -> ToolOutcome:
    """The engine's own shell: ``bash -c`` over the sandbox's files, what it wrote put back."""
    prefix = workdir.rstrip("/") + "/"
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for path, data in sandbox.files.items():
            if path.startswith(prefix):
                (root / path[len(prefix) :]).parent.mkdir(parents=True, exist_ok=True)
                (root / path[len(prefix) :]).write_bytes(data)
        proc = await asyncio.to_thread(
            subprocess.run, ["bash", "-c", command.replace("python3 ", f"{sys.executable} ")],
            cwd=root, capture_output=True, text=True, check=False,
        )
        for file in root.rglob("*"):
            if file.is_file():
                sandbox.files[prefix + str(file.relative_to(root))] = file.read_bytes()
    return ToolOutcome(proc.stdout + proc.stderr, is_error=proc.returncode != 0)


async def test_emptyout_an_output_written_after_a_tool_call_is_the_deliverable_with_the_runs_output() -> None:
    """EMPTYOUT: the output file exists (empty) when a gateway call saves the workspace and publishes it as a draft;
    the engine's own shell then runs the script into it. The artifact the turn leaves is the run's output."""
    world = World(adapter=WatchingAdapter(shell=_bash))
    prompt = (
        'write primes.py print(*[n for n in range(2, 30) if all(n % d for d in range(2, n))], sep="\\n")\n'
        "shell mkdir -p out && : > out/first_ten_primes_output.txt\n"
        'tool lookup {"q": "z"}\n'
        "shell python3 primes.py > out/first_ten_primes_output.txt\n"
        "say ok"
    )
    outcome = await world.controller.run_turn(world.run(), task(0, prompt))
    assert outcome.reason == "completed"
    assert [s.reason for s in world.sink.saves] == ["before_effect", "turn_end"]
    binding = world.run().binding
    [artifact] = world.artifacts.listing(binding)
    assert (artifact.name, artifact.lifecycle) == ("first_ten_primes_output.txt", "final")
    content = await world.artifacts.content(binding, artifact.artifact_id, artifact.version)
    assert content == b"2\n3\n5\n7\n11\n13\n17\n19\n23\n29\n"


def test_ct6_deliverables_skip_symlinks_and_files_outside_out() -> None:
    ref = ObjectRef("o1", "sha256:" + "a" * 64, 3)
    upserts = {
        "out/a.csv": WorkspaceFile("out/a.csv", ref, "text/csv"),
        "out/link": WorkspaceFile("out/link", ref, mode=SYMLINK_MODE),
        "out/x.svg": WorkspaceFile("out/x.svg", ref, "image/svg+xml"),
        "out/y.HTM": WorkspaceFile("out/y.HTM", ref),
        "notes.md": WorkspaceFile("notes.md", ref),
        "outside/out/b.txt": WorkspaceFile("outside/out/b.txt", ref),
        "outside/out/c": WorkspaceFile("outside/out/c", ref, mode=SYMLINK_MODE),
    }
    state = SyncState("sbx", "/workspace", empty_manifest("ws:s"))
    saved = Saved(state, Collected(ChangeSet(upserts)), None)
    outputs, refused = deliverables(saved, "out")
    assert [(o.path, o.mime_type) for o in outputs] == [("a.csv", "text/csv")]
    assert sorted((r.path, r.reason_code) for r in refused) == [
        ("link", OUTPUT_SYMLINK),
        ("x.svg", OUTPUT_ACTIVE_CONTENT),
        ("y.HTM", OUTPUT_ACTIVE_CONTENT),
    ]


# RO-1..RO-3: what the sync left out under out/ is refused with the sync's reason; out itself is "."


@pytest.mark.parametrize(
    ("upserts", "skipped", "expected"),
    [
        ({}, {"out/passwd.txt": SYMLINK_ESCAPES}, [("passwd.txt", SYMLINK_ESCAPES)]),
        ({}, {"out/big.bin": FILE_TOO_LARGE}, [("big.bin", FILE_TOO_LARGE)]),
        ({}, {"out/hard.txt": HARD_LINK}, [("hard.txt", HARD_LINK)]),
        ({}, {"out/pipe": SPECIAL_FILE}, [("pipe", SPECIAL_FILE)]),
        ({}, {"out/.opencode/x.json": ENGINE_CONFIG}, [(".opencode/x.json", ENGINE_CONFIG)]),
        ({}, {"out": SYMLINK_ESCAPES}, [(".", SYMLINK_ESCAPES)]),
        ({"out": SYMLINK_MODE}, {}, [(".", OUTPUT_SYMLINK)]),
        ({"out/sub/in.csv": SYMLINK_MODE}, {}, [("sub/in.csv", OUTPUT_SYMLINK)]),
        ({"out": 0o100644}, {"passwd": SYMLINK_ESCAPES, "outside/out/x": FILE_TOO_LARGE, "outx": SPECIAL_FILE}, []),
    ],
)
def test_ro_1_3_deliverables_refuse_links_and_skipped_entries_under_out(
    upserts: dict[str, int], skipped: dict[str, str], expected: list[tuple[str, str]]
) -> None:
    ref = ObjectRef("o1", "sha256:" + "a" * 64, 3)
    files = {path: WorkspaceFile(path, ref, mode=mode) for path, mode in upserts.items()}
    state = SyncState("sbx", "/workspace", empty_manifest("ws:s"))
    saved = Saved(state, Collected(ChangeSet(files), skipped), None)
    outputs, refused = deliverables(saved, "out")
    assert outputs == []
    assert [(r.path, r.reason_code) for r in refused] == expected


LINKS = {"out/passwd.txt": "/etc/passwd", "out/input-link.csv": "../notes.md", "elsewhere": "/etc"}


class LinkingAdapter(WatchingAdapter):
    """``FakeEngine`` in a sandbox whose workspace already holds links (the fake engine writes regular files only)."""

    def __init__(self, links: Mapping[str, str] = LINKS) -> None:
        super().__init__()
        self.links = links

    @override
    async def open(
        self,
        sandbox: SandboxHandle,
        launch: EngineLaunch,
        restore: EngineState | None,
        *,
        correlation: Mapping[str, str] | None = None,
    ) -> FakeEngineSession:
        assert isinstance(sandbox, FakeSandboxHandle)
        for path, target in self.links.items():
            sandbox.put_symlink(f"/workspace/{path}", target)
        return await super().open(sandbox, launch, restore, correlation=correlation)


# RO-4..RO-6


async def test_ro_4_6_the_turn_records_each_refused_output_once(caplog: pytest.LogCaptureFixture) -> None:
    world = World(adapter=LinkingAdapter(), config_overrides={"autosave_changes": 1})
    prompt = "write out/a.md one\nwrite out/b.md two\nsay ok"
    with caplog.at_level(logging.WARNING):
        outcome = await world.controller.run_turn(world.run(), task(0, prompt))
    assert outcome.reason == "completed"
    collects = [r for r in caplog.records if getattr(r, "harness_event", "") == "workspace_sync.skipped"]
    assert len(collects) >= 2, "the escaping link must recur across saves for this case to mean anything"
    assert sorted((r.path, r.reason_code) for r in outcome.refused_outputs) == [
        ("input-link.csv", OUTPUT_SYMLINK),
        ("passwd.txt", SYMLINK_ESCAPES),
    ]
    assert sorted(p.path for p in outcome.published) == ["a.md", "b.md"]
    lines = [r for r in caplog.records if getattr(r, "harness_event", "") == "engine.output.refused"]
    assert sorted(r.harness["reasonCode"] for r in lines) == [OUTPUT_SYMLINK, SYMLINK_ESCAPES]  # type: ignore[attr-defined]
    for line in lines:
        fields: dict[str, object] = line.harness  # type: ignore[attr-defined]
        assert fields["runId"] == RUN
        shown = f"{line.getMessage()} {fields}"
        assert "passwd" not in shown and "input-link" not in shown


@pytest.mark.parametrize(
    ("modes", "ran_on"),
    [
        (frozenset({RuntimeMode.DOCKER_HARDENED, RuntimeMode.GVISOR}), RuntimeMode.GVISOR),
        (frozenset({RuntimeMode.DOCKER_HARDENED}), RuntimeMode.DOCKER_HARDENED),
    ],
    ids=["gvisor-offered", "runc-only"],
)
async def test_gvs8_the_checkpoint_records_the_runtime_the_attempt_ran_on(
    modes: frozenset[RuntimeMode], ran_on: RuntimeMode, caplog: pytest.LogCaptureFixture
) -> None:
    """R31-GVISO: the run says where it ran (the strongest offered at or above its floor), not what config says;
    the journal's copy is sealed, so the worker's log line says it too."""
    caplog.set_level(logging.INFO)
    world = World(modes=modes)
    outcome = await world.controller.run_turn(world.run(), task(0, "say alpha"))
    [line] = [r for r in caplog.records if getattr(r, "harness_event", "") == "engine.checkpointed"]
    assert line.harness["isolation"] == ran_on.value  # type: ignore[attr-defined]
    [(spec, _)] = world.broker.acquired
    assert spec.mode is ran_on
    assert outcome.checkpoint is not None and outcome.checkpoint.isolation is ran_on
    data = checkpoint_data(outcome.checkpoint, "trn_0123456789abcdef0123456789abcdef")
    assert data["isolation"] == ran_on.value
    assert checkpoint_of(data) == outcome.checkpoint


async def test_ro_4_a_save_with_only_skipped_entries_still_records_them() -> None:
    world = World(adapter=LinkingAdapter({"out": "/etc", "elsewhere": "/etc"}))
    outcome = await world.controller.run_turn(world.run(), task(0, "say ok"))
    assert world.sink.saves == []
    assert [(r.path, r.reason_code) for r in outcome.refused_outputs] == [(".", SYMLINK_ESCAPES)]
    assert outcome.published == ()


async def test_ct7_the_state_is_checkpointed_in_the_session_scope_without_placeholders(world: World) -> None:
    outcome = await world.controller.run_turn(world.run(), task(0, "say alpha"))
    assert outcome.checkpoint is not None
    assert [c.blob for c in world.sink.checkpoints] == [outcome.checkpoint]
    payload = await world.objects.get(SCOPE, outcome.checkpoint.content)
    for route in ("gateway:model", "gateway:tools"):
        assert placeholder_for(KEY, f"{RUN}.a1", route).encode() not in payload


async def test_ct8_the_next_turn_restores_the_checkpoint(world: World) -> None:
    first = await world.controller.run_turn(world.run(), task(0, "say alpha\nRESUME-MARKER-1"))
    second = await world.controller.run_turn(
        world.run(2, restore=first.checkpoint), FollowUp(turn_id_for(RUN, 1), "say beta")
    )
    assert (second.reason, second.restored, second.restore_issue) == ("completed", "state", None)
    history = world.gateway.model_calls[-1].messages
    assert any(m.role == "user" and "RESUME-MARKER-1" in m.content for m in history)


@pytest.mark.parametrize("damage", ["corrupt", "missing", "foreign_version"])
async def test_ct9_unusable_state_resumes_from_the_workspace_flagged(
    world: World, damage: str, caplog: pytest.LogCaptureFixture
) -> None:
    first = await world.controller.run_turn(world.run(), task(0, "write a.md x\nsay alpha"))
    assert first.checkpoint is not None
    blob = first.checkpoint
    stored = world.objects._objects  # pyright: ignore[reportPrivateUsage]
    key = (ORG, blob.content.object_id)
    if damage == "corrupt":
        stored[key] = stored[key][:-1] + b"!"
    elif damage == "missing":
        del stored[key]
    else:
        other = dataclasses.replace(FAKE_VERSION, version="0.0.1-other")
        blob = EngineStateBlob(other, blob.schema_version, blob.content)
    with caplog.at_level(logging.WARNING):
        second = await world.controller.run_turn(
            world.run(2, restore=blob, resume_summary="Wrote a.md."), FollowUp(turn_id_for(RUN, 1), "say beta")
        )
    expected = {"corrupt": "state_corrupt", "missing": "state_missing", "foreign_version": "state_incompatible"}[damage]
    assert (second.reason, second.restored, second.restore_issue) == ("completed", "workspace", expected)
    system = world.gateway.model_calls[-1].system
    assert system.count(RESUME_NOTE) == 1 and "Wrote a.md." in system
    unusable = [r for r in caplog.records if getattr(r, "harness_event", "") == "engine.state.unusable"]
    assert len(unusable) == 1


async def test_ct10_cancel_interrupts_saves_discards_drafts_and_releases(world: World) -> None:
    run = world.run()
    runner = asyncio.ensure_future(world.controller.run_turn(run, task(0, "write out/draft.md x\nhold\nsay never")))
    await asyncio.wait_for(world.gateway.holding.wait(), 5)
    assert await world.controller.cancel(run, reason="user")
    outcome = await asyncio.wait_for(runner, 5)
    assert (outcome.reason, outcome.cancelled, outcome.checkpoint) == ("interrupted", True, None)
    assert [s.reason for s in world.sink.saves] == ["turn_end"]
    history = await world.publications.history(run.binding)
    assert [c.lifecycle for c in history.cards] == ["draft", "discarded"]
    assert await world.artifacts.find_by_name(run.binding, "draft.md") is None
    assert world.broker.sandboxes == {} and world.host.mounted == 0
    assert not await world.controller.cancel(run, reason="again")


@pytest.mark.parametrize(
    ("limits", "prompt", "limit"),
    [
        (RunLimits(max_tokens=20), "write a x\nwrite b y\nwrite c z\nsay done", "tokens"),
        (RunLimits(max_wall_s=60), "hold\nsay never", "wall_time"),
    ],
)
async def test_ct11_limits_stop_the_turn(world: World, limits: RunLimits, prompt: str, limit: str) -> None:
    run = world.run()
    run = dataclasses.replace(run, profile=dataclasses.replace(run.profile, limits=limits))
    runner = asyncio.ensure_future(world.controller.run_turn(run, task(0, prompt)))
    if limit == "wall_time":
        await asyncio.wait_for(world.gateway.holding.wait(), 5)
        world.clock.advance(61)
    outcome = await asyncio.wait_for(runner, 5)
    assert (outcome.reason, outcome.limit) == ("limit", limit)
    assert outcome.checkpoint is not None


async def test_fx3_3_a_failed_autosave_is_logged_and_the_wall_limit_still_stops_the_turn(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = ManualClock()
    ticks = Ticks(clock)
    world = World(clock=clock, store=FlakyWorkspaceStore(clock=clock, failures=1), sleep=ticks.sleep)
    run = world.run()
    run = dataclasses.replace(run, profile=dataclasses.replace(run.profile, limits=RunLimits(max_wall_s=600)))
    runner = asyncio.ensure_future(world.controller.run_turn(run, task(0, "write a.md x\nhold\nsay never")))
    await asyncio.wait_for(world.gateway.holding.wait(), 5)
    await ticks.parked()
    with caplog.at_level(logging.WARNING):
        await ticks.advance(world.config.autosave_interval_s)
        assert [r.harness["errorType"] for r in caplog.records if getattr(r, "harness_event", "") == "engine.turn.autosave_failed"] == [  # type: ignore[attr-defined]
            "WorkspaceStoreUnavailable"
        ]
        assert world.sink.saves == []
        await ticks.advance(world.config.autosave_interval_s)
        assert [s.reason for s in world.sink.saves] == ["autosave"]
        _ = clock.advance(600)
        outcome = await asyncio.wait_for(runner, 5)
    assert (outcome.reason, outcome.limit) == ("limit", "wall_time")


async def test_ct11_a_step_ceiling_ends_the_turn_as_a_limit(world: World) -> None:
    run = world.run()
    run = dataclasses.replace(run, profile=dataclasses.replace(run.profile, limits=RunLimits(max_steps=1)))
    outcome = await world.controller.run_turn(run, task(0, "write a x\nwrite b y\nsay done"))
    assert outcome.reason == "limit"


async def _refused(world: World, run: EngineRunContext, error: type[Exception], caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING), pytest.raises(error):
        await world.controller.run_turn(run, task(0, "say ok"))
    assert world.broker.acquired == [] and world.host.mounted == 0
    warned = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warned) == 1


async def test_ct12_a_version_off_the_allowlist(world: World, caplog: pytest.LogCaptureFixture) -> None:
    run = world.run()
    other = dataclasses.replace(FAKE_VERSION, binary_digest="sha256:" + "e" * 64)
    await _refused(world, dataclasses.replace(run, profile=dataclasses.replace(run.profile, engine=other)), EngineVersionNotAllowed, caplog)


async def test_ct12_isolation_the_broker_lacks_is_refused_never_downgraded(world: World, caplog: pytest.LogCaptureFixture) -> None:
    run = world.run()
    strict = dataclasses.replace(run.profile, isolation_min=RuntimeMode.MICROVM)
    await _refused(world, dataclasses.replace(run, profile=strict), IsolationUnavailable, caplog)


async def test_ct12_a_grant_over_the_engine_limit(caplog: pytest.LogCaptureFixture) -> None:
    world = World(adapter=FakeEngineAdapter(max_tools=3))
    await _refused(world, world.run(grant_size=4), GrantTooLarge, caplog)


async def test_ct12_an_engine_without_an_image(caplog: pytest.LogCaptureFixture) -> None:
    world = World(images={})
    await _refused(world, world.run(), EngineImageNotConfigured, caplog)


def _broker_reports(world: World, monkeypatch: pytest.MonkeyPatch, unavailable: str | None) -> None:
    from harness_core.sandbox.types import ENGINES_UNAVAILABLE, BrokerHealth

    real = world.broker.health

    async def health() -> BrokerHealth:
        seen = await real()
        detail = dict(seen.detail) if unavailable is None else {**seen.detail, ENGINES_UNAVAILABLE: unavailable}
        return BrokerHealth(ok=seen.ok, modes=seen.modes, active=seen.active, detail=detail)

    monkeypatch.setattr(world.broker, "health", health)


async def test_h1_ei2_an_engine_whose_image_the_broker_lacks_is_refused_before_a_sandbox(
    world: World, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from harness_core.engines.errors import EngineImageUnavailable

    _broker_reports(world, monkeypatch, f"codex@0.160.0,{FAKE_VERSION.ref}")
    await _refused(world, world.run(), EngineImageUnavailable, caplog)
    assert "reasonCode=engine_unavailable" in caplog.text


@pytest.mark.parametrize("unavailable", [None, "", "codex@0.160.0"])
async def test_h1_ei3_a_broker_that_lists_no_such_engine_runs_the_turn(
    world: World, monkeypatch: pytest.MonkeyPatch, unavailable: str | None
) -> None:
    _broker_reports(world, monkeypatch, unavailable)
    outcome = await world.controller.run_turn(world.run(), task(0, "say ok"))
    assert outcome.reason == "completed" and len(world.broker.acquired) == 1


async def test_ep15_a_grant_over_the_profile_tool_ceiling(world: World, caplog: pytest.LogCaptureFixture) -> None:
    run = world.run()
    capped = dataclasses.replace(run.profile, tools=ToolCeiling(max_tools=run.grant_size - 1))
    await _refused(world, dataclasses.replace(run, profile=capped), GrantTooLarge, caplog)


async def test_ep15_a_skill_outside_the_profile(world: World, caplog: pytest.LogCaptureFixture) -> None:
    run = world.run(skills=(SkillPin("report", "sha256:" + "9" * 64),))
    narrowed = dataclasses.replace(run.profile, skills=frozenset({"pdf"}))
    await _refused(world, dataclasses.replace(run, profile=narrowed), SkillNotAllowed, caplog)
    assert "reasonCode=skill_not_allowed" in caplog.text


async def test_ep15_egress_beyond_the_gateways_is_refused(world: World, caplog: pytest.LogCaptureFixture) -> None:
    run = world.run()
    wider = dataclasses.replace(run.profile, egress="allowlist:pypi")
    await _refused(world, dataclasses.replace(run, profile=wider), EgressUnavailable, caplog)


class _StubSession:
    def __init__(self, events: list[EngineEvent], *, wait_for_interrupt: bool) -> None:
        self._events = events
        self._wait = wait_for_interrupt
        self.interrupted = asyncio.Event()
        self.answers: list[tuple[str, InteractionDecision]] = []

    async def run_turn(self, task: EngineTask | FollowUp) -> AsyncIterator[EngineEvent]:
        for event in self._events:
            yield event
        if self._wait:
            await self.interrupted.wait()
            yield TurnCompleted(EngineEventMeta(task.turn_id, len(self._events)), "interrupted")
        else:
            yield TurnCompleted(EngineEventMeta(task.turn_id, len(self._events)), "completed")

    async def steer(self, text: str) -> None: ...

    async def interrupt(self) -> None:
        self.interrupted.set()

    async def compact(self, focus: str | None, *, instructions: str = "") -> CompactReport:
        raise CompactUnsupported("this test engine does not compact")

    async def answer(self, request_id: str, decision: InteractionDecision) -> None:
        self.answers.append((request_id, decision))

    async def capture_state(self) -> EngineState:
        return EngineState(FAKE_VERSION, 1, b"{}")

    async def health(self) -> SessionHealth:
        return SessionHealth(alive=True)

    async def close(self) -> None: ...


class _StubAdapter:
    kind: ClassVar[EngineKind] = "jcode"

    def __init__(self, session: _StubSession | None, *, fail: bool = False) -> None:
        self.session = session
        self._fail = fail

    def capabilities(self) -> EngineCapabilities:
        return FakeEngineAdapter().capabilities()

    def render_launch(self, profile: EngineProfile, endpoints: GatewayEndpoints, skills_dir: str) -> EngineLaunch:
        return EngineLaunch(engine=profile.engine, argv=("stub",), env={})

    async def open(self, sandbox, launch, restore, *, correlation=None) -> _StubSession:  # type: ignore[no-untyped-def]
        if self._fail or self.session is None:
            raise RuntimeError("the engine would not start")
        return self.session


def _meta(seq: int) -> EngineEventMeta:
    return EngineEventMeta(turn_id_for(RUN, 0), seq)


@pytest.mark.parametrize("code", [BUDGET_EXHAUSTED, "budget_insufficient"])
async def test_ct11_the_model_gateway_budget_refusal_stops_the_turn(code: str) -> None:
    session = _StubSession([FileChanged(_meta(0), "c1", "a.md"), EngineError(_meta(1), code, "budget")], wait_for_interrupt=True)
    world = World(adapter=_StubAdapter(session))  # type: ignore[arg-type]
    outcome = await world.controller.run_turn(world.run(), task(0, "x"))
    assert (outcome.reason, outcome.limit) == ("limit", "budget")
    assert session.interrupted.is_set()


async def test_ct13_a_failure_while_driving_still_releases(world: World) -> None:
    world = World(adapter=_StubAdapter(None, fail=True))  # type: ignore[arg-type]
    with pytest.raises(RuntimeError):
        await world.controller.run_turn(world.run(), task(0, "x"))
    assert len(world.broker.acquired) == 1 and len(world.broker.released) == 1
    assert world.broker.sandboxes == {} and world.host.mounted == 0


async def test_ct14_every_line_carries_the_run_and_none_carries_content(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG, logger="harness_core.engines.controller"):
        _ = await world.controller.run_turn(world.run(), task(0, f"{SECRET_PROMPT}\nwrite out/r.md SECRET-FILE\nsay ok"))
    lines = [r for r in caplog.records if r.name == "harness_core.engines.controller"]
    assert lines
    for record in lines:
        fields = record.harness  # type: ignore[attr-defined]
        assert {k: fields[k] for k in ("orgId", "sessionId", "runId", "attemptId")} == {
            "orgId": ORG, "sessionId": SESSION, "runId": RUN, "attemptId": f"{RUN}.a1"
        }
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert SECRET_PROMPT not in text and "SECRET-FILE" not in text
    for route in ("gateway:model", "gateway:tools"):
        assert placeholder_for(KEY, f"{RUN}.a1", route) not in text


async def test_ct15_an_engine_s_own_approval_ask_is_denied() -> None:
    ask = ApprovalRequested(_meta(0), "per_1", "bash", ("rm -rf",))
    session = _StubSession([ask], wait_for_interrupt=False)
    world = World(adapter=_StubAdapter(session))  # type: ignore[arg-type]
    outcome = await world.controller.run_turn(world.run(), task(0, "x"))
    assert outcome.reason == "completed"
    assert session.answers == [("per_1", "denied")]


async def test_ct16_same_key_same_attempt_same_spec() -> None:
    first, second = World(), World()
    _ = await first.controller.run_turn(first.run(), task(0, "say ok"))
    _ = await second.controller.run_turn(second.run(), task(0, "say ok"))
    assert first.broker.acquired == second.broker.acquired
    third = World()
    third.controller = third.make_controller(b"z" * 32)
    _ = await third.controller.run_turn(third.run(), task(0, "say ok"))
    assert third.broker.acquired[0][0].egress != first.broker.acquired[0][0].egress


def test_ct19_each_engine_gets_the_first_model_api_the_gateway_serves_that_it_speaks() -> None:
    """E8: the gateway serves chat completions (E3) and Responses (E3b) at one address; Codex speaks only
    Responses, opencode only chat completions."""
    served = EngineControllerConfig(images={}).model_apis
    chat = EngineCapabilities(False, True, True, frozenset({ModelApi.CHAT_COMPLETIONS}), None, "port")
    responses = dataclasses.replace(chat, model_apis=frozenset({ModelApi.RESPONSES}))
    both = dataclasses.replace(chat, model_apis=frozenset({ModelApi.RESPONSES, ModelApi.CHAT_COMPLETIONS}))
    assert model_api_for(chat, served) is ModelApi.CHAT_COMPLETIONS
    assert model_api_for(responses, served) is ModelApi.RESPONSES
    assert model_api_for(both, served) is served[0]
    # E3c: the gateway serves Anthropic Messages too (Claude Code, E9).
    messages = dataclasses.replace(chat, model_apis=frozenset({ModelApi.ANTHROPIC_MESSAGES}))
    assert model_api_for(messages, served) is ModelApi.ANTHROPIC_MESSAGES
    with pytest.raises(ModelApiUnavailable):
        model_api_for(responses, (ModelApi.CHAT_COMPLETIONS,))


async def test_ct19_an_engine_the_gateway_cannot_serve_is_refused_before_acquiring(
    caplog: pytest.LogCaptureFixture,
) -> None:
    world = World(config_overrides={"model_apis": (ModelApi.RESPONSES,)})
    await _refused(world, world.run(), ModelApiUnavailable, caplog)


class TaskRecordingAdapter(WatchingAdapter):
    """``FakeEngine``, keeping each task and steer exactly as the controller handed it to the session."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.handed: list[EngineTask | FollowUp | str] = []

    async def open(self, sandbox, launch, restore, *, correlation=None):  # type: ignore[no-untyped-def] # noqa: ANN201
        session = await super().open(sandbox, launch, restore, correlation=correlation)
        handed = self.handed
        run_turn, steer = session.run_turn, session.steer

        def recording_turn(task: EngineTask | FollowUp) -> AsyncIterator[EngineEvent]:
            handed.append(task)
            return run_turn(task)

        async def recording_steer(text: str) -> None:
            handed.append(text)
            await steer(text)

        session.run_turn = recording_turn  # type: ignore[method-assign]
        session.steer = recording_steer  # type: ignore[method-assign]
        return session


@pytest.mark.parametrize("typed", ["/compact", "/clear", "/model x", "/config k=v", "/report", "  /compact"])
async def test_slash0_a_leading_slash_reaches_every_adapter_as_text(typed: str) -> None:
    adapter = TaskRecordingAdapter()
    world = World(adapter=adapter)
    outcome = await world.controller.run_turn(world.run(), task(0, f"{typed}\nsay done"))
    assert outcome.final_text == "done"
    [handed] = adapter.handed
    assert isinstance(handed, EngineTask)
    assert handed.prompt == f"⁠{typed}\nsay done"
    follow = FollowUp(turn_id_for(RUN, 1), f"{typed}\nsay again")
    _ = await world.controller.run_turn(world.run(), follow)
    assert isinstance(adapter.handed[-1], FollowUp)
    assert adapter.handed[-1].text == f"⁠{typed}\nsay again"


async def test_slash0_ordinary_text_reaches_the_adapter_byte_for_byte() -> None:
    adapter = TaskRecordingAdapter()
    world = World(adapter=adapter)
    sent = task(0, "say done\nA path /etc/hosts and 1/2")
    _ = await world.controller.run_turn(world.run(), sent)
    assert adapter.handed == [sent]
    assert adapter.handed[0] is sent
