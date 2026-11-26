"""RL-W1 (P5.9a): start time on the release images, acquire to a ready engine, cold vs warm.

Cold: the broker without a pool. Warm: the broker's pool, refilled between
starts as an idle host's background refill would (the refill is not timed).
``P59A_SAMPLES`` starts per lane (20 by default); ``P59A_REPORT`` appends each
lane's samples and p50/p95 per phase as a JSON line.

EH2-22: the same starts for every engine with the broker's binary check off (each session hashes its binary in the
sandbox, EH1) and on (the broker vouches for it once per image id, EH2). Reported, not gated.

PF-01 (design-plan §8, C7.01): a warm start as production makes it (the broker vouches for the binary, EH2; the
controller reads the image's tool-folder contract and seeds ``/pipeshub`` every turn, R3), for every released engine,
with and without a pinned skill: p50 within 2 s and p95 within 5 s. Each sample carries the host's one-minute load
average: a start time taken on a loaded host is not a result (QA-22's rule).
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from harness_engines.release import release_binary_pins
from tests.unit.harness.engines.lane_releases import lane_releases
from tests.unit.harness.engines.real_stack import (
    engine_broker,
    lane_available,
    prime_pool,
)
from tests.unit.harness.engines.start_time import (
    Engine,
    StartSample,
    engine_spec,
    instrument,
    one_start,
    percentile,
    summary,
)
from tests.unit.harness.sandbox_broker.conftest import cleanup, cleanup_networks

if TYPE_CHECKING:
    from tests.unit.harness.engines.real_stack import RealStack

pytestmark = [pytest.mark.docker, pytest.mark.slow, pytest.mark.timeout(3600, method="thread")]

SAMPLES = int(os.environ.get("P59A_SAMPLES", "20"))
WARM_P50_MS = 3000.0
PF01_P50_MS = 2000.0
PF01_P95_MS = 5000.0
POOL_CEILING = 2


def _report(lane: str, kind: str, samples: list[StartSample]) -> None:
    path = os.environ.get("P59A_REPORT")
    if not path:
        return
    with Path(path).open("a", encoding="utf-8") as out:
        record = {
            "lane": lane,
            "engine": kind,
            "summary": summary(samples),
            "load1_max": max(s.load1 for s in samples),
            "samples": [s.__dict__ for s in samples],
        }
        out.write(json.dumps(record) + "\n")


async def _starts(
    stack: RealStack, kind: str, *, warm: bool, skills: bool = False, pins: bool = False, folder: bool = False
) -> list[StartSample]:
    engine = Engine.of(kind)
    available, reason = lane_available(engine.image)
    if not available:
        pytest.skip(reason)
    namespace = f"io.pipeshub.p59a.{uuid.uuid4().hex[:8]}"
    prefix = "pxh-p59a-" + namespace.rsplit(".", 1)[1]
    broker, provider = await engine_broker(
        stack,
        namespace,
        prefix,
        image=engine.image,
        pool_ceiling=POOL_CEILING if warm else 0,
        binary_pins=release_binary_pins(lane_releases()) if pins else None,
    )
    timer = instrument(broker)
    samples: list[StartSample] = []
    try:
        if warm:
            template = engine_spec(
                engine,
                model_upstream="http://m/x",
                tools_upstream="http://t/x",
                placeholders=("a" * 16, "b" * 16),
                labels={},
                folder=folder,
            )
            assert await prime_pool(broker, template) == POOL_CEILING
        for _ in range(SAMPLES):
            samples.append(await one_start(stack, broker, timer, engine, skills=skills, folder=folder))
            if warm:
                _ = await broker.fill_warm()
    finally:
        await broker.aclose()
        await provider.run_blocking(cleanup, provider, namespace, prefix)
        await provider.run_blocking(cleanup_networks, provider, namespace, stack.proxy)
        provider.close()
    return samples


@pytest.mark.parametrize("kind", ["opencode", "codex"])
async def test_rlw1_cold_start(stack: RealStack, kind: str) -> None:
    samples = await _starts(stack, kind, warm=False)
    _report("cold", kind, samples)
    assert len(samples) == SAMPLES
    assert not any(s.warm for s in samples)


@pytest.mark.parametrize("kind", ["opencode", "codex"])
async def test_rlw1_warm_start_p50_within_three_seconds(stack: RealStack, kind: str) -> None:
    samples = await _starts(stack, kind, warm=True)
    _report("warm", kind, samples)
    assert len(samples) == SAMPLES
    assert all(s.warm for s in samples), "a start was not served from the pool"
    assert len({s.sandbox_id for s in samples}) == SAMPLES, "a sandbox served two starts"
    p50 = percentile([s.total_ms for s in samples], 50)
    assert p50 <= WARM_P50_MS, summary(samples)


@pytest.mark.parametrize("kind", ["opencode"])
async def test_rlw1_warm_start_with_a_pinned_skill(stack: RealStack, kind: str) -> None:
    """Reported, not gated: a run that pins skills seeds them through a helper container."""
    samples = await _starts(stack, kind, warm=True, skills=True)
    _report("warm+skill", kind, samples)
    assert all(s.warm for s in samples)


@pytest.mark.parametrize("pins", [False, True], ids=["session-hash", "broker-check"])
@pytest.mark.parametrize("warm", [False, True], ids=["cold", "warm"])
@pytest.mark.parametrize("kind", ["opencode", "codex", "claude_code"])
async def test_eh2_22_session_start_with_and_without_the_broker_check(
    stack: RealStack, kind: str, warm: bool, pins: bool
) -> None:
    samples = await _starts(stack, kind, warm=warm, pins=pins)
    _report(f"eh2:{'warm' if warm else 'cold'}:{'broker' if pins else 'session'}", kind, samples)
    assert len(samples) == SAMPLES
    assert all(s.warm == warm for s in samples)


@pytest.mark.parametrize("skills", [False, True], ids=["plain", "pinned-skill"])
@pytest.mark.parametrize("kind", ["opencode", "codex", "claude_code"])
async def test_pf01_warm_session_ready_within_the_budget(stack: RealStack, kind: str, skills: bool) -> None:
    samples = await _starts(stack, kind, warm=True, skills=skills, pins=True, folder=True)
    _report(f"pf01:{'skill' if skills else 'plain'}", kind, samples)
    assert all(s.warm for s in samples), "a start was not served from the pool"
    totals = [s.total_ms for s in samples]
    assert percentile(totals, 50) <= PF01_P50_MS, summary(samples)
    assert percentile(totals, 95) <= PF01_P95_MS, summary(samples)
