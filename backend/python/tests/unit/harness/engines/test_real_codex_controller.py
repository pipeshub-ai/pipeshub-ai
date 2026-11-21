"""RX-C7, RX-C8 (E8): ``EngineController`` with Codex on its real binary, end to end.

E0's controller lane (``test_real_controller.py``) with the release registry's Codex adapter and E3's real
model gateway as the deployment's model route (the proxy's static upstream, as in production): the
controller picks the Responses API for Codex from its capabilities, and the gateway's real
``OpenAITransport`` calls the scripted model as the org's provider.
"""

from __future__ import annotations

import dataclasses
import json
import sys
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from app.harness.gateway.host import HttpToolGatewayHost
from harness_core.access.types import (
    AccessRouteRef,
    PinnedRoute,
    RunBudget,
    route_digest,
)
from harness_core.artifacts.memory import InMemoryArtifactStore, InMemoryPublicationLog
from harness_core.artifacts.publisher import WorkspaceArtifactPublisher
from harness_core.engines.checkpoints import ObjectStoreEngineCheckpoints
from harness_core.engines.controller import (
    EngineController,
    EngineControllerConfig,
    EngineRunContext,
)
from harness_core.engines.events import turn_id_for
from harness_core.engines.memory import InMemoryEngineEventSink
from harness_core.engines.types import EngineTask, FollowUp
from harness_core.interactions.ports import RunBinding
from harness_core.sandbox.types import Resources
from harness_core.skills.builder import StoredSkillBundleBuilder
from harness_core.skills.materializer import TarSkillMaterializer
from harness_core.skills.memory import InMemorySkillContentSource
from harness_core.skills.types import SkillPin, SkillRef
from harness_core.testing.clock import ManualClock
from harness_core.testing.engines.endpoint_gateway import EndpointConformanceGateway
from harness_core.testing.engines.gateway import GrantedTool
from harness_core.workspace.changes import ChangeSet
from harness_core.workspace.memory import (
    InMemoryBinaryObjectStore,
    InMemoryKeyProvider,
    InMemoryWorkspaceStore,
    InMemoryWorkspaceWriters,
)
from harness_core.workspace.sealing import SealedObjectStore
from harness_core.workspace.sync.strategies import SyncStrategies
from harness_core.workspace.sync.tar_sync import TarWorkspaceSync
from harness_core.workspace.types import ObjectScope, WorkspaceFile
from harness_engines.pipeshub import sdk as sdk_module
from harness_engines.release import (
    release_default_profile,
    release_registry,
)
from tests.unit.harness.engines.lane_releases import lane_release, lane_releases
from tests.unit.harness.engines.ph_e2e import (
    INPUTS_PROMPT,
    INPUTS_SEED,
    REPOSITORY,
    SDK_PROMPT,
    SESSION_PH_PROMPT,
    assert_c312_commands_worked,
    assert_input_staged_on_first_read,
    assert_ph_called_the_tool,
    assert_session_ph_found_the_repository_tool,
    assert_stubs_called_the_tool,
    run_c312_turn,
    with_input_staging,
    with_tool_folder,
)
from tests.unit.harness.engines.ph_e2e import PROMPT as PH_PROMPT
from tests.unit.harness.engines.real_stack import (
    OWNER,
    ModelGatewayLane,
    _free_port,  # pyright: ignore[reportPrivateUsage]
    engine_broker,
    lane_available,
    start_stack,
    stop_stack,
)
from tests.unit.harness.sandbox_broker.conftest import cleanup, cleanup_networks

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator, Mapping

    from app.harness.sandbox.docker_broker import DockerSandboxBroker
    from harness_core.engines.types import EngineVersion
    from tests.unit.harness.engines.real_stack import RealStack

pytestmark = [pytest.mark.docker, pytest.mark.timeout(900, method="thread")]

IMAGE: str = lane_release("codex").image
TENANT, SESSION = "tenant", "scope"
GRANT = (GrantedTool("search", "Search the knowledge base."),)
SKILL_MD = b"---\nname: report\ndescription: Use when asked for a report.\n---\nPut reports under out/.\n"
MODEL_KEY = "codex-run"
# The model gateway's port on the upstream bridge, chosen when the module's stack starts.
_E3_PORT: dict[str, int] = {}


