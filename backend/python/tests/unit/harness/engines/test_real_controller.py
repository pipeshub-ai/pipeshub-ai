"""RX-3..RX-6 (E0): ``EngineController`` with opencode on its real binary, end to end.

The same real lane as the conformance suite (``real_stack.py``), with the
controller's production wiring: ``HttpToolGatewayHost`` over ``ToolGatewayApp``,
``TarWorkspaceSync`` and ``ObjectStoreEngineCheckpoints`` over objects sealed with
the session key, the release registry and default profile. The model gateway is
the proxy's static upstream (configuration, as E3 is in production).
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import os
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from app.harness.gateway.host import HttpToolGatewayHost
from harness_core.access.types import AccessRouteRef
from harness_core.artifacts.memory import InMemoryArtifactStore, InMemoryPublicationLog
from harness_core.artifacts.publisher import WorkspaceArtifactPublisher
from harness_core.engines.checkpoints import ObjectStoreEngineCheckpoints
from harness_core.engines.controller import (
    RESUME_NOTE,
    EngineController,
    EngineControllerConfig,
    EngineRunContext,
    EngineTurnOutcome,
)
from harness_core.engines.durable.inputs import StoreInputSource
from harness_core.engines.events import CommandStarted, EngineStateBlob, turn_id_for
from harness_core.engines.memory import InMemoryEngineEventSink
from harness_core.engines.types import EngineTask, FollowUp
from harness_core.interactions.ports import RunBinding
from harness_core.journal.types import InputArtifactRef
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
    HOSTILE_PROMPT,
    INPUTS_PROMPT,
    INPUTS_SEED,
    J26_PROMPT,
    J26_SKILL,
    REPOSITORY,
    SDK_PROMPT,
    SESSION_PH_PROMPT,
    assert_c312_commands_worked,
    assert_hostile_names_refused,
    assert_input_staged_on_first_read,
    assert_pdf_read_on_first_read,
    assert_ph_called_the_tool,
    assert_session_ph_found_the_repository_tool,
    assert_stubs_called_the_tool,
    big_pdf,
    run_c312_turn,
    with_input_staging,
    with_tool_folder,
)
from tests.unit.harness.engines.ph_e2e import PROMPT as PH_PROMPT
from tests.unit.harness.engines.real_stack import IMAGE, OWNER, STATIC_MODEL_KEY

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.harness.sandbox.docker_broker import DockerSandboxBroker
    from tests.unit.harness.engines.real_stack import RealStack

pytestmark = [pytest.mark.docker, pytest.mark.timeout(900, method="thread")]

TENANT, SESSION, RUN = "tenant", "scope", "run"
GRANT = (GrantedTool("search", "Search the knowledge base."),)
SKILL_MD = b"---\nname: report\ndescription: Use when asked for a report.\n---\nPut reports under out/.\n"


def _pinned():  # noqa: ANN202
    return lane_release("opencode").version


@dataclass
class Lane:
    stack: RealStack
    broker: DockerSandboxBroker
    clock: ManualClock = field(default_factory=ManualClock)
    raw: InMemoryBinaryObjectStore = field(default_factory=InMemoryBinaryObjectStore)
    keys: InMemoryKeyProvider = field(default_factory=InMemoryKeyProvider)
    sink: InMemoryEngineEventSink = field(default_factory=InMemoryEngineEventSink)
    skills: InMemorySkillContentSource = field(default_factory=InMemorySkillContentSource)
    artifacts: InMemoryArtifactStore = field(default_factory=InMemoryArtifactStore)
    publications: InMemoryPublicationLog = field(default_factory=InMemoryPublicationLog)
    gateways: dict[int, EndpointConformanceGateway] = field(default_factory=dict[int, EndpointConformanceGateway])
    # The run image; E0b's PR-3 runs the engine-only variant.
    image: str = IMAGE

    def __post_init__(self) -> None:
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
            # One key per test: the broker is shared, so a sandbox a test could not remove (the daemon timed out)
            # would otherwise hold the next tests' placeholders, and each of them fails "held by another live
            # sandbox" (QA-AUTO: one Docker timeout failed seven tests).
            placeholder_key=b"real-lane-placeholder-" + os.urandom(16).hex().encode(),
            config=EngineControllerConfig(
                images={_pinned(): self.image}, resources=Resources(cpus=1.0, memory_mb=1024, pids=256, scratch_mb=256)
            ),
            writers=InMemoryWorkspaceWriters(clock=self.clock),
        )

    @property
    def scope(self) -> ObjectScope:
        return ObjectScope(TENANT, f"ws:{SESSION}")

    def gateway(self, attempt: int) -> EndpointConformanceGateway:
        """The run's tool endpoint for one attempt; the scripted model (one brain for the run) answers on the
        deployment's model route."""
        if attempt not in self.gateways:
            gateway = EndpointConformanceGateway(
                endpoints=EngineControllerConfig(images={}).endpoints("x" * 16, "y" * 16),
                grant=GRANT,
                binding=RunBinding(TENANT, SESSION, RUN),
                attempt=attempt,
            )
            if self.gateways:
                first = next(iter(self.gateways.values()))
                gateway.brain = first.brain
                gateway.model_calls = first.model_calls
            self.gateways[attempt] = gateway
            self.stack.models.register(gateway, STATIC_MODEL_KEY)
            self.stack.models.gateways[STATIC_MODEL_KEY] = gateway
            self.stack.models.authorizations.setdefault(STATIC_MODEL_KEY, [])
            self.stack.models.bind(RUN, f"{RUN}.a{attempt}", gateway.claims.binding)
        return self.gateways[attempt]

    def run(self, attempt: int, **changes: object) -> EngineRunContext:
        gateway = self.gateway(attempt)
        context = EngineRunContext(
            tenant=TENANT,
            session_id=SESSION,
            run_id=RUN,
            attempt_id=f"{RUN}.a{attempt}",
            fence=attempt,
            lease_holder="worker-real",
            profile=release_default_profile(_pinned(), AccessRouteRef("route-azure-gpt", "d1")),
            tools=gateway.endpoint,
            tool_binding=gateway.claims.binding,
            grant_size=len(GRANT),
            artifacts=WorkspaceArtifactPublisher(
                self.artifacts, self.publications, self.objects, object_scope=lambda b: ObjectScope(b.tenant, f"ws:{b.scope}")
            ),
            # Lanes share the daemon (and RUN): the stack's suffix keeps kill() to this lane's sandboxes.
            labels={**OWNER, "pxh.lane": self.stack.suffix},
        )
        return dataclasses.replace(context, **changes)  # type: ignore[arg-type]

    async def seed(self, files: Mapping[str, bytes]) -> None:
        entries = [WorkspaceFile(path, await self.objects.put(self.scope, data)) for path, data in files.items()]
        await self.store.commit(ObjectScope(TENANT), f"ws:{SESSION}", base_revision=0, changes=ChangeSet.of(*entries), reason="seed")

    async def head(self) -> dict[str, bytes]:
        head = await self.store.head(ObjectScope(TENANT), f"ws:{SESSION}")
        return {p: await self.objects.get(self.scope, e.content) for p, e in head.files.items()}

    def kill(self, attempt: int) -> int:
        containers = self.stack.client.containers.list(
            filters={"label": [f"attemptId={RUN}.a{attempt}", f"pxh.lane={self.stack.suffix}"]}
        )
        for container in containers:
            container.kill()
        return len(containers)


