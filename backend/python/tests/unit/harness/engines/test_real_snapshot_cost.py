"""MS-1 (P5.9b): what a resume or a warm start costs without and with a sandbox snapshot, on the release image.

Each sample is two sandboxes of one session, as a resume is: the first is a turn that starts the engine and
uses a tool that builds state in ``HOME`` (matplotlib's first plot builds its font cache), then is
snapshotted; the second is the next attempt, which materializes the workspace, restores the snapshot (or
not), starts the engine and uses the tool again. Phases are timed separately. ``P59B_SAMPLES`` samples (5 by
default); ``P59B_REPORT`` appends the samples and p50/p95 per phase as a JSON line. Reported, not gated.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

from harness_core.engines.snapshots import (
    ObjectStoreSandboxSnapshots,
    SandboxSnapshots,
    capture,
    read_archive,
)
from harness_core.logs import HarnessLog
from harness_core.sandbox.tar import tar_stream
from harness_core.testing.engines.contract import SKILLS_DIR, make_profile
from harness_core.testing.engines.endpoint_gateway import EndpointConformanceGateway
from harness_core.workspace.memory import InMemoryBinaryObjectStore, InMemoryKeyProvider
from harness_core.workspace.sealing import SealedObjectStore
from harness_core.workspace.types import ObjectScope
from harness_engines.release import release_snapshot_policy
from tests.unit.harness.engines.real_stack import engine_broker, lane_available
from tests.unit.harness.engines.start_time import (
    GRANT,
    LEASE,
    WORKSPACE,
    WORKSPACE_FILES,
    Engine,
    _endpoints,  # pyright: ignore[reportPrivateUsage]
    _Harness,  # pyright: ignore[reportPrivateUsage]
    engine_spec,
    percentile,
)
from tests.unit.harness.engines.test_real_conformance import RecordingEndpoint
from tests.unit.harness.sandbox_broker.conftest import cleanup, cleanup_networks

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.harness.sandbox.docker_broker import DockerSandboxBroker
    from harness_core.interactions.ports import RunBinding
    from harness_core.sandbox.ports import SandboxHandle
    from tests.unit.harness.engines.real_stack import RealStack

pytestmark = [pytest.mark.docker, pytest.mark.slow, pytest.mark.timeout(3600, method="thread")]

SAMPLES = int(os.environ.get("P59B_SAMPLES", "5"))
SCOPE = ObjectScope("org-p59b", "ws:session-cost")
PLOT = "import matplotlib.pyplot as p; p.figure(); p.plot([1, 2], [3, 4]); p.savefig('/tmp/plot.png')"


@dataclass
class CostSample:
    acquire_ms: float
    files_ms: float
    restore_ms: float
    engine_ms: float
    tool_ms: float
    capture_ms: float = 0.0
    seal_ms: float = 0.0
    snapshot_bytes: int = 0
    snapshot_files: int = 0


async def _run(sandbox: SandboxHandle, argv: list[str]) -> int:
    process = await sandbox.exec(argv, env={})

    async def drain(stream: AsyncIterator[bytes]) -> None:
        async for _ in stream:
            pass

    await asyncio.gather(drain(process.stdout), drain(process.stderr))
    return await process.wait(timeout_s=120)


async def _attempt(
    stack: RealStack, broker: DockerSandboxBroker, engine: Engine, snapshots: SandboxSnapshots, *, restore: bool
) -> tuple[CostSample, SandboxHandle, Any]:
    gateway = EndpointConformanceGateway(endpoints=_endpoints(engine, ("x" * 16, "y" * 16)), grant=GRANT)
    binding = gateway.claims.binding
    run_id = cast("RunBinding", binding).run_id
    stack.models.bind(run_id, getattr(binding, "attempt_id", ""), binding)
    placeholders = (f"pxh-model-{uuid.uuid4().hex}", f"pxh-tools-{uuid.uuid4().hex}")
    spec = engine_spec(
        engine,
        model_upstream=stack.model_upstream(gateway),
        tools_upstream=await stack.mount(RecordingEndpoint(gateway), binding),
        placeholders=placeholders,
        # The run's correlation labels, as the controller sets them: the egress proxy binds the run's gateway token
        # to them, and the Tool Gateway refuses a token bound to no attempt (opencode then never connects its tools).
        labels={
            "pipeshub.agent": "P5.9b",
            "orgId": binding.tenant,
            "sessionId": binding.scope,
            "runId": run_id,
            "attemptId": getattr(binding, "attempt_id", ""),
        },
    )
    started = time.perf_counter()
    handle = await broker.acquire(spec, lease=LEASE, idempotency_key=f"p59b-{uuid.uuid4().hex}")
    acquired = time.perf_counter()
    await handle.put_tar(WORKSPACE, tar_stream(WORKSPACE_FILES))
    materialized = time.perf_counter()
    if restore and snapshots.last is not None:
        assert await snapshots.restore_into(handle, snapshots.last)
    restored = time.perf_counter()
    launch = engine.adapter.render_launch(make_profile(cast("Any", _Harness(engine))), _endpoints(engine, placeholders), SKILLS_DIR)
    session = await engine.adapter.open(handle, launch, None, correlation={"runId": run_id})
    opened = time.perf_counter()
    assert await _run(handle, ["python3", "-c", PLOT]) == 0
    used = time.perf_counter()
    sample = CostSample(
        acquire_ms=(acquired - started) * 1000,
        files_ms=(materialized - acquired) * 1000,
        restore_ms=(restored - materialized) * 1000,
        engine_ms=(opened - restored) * 1000,
        tool_ms=(used - opened) * 1000,
    )
    return sample, handle, session


async def _sample(stack: RealStack, broker: DockerSandboxBroker, engine: Engine) -> tuple[CostSample, CostSample, CostSample]:
    """The first turn (snapshotted), its resume without a snapshot, and its resume with it."""
    keys = InMemoryKeyProvider()
    store = ObjectStoreSandboxSnapshots(SealedObjectStore(InMemoryBinaryObjectStore(), keys))
    policy = release_snapshot_policy()
    snapshots = SandboxSnapshots(
        store=store, policy=policy, scope=SCOPE, secrets=tuple, log=HarnessLog(logging.getLogger("p59b.cost"))
    )
    first, handle, session = await _attempt(stack, broker, engine, snapshots, restore=False)
    try:
        started = time.perf_counter()
        data = await capture(handle, policy)
        captured = time.perf_counter()
        assert data is not None
        archive = read_archive(data, policy)
        ref = await store.save(SCOPE, archive.to_tar())
        sealed = time.perf_counter()
        snapshots.last = ref
        first.capture_ms, first.seal_ms = (captured - started) * 1000, (sealed - captured) * 1000
        first.snapshot_bytes, first.snapshot_files = archive.size, archive.files
    finally:
        await session.close()
        await broker.release(handle)
    results: list[CostSample] = []
    for restore in (False, True):
        sample, handle, session = await _attempt(stack, broker, engine, snapshots, restore=restore)
        await session.close()
        await broker.release(handle)
        results.append(sample)
    return first, results[0], results[1]


def _summary(samples: list[CostSample]) -> dict[str, tuple[float, float]]:
    fields = ("acquire_ms", "files_ms", "restore_ms", "engine_ms", "tool_ms", "capture_ms", "seal_ms", "snapshot_bytes")
    return {f: (percentile([getattr(s, f) for s in samples], 50), percentile([getattr(s, f) for s in samples], 95)) for f in fields}


async def test_ms1_resume_cost_without_and_with_a_snapshot(stack: RealStack) -> None:
    engine = Engine.of("opencode")
    available, reason = lane_available(engine.image)
    if not available:
        pytest.skip(reason)
    namespace = f"io.pipeshub.p59b.{uuid.uuid4().hex[:8]}"
    prefix = "pxh-p59b-" + namespace.rsplit(".", 1)[1]
    broker, provider = await engine_broker(stack, namespace, prefix, image=engine.image, pool_ceiling=0)
    rows: dict[str, list[CostSample]] = {"first": [], "resume": [], "resume+snapshot": []}
    try:
        for _ in range(SAMPLES):
            first, plain, restored = await _sample(stack, broker, engine)
            rows["first"].append(first)
            rows["resume"].append(plain)
            rows["resume+snapshot"].append(restored)
    finally:
        await broker.aclose()
        await provider.run_blocking(cleanup, provider, namespace, prefix)
        await provider.run_blocking(cleanup_networks, provider, namespace, stack.proxy)
        provider.close()
    report = {lane: _summary(samples) for lane, samples in rows.items()}
    print(json.dumps(report, indent=1))
    if path := os.environ.get("P59B_REPORT"):
        with Path(path).open("a", encoding="utf-8") as out:
            out.write(json.dumps({"report": report, "samples": {k: [asdict(s) for s in v] for k, v in rows.items()}}) + "\n")
    assert all(s.snapshot_files > 0 for s in rows["first"])
