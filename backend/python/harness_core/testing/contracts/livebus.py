"""Contract suite every ``LiveBusPort`` adapter must pass (L-17).

Subclass :class:`LiveBusContract` and provide a ``bus`` fixture returning the
adapter under test, built with ``maxlen=LiveBusContract.MAXLEN``. The
in-memory adapter runs it in ``tests/unit/harness_core/journal``; the Redis
Streams adapter in ``tests/unit/harness/test_livebus.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.journal.types import LiveFrame

if TYPE_CHECKING:
    from harness_core.journal.ports import LiveBusPort


def frame(
    run_id: str = "run-1",
    n: int = 1,
    *,
    fence: int = 1,
    attempt: int = 1,
    text: str = "delta",
    kind: str = "text_delta",
) -> LiveFrame:
    return LiveFrame(run_id, fence, attempt, n, kind, {"text": text})  # type: ignore[arg-type]


class LiveBusContract:
    MAXLEN = 50

    async def test_frames_come_back_in_publish_order(self, bus: LiveBusPort) -> None:
        sent = [frame(n=n, text=f"t{n}") for n in range(1, 6)]
        for f in sent:
            await bus.publish(f)
        assert await bus.read("run-1") == sent

    async def test_frame_ids_name_run_fence_attempt_and_number(
        self, bus: LiveBusPort
    ) -> None:
        await bus.publish(frame(n=3, fence=2, attempt=4))
        [got] = await bus.read("run-1")
        assert got.frame_id == "d:run-1:2:4:3"

    async def test_runs_are_isolated(self, bus: LiveBusPort) -> None:
        await bus.publish(frame("run-a", 1, text="a"))
        await bus.publish(frame("run-b", 1, text="b"))
        assert [f.data for f in await bus.read("run-a")] == [{"text": "a"}]
        assert [f.data for f in await bus.read("run-b")] == [{"text": "b"}]
        assert await bus.read("run-unknown") == []

    async def test_text_and_kinds_roundtrip_exactly(self, bus: LiveBusPort) -> None:
        text = 'line\n"quoted" ::artifact[x](y) é中\U0001f600'
        await bus.publish(frame(text=text))
        await bus.publish(frame(n=2, kind="reasoning_delta", text="why"))
        got = await bus.read("run-1")
        assert [(f.kind, f.data) for f in got] == [
            ("text_delta", {"text": text}),
            ("reasoning_delta", {"text": "why"}),
        ]

    async def test_frames_from_every_fence_are_kept_for_the_consumer_to_filter(
        self, bus: LiveBusPort
    ) -> None:
        await bus.publish(frame(n=1, fence=2))
        await bus.publish(frame(n=9, fence=1))
        assert [f.fence for f in await bus.read("run-1")] == [2, 1]

    async def test_the_buffer_is_bounded_and_keeps_the_newest(
        self, bus: LiveBusPort
    ) -> None:
        # Adapters may trim approximately (Redis MAXLEN ~ trims whole nodes).
        total = self.MAXLEN * 10
        for n in range(1, total + 1):
            await bus.publish(frame(n=n))
        kept = [f.n for f in await bus.read("run-1")]
        assert kept[-1] == total
        assert self.MAXLEN <= len(kept) <= self.MAXLEN * 4
        assert kept == list(range(total - len(kept) + 1, total + 1))


__all__ = ["LiveBusContract", "frame"]
