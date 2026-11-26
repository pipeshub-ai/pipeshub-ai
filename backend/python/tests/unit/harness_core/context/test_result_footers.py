"""P4.9 RF-1..RF-3: the step footers a compiled request showed on results are journaled when new and
the session fold collects every run's, so a later run repeats an earlier run's results byte for byte."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from harness_core.context.results import RESULT_FOOTERS_SLOT
from harness_core.kernel.core.messages import ToolMessage
from harness_core.kernel.fold import FOLD_VERSION, Checkpoint, checkpoint_is_valid
from tests.unit.harness_core.durable.support import World, scripted

from .kernel_support import Host, answered, compiler, dump, looked_up

if TYPE_CHECKING:
    from harness_core.context.types import HostBands
    from harness_core.kernel.durable.ports import ModelCall
    from harness_core.kernel.fold import RunState


@dataclass
class FootingHost(Host):
    """``Host`` with a step footer on each of this run's results, as PipesHub's host gives them."""

    async def bands(self, call: ModelCall, turn: int, state: RunState) -> HostBands:
        bands = await super().bands(call, turn, state)
        footers = {
            inv.call_id: f"[loop: step {inv.turn}]"
            for inv in state.invocations.values()
            if inv.state == "completed"
        }
        return replace(bands, result_footers=footers)


async def _run(world: World, host: Host, run_id: str, *replies: object, text: str = "look it up") -> list[ModelCall]:
    world.admit(run_id, text=text, max_turns=12)
    _, port = scripted(*replies)  # type: ignore[arg-type]
    assert (await world.run(world.kernel(port, context=compiler(host)), run_id)).status == "completed"
    return port.calls


def _history(call: ModelCall) -> list[str]:
    """The request without its tail (the host's per-turn text, always last)."""
    return dump(call.messages[:-1])


async def test_rf1_rf2_an_earlier_runs_results_keep_their_footers_in_a_later_run() -> None:
    world = World()
    host = FootingHost()
    first = await _run(world, host, "run-1", looked_up(300, "a"), answered())
    second = await _run(world, host, "run-2", answered(), text="thanks")
    footed = [m for m in first[-1].messages if isinstance(m, ToolMessage)]
    assert footed and all(m.step_footer for m in footed)
    again = [m for m in second[0].messages if isinstance(m, ToolMessage)]
    assert [(m.tool_call_id, m.step_footer) for m in again] == [(m.tool_call_id, m.step_footer) for m in footed]
    assert _history(second[0])[: len(_history(first[-1]))] == _history(first[-1]), "the cache prefix survives"
    saved = [
        e.data["slots"][RESULT_FOOTERS_SLOT]
        for e in world.events("loop.state_saved")
        if RESULT_FOOTERS_SLOT in e.data["slots"]
    ]
    assert saved == [{m.tool_call_id: m.step_footer for m in footed}], "journaled once, when new"


@dataclass
class OnceFootingHost(FootingHost):
    """Adds a note to the newest result's footer for one request only, as the deadline warning is given."""

    async def bands(self, call: ModelCall, turn: int, state: RunState) -> HostBands:
        bands = await super().bands(call, turn, state)
        if turn != 2:
            return bands
        newest = call.messages[-1]
        assert isinstance(newest, ToolMessage) and newest.tool_call_id
        noted = {**bands.result_footers, newest.tool_call_id: bands.result_footers[newest.tool_call_id] + " [note]"}
        return replace(bands, result_footers=noted)


async def test_rf4_sync2_f2_a_footer_once_shown_stays_in_later_requests() -> None:
    calls = await _run(World(), OnceFootingHost(), "run-1", looked_up(300, "a"), looked_up(300, "b"), answered())
    footers = [[m.step_footer for m in c.messages if isinstance(m, ToolMessage)] for c in calls]
    assert footers[1] == ["[loop: step 1] [note]"]
    assert footers[2] == ["[loop: step 1] [note]", "[loop: step 2]"], "the history stays append-only"


def test_rf3_a_checkpoint_without_the_journaled_footers_is_refolded() -> None:
    checkpoint = Checkpoint(
        run_id="run-1",
        stream_id="ses1",
        fold_version=FOLD_VERSION,
        seq=3,
        state={
            "run": {"model_route": None, "invocations": {}},
            "session": {"compactions": [], "reduced_at_seq": 0, "usage_includes_cached": True},
        },
    )
    assert not checkpoint_is_valid(checkpoint, run_id="run-1", head_seq=3, ref="r", referenced_ref="r")
    session = {"compactions": [], "reduced_at_seq": 0, "result_footers": {}, "usage_includes_cached": True, "tool_lists": {}}
    whole = checkpoint.model_copy(update={"state": {**checkpoint.state, "session": session}})
    assert checkpoint_is_valid(whole, run_id="run-1", head_seq=3, ref="r", referenced_ref="r")
