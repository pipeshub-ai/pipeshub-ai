"""run_chain on a fake clock: order, fallback, per-attempt time, cooldowns."""

from dataclasses import dataclass, field

import pytest

from app.web.chain import ChainFailed, ChainOutcome, Cooldowns, run_chain
from app.web.deadline import Deadline
from app.web.errors import (
    AuthFailed,
    Blocked,
    NotConfigured,
    RateLimited,
    TimedOut,
    UpstreamError,
    WebError,
)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@dataclass
class FakeSource:
    id: str
    outcome: object  # a value to return, or a WebError to raise
    takes_s: float = 0.0
    clock: Clock | None = None
    calls: list[float] = field(default_factory=list)  # the time each call was given

    @property
    def cooldown_key(self) -> str:
        return f"key:{self.id}"

    async def run(self, deadline: Deadline) -> object:
        self.calls.append(deadline.remaining())
        if self.clock:
            self.clock.now += self.takes_s
        if isinstance(self.outcome, WebError):
            raise self.outcome
        return self.outcome


async def chain(sources: list[FakeSource], clock: Clock, *, cooldowns: Cooldowns | None = None, seconds: float = 10, attempt_s: float = 3) -> ChainOutcome[object]:
    return await run_chain(
        sources, lambda source, deadline: source.run(deadline), Deadline.after(seconds, clock),
        attempt_s=attempt_s, cooldowns=cooldowns or Cooldowns(clock),
    )


async def test_first_answer_wins() -> None:
    clock = Clock()
    first, second = FakeSource("a", ["r1"]), FakeSource("b", ["r2"])
    outcome = await chain([first, second], clock)
    assert outcome.value == ["r1"]
    assert outcome.served_by == "a"
    assert [(a.source, a.error_type) for a in outcome.attempts] == [("a", None)]
    assert second.calls == []


async def test_falls_back_in_order_and_records_every_attempt() -> None:
    clock = Clock()
    sources = [FakeSource("a", Blocked("no")), FakeSource("b", NotConfigured("key")), FakeSource("c", ["r"])]
    outcome = await chain(sources, clock)
    assert outcome.served_by == "c"
    assert [(a.source, a.error_type) for a in outcome.attempts] == [("a", "blocked"), ("b", "not_configured"), ("c", None)]


async def test_an_empty_answer_is_an_answer() -> None:
    clock = Clock()
    later = FakeSource("b", ["r"])
    outcome = await chain([FakeSource("a", []), later], clock)
    assert outcome.value == [] and later.calls == []


async def test_each_attempt_is_capped_but_the_last_gets_what_is_left() -> None:
    clock = Clock()
    first = FakeSource("a", TimedOut("slow"), takes_s=3, clock=clock)
    last = FakeSource("b", ["r"], clock=clock)
    await chain([first, last], clock, seconds=10, attempt_s=3)
    assert first.calls == [3]
    assert last.calls == [7]


async def test_stops_when_the_deadline_passes() -> None:
    clock = Clock()
    first = FakeSource("a", TimedOut("slow"), takes_s=10, clock=clock)
    second = FakeSource("b", ["r"])
    with pytest.raises(ChainFailed) as info:
        await chain([first, second], clock, seconds=10, attempt_s=20)
    assert second.calls == []
    assert info.value.error_type == "timeout"


async def test_all_failing_reports_the_last_error_with_every_attempt() -> None:
    clock = Clock()
    sources = [FakeSource("a", AuthFailed("key rejected")), FakeSource("b", UpstreamError("down", status=503))]
    with pytest.raises(ChainFailed) as info:
        await chain(sources, clock)
    error = info.value
    assert error.error_type == "upstream_error"
    assert error.retryable is True
    assert [a.source for a in error.attempts] == ["a", "b"]
    assert "a: key rejected" in error.message and "b: down" in error.message
    assert error.to_tool_payload()["error_type"] == "upstream_error"


async def test_no_time_at_all() -> None:
    clock = Clock()
    with pytest.raises(TimedOut):
        await chain([FakeSource("a", ["r"])], clock, seconds=0)


class TestCooldowns:
    async def test_a_blocked_source_is_skipped_until_it_cools_down(self) -> None:
        clock = Clock()
        cooldowns = Cooldowns(clock)
        blocked = FakeSource("a", Blocked("bot check"))
        backup = FakeSource("b", ["r"])
        await chain([blocked, backup], clock, cooldowns=cooldowns)
        await chain([blocked, backup], clock, cooldowns=cooldowns)
        assert len(blocked.calls) == 1
        clock.now += 301
        await chain([blocked, backup], clock, cooldowns=cooldowns)
        assert len(blocked.calls) == 2

    async def test_retry_after_sets_the_cooldown(self) -> None:
        clock = Clock()
        cooldowns = Cooldowns(clock)
        cooldowns.failed("k", RateLimited("slow", retry_after_s=5))
        assert cooldowns.cooling("k")
        clock.now = 6
        assert not cooldowns.cooling("k")

    async def test_transient_errors_do_not_cool_down(self) -> None:
        cooldowns = Cooldowns(Clock())
        cooldowns.failed("k", TimedOut("slow"))
        cooldowns.failed("j", UpstreamError("5xx", status=500))
        assert not cooldowns.cooling("k") and not cooldowns.cooling("j")

    async def test_success_clears_a_cooldown(self) -> None:
        clock = Clock()
        cooldowns = Cooldowns(clock)
        cooldowns.failed("key:a", Blocked("x"))
        # Every source cooling: the chain still tries them rather than failing without trying.
        source = FakeSource("a", ["r"])
        outcome = await chain([source], clock, cooldowns=cooldowns)
        assert outcome.served_by == "a"
        assert not cooldowns.cooling("key:a")
