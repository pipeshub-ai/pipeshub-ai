"""SLASH2 C1 ``/stop``, the workers' half: Node journals the same cancel the Stop button does (Node's
``slash2-c1-run-commands-conformance.test.ts``); the loop kernel and the engine kernel (opencode, Codex and
Claude Code runs) each end the run ``cancelled`` with exactly one ``run.cancelled``. Each engine adapter's own
interrupt is the engine contract's cf8, which every adapter lane runs."""

from __future__ import annotations

import asyncio

from harness_core.testing.engines.durable import EngineWorld

from .support import RUN, World
from .test_ux3_prompt_cancel import (  # pyright: ignore[reportPrivateUsage]
    SAFETY_S,
    _HangingModel,
    _Wake,
)


async def test_slash2_c1_stop_mid_turn_on_the_loop() -> None:
    world = World()
    world.admit()
    model, wake = _HangingModel(), _Wake()
    running = asyncio.create_task(world.run(world.kernel(model, cancel_wakeup=wake)))  # type: ignore[arg-type]
    await asyncio.wait_for(model.started.wait(), SAFETY_S)
    world.journal.request_cancel(RUN)
    wake.fired.set()
    assert (await asyncio.wait_for(running, SAFETY_S)).status == "cancelled"
    assert len(world.events("run.cancelled")) == 1


async def test_slash2_c1_stop_mid_turn_on_an_engine() -> None:
    world = EngineWorld()
    world.admit("run-slash2", "hold\nsay never")
    attempt = asyncio.ensure_future(world.attempt("run-slash2"))
    await asyncio.wait_for(world.gateway.holding.wait(), SAFETY_S)
    world.journal.request_cancel("run-slash2")
    world.wakeup.wake("run-slash2")
    disposition = await asyncio.wait_for(attempt, SAFETY_S)
    world.gateway.release()
    assert disposition.status == "cancelled"
    assert world.types("run-slash2").count("run.cancelled") == 1