def _pinned() -> EngineVersion:
    return lane_release("codex").version


@pytest.fixture(scope="module")
def real_stack() -> Iterator[RealStack]:
    """The conftest's stack, with E3's gateway as the proxy's static model upstream."""
    available, reason = lane_available(IMAGE)
    if not available:
        pytest.skip(f"{reason} (deployment/sandbox-images/codex/build.sh)")

    def upstreams(stack: RealStack) -> Mapping[str, str]:
        _E3_PORT["port"] = _free_port(stack.host_ip)
        return {"EGRESS_UPSTREAMS": json.dumps({"model-gateway": f"http://{stack.host_ip}:{_E3_PORT['port']}"})}

    stack = start_stack(upstreams=upstreams)
    yield stack
    stop_stack(stack)


@pytest.fixture
async def model_gateway(stack: RealStack) -> AsyncIterator[ModelGatewayLane]:
    async with ModelGatewayLane(stack, _E3_PORT["port"]).serving() as lane:
        yield lane


@pytest.fixture
async def codex_broker(stack: RealStack) -> AsyncIterator[DockerSandboxBroker]:
    namespace = f"io.pipeshub.e8ctl.{uuid.uuid4().hex[:8]}"
    prefix = "pxh-e8c-" + namespace.rsplit(".", 1)[1]
    created, provider = await engine_broker(stack, namespace, prefix, image=IMAGE)
    yield created
    await provider.run_blocking(cleanup, provider, namespace, prefix)
    await provider.run_blocking(cleanup_networks, provider, namespace, stack.proxy)
    provider.close()


