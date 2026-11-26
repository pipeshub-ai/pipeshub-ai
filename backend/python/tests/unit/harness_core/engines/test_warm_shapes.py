"""ST-2 (P5.9a): the pool pre-creates exactly what an engine turn acquires, and the turn is served warm."""

from __future__ import annotations

from harness_core.engines.controller import (
    EngineControllerConfig,
    engine_sandbox_spec,
    engine_warm_shapes,
)
from harness_core.sandbox.pool import WarmPoolConfig, shape_of
from harness_core.sandbox.types import MountSpec, Resources, RuntimeMode
from harness_core.testing.ids import SeededIds
from tests.unit.harness_core.engines.test_controller import (
    IMAGE,
    RUN,
    RecordingBroker,
    World,
    task,
)


def test_st2_the_shape_of_every_turn_s_spec_is_the_pool_s() -> None:
    config = EngineControllerConfig(images={}, resources=Resources(cpus=1.0, memory_mb=1024, pids=256, scratch_mb=256))
    (shape,) = engine_warm_shapes(config, ["pxh-opencode-tools:1.18.34"], RuntimeMode.DOCKER_HARDENED)
    for attempt in ("a1", "a2"):
        spec = engine_sandbox_spec(
            config,
            image="pxh-opencode-tools:1.18.34",
            mode=RuntimeMode.DOCKER_HARDENED,
            workspace=MountSpec(config.workspace_root),
            labels={"runId": RUN, "attemptId": attempt},
            placeholders=(f"pxh-model-{attempt}-0123456789", f"pxh-tools-{attempt}-0123456789"),
            tools_upstream=f"http://worker-{attempt}:8098/x",
        )
        assert shape_of(spec) == shape
    assert len(engine_warm_shapes(config, ["a:1", "b:2", "a:1"], RuntimeMode.DOCKER_HARDENED)) == 2


async def test_st2_an_engine_turn_on_a_pooled_broker_is_served_warm_with_a_scratch_workspace() -> None:
    world = World()
    shapes = engine_warm_shapes(world.config, [IMAGE], RuntimeMode.GVISOR)
    world.broker = RecordingBroker(
        clock=world.clock,
        ids=SeededIds(),
        modes=world.modes,
        egress_proxy=world.proxy,
        persistent_mounts=True,
        warm_pool=WarmPoolConfig(ceiling=1, shapes=shapes),
    )
    world.controller = world.make_controller()
    assert await world.broker.fill_warm() == 1
    (warm_id,) = world.broker.warm_ids()
    outcome = await world.controller.run_turn(world.run(), task(0, "say done"))
    assert outcome.reason == "completed"
    [(spec, _)] = world.broker.acquired
    assert all(m.source is None for m in spec.mounts), "a pooled broker got the volume strategy"
    assert ("acquire_warm", warm_id, ()) in world.broker.calls
    assert world.broker.released == [warm_id]
    await world.broker.pool.aclose()
