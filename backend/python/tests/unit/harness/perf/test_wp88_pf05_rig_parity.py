"""WP88-PF05-1, -4 (C7.04, DP8-5): PF-05's rig runs the engine controller as a worker does, and Node as it ships.

The unit-test world ticks every turn and sleeps every controller wait for 1 ms, so a test does not wait on a
worker's seconds. Under the gate that was ten runs' tickers waking the event loop the gateway shares about four
thousand times a second, which no worker does (its ticker runs every ``TICK_S``): the measured overhead carried
the rig's own wake-ups.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING

from harness_core.engines.controller import TICK_S, EngineControllerConfig
from harness_core.testing.engines.durable import EngineWorld
from tests.unit.harness.engines.p9_lane import journal_server_env, shipped_node_flags
from tests.unit.harness.perf.tool_gateway import Pace, rig_world

if TYPE_CHECKING:
    from pathlib import Path


async def _handle(_request: object) -> object:
    return {}


def test_wp88_pf05_1_the_rig_ticks_and_sleeps_as_the_worker_does() -> None:
    world = rig_world(SimpleNamespace(base="http://gateway.test", handle=_handle), Pace(rate_per_s=1.0))  # type: ignore[arg-type]
    assert world.config.tick_s == TICK_S == EngineControllerConfig(images={}).tick_s
    assert world.controller_sleep is asyncio.sleep


def test_wp88_pf05_1_a_unit_test_world_still_ticks_fast() -> None:
    world = EngineWorld()
    assert world.config.tick_s == 0.001
    assert world.controller_sleep is None


def test_wp88_pf05_4_the_journal_server_runs_node_with_the_flags_the_image_ships(tmp_path: Path) -> None:
    flags = shipped_node_flags()
    assert "--experimental-async-context-frame" in flags.split()
    env = journal_server_env("127.0.0.1", 1, "mongodb://unused", "pxh_unused", tmp_path)
    assert set(flags.split()) <= set(env["NODE_OPTIONS"].split())