@dataclass
class Lane:
    stack: RealStack
    broker: DockerSandboxBroker
    models: ModelGatewayLane
    run_id: str = field(default_factory=lambda: f"run-{uuid.uuid4().hex[:8]}")
    budget: RunBudget | None = None
    clock: ManualClock = field(default_factory=ManualClock)
    raw: InMemoryBinaryObjectStore = field(default_factory=InMemoryBinaryObjectStore)
    keys: InMemoryKeyProvider = field(default_factory=InMemoryKeyProvider)
    sink: InMemoryEngineEventSink = field(default_factory=InMemoryEngineEventSink)
    skills: InMemorySkillContentSource = field(default_factory=InMemorySkillContentSource)
    artifacts: InMemoryArtifactStore = field(default_factory=InMemoryArtifactStore)
    publications: InMemoryPublicationLog = field(default_factory=InMemoryPublicationLog)
    gateways: dict[int, EndpointConformanceGateway] = field(default_factory=dict[int, EndpointConformanceGateway])
    # The engine under test (E9 runs the same lane on Claude Code).
    version: EngineVersion = field(default_factory=_pinned)
    image: str = IMAGE
    agent: str = "E8"

    def __post_init__(self) -> None:
        self.route = PinnedRoute("openai", f"scripted-{self.run_id}", config_model="gpt-4o")
        self.objects = SealedObjectStore(self.raw, self.keys)
        self.store = InMemoryWorkspaceStore(clock=self.clock)
        self.builder = StoredSkillBundleBuilder(source=self.skills, objects=self.objects, workspaces=self.store)
        self.controller = EngineController(
            registry=release_registry(releases=lane_releases()),
            broker=self.broker,
            workspaces=self.store,
            sync_for=lambda scope: TarWorkspaceSync(objects=self.objects, scope=scope, clock=self.clock),
            strategies=SyncStrategies(),
            skill_bundles=self.builder,
            skill_materializer=TarSkillMaterializer(objects=self.objects, workspaces=self.store),
            checkpoints=ObjectStoreEngineCheckpoints(self.objects),
            sink=self.sink,
            tool_host=HttpToolGatewayHost(self.stack.gateway.app, base_url=self.stack.gateway_base),
            clock=self.clock,
            placeholder_key=b"real-lane-placeholder-key-0123456789",
            writers=InMemoryWorkspaceWriters(clock=self.clock),
            config=EngineControllerConfig(
                images={self.version: self.image}, resources=Resources(cpus=1.0, memory_mb=1024, pids=256, scratch_mb=256)
            ),
        )

    @property
    def scope(self) -> ObjectScope:
        return ObjectScope(TENANT, f"ws:{SESSION}")

    def gateway(self, attempt: int) -> EndpointConformanceGateway:
        """The run's tool endpoint for one attempt; one scripted model (one brain) answers the whole run."""
        if attempt not in self.gateways:
            gateway = EndpointConformanceGateway(
                endpoints=EngineControllerConfig(images={}).endpoints("x" * 16, "y" * 16),
                grant=GRANT,
                binding=RunBinding(TENANT, SESSION, self.run_id),
                attempt=attempt,
                routes_digest=route_digest((self.route,)),
            )
            if self.gateways:
                first = next(iter(self.gateways.values()))
                gateway.brain = first.brain
                gateway.model_calls = first.model_calls
                self.stack.models.gateways[MODEL_KEY] = gateway
            else:
                self.models.pin(gateway, self.route, budget=self.budget, key=MODEL_KEY)
            self.gateways[attempt] = gateway
            self.stack.models.bind(self.run_id, f"{self.run_id}.a{attempt}", gateway.claims.binding)
        return self.gateways[attempt]

    def run(self, attempt: int, **changes: object) -> EngineRunContext:
        gateway = self.gateway(attempt)
        context = EngineRunContext(
            tenant=TENANT,
            session_id=SESSION,
            run_id=self.run_id,
            attempt_id=f"{self.run_id}.a{attempt}",
            fence=attempt,
            lease_holder="worker-real",
            profile=release_default_profile(self.version, AccessRouteRef("route-1", "d1")),
            tools=gateway.endpoint,
            tool_binding=gateway.claims.binding,
            grant_size=len(GRANT),
            artifacts=WorkspaceArtifactPublisher(
                self.artifacts, self.publications, self.objects, object_scope=lambda b: ObjectScope(b.tenant, f"ws:{b.scope}")
            ),
            labels={**OWNER, "pipeshub.agent": self.agent, "pxh.lane": self.stack.suffix},
        )
        return dataclasses.replace(context, **changes)  # type: ignore[arg-type]

    async def seed(self, files: Mapping[str, bytes]) -> None:
        entries = [WorkspaceFile(path, await self.objects.put(self.scope, data)) for path, data in files.items()]
        await self.store.commit(ObjectScope(TENANT), f"ws:{SESSION}", base_revision=0, changes=ChangeSet.of(*entries), reason="seed")

    async def head(self) -> dict[str, bytes]:
        head = await self.store.head(ObjectScope(TENANT), f"ws:{SESSION}")
        return {p: await self.objects.get(self.scope, e.content) for p, e in head.files.items()}

    def task(self, index: int, prompt: str) -> EngineTask:
        return EngineTask(turn_id=turn_id_for(self.run_id, index), prompt=prompt)


async def test_rxc7_a_codex_turn_end_to_end_then_the_next_turn_restores_it(
    stack: RealStack, codex_broker: DockerSandboxBroker, model_gateway: ModelGatewayLane
) -> None:
    lane = Lane(stack, codex_broker, model_gateway)
    await lane.seed({"data/in.csv": b"a,b\n1,2\n"})
    lane.skills.publish(TENANT, "report", {"SKILL.md": SKILL_MD})
    bundle = await lane.builder.build(lane.scope, SkillRef("report"))
    run = lane.run(1, skills=(SkillPin("report", bundle.digest),))
    prompt = (
        "RXC7-MARKER\nshell mkdir -p out && cp data/in.csv out/report.csv && cat /skills/report/SKILL.md > notes.md\n"
        'tool search {"q": "churn"}\nsay done'
    )
    first = await lane.controller.run_turn(run, lane.task(0, prompt))
    assert (first.reason, first.final_text.strip()) == ("completed", "done")
    head = await lane.head()
    assert head["out/report.csv"] == b"a,b\n1,2\n"
    assert head["notes.md"] == SKILL_MD
    assert [(p.path, p.lifecycle) for p in first.published] == [("report.csv", "draft")]
    history = await lane.publications.history(run.binding)
    assert [c.lifecycle for c in history.cards] == ["draft", "final"]
    assert "before_effect" in [s.reason for s in lane.sink.saves]
    assert first.checkpoint is not None and lane.sink.checkpoints[-1].blob == first.checkpoint
    # The controller gave Codex the Responses API; every model call went through E3b, none refused.
    assert {(r.method, r.path) for r in model_gateway.requests} == {("POST", "/v1/responses")}
    assert model_gateway.refused == []
    second = await lane.controller.run_turn(
        lane.run(2, restore=first.checkpoint), FollowUp(turn_id_for(lane.run_id, 1), "say again")
    )
    assert (second.reason, second.restored, second.restore_issue) == ("completed", "state", None)
    last = lane.gateways[1].model_calls[-1]
    assert any("RXC7-MARKER" in m.content for m in last.messages if m.role == "user")


