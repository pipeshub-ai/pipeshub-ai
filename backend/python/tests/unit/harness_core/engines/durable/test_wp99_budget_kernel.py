"""WP99-PLAN-3, WP99-WAIT-2, WP99-SUCC: the engine kernel and controller read the run budget (ADR-RUN-01 D4), and a
run retired onto a declared successor version resumes there (ADR-OPS-01 D6, C14.02).
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from typing import TYPE_CHECKING, Any, cast

from harness_core.budget.run_budget import PersonWaits, RunBudget
from harness_core.contracts import dump_contract, validate_contract
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.engines.allowance import plan_words
from harness_core.engines.registry import AllowlistEngineRegistry
from harness_core.engines.types import EngineVersion, RunLimits
from harness_core.testing.clock import ManualClock
from harness_core.testing.engines.durable import (
    EngineWorld,
    ScriptedEngineAdapter,
    engine_profile,
    engine_run_spec,
)
from harness_core.testing.engines.fake_engine import FAKE_VERSION
from tests.unit.harness_core.engines.test_controller import Ticks, World, task

if TYPE_CHECKING:
    import pytest

RUN = "run-wp99"
RETIRED = EngineVersion(FAKE_VERSION.kind, "0.0.0-retired", "sha256:" + "e" * 64)
OLD_IMAGE = "sha256:" + "a" * 64
NEW_IMAGE = "sha256:" + "b" * 64


# ---- WP99-PLAN-3: the engine's brief carries its time plan ----


async def test_wp99_plan_3_the_engines_instructions_carry_its_time_plan() -> None:
    world = EngineWorld(adapter=ScriptedEngineAdapter())
    limits = RunLimits(max_steps=40, max_wall_s=600)
    world.admit(RUN, "say hello", spec=engine_run_spec(engine_profile(max_steps=40, max_wall_s=600)))
    assert (await world.attempt(RUN)).status == "completed"
    now = world.clock.now().timestamp()
    plan = RunBudget(1.0, 1_000_000, deadline_at_s=None).plan(now, limits, metered=False)
    assert plan is not None
    system = world.gateway.model_calls[0].system
    assert "Your allowance for this task is 40 model steps and about 10 minutes" in system
    assert plan_words(plan) in system


# ---- WP99-WAIT-2: a person's wait inside the turn pauses its working time ----


async def test_wp99_wait_2_an_approval_held_in_the_turn_does_not_spend_its_working_time() -> None:
    """A 60-second turn whose person decides for two minutes is not cut: the wait is not working time."""
    clock = ManualClock()
    ticks = Ticks(clock)
    world = World(clock=clock, sleep=ticks.sleep)
    waits = PersonWaits(clock.monotonic)
    run = world.run(waits=waits)
    run = dataclasses.replace(run, profile=dataclasses.replace(run.profile, limits=RunLimits(max_wall_s=60)))
    runner = asyncio.ensure_future(world.controller.run_turn(run, task(0, "hold\nsay done")))
    await asyncio.wait_for(world.gateway.holding.wait(), 5)
    await ticks.parked()
    decided = asyncio.Event()

    async def person() -> None:
        async with waits.waiting():
            await decided.wait()

    asking = asyncio.ensure_future(person())
    await asyncio.sleep(0)
    await ticks.advance(120)
    assert not runner.done(), "the person's two minutes were counted as the turn's working time"
    decided.set()
    await asking
    world.gateway.release()
    outcome = await asyncio.wait_for(runner, 5)
    assert outcome.reason == "completed"


# ---- WP99-SUCC: a retired version resumes on its declared successor ----


def _retired_spec(*, image_pin: str | None = None) -> RunSpec:
    profile = dataclasses.replace(engine_profile(), engine=RETIRED)
    wire = cast("dict[str, Any]", dump_contract(engine_run_spec(profile)))
    if image_pin is not None:
        wire["pins"] = {"sandboxImage": image_pin}
    return validate_contract(RunSpec, wire)


def _successors() -> AllowlistEngineRegistry:
    return AllowlistEngineRegistry(
        [ScriptedEngineAdapter()], [FAKE_VERSION], successors={RETIRED.ref: FAKE_VERSION}
    )


async def test_wp99_succ_1_without_a_successor_a_retired_versions_run_is_refused() -> None:
    world = EngineWorld()
    world.admit(RUN, "say hello", spec=_retired_spec())
    done = await world.attempt(RUN)
    assert (done.status, done.reason) == ("failed", "version_not_allowed")


async def test_wp99_succ_2_a_retired_versions_run_resumes_on_its_successor(caplog: pytest.LogCaptureFixture) -> None:
    """The kernel moves the pinned profile to the successor: the registry admits it exactly, and the old image's
    digest pin no longer holds the sandbox (the successor's image is another)."""
    world = EngineWorld()
    world.broker.image_digests[next(iter(world.config.images.values()))] = NEW_IMAGE
    world.admit(RUN, "say hello", spec=_retired_spec(image_pin=OLD_IMAGE))
    with caplog.at_level(logging.INFO):
        done = await world.attempt(RUN, kernel=world.kernel(successors=_successors()))
    assert done.status == "completed"
    moved = [r for r in caplog.records if getattr(r, "harness_event", "") == "engine.kernel.resumed_on_successor"]
    assert [r.harness["successor"] for r in moved] == [FAKE_VERSION.version]  # type: ignore[attr-defined]


async def test_wp99_succ_3_a_listed_version_runs_itself_and_keeps_its_pins() -> None:
    """A run of a version the release still lists is never moved: its image pin still holds the sandbox."""
    world = EngineWorld()
    world.broker.image_digests[next(iter(world.config.images.values()))] = NEW_IMAGE
    wire = cast("dict[str, Any]", dump_contract(engine_run_spec()))
    wire["pins"] = {"sandboxImage": OLD_IMAGE}
    world.admit(RUN, "say hello", spec=validate_contract(RunSpec, wire))
    done = await world.attempt(RUN, kernel=world.kernel(successors=_successors()))
    assert (done.status, done.reason) == ("failed", "image_digest_mismatch")
    assert _successors().resumed_on(FAKE_VERSION) is None
