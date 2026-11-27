"""WP65-SINK-D1: engine output presented while the engine's gateway calls commit, on the real lane.

In a manual test, Codex's parallel ``ph`` calls committed their intents while a command's record was being written, and
the sink's next flush raised ``AssertionError``, ending the turn mid-research. Here every engine event is held
150 ms after it is presented (``engine.sink.presented``, the await a command record takes), and the engine makes a
batch of parallel effectful calls, so their commits cut the sink's batch while lines are being presented. The run
completes with each call's effect once and every step on the journal. ``E1_ENGINE=codex`` runs it on Codex.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from typing import TYPE_CHECKING

import pytest

from tests.unit.harness.engines.durable_stack import durable_lane
from tests.unit.harness.engines.real_stack import engine_image, lane_available

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.docker, pytest.mark.timeout(900, method="thread")]
ENGINE = os.environ.get("E1_ENGINE", "opencode")
CALLS = 8
SCRIPT = "\n".join(
    [
        "SINKRACE",
        "tools create " + json.dumps([{"title": f"t{n}"} for n in range(CALLS)]),
        "tools create " + json.dumps([{"title": f"u{n}"} for n in range(CALLS)]),
        "say gathered",
    ]
)
HELD = {"engine.sink.presented": {"action": "delay", "ms": 150, "trigger": "always"}}


@pytest.fixture(autouse=True)
def _available() -> None:
    available, reason = lane_available(engine_image(ENGINE))
    if not available:
        pytest.skip(reason)


async def test_wp65_sink_d1_parallel_calls_while_events_are_presented_complete_the_run(tmp_path: Path) -> None:
    async with durable_lane(tmp_path, engine=ENGINE) as lane:
        await lane.admit("run-sink", SCRIPT)
        worker = await lane.worker("run-sink", arms=HELD)
        result = await worker.wait(600)
        log = worker.log()
        assert "AssertionError" not in log and "engine.kernel.error" not in log, log[-4000:]
        assert result is not None and result["status"] == "completed", (result, log[-4000:])
        events = await lane.events("run-sink")
        types = Counter(e["type"] for e in events)
        assert (types["tool.intent"], types["tool.outcome"]) == (2 * CALLS, 2 * CALLS)
        assert lane.saas.effects.total() == 2 * CALLS
        assert types["x.pipeshub.engine.progress"] >= 2 * CALLS, "the steps shown while calls committed are journaled"
        assert types["run.completed"] == 1