async def test_rxc8_the_model_gateways_budget_refusal_ends_the_turn_as_a_limit(
    stack: RealStack, codex_broker: DockerSandboxBroker, model_gateway: ModelGatewayLane
) -> None:
    lane = Lane(stack, codex_broker, model_gateway, budget=RunBudget(max_cost_usd=None, max_tokens=1))
    outcome = await lane.controller.run_turn(lane.run(1), lane.task(0, 'tool search {"q": 1}\nsay never'))
    assert (outcome.reason, outcome.limit) == ("limit", "budget")


async def test_emptyout_codex_runs_the_script_it_wrote_in_its_own_shell_and_the_output_is_the_deliverable(
    stack: RealStack, codex_broker: DockerSandboxBroker, model_gateway: ModelGatewayLane
) -> None:
    """EMPTYOUT on the real binary and the release run image: the output file exists empty when a gateway call
    publishes it as a draft, then ``python3`` in Codex's own shell fills it; the final artifact is the output."""
    lane = Lane(stack, codex_broker, model_gateway)
    program = "print(*[n for n in range(2, 30) if all(n % d for d in range(2, n))], sep=chr(10))"
    prompt = (
        f"shell mkdir -p out && printf '%s\\n' '{program}' > primes.py && : > out/first_ten_primes_output.txt\n"
        'tool search {"q": "primes"}\n'
        "shell python3 primes.py > out/first_ten_primes_output.txt\n"
        "say done"
    )
    run = lane.run(1)
    outcome = await lane.controller.run_turn(run, lane.task(0, prompt))
    assert outcome.reason == "completed"
    primes = b"2\n3\n5\n7\n11\n13\n17\n19\n23\n29\n"
    assert (await lane.head())["out/first_ten_primes_output.txt"] == primes
    [artifact] = lane.artifacts.listing(run.binding)
    assert (artifact.name, artifact.lifecycle) == ("first_ten_primes_output.txt", "final")
    assert await lane.artifacts.content(run.binding, artifact.artifact_id, artifact.version) == primes


async def test_lcfix_a_command_codex_left_running_is_stopped_and_the_answer_says_so(
    stack: RealStack, codex_broker: DockerSandboxBroker, model_gateway: ModelGatewayLane
) -> None:
    """LCFIX on the real binary: Codex's ``exec_command`` yields after about 10 s while ``sleep 40`` runs; the turn
    ends, the command is stopped before the sandbox goes, and the answer says one was cut off."""
    from harness_core.engines.background import stopped_note

    lane = Lane(stack, codex_broker, model_gateway)
    outcome = await lane.controller.run_turn(lane.run(1), lane.task(0, "shell sleep 40\nsay done"))
    assert outcome.reason == "completed"
    assert outcome.final_text.strip().endswith(stopped_note(1)), outcome.final_text