def task(index: int, prompt: str) -> EngineTask:
    return EngineTask(turn_id=turn_id_for(RUN, index), prompt=prompt)


def user_texts(lane: Lane) -> list[str]:
    calls = lane.gateways[min(lane.gateways)].model_calls
    return [m.content for m in calls[-1].messages if m.role == "user"]


@pytest.fixture
def lane(stack: RealStack, broker: DockerSandboxBroker) -> Lane:
    return Lane(stack, broker)


async def test_rx3_a_turn_end_to_end_then_the_next_turn_restores_it(lane: Lane) -> None:
    await lane.seed({"data/in.csv": b"a,b\n1,2\n"})
    lane.skills.publish(TENANT, "report", {"SKILL.md": SKILL_MD})
    bundle = await lane.builder.build(lane.scope, SkillRef("report"))
    run = lane.run(1, skills=(SkillPin("report", bundle.digest),))
    prompt = (
        "RX3-MARKER\nshell mkdir -p out && cp data/in.csv out/report.csv && cat /skills/report/SKILL.md > notes.md\n"
        'tool search {"q": "churn"}\nsay done'
    )
    first = await lane.controller.run_turn(run, task(0, prompt))
    assert (first.reason, first.final_text.strip()) == ("completed", "done")
    head = await lane.head()
    assert head["out/report.csv"] == b"a,b\n1,2\n"
    assert head["notes.md"] == SKILL_MD
    assert [(p.path, p.lifecycle) for p in first.published] == [("report.csv", "draft")]
    history = await lane.publications.history(run.binding)
    assert [c.lifecycle for c in history.cards] == ["draft", "final"]
    assert "before_effect" in [s.reason for s in lane.sink.saves]
    assert first.checkpoint is not None and lane.sink.checkpoints[-1].blob == first.checkpoint
    second = await lane.controller.run_turn(lane.run(2, restore=first.checkpoint), FollowUp(turn_id_for(RUN, 1), "say again"))
    assert (second.reason, second.restored, second.restore_issue) == ("completed", "state", None)
    assert any("RX3-MARKER" in text for text in user_texts(lane))


