"""R43MCP-FU-5: what a shaper records about the request it built is journaled with that turn's response.

The PipesHub loop builds its system prompt in ``TurnShaper.shape``, outside the journal's ``contextDigest``; nothing a
shaper knew about the request reached the journal, so a deployment could not show what its model was told. A key the
shaper adds to ``ModelCall.slots`` now rides the turn's commit as ``loop.state_saved``; an unchanged value is not
journaled again, and a resumed attempt that shows the same request journals nothing new.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from harness_core.failpoints import Arm, Nth, Raise
from harness_core.kernel.durable import ToolExecution
from harness_core.testing import armed

from .support import Crash, World, reply, scripted

if TYPE_CHECKING:
    from harness_core.kernel.durable.ports import ModelCall
    from harness_core.kernel.fold import RunState

SLOT = "prompt.shown"


@dataclass
class RecordingShaper:
    """Records the stable part of each request's prompt; ``moves`` names the turns whose prompt changed."""

    moves: frozenset[int] = frozenset()

    async def shape(self, call: ModelCall, turn: int, state: RunState) -> ModelCall:
        version = sum(1 for t in self.moves if t <= turn)
        return dataclasses.replace(call, system=f"prompt v{version}", slots={**call.slots, SLOT: {"v": version}})

    async def enter(self, turn: int, state: RunState) -> None:
        return None


async def _lookup(args: Any, ctx: Any) -> ToolExecution:  # noqa: ANN401
    return ToolExecution(status="ok", content={"text": "found"})


def _world() -> World:
    world = World()
    world.tools.add("lookup", _lookup, tags=[("category", "read")])
    world.admit(text="look it up")
    return world


def _saved(world: World) -> list[object]:
    return [e.data["slots"][SLOT] for e in world.events("loop.state_saved") if SLOT in e.data["slots"]]


async def test_a_shapers_slot_is_journaled_with_the_first_response_and_only_again_when_it_changes() -> None:
    world = _world()
    _, port = scripted(reply("", ("lookup", {})), reply("", ("lookup", {})), reply("done"))
    shaper = RecordingShaper(moves=frozenset({3}))
    assert (await world.run(world.kernel(port, shaper=shaper))).status == "completed"
    assert _saved(world) == [{"v": 0}, {"v": 1}]
    request = next(i for i, e in enumerate(world.events()) if e.type == "model.request")
    saved = next(i for i, e in enumerate(world.events()) if e.type == "loop.state_saved" and SLOT in e.data["slots"])
    assert saved < request, "journaled in the commit that holds the turn's request"


async def test_a_resumed_attempt_that_shows_the_same_request_journals_nothing_new() -> None:
    world = _world()
    _, port = scripted(reply("", ("lookup", {})), reply("", ("lookup", {})), reply("done"))
    with armed({"worker.model.after_response_commit": Arm(Raise(Crash), Nth(2))}):
        await world.crash(world.kernel(port, shaper=RecordingShaper()))
    assert (await world.run(world.kernel(port, shaper=RecordingShaper()))).status == "completed"
    assert _saved(world) == [{"v": 0}]


async def test_a_shaper_that_adds_nothing_journals_nothing() -> None:
    @dataclass
    class Plain:
        async def shape(self, call: ModelCall, turn: int, state: RunState) -> ModelCall:
            return dataclasses.replace(call, system="p")

        async def enter(self, turn: int, state: RunState) -> None:
            return None

    world = _world()
    _, port = scripted(reply("done"))
    assert (await world.run(world.kernel(port, shaper=Plain()))).status == "completed"
    assert world.events("loop.state_saved") == []