async def test_ph_e2e_codex_runs_ph_from_its_shell_through_the_real_gateway(
    stack: RealStack, codex_broker: DockerSandboxBroker, model_gateway: ModelGatewayLane
) -> None:
    """R3 (``ph_e2e``): ``ph call`` in Codex's own shell (which drops ``*TOKEN*`` variables: ``ph`` reads the
    placeholder from the session folder), through the egress proxy, to the run's tool."""
    lane = Lane(stack, codex_broker, model_gateway)
    with_tool_folder(lane.controller)
    outcome = await lane.controller.run_turn(lane.run(1), lane.task(0, PH_PROMPT))
    assert_ph_called_the_tool(outcome, await lane.head(), lane.gateways[1])


async def test_ws14_sdk_e2e_codex_calls_the_tool_through_the_python_and_typescript_stubs(
    stack: RealStack, codex_broker: DockerSandboxBroker, model_gateway: ModelGatewayLane
) -> None:
    """WS-14 (C3.15, C3.16): from Codex's shell, which drops ``*TOKEN*`` variables, the stubs reach the tool as
    ``ph`` does (the placeholder from the session folder) and answer what ``ph tools call`` answers."""
    lane = Lane(stack, codex_broker, model_gateway)
    with_tool_folder(lane.controller)
    outcome = await lane.controller.run_turn(lane.run(1), lane.task(0, SDK_PROMPT))
    assert_stubs_called_the_tool(outcome, await lane.head(), lane.gateways[1])


async def test_ph_e2e_c312_codex_runs_ph_skill_publish_and_ask_through_the_real_gateway(
    stack: RealStack, codex_broker: DockerSandboxBroker, model_gateway: ModelGatewayLane
) -> None:
    """C3.12 (``ph_e2e``): ``ph skill``, ``ph publish`` and ``ph ask`` from Codex's shell, through the egress proxy and
    the real tool gateway, as their tool forms are."""
    lane = Lane(stack, codex_broker, model_gateway)
    run, outcome, head = await run_c312_turn(lane, TENANT, SKILL_MD, lane.task)
    await assert_c312_commands_worked(lane, run, outcome, head, SKILL_MD)


async def test_j26_c5_01_codex_places_a_large_input_on_its_first_read(
    stack: RealStack, codex_broker: DockerSandboxBroker, model_gateway: ModelGatewayLane
) -> None:
    """J-26 (C5.01): Codex starts without the large input and places it from its shell with the session's
    ``ph inputs get``, through the egress proxy and the real gateway, to the controller."""
    lane = Lane(stack, codex_broker, model_gateway)
    with_tool_folder(lane.controller)
    with_input_staging(lane.controller)
    await lane.seed(INPUTS_SEED)
    outcome = await lane.controller.run_turn(lane.run(1), lane.task(0, INPUTS_PROMPT))
    assert_input_staged_on_first_read(outcome, await lane.head())


async def test_ws14_c5_01_codex_stubs_rendered_on_first_import_answer_as_ph(
    stack: RealStack, codex_broker: DockerSandboxBroker, model_gateway: ModelGatewayLane,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WS-14 + C5.01: a grant past the eager threshold gets stubs rendered on first import in Codex's image."""
    monkeypatch.setattr(sdk_module, "SDK_EAGER_MAX_TOOLS", 0)
    lane = Lane(stack, codex_broker, model_gateway)
    with_tool_folder(lane.controller)
    outcome = await lane.controller.run_turn(lane.run(1), lane.task(0, SDK_PROMPT))
    assert_stubs_called_the_tool(outcome, await lane.head(), lane.gateways[1])


async def test_wp70_sessionph_codex_finds_the_repository_tool_with_the_session_ph(
    stack: RealStack, codex_broker: DockerSandboxBroker, model_gateway: ModelGatewayLane, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WP70-SESSIONPH (Codex, whose shell is ``bash -lc``): the session folder's ph comes first."""
    monkeypatch.setattr(sys.modules[__name__], "GRANT", (*GRANT, REPOSITORY))
    lane = Lane(stack, codex_broker, model_gateway)
    with_tool_folder(lane.controller)
    outcome = await lane.controller.run_turn(lane.run(1), lane.task(0, SESSION_PH_PROMPT))
    assert_session_ph_found_the_repository_tool(outcome, await lane.head())
