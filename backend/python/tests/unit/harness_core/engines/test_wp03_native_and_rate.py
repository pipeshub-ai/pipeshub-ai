"""WP-03 building blocks: the native tape (C2.39, UP-07), unknown native events (C2.40, EV-13), the per-run
event rate (C2.31, C6.39, SEC-DOS-02) and the LiveBus frame cap."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from harness_core.engines.events import NativeUnknown, TextDelta
from harness_core.engines.native import (
    MAX_UNKNOWN_BYTES,
    NativeTape,
    native_kind,
    recorded,
    reparse,
    set_recorded,
    unknown_event,
)
from harness_core.engines.rate import EngineEventFlood, EventRate, EventRateLimiter
from harness_core.engines.stream import TurnSeq
from harness_core.journal.live_rate import LiveRate, LiveRateCap
from harness_core.journal.memory import InMemoryLiveBus
from harness_core.journal.types import LiveFrame

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@dataclass
class _Toy:
    """A mapper shaped as the adapters' are: recorded entry points, deterministic over its inputs."""

    turn_id: str
    tape: NativeTape | None = None
    loud: bool = False
    _seq: TurnSeq = field(init=False)

    def __post_init__(self) -> None:
        self._seq = TurnSeq(self.turn_id)

    @recorded
    def feed(self, message: dict[str, object]) -> list[object]:
        if message.get("kind") == "text":
            text = str(message["text"])
            return [TextDelta(self._seq.next(), "m1", text.upper() if self.loud else text)]
        return [unknown_event(self._seq, str(message.get("kind")), message)]  # type: ignore[arg-type]


def test_up07_a_transcript_reparsed_by_a_fresh_mapper_yields_the_same_events() -> None:
    tape = NativeTape()
    tape.begin("trn_1")
    live = _Toy("trn_1", tape=tape)
    seen = [*live.feed({"kind": "text", "text": "a"})]
    set_recorded(live, "loud", True)
    seen += live.feed({"kind": "text", "text": "b"})
    seen += live.feed({"kind": "brand/new", "x": 1})
    [part] = tape.finish()
    assert part.final and not part.truncated and part.lines == 4
    assert reparse(part.data, _Toy("trn_1")) == seen


def test_c239_a_set_that_changes_nothing_is_not_taped() -> None:
    tape = NativeTape()
    tape.begin("trn_1")
    toy = _Toy("trn_1", tape=tape)
    set_recorded(toy, "loud", False)
    assert tape.finish() == []


def test_c239_parts_are_cut_at_their_size_and_the_turn_is_capped_and_says_so() -> None:
    tape = NativeTape(part_bytes=100, max_bytes=1000)
    tape.begin("trn_1")
    toy = _Toy("trn_1", tape=tape)
    for n in range(200):
        toy.feed({"kind": "text", "text": f"line {n}"})
    taken = tape.take()
    assert taken and all(not p.final and len(p.data) >= 100 for p in taken)
    rest = tape.finish()
    assert rest[-1].final and rest[-1].truncated
    kept = sum(len(p.data) for p in [*taken, *rest])
    assert kept <= 1000
    # Every kept line is whole and re-parses: a cut transcript is a prefix, never a broken line.
    assert len(reparse([p.data for p in [*taken, *rest]], _Toy("trn_1"))) == sum(p.lines for p in [*taken, *rest])
    assert [p.index for p in [*taken, *rest]] == list(range(len(taken) + len(rest)))


def test_ev13_an_unknown_message_is_kept_cut_at_its_bound_with_its_whole_size() -> None:
    big = {"kind": "new/thing", "blob": "x" * (MAX_UNKNOWN_BYTES * 2)}
    event = unknown_event(TurnSeq("trn_1"), "new/thing", big)  # type: ignore[arg-type]
    assert isinstance(event, NativeUnknown)
    assert len(event.raw) == MAX_UNKNOWN_BYTES and event.truncated
    assert event.size_bytes == len(json.dumps(big, separators=(",", ":")).encode())
    assert native_kind("an engine's error: secret!") == "an_engine_s_error:_secret_"
    assert len(native_kind("x" * 500)) == 64


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept = 0.0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept += seconds
        self.now += seconds


async def test_sec_dos_02_a_burst_passes_without_waiting_and_a_steady_rate_is_throttled_not_failed() -> None:
    clock = _Clock()
    limiter = EventRateLimiter(EventRate(per_second=100, burst=50, max_throttled_s=5), clock=clock, sleep=clock.sleep)
    for _ in range(50):
        await limiter.take()
    assert clock.slept == 0
    for _ in range(300):
        await limiter.take()
    # 300 past the burst at 100/s: about three seconds of throttling, within the budget.
    assert 2.5 <= limiter.throttled_s <= 3.5


