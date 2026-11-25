"""WP65-CANCEL-D1: a cancel reaches a durable engine turn that is still waiting before its engine opens.

In a manual test, a Claude Code turn waiting for its person's sign-in ignored the cancel for about 13 minutes and ended
``failed``/``signin_timeout``. Here the turn holds two minutes after its sandbox is acquired
(``engine.turn.after_acquire``, before the engine opens, where a sign-in waits), on the real lane: Node's journal, the
worker's kernel, the Docker broker. Cancelled there, the run ends ``cancelled`` within seconds, with its sandbox gone.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import TYPE_CHECKING

import pytest

from tests.unit.harness.engines.durable_stack import durable_lane
from tests.unit.harness.engines.real_stack import engine_image, lane_available

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.docker, pytest.mark.timeout(900, method="thread")]
ENGINE = os.environ.get("E1_ENGINE", "opencode")
HOLD_MS = 120_000
HELD = {"engine.turn.after_acquire": {"action": "delay", "ms": HOLD_MS}}


@pytest.fixture(autouse=True)
def _available() -> None:
    available, reason = lane_available(engine_image(ENGINE))
    if not available:
        pytest.skip(reason)


async def test_wp65_cancel_d1_a_turn_held_before_its_engine_opens_ends_cancelled_at_once(tmp_path: Path) -> None:
    async with durable_lane(tmp_path, engine=ENGINE) as lane:
        await lane.admit("run-cancel", "write out/a.md never\nsay never")
        worker = await lane.worker("run-cancel", arms=HELD)
        deadline = time.monotonic() + 120
        while not lane.sandboxes("run-cancel"):
            assert time.monotonic() < deadline, worker.log()[-4000:]
            await asyncio.sleep(0.5)
        cancelled_at = time.monotonic()
        _ = (await lane.http.post("/cancel", json={"runId": "run-cancel"})).raise_for_status()
        result = await worker.wait(HOLD_MS / 1000 + 120)
        took = time.monotonic() - cancelled_at
        assert result is not None and result["status"] == "cancelled", (result, worker.log()[-4000:])
        # wall-clock-ok: the hold is 120 s; a cancel that reaches the held turn ends it in seconds
        assert took < 30, f"the cancel took {took:.0f} s: it waited out the hold"
        assert (await lane.run("run-cancel"))["status"] == "cancelled"
        assert lane.sandboxes("run-cancel") == []
        assert "engine.session.opened" not in worker.log(), "the engine never opened"
