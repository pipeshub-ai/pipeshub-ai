"""P5.11 RC-D1: durable ``run_code`` through a real Docker broker and the production sandbox image.

The real assembler and ``RunDriver``; ``SANDBOX_MODE=broker`` with the broker in this process, in its own
label namespace and ``pxh-`` volume prefix, its egress network a rig /28. The program asks for pandas (in the
image): before P5.11 the install staged under the 100 MB ``/tmp`` and failed with ENOSPC.
"""

from __future__ import annotations

import json
import os
import uuid
from contextlib import ExitStack
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from harness_core.driver import RunDriver
from harness_core.sandbox.types import LeaseRef, MountSpec, RuntimeMode, SandboxSpec
from harness_core.testing import (
    ManualClock,
    ModelScript,
    Reply,
    ScriptedModel,
    ToolCallSpec,
    Turn,
)
from harness_core.testing.durable import ScriptedModelPort
from tests.harness_replay.artifacts import ParityArtifactRegistry
from tests.support.docker_networks import create_rig_network, remove_network_quietly
from tests.unit.harness.assembly import world as w

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.docker

IMAGE = os.environ.get("SANDBOX_TEST_IMAGE", "pxh-sandbox:x1")
OWNER = {"pipeshub.harness.owner": "x-lead"}
# Inter-container traffic off, as the broker requires of its egress bridge: one without it is removed and made anew.
EGRESS_BRIDGE = {"com.docker.network.bridge.enable_icc": "false"}
CODE = (
    "import os, pathlib\n"
    "import pandas as pd\n"
    "df = pd.DataFrame({'region': ['west', 'east', 'west'], 'total': [40, 7, 2]})\n"
    "out = df.groupby('region', as_index=False)['total'].sum()\n"
    "pathlib.Path(os.environ['OUTPUT_DIR'], 'by_region.csv').write_text(out.to_csv(index=False))\n"
    "print(out.to_dict('records'))\n"
)


def _docker() -> Any:  # noqa: ANN401 - docker.DockerClient
    try:
        import docker

        client = docker.from_env()
        client.ping()
        client.images.get(IMAGE)
    except Exception as exc:
        pytest.skip(f"Docker or {IMAGE} unavailable: {type(exc).__name__}")
    return client


class _Lane:
    def __init__(self, broker: Any, specs: list[SandboxSpec], suffix: str) -> None:  # noqa: ANN401
        self.broker = broker
        self.specs = specs
        self.suffix = suffix


@pytest.fixture
async def lane() -> AsyncIterator[_Lane]:
    from app.harness.sandbox.docker_broker import DockerSandboxBroker
    from app.harness.sandbox.docker_client import DockerClientProvider
    from harness_core.clock import SystemClock
    from harness_core.ids import RandomIds

    client = _docker()
    suffix = uuid.uuid4().hex[:8]
    network = f"pxh-p511-egress-{suffix}"
    create_rig_network(client, network, labels={**OWNER, "pxh.p511": suffix}, options=EGRESS_BRIDGE)
    provider = DockerClientProvider(max_workers=8)
    broker = await DockerSandboxBroker.create(
        provider=provider,
        clock=SystemClock(),
        ids=RandomIds(),
        allowed_images=frozenset({IMAGE}),
        helper_image=IMAGE,
        egress_network=network,
        label_namespace=f"io.pipeshub.p511.{suffix}",
        volume_prefix=f"pxh-p511-{suffix}",
    )
    specs: list[SandboxSpec] = []
    acquire = broker.acquire

    async def recording(spec: SandboxSpec, **kwargs: Any) -> Any:  # noqa: ANN401
        specs.append(spec)
        return await acquire(spec, **kwargs)

    broker.acquire = recording  # type: ignore[method-assign]
    try:
        yield _Lane(broker, specs, suffix)
    finally:
        try:
            _ = await broker.reap(labels={}, older_than_s=0)
        finally:
            remove_network_quietly(client, network)
            provider.close()