async def test_sec_dos_02_a_flood_past_the_throttle_budget_fails_with_the_flood_code() -> None:
    clock = _Clock()
    limiter = EventRateLimiter(EventRate(per_second=100, burst=50, max_throttled_s=1), clock=clock, sleep=clock.sleep)
    with pytest.raises(EngineEventFlood) as flood:
        for _ in range(1_000_000):
            await limiter.take()
    assert flood.value.reason_code == "event_flood"
    # Stopped near burst + rate x budget, never after the whole million.
    assert limiter.events < 50 + 100 * 1 + 10


async def test_sec_dos_02_a_clock_that_stands_still_still_ends_a_flood() -> None:
    async def no_sleep(_s: float) -> None:
        return None

    limiter = EventRateLimiter(EventRate(per_second=1000, burst=10, max_throttled_s=0.1), clock=lambda: 0.0, sleep=no_sleep)
    with pytest.raises(EngineEventFlood):
        for _ in range(1_000_000):
            await limiter.take()
    assert limiter.events < 200


def test_sec_dos_02_the_rate_refuses_nonsense() -> None:
    with pytest.raises(ValueError):
        EventRate(per_second=0)


def _frame(run_id: str, n: int) -> LiveFrame:
    return LiveFrame(run_id, 1, 1, n, "text_delta", {"text": "t"})


async def test_sec_dos_02_the_livebus_drops_a_flooding_runs_frames_and_keeps_another_runs() -> None:
    now = [0.0]
    cap = LiveRateCap(LiveRate(per_second=10, burst=20), clock=lambda: now[0])
    bus = InMemoryLiveBus(maxlen=100_000, cap=cap)
    for n in range(10_000):
        await bus.publish(_frame("flood", n))
    await bus.publish(_frame("calm", 1))
    assert len(await bus.read("flood")) == 20
    assert cap.dropped("flood") == 10_000 - 20
    assert [f.n for f in await bus.read("calm")] == [1]
    now[0] += 1.0
    await bus.publish(_frame("flood", 10_001))
    assert len(await bus.read("flood")) == 21


def test_sec_dos_02_the_livebus_cap_forgets_runs_it_has_not_heard_from() -> None:
    cap = LiveRateCap(LiveRate(per_second=1, burst=1), clock=lambda: 0.0, max_runs=3)
    for run in ("a", "b", "c", "d"):
        assert cap.admit(run)
    assert not cap.admit("d")
    # "a" was the oldest: its bucket is gone, so the table stays at its bound.
    assert cap.dropped("a") == 0 and cap.admit("a")


class _Flooding:
    """A process whose stdout is one valid JSON line after another, as fast as it is read."""

    def __init__(self, lines: int) -> None:
        self._lines = lines
        self.read = 0

    @property
    def stdout(self) -> AsyncIterator[bytes]:
        return self._out()

    async def _out(self) -> AsyncIterator[bytes]:
        chunk = b'{"method":"item/agentMessage/delta","params":{"delta":"x"}}\n' * 100
        while self.read < self._lines:
            self.read += 100
            yield chunk

    @property
    def stderr(self) -> AsyncIterator[bytes]:
        return self._err()

    async def _err(self) -> AsyncIterator[bytes]:
        for chunk in ():
            yield chunk

    async def write(self, data: bytes) -> None:
        return None

    async def close_stdin(self) -> None:
        return None


async def test_sec_dos_02_the_engine_channel_throttles_a_flood_then_ends_its_stream_with_the_flood() -> None:
    import asyncio

    from harness_core.engines.jsonlines import JsonLinesChannel

    process = _Flooding(1_000_000)
    got = 0
    ended: asyncio.Future[BaseException | None] = asyncio.get_running_loop().create_future()

    async def on_message(_message: object) -> None:
        nonlocal got
        got += 1

    async def on_end(error: BaseException | None) -> None:
        ended.set_result(error)

    channel = JsonLinesChannel(
        process,  # type: ignore[arg-type]
        on_message,
        on_end,
        rate=EventRate(per_second=10_000, burst=1_000, max_throttled_s=0.2),
    )
    channel.start()
    error = await asyncio.wait_for(ended, 10)
    await channel.close()
    assert isinstance(error, EngineEventFlood)
    # The reader stopped reading: the million lines were never all taken in, nor queued.
    assert got < 1_000 + 10_000 * 0.2 + 1_000 and process.read < 1_000_000