async def test_rx4_a_sandbox_killed_after_an_autosave_is_replaced_and_the_turn_continues(lane: Lane) -> None:
    first = await lane.controller.run_turn(lane.run(1), task(0, "RX4-TURN-0\nsay ready"))
    assert first.checkpoint is not None
    crashed = asyncio.ensure_future(
        lane.controller.run_turn(
            lane.run(2, restore=first.checkpoint),
            task(1, 'shell echo saved > saved.txt\ntool search {"q": "x"}\nhold\nsay never'),
        )
    )
    model = lane.stack.models.gateways[STATIC_MODEL_KEY]
    held = model.holding
    await asyncio.wait_for(held.wait(), 120)
    assert "saved.txt" in (await lane.head())
    assert lane.kill(2) == 1
    with contextlib.suppress(Exception):
        outcome = await asyncio.wait_for(crashed, 120)
        assert outcome.reason == "failed"
    model.release()
    redriven = await lane.controller.run_turn(
        lane.run(3, restore=first.checkpoint), task(1, "shell cat saved.txt\nsay continued")
    )
    assert (redriven.reason, redriven.restored) == ("completed", "state")
    assert any("RX4-TURN-0" in text for text in user_texts(lane))
    tool_results = [m.content for m in lane.gateways[1].model_calls[-1].messages if m.role == "tool"]
    assert any("saved" in content for content in tool_results)


@pytest.mark.parametrize("damage", ["corrupt", "foreign_version"])
async def test_rx5_unusable_state_resumes_from_the_workspace_flagged(lane: Lane, damage: str) -> None:
    first = await lane.controller.run_turn(lane.run(1), task(0, "shell echo kept > kept.txt\nsay ready"))
    assert first.checkpoint is not None
    blob = first.checkpoint
    if damage == "corrupt":
        inner = blob.content.object_id.split("/", 3)[3]
        key = (TENANT, inner)
        lane.raw._objects[key] = lane.raw._objects[key][:-1] + b"!"  # pyright: ignore[reportPrivateUsage]
    else:
        blob = EngineStateBlob(dataclasses.replace(blob.engine, version="1.18.33"), blob.schema_version, blob.content)
    outcome: EngineTurnOutcome = await lane.controller.run_turn(
        lane.run(2, restore=blob, resume_summary="kept.txt was written."),
        FollowUp(turn_id_for(RUN, 1), "shell cat kept.txt\nsay resumed"),
    )
    expected = {"corrupt": "state_corrupt", "foreign_version": "state_incompatible"}[damage]
    assert (outcome.reason, outcome.restored, outcome.restore_issue) == ("completed", "workspace", expected)
    system = lane.gateways[1].model_calls[-1].system
    assert RESUME_NOTE in system and "kept.txt was written." in system


async def test_rx6_the_model_gateway_budget_refusal_ends_the_turn_as_a_limit(lane: Lane) -> None:
    run = lane.run(1)
    lane.stack.models.budget_spent.add(STATIC_MODEL_KEY)
    try:
        outcome = await lane.controller.run_turn(run, task(0, "shell echo x > partial.txt\nsay never"))
    finally:
        lane.stack.models.budget_spent.discard(STATIC_MODEL_KEY)
    assert (outcome.reason, outcome.limit) == ("limit", "budget")


async def test_ph_e2e_opencode_runs_ph_from_its_shell_through_the_real_gateway(lane: Lane) -> None:
    """R3 (``ph_e2e``): ``ph call`` in opencode's own shell, through the egress proxy, to the run's tool."""
    with_tool_folder(lane.controller)
    outcome = await lane.controller.run_turn(lane.run(1), task(0, PH_PROMPT))
    assert_ph_called_the_tool(outcome, await lane.head(), lane.gateways[1])


async def test_ws14_sdk_e2e_opencode_calls_the_tool_through_the_python_and_typescript_stubs(lane: Lane) -> None:
    """WS-14 (C3.15, C3.16): from opencode's shell, the session folder's Python and TypeScript stubs answer what
    ``ph tools call`` answers, through the egress proxy and the real gateway."""
    with_tool_folder(lane.controller)
    outcome = await lane.controller.run_turn(lane.run(1), task(0, SDK_PROMPT))
    assert_stubs_called_the_tool(outcome, await lane.head(), lane.gateways[1])