@pytest.mark.timeout(600)
async def test_rc_d1_durable_run_code_with_pandas_on_a_real_broker(lane: _Lane) -> None:
    async def source() -> Any:  # noqa: ANN401
        return lane.broker

    artifacts = ParityArtifactRegistry(w.ORG, "ses-1")
    clock = ManualClock()
    journal = w.pipeshub_journal(clock=clock, lease_ms=30_000)
    journal.admit(
        "run-1", org_id=w.ORG, session_id="ses-1", spec=w.pinned_spec(tool_ids=[], sandbox="agent-code"),
        input_text="totals by region",
    )
    port = ScriptedModelPort(
        ScriptedModel(
            ModelScript(
                id="d1",
                turns=[
                    Turn(reply=Reply(tool_calls=[ToolCallSpec(
                        name="run_code", args={"code": CODE, "language": "python", "packages": ["pandas"]},
                    )])),
                    Turn(reply=Reply(text="West leads with 42.")),
                ],
            )
        )
    )
    env = {
        "SANDBOX_MODE": "broker",
        "SANDBOX_DOCKER_IMAGE": IMAGE,
        "SANDBOX_EGRESS_NETWORK": f"pxh-p511-egress-{lane.suffix}",
        "PIPESHUB_ENABLE_CODE_EXECUTION": "true",
        "SANDBOX_ALLOW_NETWORK": "false",
    }
    with ExitStack() as stack:
        stack.enter_context(w.deployment())
        stack.enter_context(patch("app.services.artifact_registry.ArtifactRegistryService", new=artifacts))
        stack.enter_context(patch("app.harness.sandbox.client.process_broker_client.get", new=source))
        stack.enter_context(patch.dict(os.environ, env))
        driver = RunDriver(
            journal=journal,
            assembler=w.ClaimAssembler(PipesHubRuntimeAssembler(w.Services(), model_factory=w.model_factory(port))),
            clock=clock,
        )
        done = await driver.run_claimed(await w.claim_next(journal))

    assert done.status == "completed"
    events = journal.events("ses-1")
    [outcome] = [e for e in events if e.type == "tool.outcome"]
    assert outcome.data["status"] == "ok"
    [shown] = [e for e in events if e.type == "x.pipeshub.tool.result_presented"]
    assert "No space left" not in json.dumps(shown.data)
    assert [e.data["fileName"] for e in events if e.type == "x.pipeshub.artifact.presented"] == ["by_region.csv"]
    assert {f.name: f.content for f in artifacts.outputs()} == {"by_region.csv": b"region,total\neast,7\nwest,42\n"}

    # The install sandbox (pandas, already in the image: nothing installed) and the run, both labelled for the reap.
    assert len(lane.specs) == 2
    # Nothing was installed, so the run mounts no /deps (the image's pandas is used).
    assert [m.target for m in lane.specs[1].mounts] == ["/src", "/output"]
    for spec in lane.specs:
        assert {k: spec.labels.get(k) for k in ("orgId", "conversationId", "runId")} == {
            "orgId": w.ORG, "conversationId": "ses-1", "runId": "run-1",
        }
        assert spec.labels.get("attemptId")
    assert await lane.broker.reap(labels={"runId": "run-1"}, older_than_s=0) == 0

    # A sandbox a killed attempt left is removed by the sweeper's reap labels.
    left = SandboxSpec(
        image_digest=IMAGE,
        mode=RuntimeMode.DOCKER_HARDENED,
        mounts=(MountSpec("/src"),),
        labels={"orgId": w.ORG, "conversationId": "ses-1", "runId": "run-1", "attemptId": "att-dead"},
    )
    _ = await lane.broker.acquire(left, lease=LeaseRef("p511-test", 1), idempotency_key=f"p511-{lane.suffix}")
    assert await lane.broker.reap(labels={"orgId": w.ORG, "runId": "run-1"}, older_than_s=0) == 1


@pytest.mark.timeout(900)
@pytest.mark.parametrize("case_id", ["da-05-revenue-by-product-file", "od-01-incident-memo"])
async def test_rc_d2_wave1_cases_pass_on_the_durable_loops_broker_lane(case_id: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """The live lane's sandbox (``--sandbox broker``) with the scripted reference model: what RC-E2 runs live."""
    from tests.support.parity_cases.wave1_cases import select
    from tests.support.parity_cases.wave1_durable import DurableLoopSystem
    from tests.support.parity_cases.wave1_outputs import assert_produced
    from tests.support.parity_cases.wave1_reference import script_for
    from tests.support.parity_cases.wave1_systems import task_for

    _ = _docker()
    monkeypatch.setenv("SANDBOX_TEST_IMAGE", IMAGE)
    (case,) = select(ids=[case_id])
    run = await DurableLoopSystem(sandbox="broker").run(task_for(case, 0, script_for(case)))
    assert (run.status, run.error) == ("completed", None)
    assert_produced(case_id, run)


@pytest.mark.timeout(600)
async def test_rc_d3_the_docker_backend_installs_nothing_the_image_has() -> None:
    """RC-11 on the Docker backend, whose install container has a 100 MB ``/tmp``: pandas no longer fails ENOSPC."""
    from app.agent_loop_lib.sandbox.coding.docker import DockerCodingSandbox
    from app.harness.sandbox.docker_client import DockerClientProvider

    client = _docker()
    suffix = uuid.uuid4().hex[:8]
    network = f"pxh-p511-egress-{suffix}"
    create_rig_network(client, network, labels={**OWNER, "pxh.p511": suffix}, options=EGRESS_BRIDGE)
    provider = DockerClientProvider(max_workers=4)
    sandbox = DockerCodingSandbox(
        image=IMAGE, egress_network=network, image_node_modules="/home/sandbox/node_modules", provider=provider
    )
    try:
        await sandbox.provision()
        installed = await sandbox.install_packages(["pandas", "openpyxl"], "python")
        assert installed.success, installed.stderr[-500:]
        assert "No space left" not in installed.stderr
        assert sandbox._has_deps() == (False, False)  # pyright: ignore[reportPrivateUsage]
    finally:
        await sandbox.destroy()
        remove_network_quietly(client, network)
        provider.close()
