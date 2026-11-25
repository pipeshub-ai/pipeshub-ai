"""RT-1..RT-5: journal round trips on a turn's critical path (P1a gate, B-2).

Every commit and read is an HTTP round trip to Node and a Mongo transaction or
query, so the kernel reads only what a commit receipt cannot tell it, and the
first attempt's capability record rides with the run's first commit.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.failpoints import Raise
from harness_core.journal import InMemoryLiveBus
from harness_core.testing import armed

from .support import RUN, Crash, World, reply, scripted

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pydantic import JsonValue

    from harness_core.journal import JournalEvent

OBSERVED = {"mcp:inst-1": "sha256:" + "a" * 64}


class _Observed:
    def __init__(self, value: dict[str, JsonValue]) -> None:
        self.value = value

    async def observe(self) -> Mapping[str, JsonValue]:
        return dict(self.value)


class _CountingWorld(World):
    reads: int = 0

    def __post_init__(self) -> None:
        super().__post_init__()
        read = self.journal.read

        async def counted(
            stream_id: str, *, after_seq: int = 0, limit: int | None = None
        ) -> list[JournalEvent]:
            self.reads += 1
            return await read(stream_id, after_seq=after_seq, limit=limit)

        self.journal.read = counted  # type: ignore[method-assign]


def _world() -> _CountingWorld:
    world = _CountingWorld()
    world.admit()
    return world


async def test_rt1_a_read_tool_turn_reads_the_journal_only_to_load() -> None:
    world = _world()
    _, port = scripted(reply("", ("lookup", {"q": "x"})), reply("found it"))
    done = await world.run(world.kernel(port, capability_check=_Observed(OBSERVED)))
    assert done.status == "completed"
    assert world.reads == 1


class _Restore:
    def __init__(self) -> None:
        self.events: list[JournalEvent] = []

    async def restore(self, events: Sequence[JournalEvent]) -> None:
        self.events = list(events)


async def test_rt1_the_restore_reads_the_events_the_load_folded() -> None:
    """A run's in-process state is rebuilt from this run's events without a
    second read of the stream."""
    world = _world()
    world.admit("run0", text="an earlier run")
    _, earlier = scripted(reply("earlier answer"))
    _ = await world.run(world.kernel(earlier), "run0")
    _, port = scripted(reply("", ("lookup", {"q": "x"})), reply("found it"))
    restore = _Restore()
    world.reads = 0
    done = await world.run(world.kernel(port, restore=restore))
    assert done.status == "completed"
    assert world.reads == 1
    assert restore.events and {e.run_id for e in restore.events} == {RUN}
    assert [e.type for e in restore.events][:2] == ["run.admitted", "run.claimed"]


async def test_rt2_the_capability_record_rides_with_the_first_commit() -> None:
    world = _world()
    _, port = scripted(reply("hello"))
    _ = await world.run(world.kernel(port, capability_check=_Observed(OBSERVED)))
    commits = world.journal.commits(RUN)
    assert not any(":capabilities:" in command for command, _ in commits)
    first_command, first_types = commits[0]
    assert ":model:" in first_command
    assert first_types[0] == "loop.state_saved"
    (saved,) = world.events("loop.state_saved")
    assert saved.data == {"slots": {"capabilities.observed": OBSERVED}}


async def test_rt3_a_crash_before_the_first_commit_leaves_no_capability_record() -> None:
    """Nothing ran on the first observation, so the next attempt pins its own."""
    world = _world()
    _, port = scripted(reply("first"), reply("second"))
    with armed({"worker.model.before_response_commit": Raise(Crash)}):
        await world.crash(world.kernel(port, capability_check=_Observed(OBSERVED)))
    assert world.events("loop.state_saved") == []
    changed = {"mcp:inst-1": "sha256:" + "b" * 64}
    done = await world.run(world.kernel(port, capability_check=_Observed(changed)))
    assert done.status == "completed"
    (saved,) = world.events("loop.state_saved")
    assert saved.data == {"slots": {"capabilities.observed": changed}}


async def test_rt4_a_node_event_that_lands_before_a_commit_is_folded_by_it() -> None:
    """A cancel journaled while a read tool runs is seen when its outcomes
    commit: the receipt leaves a gap, and the gap is read."""
    world = _world()
    world.on_read = lambda w: w.journal.request_cancel(RUN)
    _, port = scripted(reply("", ("lookup", {"q": "x"})), reply("never sampled"))
    cancelled = await world.run(world.kernel(port))
    assert cancelled.status == "cancelled"
    assert len(port.calls) == 1


async def test_rt5_the_first_delta_of_a_sample_is_published_at_once() -> None:
    """Coalescing starts after the first frame: time to first token never
    waits out a window."""
    world = _world()
    bus = InMemoryLiveBus()
    world.live = bus  # type: ignore[assignment]
    _, port = scripted(reply("hello there"))
    _ = await world.run(world.kernel(port, live_window_s=60.0))
    frames = await bus.read(RUN)
    assert [(f.n, f.kind) for f in frames] == [(1, "text_delta"), (2, "text_delta")]
    assert frames[0].data["text"] != "hello there"
    assert "".join(str(f.data["text"]) for f in frames) == "hello there"