async def test_ph_e2e_c312_opencode_runs_ph_skill_publish_and_ask_through_the_real_gateway(lane: Lane) -> None:
    """C3.12 (``ph_e2e``): ``ph skill``, ``ph publish`` and ``ph ask`` from opencode's shell, through the egress proxy and
    the real tool gateway, as their tool forms are: the skill from the skills folder, the file a draft then final
    artifact, the question the run's ``ask_user``."""
    run, outcome, head = await run_c312_turn(lane, TENANT, SKILL_MD, task)
    await assert_c312_commands_worked(lane, run, outcome, head, SKILL_MD)


async def test_j26_c5_01_opencode_places_a_large_input_on_its_first_read(lane: Lane) -> None:
    """J-26 (C5.01): opencode starts without the large input and places it from its shell with the session's
    ``ph inputs get``, through the egress proxy and the real gateway, to the controller."""
    with_tool_folder(lane.controller)
    with_input_staging(lane.controller)
    await lane.seed(INPUTS_SEED)
    outcome = await lane.controller.run_turn(lane.run(1), task(0, INPUTS_PROMPT))
    assert_input_staged_on_first_read(outcome, await lane.head())


async def test_sec_fs_02_a_hostile_engine_names_no_path_outside_its_folder(lane: Lane) -> None:
    """SEC-FS-02 (Docker, opencode): a hostile engine makes ``in`` a link to ``/etc`` and asks for a large input; the
    bytes never land in ``/etc``. A traversing input name names nothing; outputs named ``CON.txt`` and ``report.``
    are refused, not published; a drive, an encoded separator and a backslash link stay out of the workspace."""
    with_tool_folder(lane.controller)
    with_input_staging(lane.controller)
    await lane.seed(INPUTS_SEED)
    outcome = await lane.controller.run_turn(lane.run(1), task(0, HOSTILE_PROMPT))
    assert_hostile_names_refused(outcome, await lane.head())


async def test_j26_9_opencode_reads_a_50_mb_pdf_the_run_was_given_with_the_pdf_skill(lane: Lane) -> None:
    """J26-9 (J-26, WP-69): a 50 MB PDF, past the old 25 MiB cap, is taken as the run's input with the release
    settings, waits for its first read (over 8 MiB), is placed by the engine's own ``ph inputs get`` and read page by
    page with pypdf as the mounted pdf skill says, within the release sandbox's memory and scratch."""
    with_tool_folder(lane.controller)
    pdf = big_pdf()
    binding = RunBinding(TENANT, SESSION, RUN)
    stored, _ = await lane.artifacts.register_output(
        binding, artifact_id="art-j26", name="report.pdf", mime_type="application/pdf", content=pdf,
        lifecycle="final",
    )
    source = StoreInputSource(lane.artifacts, lane.objects, object_scope=lambda b: ObjectScope(b.tenant, f"ws:{b.scope}"))
    (given,) = await source.resolve(binding, [InputArtifactRef(stored.artifact_id)])
    lane.skills.publish(TENANT, "pdf", {"SKILL.md": J26_SKILL})
    bundle = await lane.builder.build(lane.scope, SkillRef("pdf"))
    run = lane.run(1, inputs=(given,), skills=(SkillPin("pdf", bundle.digest),))
    outcome = await lane.controller.run_turn(run, task(0, J26_PROMPT))
    assert_pdf_read_on_first_read(outcome, await lane.head(), pdf)
    # Progress is visible: the activity view lists the command that placed the input, as the engine ran it.
    commands = [e.command for e in lane.sink.of_run(RUN) if isinstance(e, CommandStarted)]
    assert any("inputs get report.pdf" in command for command in commands), commands


async def test_ws14_c5_01_opencode_stubs_rendered_on_first_import_answer_as_ph(
    lane: Lane, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WS-14 + C5.01: a grant past the eager threshold gets stubs rendered on first import, on the image's own
    CPython and Node; they answer what ``ph tools call`` answers."""
    monkeypatch.setattr(sdk_module, "SDK_EAGER_MAX_TOOLS", 0)
    with_tool_folder(lane.controller)
    outcome = await lane.controller.run_turn(lane.run(1), task(0, SDK_PROMPT))
    assert_stubs_called_the_tool(outcome, await lane.head(), lane.gateways[1])


async def test_wp70_sessionph_opencode_finds_the_repository_tool_with_the_session_ph(
    lane: Lane, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WP70-SESSIONPH (opencode): the session folder's ph comes first in its shell and a login shell."""
    monkeypatch.setattr(sys.modules[__name__], "GRANT", (*GRANT, REPOSITORY))
    with_tool_folder(lane.controller)
    outcome = await lane.controller.run_turn(lane.run(1), task(0, SESSION_PH_PROMPT))
    assert_session_ph_found_the_repository_tool(outcome, await lane.head())
