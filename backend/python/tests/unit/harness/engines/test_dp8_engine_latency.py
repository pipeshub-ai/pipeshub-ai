"""DP8-4, DP8-8, DP8-9 (design-plan §8): an engine turn's latencies on the real binaries, sampled.

Each engine runs on its own real-binary conformance lane (its image under the Docker broker, the egress proxy, the
real gateways, the scripted model), and every turn goes through ``engine_session_chain`` as the controller hands it.
On one open session (``DP8_SAMPLES`` of each, 20 by default):

* ``first_event_ms`` (DP8-4): the turn handed to the session until its first event reaches the consumer, which is
  where the controller forwards it to the sink. The scripted model answers at once, so this is the harness's and the
  engine's own time without the model's TTFT (the model gateway's hop is in it, PF-07's). Session ready, before a
  run's first turn, is PF-01's and PF-02's row.
* ``lag_ms`` (DP8-8, sandbox leg): a shell command in the sandbox writes the sandbox's clock as its last act, and
  its ``CommandFinished`` reaches the consumer; the sandbox and this process read one kernel clock (runc). The clock
  is read with the images' ``python3``: their busybox ``date`` has no sub-second field. The leg
  from the sink to the browser is the LiveBus's (SLO #5), measured on the rig.
* ``stop_ms`` (DP8-9): an interrupt of a turn whose model call is held until its ``TurnCompleted``.

And on ``DP8_KILL_SAMPLES`` fresh sessions (5 by default), an engine stopped mid-turn (SIGSTOP: it answers neither its
interrupt nor SIGINT nor SIGTERM): ``kill_stop_ms`` until its turn ends and ``kill_ms`` until ``close`` has it dead.

``DP8_REPORT`` appends a JSON line per engine. The numbers are judged against §8 where they are recorded
(STATUS.md DP8 rows); the test asserts only that every sample ran, as host load would otherwise fail it.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

import tests.unit.harness.engines.test_real_acp_conformance as acp_opencode_lane
import tests.unit.harness.engines.test_real_claude_code_conformance as claude_code_lane
import tests.unit.harness.engines.test_real_codex_conformance as codex_lane
import tests.unit.harness.engines.test_real_conformance as opencode_lane
import tests.unit.harness.engines.test_real_dsh_acp_conformance as dsh_lane
import tests.unit.harness.engines.test_real_gemini_acp_conformance as gemini_lane
import tests.unit.harness.engines.test_real_goose_acp_conformance as goose_lane
import tests.unit.harness.engines.test_real_qwen_acp_conformance as qwen_lane
from harness_core.engines.events import (
    CommandFinished,
    TurnCompleted,
    turn_id_for,
)
from harness_core.engines.types import EngineTask
from tests.unit.harness.engines.start_time import percentile
from tests.unit.harness.engines.test_real_acp_conformance import (  # noqa: F401 - the lane's fixtures
    acp_broker,
    acp_image,
)
from tests.unit.harness.engines.test_real_claude_code_conformance import (  # noqa: F401 - the lane's fixtures
    claude_broker,
    claude_code_image,
)
from tests.unit.harness.engines.test_real_codex_conformance import (  # noqa: F401 - the lane's fixtures
    codex_broker,
    codex_image,
    model_gateway,
)
from tests.unit.harness.engines.test_real_gemini_acp_conformance import (  # noqa: F401 - the lane's fixtures
    gemini_broker,
    gemini_image,
)

if TYPE_CHECKING:
    from harness_core.testing.engines.contract import EngineConformanceHarness, Rig

pytestmark = [pytest.mark.docker, pytest.mark.slow, pytest.mark.timeout(3600, method="thread")]

SAMPLES = int(os.environ.get("DP8_SAMPLES", "20"))
KILL_SAMPLES = int(os.environ.get("DP8_KILL_SAMPLES", "5"))
RUN = "run-dp8"
SANDBOX_CLOCK = "python3 -c 'import time; print(time.time_ns())'"
TARGETS_MS = {"first_event_ms": 1500.0, "lag_ms": 300.0, "stop_ms": 2000.0, "kill_stop_ms": 2000.0,
              "kill_ms": 10_000.0}


def _task(index: int, prompt: str) -> EngineTask:
    return EngineTask(turn_id=turn_id_for(RUN, index), prompt=prompt)


def _stats(values: list[float]) -> dict[str, float]:
    return {"p50": round(percentile(values, 50), 1), "p95": round(percentile(values, 95), 1),
            "max": round(max(values), 1)}


def _ms(since: float) -> float:
    return (time.perf_counter() - since) * 1000


class Dp8Profile:
    """The profile's case, over a lane's own ``open_rig`` and the contract's held-turn and stall helpers."""

    turn_timeout_s: float

    async def _first_event(self, rig: Rig, index: int, sample: int) -> tuple[float, str]:
        started = time.perf_counter()
        first: tuple[float, str] | None = None
        ended: TurnCompleted | None = None

        async def run() -> None:
            nonlocal first, ended
            async for event in rig.session.run_turn(_task(index, f"say hi {sample}")):
                if first is None:
                    first = (_ms(started), type(event).__name__)
                if isinstance(event, TurnCompleted):
                    ended = event

        await asyncio.wait_for(run(), self.turn_timeout_s)
        assert first is not None and ended is not None and ended.reason == "completed"
        return first

    async def _lag(self, rig: Rig, index: int, sample: int) -> float:
        path = f"/workspace/dp8-lag-{sample}"
        arrived_ns: list[int] = []

        async def run() -> None:
            async for event in rig.session.run_turn(_task(index, f"shell {SANDBOX_CLOCK} > {path}\nsay done")):
                if isinstance(event, CommandFinished) and not arrived_ns:
                    arrived_ns.append(time.time_ns())

        await asyncio.wait_for(run(), self.turn_timeout_s)
        assert arrived_ns, "the engine reported no finished command"
        process = await rig.sandbox.exec(["cat", path], env={})
        written = b"".join([chunk async for chunk in process.stdout]).strip()
        assert await process.wait(timeout_s=30) == 0
        assert written.isdigit(), f"the sandbox's clock read {written!r}"
        lag_ms = (arrived_ns[0] - int(written)) / 1e6
        assert 0 <= lag_ms < self.turn_timeout_s * 1000, f"the sandbox's clock read {written!r}: not this turn's"
        return lag_ms

    async def _stop(self, rig: Rig, index: int, sample: int) -> float:
        runner, ended_at = await self.start_timed_held_turn(rig, _task(index, f"hold\nsay never\nDP8-HOLD-{sample}"))
        started = time.monotonic()
        await rig.session.interrupt()
        events = await runner
        assert isinstance(events[-1], TurnCompleted) and events[-1].reason == "interrupted"
        return (ended_at[0] - started) * 1000

    async def _kill(self, harness: EngineConformanceHarness, rigs: list[Rig], sample: int) -> tuple[float, float]:
        rig = await self._open(harness, rigs)
        runner, ended_at = await self.start_timed_held_turn(rig, _task(0, f"hold\nsay never\nDP8-KILL-{sample}"))
        await self.stall_engine(rig)
        started = time.monotonic()
        await rig.session.interrupt()
        events = await runner
        stopped_ms = (ended_at[0] - started) * 1000
        await rig.session.close()
        killed_ms = (time.monotonic() - started) * 1000
        rig.gateway.release()
        assert isinstance(events[-1], TurnCompleted) and events[-1].reason == "interrupted"
        await self.assert_engine_gone(rig)
        return stopped_ms, killed_ms

    async def test_dp8_engine_latency_profile(self, engine_harness: EngineConformanceHarness, rigs: list[Rig]) -> None:
        samples: dict[str, list[float]] = {name: [] for name in TARGETS_MS}
        first_kinds: Counter[str] = Counter()
        loads: list[float] = [os.getloadavg()[0]]
        rig = await self._open(engine_harness, rigs)
        index = 0
        _ = await self._first_event(rig, index, -1)
        for sample in range(SAMPLES):
            first, kind = await self._first_event(rig, index + 1, sample)
            samples["first_event_ms"].append(first)
            first_kinds[kind] += 1
            samples["lag_ms"].append(await self._lag(rig, index + 2, sample))
            index += 2
            loads.append(os.getloadavg()[0])
        # The interrupts last: a turn after one is the cf8 cases' to judge, not a latency sample's.
        for sample in range(SAMPLES):
            index += 1
            samples["stop_ms"].append(await self._stop(rig, index, sample))
            loads.append(os.getloadavg()[0])
        for sample in range(KILL_SAMPLES):
            stopped, killed = await self._kill(engine_harness, rigs, sample)
            samples["kill_stop_ms"].append(stopped)
            samples["kill_ms"].append(killed)
            loads.append(os.getloadavg()[0])
        row: dict[str, Any] = {
            "lane": engine_harness.version.kind,
            "version": engine_harness.version.version,
            **samples,
            **{name.removesuffix("_ms"): _stats(values) for name, values in samples.items()},
            "first_event_kinds": dict(first_kinds),
            "targets_ms": TARGETS_MS,
            "load1_max": max(loads),
        }
        print(json.dumps({k: v for k, v in row.items() if not k.endswith("_ms")}, indent=1))
        if path := os.environ.get("DP8_REPORT"):
            with Path(path).open("a", encoding="utf-8") as out:
                out.write(json.dumps(row) + "\n")
        assert [len(samples[n]) for n in TARGETS_MS] == [SAMPLES] * 3 + [KILL_SAMPLES] * 2


