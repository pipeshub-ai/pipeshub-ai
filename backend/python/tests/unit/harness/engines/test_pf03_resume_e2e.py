"""PF-03 (C7.06): end-to-end resume from a snapshot, p95 over ``PF03_SAMPLES`` (20 by default) resumes.

A resume is a run's next attempt on the release image, as MS-1 (``test_real_snapshot_cost.py``) times it phase by
phase, measured here as one span: from the acquire of the new sandbox until the engine's session is open (``ready``:
the run can take its next step) and until its first tool use answered (``first_tool``: matplotlib's first plot, the
tool state the snapshot restores). Two lanes: ``cold`` (no warm pool, the shipped default) and ``warm`` (a pool of the
engine's shape, ``SANDBOX_WARM_POOL_MAX``). ``PF03_REPORT`` appends a JSON line per lane: samples, p50/p95/max, the
host's one-minute load. Gated by ``slo_gates`` (profile ``resume``); the test itself asserts only that it ran.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from harness_core.engines.snapshots import (
    ObjectStoreSandboxSnapshots,
    SandboxSnapshots,
    capture,
    read_archive,
)
from harness_core.logs import HarnessLog
from harness_core.workspace.memory import InMemoryBinaryObjectStore, InMemoryKeyProvider
from harness_core.workspace.sealing import SealedObjectStore
from harness_engines.release import release_snapshot_policy
from tests.unit.harness.engines.real_stack import engine_broker, lane_available
from tests.unit.harness.engines.start_time import Engine, percentile
from tests.unit.harness.engines.test_real_snapshot_cost import (  # pyright: ignore[reportPrivateUsage]
    SCOPE,
    _attempt,
)
from tests.unit.harness.sandbox_broker.conftest import cleanup, cleanup_networks

if TYPE_CHECKING:
    from app.harness.sandbox.docker_broker import DockerSandboxBroker
    from tests.unit.harness.engines.real_stack import RealStack

pytestmark = [pytest.mark.docker, pytest.mark.slow, pytest.mark.timeout(3600, method="thread")]

SAMPLES = int(os.environ.get("PF03_SAMPLES", "20"))
SLO_MS = 10_000.0


def _stats(values: list[float]) -> dict[str, float]:
    return {"p50": percentile(values, 50), "p95": percentile(values, 95), "max": max(values)}


async def _resume(stack: RealStack, broker: DockerSandboxBroker, engine: Engine) -> tuple[float, float, dict[str, float]]:
    """One first turn (snapshotted, released) and its resume with the snapshot: the resume's ready and first-tool ms."""
    keys = InMemoryKeyProvider()
    store = ObjectStoreSandboxSnapshots(SealedObjectStore(InMemoryBinaryObjectStore(), keys))
    policy = release_snapshot_policy()
    snapshots = SandboxSnapshots(
        store=store, policy=policy, scope=SCOPE, secrets=tuple, log=HarnessLog(logging.getLogger("pf03.resume"))
    )
    _, handle, session = await _attempt(stack, broker, engine, snapshots, restore=False)
    try:
        data = await capture(handle, policy)
        assert data is not None
        snapshots.last = await store.save(SCOPE, read_archive(data, policy).to_tar())
    finally:
        await session.close()
        await broker.release(handle)
    resumed, handle, session = await _attempt(stack, broker, engine, snapshots, restore=True)
    await session.close()
    await broker.release(handle)
    ready = resumed.acquire_ms + resumed.files_ms + resumed.restore_ms + resumed.engine_ms
    phases = {"acquire": resumed.acquire_ms, "workspace": resumed.files_ms, "restore": resumed.restore_ms,
              "engine": resumed.engine_ms, "tool": resumed.tool_ms}
    return ready, ready + resumed.tool_ms, phases


@pytest.mark.parametrize("lane", ["cold", "warm"])
async def test_pf03_resume_from_a_snapshot_end_to_end(stack: RealStack, lane: str) -> None:
    engine = Engine.of("opencode")
    available, reason = lane_available(engine.image)
    if not available:
        pytest.skip(reason)
    namespace = f"io.pipeshub.pf03.{uuid.uuid4().hex[:8]}"
    prefix = "pxh-pf03-" + namespace.rsplit(".", 1)[1]
    broker, provider = await engine_broker(
        stack, namespace, prefix, image=engine.image, pool_ceiling=2 if lane == "warm" else 0
    )
    ready: list[float] = []
    first_tool: list[float] = []
    phases: list[dict[str, float]] = []
    loads: list[float] = []
    try:
        for _ in range(SAMPLES):
            if lane == "warm":
                _ = await broker.fill_warm()
            r, t, p = await _resume(stack, broker, engine)
            ready.append(r)
            first_tool.append(t)
            phases.append(p)
            loads.append(os.getloadavg()[0])
    finally:
        await broker.aclose()
        await provider.run_blocking(cleanup, provider, namespace, prefix)
        await provider.run_blocking(cleanup_networks, provider, namespace, stack.proxy)
        provider.close()
    row = {
        "lane": lane,
        "n": len(ready),
        "ready_ms": ready,
        "first_tool_ms": first_tool,
        "ready": _stats(ready),
        "first_tool": _stats(first_tool),
        "phases_p95": {k: percentile([p[k] for p in phases], 95) for k in phases[0]},
        "warm_served": broker.pool.served,
        "load1": loads,
        "load1_max": max(loads),
        "slo_ms": SLO_MS,
    }
    print(json.dumps({k: v for k, v in row.items() if not k.endswith("_ms") and k != "load1"}, indent=1))
    if path := os.environ.get("PF03_REPORT"):
        with Path(path).open("a", encoding="utf-8") as out:
            out.write(json.dumps(row) + "\n")
    assert len(ready) == SAMPLES
