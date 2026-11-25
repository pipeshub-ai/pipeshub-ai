"""Contract suite for the command-output port (UX5 CO, BP).

Subclass :class:`CommandActivityContract` and provide a ``bus`` fixture: a ``LiveBusPort`` the activity publishes to
and the test reads back. The in-memory bus runs it in ``tests/unit/harness_core/activity``; the Redis Streams bus
(the activity key) in ``tests/unit/harness/test_ux5_activity_bus.py``.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from harness_core.activity import ActivityLimits, BoundedCommandActivity, CommandSpec
from harness_core.journal.memory import InMemoryContentStore
from harness_core.testing.clock import ManualClock

if TYPE_CHECKING:
    from harness_core.journal.ports import LiveBusPort
    from harness_core.journal.types import LiveFrame

RUN = "run-ux5"


class GatedBus:
    """A bus whose publishes wait for the test to open it: a slow Redis, deterministically."""

    def __init__(self, inner: LiveBusPort) -> None:
        self.inner = inner
        self.open = asyncio.Event()

    async def publish(self, frame: LiveFrame) -> None:
        _ = await self.open.wait()
        await self.inner.publish(frame)


class FailingBus:
    def __init__(self) -> None:
        self.calls = 0

    async def publish(self, frame: LiveFrame) -> None:
        self.calls += 1
        raise ConnectionError("down")


def spec(command_id: str = "inv-1", command: str = "python report.py") -> CommandSpec:
    return CommandSpec(command_id=command_id, source="run_code", command=command, invocation_id=command_id, language="python")


def outputs(frames: list[LiveFrame], command_id: str, stream: str = "stdout") -> str:
    return "".join(
        str(f.data.get("text", ""))
        for f in frames
        if f.kind == "command_output" and f.data["commandId"] == command_id and f.data["stream"] == stream
    )


class CommandActivityContract:
    @pytest.fixture
    def clock(self) -> ManualClock:
        return ManualClock()

    @pytest.fixture
    def content(self) -> InMemoryContentStore:
        return InMemoryContentStore()

    def activity(
        self,
        bus: object,
        clock: ManualClock,
        content: InMemoryContentStore | None = None,
        limits: ActivityLimits | None = None,
    ) -> BoundedCommandActivity:
        return BoundedCommandActivity(
            bus,  # pyright: ignore[reportArgumentType]
            run_id=RUN,
            attempt_id="att-1",
            fence=3,
            clock=clock,
            content=content,
            limits=limits,
        )

    async def test_co1_frames_in_order_and_a_full_record(
        self, bus: LiveBusPort, clock: ManualClock, content: InMemoryContentStore
    ) -> None:
        activity = self.activity(bus, clock, content)
        recorder = activity.begin(spec())
        recorder.write("stdout", "line one\n")
        recorder.write("stderr", "warning: slow\n")
        recorder.write("stdout", "line two\n")
        _ = clock.advance(1.25)
        record = await recorder.finish(exit_code=0, status="completed")
        await activity.drained()
        frames = await bus.read(RUN)
        assert next(f.kind for f in frames) == "command_started"
        assert frames[-1].kind == "command_finished"
        assert frames[-1].data == {"commandId": "inv-1", "status": "completed", "exitCode": 0, "durationMs": 1250}
        assert outputs(frames, "inv-1") == "line one\nline two\n"
        assert outputs(frames, "inv-1", "stderr") == "warning: slow\n"
        assert all(f.fence == 3 for f in frames)
        assert frames[0].data["attemptId"] == "att-1"
        assert record.exit_code == 0
        assert record.duration_ms == 1250
        assert record.stdout.tail == "line one\nline two\n"
        assert record.stdout.bytes == len("line one\nline two\n")
        assert record.stderr.tail == "warning: slow\n"
        assert record.started_at_ms == int(clock.now().timestamp() * 1000) - 1250
        await activity.aclose()

    async def test_co2_tails_keep_the_newest_output(self, bus: LiveBusPort, clock: ManualClock) -> None:
        activity = self.activity(bus, clock, limits=ActivityLimits(tail_chars=10))
        recorder = activity.begin(spec())
        recorder.write("stdout", "".join(f"{i}\n" for i in range(20)))
        record = await recorder.finish(exit_code=0, status="completed")
        assert record.stdout.tail == "15\n16\n17\n18\n19\n"[-10:]
        assert record.stdout.truncated
        assert record.stdout.bytes == sum(len(f"{i}\n") for i in range(20))
        assert not record.stderr.truncated
        await activity.aclose()

    async def test_co3_full_log_stored_once_interleaved_and_bounded(
        self, bus: LiveBusPort, clock: ManualClock, content: InMemoryContentStore
    ) -> None:
        activity = self.activity(bus, clock, content, ActivityLimits(log_bytes=12))
        recorder = activity.begin(spec())
        recorder.write("stdout", "abc\n")
        recorder.write("stderr", "err\n")
        recorder.write("stdout", "defghij\n")
        record = await recorder.finish(exit_code=1, status="failed")
        assert record.log is not None
        assert await content.get(record.log) == b"abc\nerr\ndefg"
        assert record.log_truncated
        assert await recorder.finish(exit_code=1, status="failed") is record
        await activity.aclose()

    async def test_co4_an_unfinished_command_raises_nothing(self, bus: LiveBusPort, clock: ManualClock) -> None:
        activity = self.activity(bus, clock)
        recorder = activity.begin(spec())
        recorder.write("stdout", "partial\n")
        await activity.aclose()
        frames = await bus.read(RUN)
        assert [f.kind for f in frames] == ["command_started", "command_output"]

    async def test_co5_parallel_commands_stay_apart(self, bus: LiveBusPort, clock: ManualClock) -> None:
        activity = self.activity(bus, clock)
        first, second = activity.begin(spec("inv-a")), activity.begin(spec("inv-b"))
        for i in range(5):
            first.write("stdout", f"a{i}\n")
            second.write("stdout", f"b{i}\n")
        a, b = await first.finish(exit_code=0, status="completed"), await second.finish(exit_code=2, status="failed")
        await activity.drained()
        frames = await bus.read(RUN)
        assert outputs(frames, "inv-a") == "".join(f"a{i}\n" for i in range(5))
        assert outputs(frames, "inv-b") == "".join(f"b{i}\n" for i in range(5))
        assert "b" not in a.stdout.tail
        assert "a" not in b.stdout.tail
        await activity.aclose()

    async def test_co6_frames_numbered_in_publish_order(self, bus: LiveBusPort, clock: ManualClock) -> None:
        activity = self.activity(bus, clock)
        recorder = activity.begin(spec())
        recorder.write("stdout", "x\n")
        _ = await recorder.finish(exit_code=0, status="completed")
        await activity.drained()
        numbers = [f.n for f in await bus.read(RUN)]
        assert numbers == sorted(numbers)
        assert len(set(numbers)) == len(numbers)
        await activity.aclose()

    async def test_bp1_a_slow_bus_never_blocks_the_command(
        self, bus: LiveBusPort, clock: ManualClock, content: InMemoryContentStore
    ) -> None:
        gated = GatedBus(bus)
        limits = ActivityLimits(queue_frames=4, frame_chars=8)
        activity = self.activity(gated, clock, content, limits)
        recorder = activity.begin(spec())
        lines = [f"{i:06d}\n" for i in range(100)]
        for line in lines:
            recorder.write("stdout", line)
        gated.open.set()
        record = await recorder.finish(exit_code=0, status="completed")
        await activity.drained()
        frames = await bus.read(RUN)
        shown = outputs(frames, "inv-1")
        dropped = sum(int(f.data["dropped"]) for f in frames if "dropped" in f.data)  # pyright: ignore[reportArgumentType]
        assert dropped > 0
        assert len(shown.encode()) + dropped == len("".join(lines).encode())
        assert record.live_dropped == dropped
        assert record.log is not None
        assert await content.get(record.log) == "".join(lines).encode()
        assert frames[-1].kind == "command_finished"
        await activity.aclose()

    async def test_bp2_the_live_cap_per_command(
        self, bus: LiveBusPort, clock: ManualClock, content: InMemoryContentStore
    ) -> None:
        activity = self.activity(bus, clock, content, ActivityLimits(live_chars_per_command=10))
        recorder = activity.begin(spec())
        recorder.write("stdout", "0123456789\n")
        recorder.write("stdout", "more\n")
        record = await recorder.finish(exit_code=0, status="completed")
        await activity.drained()
        frames = await bus.read(RUN)
        assert outputs(frames, "inv-1") == ""
        assert record.live_dropped == len("0123456789\nmore\n")
        assert record.log is not None
        assert await content.get(record.log) == b"0123456789\nmore\n"
        await activity.aclose()

    async def test_bp3_a_failing_bus_leaves_the_record_whole(self, clock: ManualClock) -> None:
        failing = FailingBus()
        activity = self.activity(failing, clock)
        recorder = activity.begin(spec())
        recorder.write("stdout", "hello\n")
        await activity.drained()
        recorder.write("stdout", "world\n")
        record = await recorder.finish(exit_code=0, status="completed")
        await activity.drained()
        assert failing.calls == 1
        assert record.stdout.tail == "hello\nworld\n"
        await activity.aclose()


__all__ = ["CommandActivityContract", "FailingBus", "GatedBus", "outputs", "spec"]
