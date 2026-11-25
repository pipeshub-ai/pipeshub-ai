"""R15-PUB (round 15: WP-70's writing reserve x WP-67's ``ph publish``, C3.12).

WP-70 refuses every gateway call once an engine turn reaches its writing reserve and tells the engine to write the
deliverable; ``ph publish`` is how an engine hands that deliverable over mid-turn. Refusing it there refuses the one
call the window asks for. A stopping turn (a stall) still refuses it, and every other call stays refused.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from typing import TYPE_CHECKING

from harness_core.engines.controller import (  # pyright: ignore[reportPrivateUsage]
    STOPPING_TEXT,
    _SavingEndpoint,
)
from harness_core.engines.publish_tool import ARTIFACT_PUBLISH_TOOL
from harness_core.engines.types import RunLimits
from harness_core.gateway.types import McpResult
from harness_core.testing.clock import ManualClock
from harness_core.testing.contracts.tool_endpoint import BINDING, claims_for
from tests.unit.harness_core.engines.test_controller import Ticks, World, task

if TYPE_CHECKING:
    from harness_core.engines.controller import EngineTurnOutcome

WALL_S = 600


async def _in_the_window(prompt: str) -> tuple[World, Ticks, asyncio.Future[EngineTurnOutcome]]:
    clock = ManualClock()
    ticks = Ticks(clock)
    world = World(clock=clock, sleep=ticks.sleep)
    run = world.run()
    run = dataclasses.replace(run, profile=dataclasses.replace(run.profile, limits=RunLimits(max_wall_s=WALL_S)))
    runner = asyncio.ensure_future(world.controller.run_turn(run, task(0, prompt)))
    await asyncio.wait_for(world.gateway.holding.wait(), 5)
    await ticks.parked()
    # Past the wall-time writing reserve (120 s of 600), short of the limit.
    await ticks.advance(WALL_S - 100)
    return world, ticks, runner


async def test_r15_pub_1_in_the_writing_window_the_deliverable_is_published_and_gathering_stays_refused() -> None:
    world, _, runner = await _in_the_window("write out/report.md # Findings\nhold\nsay report written")
    gathering = await world.host.call("lookup", {"q": "one more page"})
    assert gathering.is_error and gathering.disposition == "refused"
    assert gathering.text.startswith("Stop gathering"), gathering.text
    answer = await world.host.call(ARTIFACT_PUBLISH_TOOL, {"path": "report.md"})
    assert (answer.is_error, answer.disposition) == (False, "executed"), answer.text
    assert json.loads(answer.text)["lifecycle"] == "draft"
    world.gateway.release()
    outcome = await asyncio.wait_for(runner, 5)
    assert [p.path for p in outcome.published if p.path == "report.md"]


class _Inner:
    """The run's tool endpoint: nothing may reach it in these cases."""

    async def call_tool(self, *_: object, **__: object) -> McpResult:
        raise AssertionError("no tool of the grant runs")


def _endpoint(refusal: str | None, *, writing_note: str | None = None) -> tuple[_SavingEndpoint, list[str]]:
    published: list[str] = []
    endpoint = _SavingEndpoint(_Inner(), BINDING)  # type: ignore[arg-type]

    async def opening(tool: str) -> str | None:
        if refusal is not None:
            return refusal
        return None if tool == ARTIFACT_PUBLISH_TOOL else writing_note

    async def before() -> None:
        return None

    def publication(path: str) -> McpResult:
        published.append(path)
        return McpResult(json.dumps({"path": path, "lifecycle": "draft"}), is_error=False, disposition="executed",
                         retryable=False)

    endpoint.opening, endpoint.before, endpoint.publication = opening, before, publication
    return endpoint, published


async def test_r15_pub_2_a_stopping_turn_still_refuses_the_publish() -> None:
    endpoint, published = _endpoint(STOPPING_TEXT)
    answer = await endpoint.call_tool(claims_for(BINDING), ARTIFACT_PUBLISH_TOOL, {"path": "report.md"})
    assert (answer.is_error, answer.disposition, answer.text) == (True, "refused", STOPPING_TEXT)
    assert published == []


async def test_r15_pub_3_the_endpoint_asks_whether_a_publish_may_run_while_writing() -> None:
    endpoint, published = _endpoint(None, writing_note="Stop gathering: write it now.")
    answer = await endpoint.call_tool(claims_for(BINDING), ARTIFACT_PUBLISH_TOOL, {"path": "report.md"})
    assert answer.disposition == "executed" and published == ["report.md"]
    other = await endpoint.call_tool(claims_for(BINDING), "lookup", {"q": "x"})
    assert (other.disposition, other.text) == ("refused", "Stop gathering: write it now.")