def _profile_of(lane: type) -> type:
    """``lane``'s wiring (its fixtures and ``open_rig``) with only the profile's case: its conformance cases run in its
    own module."""
    profile = type(lane.__name__.replace("Test", "TestDp8", 1), (Dp8Profile, lane), {"__module__": __name__})
    for name in dir(lane):
        if name.startswith("test_"):
            setattr(profile, name, None)
    return profile


TestDp8OpencodeOnTheRealBinary = _profile_of(opencode_lane.TestOpencodeOnTheRealBinary)
TestDp8CodexOnTheRealBinary = _profile_of(codex_lane.TestCodexOnTheRealBinary)
TestDp8ClaudeCodeOnTheRealBinary = _profile_of(claude_code_lane.TestClaudeCodeOnTheRealBinary)
TestDp8AcpOpencodeOnTheRealBinary = _profile_of(acp_opencode_lane.TestAcpOpencodeOnTheRealBinary)
TestDp8AcpGeminiOnTheRealBinary = _profile_of(gemini_lane.TestAcpGeminiOnTheRealBinary)
TestDp8AcpGooseOnTheRealBinary = _profile_of(goose_lane.TestAcpGooseOnTheRealBinary)
TestDp8AcpQwenOnTheRealBinary = _profile_of(qwen_lane.TestAcpQwenOnTheRealBinary)
TestDp8AcpDshOnTheRealBinary = _profile_of(dsh_lane.TestAcpDshOnTheRealBinary)
