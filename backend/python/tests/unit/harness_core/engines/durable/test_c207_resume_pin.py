"""SL-14 (C2.07, C2.20) through the engine kernel: a run's spec pins its image digest; every attempt of the run
(a resume after the worker died) is held to that same digest, and an attempt that finds the image rebuilt fails the
run with ``engine_image_changed`` instead of running it on an image it was not admitted under."""

from __future__ import annotations

from harness_core.contracts.generated.run_spec import Pins, RunSpec
from harness_core.testing.engines.durable import IMAGE, EngineWorld, engine_run_spec
from harness_core.testing.engines.fake_engine import FAKE_VERSION

RUN = "run-pin"
PINNED = "sha256:" + "1" * 64
REBUILT = "sha256:" + "2" * 64


def _pinned_spec() -> RunSpec:
    return engine_run_spec().model_copy(update={"pins": Pins(sandbox_image=PINNED)})


def _world(digest: str) -> EngineWorld:
    world = EngineWorld()
    world.broker.image_digests[IMAGE] = digest
    world.broker.engine_images[FAKE_VERSION.ref] = digest
    return world


async def test_sl14_the_pinned_image_runs_and_every_sandbox_is_held_to_it() -> None:
    world = _world(PINNED)
    world.admit(RUN, "say done", spec=_pinned_spec())
    assert (await world.attempt(RUN)).status == "completed"
    assert world.broker.acquisitions == 1
    assert {state.spec.image_pin for state in world.broker.sandboxes.values()} <= {PINNED}


async def test_sl14_a_resume_after_the_image_was_rebuilt_fails_the_run_naming_why() -> None:
    world = _world(PINNED)
    world.admit(RUN, "say done", spec=_pinned_spec())
    # The image is rebuilt before the run's attempt runs (as after a crash and a redeploy).
    world.broker.image_digests[IMAGE] = REBUILT
    world.broker.engine_images[FAKE_VERSION.ref] = REBUILT
    outcome = await world.attempt(RUN)
    assert (outcome.status, outcome.reason) == ("failed", "engine_image_changed"), outcome
    [failed] = [e for e in world.events(RUN) if e.type == "run.failed"]
    assert failed.data.get("errorType") == "engine_image_changed"
    assert world.broker.acquisitions == 0
