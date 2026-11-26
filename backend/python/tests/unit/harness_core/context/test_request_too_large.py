"""P4.9 L8-1..L8-5: on the compiler path a request the provider refuses as too large is reduced, journaled,
and sent again: older turns compacted first, then the per-result cap halved, then refused."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from harness_core.context.results import RESULT_CAP_SLOT
from harness_core.failpoints import Raise
from harness_core.kernel.core.messages import ToolMessage
from harness_core.kernel.durable import ToolExecution
from harness_core.kernel.durable.ports import REQUEST_TOO_LARGE, ModelFailed
from harness_core.testing import armed
from tests.unit.harness_core.durable.support import Crash, World, scripted

from .kernel_support import answered, compiler, looked_up

if TYPE_CHECKING:
    from harness_core.kernel.durable.ports import ModelCall, SampledTurn
    from harness_core.testing.durable import ScriptedModelPort


def _size(call: ModelCall) -> int:
    return len(call.system or "") + sum(len(str(m.content)) for m in call.messages)


class Window:
    """A provider that refuses any request over ``limit`` characters, as a context window does."""

    def __init__(self, port: ScriptedModelPort, limit: int) -> None:
        self.port = port
        self.limit = limit
        self.refused = 0
        self.sent: list[ModelCall] = []

    async def sample(self, call: ModelCall, on_delta: Any) -> SampledTurn:  # noqa: ANN401
        if _size(call) > self.limit:
            self.refused += 1
            raise ModelFailed(REQUEST_TOO_LARGE)
        self.sent.append(call)
        return await self.port.sample(call, on_delta)

    def restore(self, raw: bytes, *, provider: str, transport: str) -> Any:  # noqa: ANN401
        return self.port.restore(raw, provider=provider, transport=transport)


def _big_lookup(world: World, chars: int) -> None:
    async def lookup(args: Any, ctx: Any) -> ToolExecution:  # noqa: ANN401
        return ToolExecution(
            status="ok", content={"text": f"{args['q']}:" + "x" * chars + ":end"}
        )

    world.tools.add("lookup", lookup, tags=[("category", "read")])


async def _run(world: World, window: Window, **kernel: Any) -> Any:  # noqa: ANN401
    return await world.run(
        world.kernel(window, context=compiler(), sleep=_no_wait, **kernel)
    )  # type: ignore[arg-type]


async def _no_wait(seconds: float) -> None:
    del seconds


async def test_l8_1_older_turns_are_compacted_when_the_provider_refuses_the_request() -> (
    None
):
    world = World()
    _big_lookup(world, 4_000)
    world.admit(max_turns=12)
    _, port = scripted(*(looked_up(300, f"q{i}") for i in range(6)), answered())
    window = Window(port, limit=20_000)
    done = await _run(world, window)
    assert done.status == "completed"
    assert window.refused >= 1
    assert len(world.events("context.compacted")) >= 1
    assert all(_size(c) <= window.limit for c in window.sent)


async def test_l8_2_newest_results_alone_too_large_halve_the_cap_until_it_fits() -> (
    None
):
    world = World()
    _big_lookup(world, 40_000)
    world.admit(max_turns=6)
    _, port = scripted(looked_up(300, "only"), answered())
    window = Window(port, limit=15_000)
    done = await _run(world, window)
    assert done.status == "completed"
    caps = [
        e.data["slots"][RESULT_CAP_SLOT]
        for e in world.events("loop.state_saved")
        if RESULT_CAP_SLOT in e.data["slots"]
    ]
    assert caps and caps == sorted(caps, reverse=True)
    (result,) = [m for m in window.sent[-1].messages if isinstance(m, ToolMessage)]
    text = str(result.content)
    assert "only:" in text and ":end" in text, "both ends kept"
    # The cap in force is also the batch's share of the window (BUDGETEND), whichever is smaller.
    assert len(text) < 15_000
    assert world.events("context.compacted") == []


async def test_l8_3_a_request_too_large_at_the_floor_cap_fails_the_run() -> None:
    world = World()
    world.admit(text="y" * 30_000, max_turns=4)
    _, port = scripted(answered())
    window = Window(port, limit=10_000)
    done = await _run(world, window)
    assert done.status == "failed"
    assert world.events("run.failed")[0].data["errorType"] == REQUEST_TOO_LARGE
    assert window.sent == []


async def test_l8_4_a_resumed_attempt_sends_the_journaled_cap() -> None:
    world = World()
    _big_lookup(world, 40_000)
    world.admit(max_turns=6)
    _, port = scripted(looked_up(300, "only"), answered())
    window = Window(port, limit=15_000)
    with armed({"worker.context.after_cap_commit": Raise(Crash)}):
        await world.crash(world.kernel(window, context=compiler(), sleep=_no_wait))  # type: ignore[arg-type]
    # The model's lookup was journaled before the crash: the resumed attempt's model answers.
    _, again = scripted(answered())
    resumed = Window(again, limit=15_000)
    done = await _run(world, resumed)
    assert done.status == "completed"

    straight = World()
    _big_lookup(straight, 40_000)
    straight.admit(max_turns=6)
    _, port = scripted(looked_up(300, "only"), answered())
    uninterrupted = Window(port, limit=15_000)
    assert (await _run(straight, uninterrupted)).status == "completed"
    assert window.refused + resumed.refused == uninterrupted.refused, (
        "the journaled cap is not rediscovered"
    )
    assert _caps(world) == _caps(straight)


def _caps(world: World) -> list[int]:
    return [
        e.data["slots"][RESULT_CAP_SLOT]
        for e in world.events("loop.state_saved")
        if RESULT_CAP_SLOT in e.data["slots"]
    ]


async def test_l8_5_without_the_compiler_a_too_large_refusal_fails_as_before() -> None:
    world = World()
    world.admit(text="y" * 30_000, max_turns=4)
    _, port = scripted(answered())
    window = Window(port, limit=10_000)
    done = await world.run(world.kernel(window, sleep=_no_wait))  # type: ignore[arg-type]
    assert (done.status, window.refused) == ("failed", 1)
