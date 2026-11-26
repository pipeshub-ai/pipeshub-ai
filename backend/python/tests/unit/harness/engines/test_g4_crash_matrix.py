"""Gate G4 (E1): durable engine runs survive crashes at every step boundary, on the real lane.

Each scenario is one run on the real opencode image (runc), driven by worker processes
(``durable_stack``) until Node holds it terminal. A crash is one of:

* ``failpoint``: the worker ``SIGKILL``s itself at a named step boundary (``kill9``), on the ``nth`` hit;
  ``twice`` scenarios crash the next attempt at the same point again;
* ``worker_kill``: the lane ``SIGKILL``s the worker a fixed delay into its attempt;
* ``sandbox_kill``: the lane ``docker kill``s the run's sandbox mid-turn (the worker lives on);
* ``node_outage``: Node becomes unreachable for the workers (longer than their lease) mid-turn.

A scheduled crash counts when it fired (the worker died there, the sandbox was killed, Node went away
while the attempt ran). A run resumed when Node holds it ``completed`` with every turn's answer; an effect
is duplicated when the SaaS the ``create`` tool writes to saw the same call more than once.
Thresholds: at least 100 crashes, at least 99% resumed, no duplicate effect.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import pytest

from harness_core.effects import args_digest
from tests.unit.harness.engines.durable_stack import (
    LEASE_MS,
    SNAPSHOT_EVENTS,
    DurableLane,
    durable_lane,
    kill,
    snapshots_on,
)
from tests.unit.harness.engines.real_stack import engine_image, lane_available

pytestmark = [pytest.mark.docker, pytest.mark.slow, pytest.mark.timeout(7200, method="thread")]

POINTS = (
    "engine.kernel.before_turn_start",
    "engine.turn.after_acquire",
    "engine.turn.after_materialize",
    "engine.turn.after_skills",
    "engine.turn.after_restore",
    "engine.turn.after_autosave",
    "engine.turn.after_publish",
    "engine.turn.after_save_before_effect",
    "worker.tool.after_intent",
    "worker.tool.after_effect",
    "engine.turn.after_save_turn_end",
    "engine.turn.before_checkpoint",
    "engine.turn.after_checkpoint",
    "engine.kernel.before_turn_commit",
    "engine.kernel.after_turn_commit",
)
TERMINAL = {"completed", "failed", "cancelled"}
MAX_ATTEMPTS = 6
PARALLEL = int(os.environ.get("G4_PARALLEL", "6"))
# The gate runs opencode (the release image); G4_ENGINE=codex or claude_code (E9 DR-2) runs a smaller sample
# through the same kernel.
ENGINE = os.environ.get("G4_ENGINE", "opencode")


@dataclass(frozen=True)
class Scenario:
    name: str
    kind: str  # failpoint | worker_kill | sandbox_kill | node_outage
    point: str = ""
    nth: int = 1
    turns: int = 1
    crashes: int = 1
    delay_s: float = 0.0


@dataclass
class Result:
    scenario: Scenario
    run_id: str
    fired: int = 0
    attempts: list[dict[str, Any] | None] = field(default_factory=list[dict[str, Any] | None])
    status: str | None = None
    turns_completed: int = 0
    answers: int = 0
    snapshots_journaled: int = 0
    effects: dict[str, int] = field(default_factory=dict[str, int])
    leftover_sandboxes: int = 0
    failure: str | None = None

    @property
    def resumed(self) -> bool:
        return self.failure is None


SAMPLE_KILLS = {f"{kind}/{i}" for kind in ("worker_kill", "sandbox_kill") for i in (0, 2, 4, 6, 8)}


def prompt(run_id: str) -> str:
    return "\n".join(
        (
            f"G4 {run_id}",
            f"write notes/a.md draft {run_id}",
            f"write out/report.md report {run_id}",
            f"write notes/b.md more {run_id}",
            f'tool create {{"title": "{run_id}"}}',
            *((f"shell mkdir -p /tmp/.cache/g4 && echo {run_id} > /tmp/.cache/g4/state",) if snapshots_on() else ()),
            f'tool lookup {{"q": "{run_id}"}}',
            f"write out/final.md final {run_id}",
            f"say finished {run_id}",
        )
    )


def follow_up(run_id: str) -> str:
    return "\n".join(
        (
            f"write notes/c.md c {run_id}",
            f"write out/second.md second {run_id}",
            f"write notes/d.md d {run_id}",
            f'tool create {{"title": "{run_id}-2"}}',
            f"write out/third.md third {run_id}",
            f"say second {run_id}",
        )
    )


def scenarios() -> list[Scenario]:
    planned: list[Scenario] = []
    for point in POINTS:
        short = point.rsplit(".", 1)[1]
        planned += [
            Scenario(f"{short}/t1", "failpoint", point),
            Scenario(f"{short}/t2-first", "failpoint", point, turns=2),
            Scenario(f"{short}/t2-second", "failpoint", point, nth=2, turns=2),
            Scenario(f"{short}/twice", "failpoint", point, crashes=2),
        ]
    planned += [Scenario(f"worker_kill/{i}", "worker_kill", delay_s=2.0 + 0.75 * i, turns=1 + i % 2) for i in range(20)]
    planned += [Scenario(f"sandbox_kill/{i}", "sandbox_kill", delay_s=0.4 * i, turns=1 + i % 2) for i in range(20)]
    planned += [Scenario(f"node_outage/{i}", "node_outage", delay_s=1.0 + 2.0 * i) for i in range(6)]
    return planned


async def _sandbox_up(lane: DurableLane, run_id: str, timeout_s: float = 90) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if await asyncio.to_thread(lane.sandboxes, run_id):
            return True
        await asyncio.sleep(0.3)
    return False


async def _attempt(lane: DurableLane, sc: Scenario, result: Result, outage: asyncio.Lock) -> dict[str, Any] | None:
    run_id = result.run_id
    arms = None
    if sc.kind == "failpoint" and result.fired < sc.crashes:
        arms = {sc.point: {"action": "kill9", "trigger": {"nth": sc.nth}}}
    worker = await lane.worker(run_id, arms=arms)
    if sc.kind == "worker_kill" and result.fired < sc.crashes:
        await asyncio.sleep(sc.delay_s)
        if worker.process.returncode is None:
            kill(worker.process)
            result.fired += 1
    elif sc.kind == "sandbox_kill" and result.fired < sc.crashes:
        if await _sandbox_up(lane, run_id):
            await asyncio.sleep(sc.delay_s)
            if worker.process.returncode is None and await asyncio.to_thread(lane.kill_sandboxes, run_id):
                result.fired += 1
    elif sc.kind == "node_outage" and result.fired < sc.crashes:
        async with outage:
            await asyncio.sleep(sc.delay_s)
            if worker.process.returncode is None:
                lane.outage.down()
                await asyncio.sleep(LEASE_MS / 1000 + 4)
                lane.outage.up()
                result.fired += 1
    disposition = await worker.wait(600)
    if arms is not None and disposition is None:
        result.fired += 1
    if disposition is not None and disposition.get("status") == "timeout":
        (lane.workdir / f"{run_id}.timeout.log").write_text(worker.log())
    return disposition


async def drive(lane: DurableLane, index: int, sc: Scenario, outage: asyncio.Lock) -> Result:
    run_id = f"g4-{index:03d}"
    result = Result(sc, run_id)
    await lane.admit(run_id, prompt(run_id))
    if sc.turns == 2:
        await lane.steer(run_id, follow_up(run_id))
    for _ in range(MAX_ATTEMPTS):
        result.attempts.append(await _attempt(lane, sc, result, outage))
        run = await lane.until_claimable(run_id)
        result.status = run["status"]
        if result.status in TERMINAL:
            break
    events = await lane.events(run_id)
    types = Counter(e["type"] for e in events)
    result.turns_completed = types["turn.completed"]
    result.answers = types["x.pipeshub.answer.finalized"]
    result.snapshots_journaled = types["engine.snapshotted"]
    titles = [run_id] + ([f"{run_id}-2"] if sc.turns == 2 else [])
    result.effects = {t: lane.saas.effects[args_digest({"title": t})] for t in titles}
    await asyncio.sleep(1)
    result.leftover_sandboxes = len(await asyncio.to_thread(lane.sandboxes, run_id))
    result.failure = _failure(result, sc, types)
    return result


def _failure(result: Result, sc: Scenario, types: Counter[str]) -> str | None:
    if result.status != "completed":
        return f"run ended {result.status} after {len(result.attempts)} attempts"
    if result.turns_completed != sc.turns:
        return f"{result.turns_completed} turns completed of {sc.turns}"
    if types["run.completed"] != 1 or result.answers != 1:
        return f"run.completed={types['run.completed']} answer.finalized={result.answers}"
    if any(n > 1 for n in result.effects.values()):
        return f"duplicate effect {result.effects}"
    # An effect may be skipped only when its intent was settled `unknown` (the worker died inside it).
    if sum(1 for n in result.effects.values() if n == 0) > types["tool.unknown"]:
        return f"an asked effect never ran {result.effects} (tool.unknown={types['tool.unknown']})"
    if result.leftover_sandboxes:
        return f"{result.leftover_sandboxes} sandboxes left after completion"
    return None


async def test_g4_crash_matrix(tmp_path: Path) -> None:
    available, reason = lane_available(engine_image(ENGINE))
    if not available:
        pytest.skip(reason)
    planned = scenarios()
    sample = ENGINE != "opencode" or bool(os.environ.get("G4_SAMPLE"))
    if sample:
        # One crash per boundary in turn 1, and a few timed worker and sandbox kills.
        planned = [sc for sc in planned if sc.name.endswith("/t1") or sc.name in SAMPLE_KILLS]
    if only := os.environ.get("G4_ONLY"):
        planned = [sc for sc in planned if only in sc.name]
    report_path = Path(os.environ.get("G4_REPORT", str(tmp_path / "g4.json")))
    async with durable_lane(tmp_path / "lane", engine=ENGINE) as lane:
        gate = asyncio.Semaphore(PARALLEL)
        outage = asyncio.Lock()

        async def one(index: int, sc: Scenario) -> Result:
            async with gate:
                try:
                    return await drive(lane, index, sc, outage)
                except Exception as exc:  # the matrix reports every failure, it does not stop
                    return Result(sc, f"g4-{index:03d}", failure=f"lane error {type(exc).__name__}: {exc}")

        # Node outages hit every worker of the lane: they run alone, after the others.
        others = [(i, sc) for i, sc in enumerate(planned) if sc.kind != "node_outage"]
        outages = [(i, sc) for i, sc in enumerate(planned) if sc.kind == "node_outage"]
        results = list(await asyncio.gather(*(one(i, sc) for i, sc in others)))
        for i, sc in outages:
            results.append(await one(i, sc))
        duplicates = lane.saas.duplicates()

    fired = [r for r in results if r.fired > 0]
    crashes = sum(r.fired for r in results)
    resumed = sum(1 for r in fired if r.resumed)
    report = {
        "engine": ENGINE,
        "scheduled": len(results),
        "crashes": crashes,
        "runs_with_crash": len(fired),
        "resumed": resumed,
        "resume_rate": resumed / len(fired) if fired else 0.0,
        "duplicate_effects": duplicates,
        "not_fired": [r.scenario.name for r in results if r.fired == 0],
        "failures": [
            {"scenario": r.scenario.name, "run": r.run_id, "failure": r.failure, "attempts": r.attempts}
            for r in results
            if r.failure is not None
        ],
        "by_kind": {
            kind: {
                "crashes": sum(r.fired for r in results if r.scenario.kind == kind),
                "resumed": sum(1 for r in fired if r.scenario.kind == kind and r.resumed),
                "runs": sum(1 for r in fired if r.scenario.kind == kind),
            }
            for kind in ("failpoint", "worker_kill", "sandbox_kill", "node_outage")
        },
        "by_point": {
            point: {
                "crashes": sum(r.fired for r in results if r.scenario.point == point),
                "resumed": sum(1 for r in fired if r.scenario.point == point and r.resumed),
                "runs": sum(1 for r in fired if r.scenario.point == point),
            }
            for point in POINTS
        },
        # P5.9a: attempts whose sandbox came from the broker's warm pool (HARNESS_TEST_WARM_POOL).
        "warm_served": sum(int(a.get("warmServed", 0)) for r in results for a in r.attempts if a),
        # P5.9b: snapshots journaled, and what each attempt's controller logged (HARNESS_TEST_SNAPSHOTS).
        "snapshots": {
            "journaled": sum(r.snapshots_journaled for r in results),
            **{
                name.rsplit(".", 1)[1]: sum(int((a.get("snapshots") or {}).get(name, 0)) for r in results for a in r.attempts if a)
                for name in SNAPSHOT_EVENTS
            },
        },
        "effects_executed": {
            "exactly_once": sum(1 for r in results for n in r.effects.values() if n == 1),
            "not_executed": sum(1 for r in results for n in r.effects.values() if n == 0),
        },
        "results": [{**asdict(r), "scenario": asdict(r.scenario)} for r in results],
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=1, default=str))
    print(json.dumps({k: v for k, v in report.items() if k != "results"}, indent=1, default=str))
    if os.environ.get("G4_ONLY") or sample:
        assert duplicates == {}
        if os.environ.get("G4_SAMPLE"):
            assert resumed == len(fired), report["failures"]
        return
    assert crashes >= 100, report["not_fired"]
    assert duplicates == {}
    assert resumed >= 0.99 * len(fired), report["failures"]
