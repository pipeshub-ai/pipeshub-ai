"""K-12: random crash schedules over a scripted multi-turn run keep the durability invariants.

Invariants, checked after every step and at the end:

* no duplicate external effect (an idempotency key or a non-idempotent invocation
  reaches the SaaS at most once);
* no re-sample of a journaled response (the model is never asked for a turn the
  journal already answered);
* the final transcript equals an independent projection of the journal, and a
  crash-free run's transcript when no effect ended up unknown;
* no dangling tool_call in the final transcript.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from typing import TYPE_CHECKING

import pytest
from hypothesis import HealthCheck, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

from harness_core.failpoints import Arm, Nth, Raise
from harness_core.kernel.core.messages import AssistantMessage
from harness_core.kernel.durable.transcript import TranscriptRenderer
from harness_core.kernel.fold import fold_session
from harness_core.testing import (
    ModelScript,
    Reply,
    ScriptedModel,
    ToolCallSpec,
    Turn,
    armed,
)
from harness_core.testing.durable import ScriptedModelPort

from .support import RUN, Crash, World, assert_paired, project, render_pairs

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from pydantic import JsonValue

    from harness_core.kernel.core.messages import Message, ToolCall
    from harness_core.kernel.durable import (
        DeltaKind,
        Disposition,
        ModelCall,
        SampledTurn,
        ToolEffect,
    )
    from harness_core.testing import ModelRequest

CRASH_POINTS = [
    "worker.model.before_response_commit",
    "worker.model.after_response_commit",
    "worker.tool.after_intent",
    "worker.tool.after_effect",
    "worker.tool.before_outcome_commit",
    "worker.batch.before_suspend",
    "worker.commit.after_ack",
]


def _turn(text: str, *calls: tuple[str, dict[str, JsonValue]]) -> Reply:
    # Stable call ids: a real provider mints new ids on a re-sample, which would
    # make a legitimately re-sampled transcript differ from the crash-free one.
    turn = len(TURNS) + 1
    specs = [
        ToolCallSpec(id=f"call_t{turn}_{i}", name=n, args=a)
        for i, (n, a) in enumerate(calls)
    ]
    return Reply(text=text, tool_calls=specs, chunks=2 if text else None)


TURNS: list[Reply] = []
TURNS.append(
    _turn(
        "",
        ("lookup", {"q": "a"}),
        ("create_issue", {"title": "x"}),
        ("post_message", {"text": "hi"}),
    )
)
TURNS.append(_turn("", ("lookup", {"q": "b"}), ("create_issue", {"title": "y"})))
TURNS.append(_turn("all done"))


def _reply_for(request: ModelRequest) -> Reply:
    answered = sum(1 for m in request.messages if m.role == "assistant")
    return TURNS[min(answered, len(TURNS) - 1)]


def _ask_to_post(call: ToolCall, effect: ToolEffect) -> str:
    return "ask" if call.name == "post_message" else "allow"


class _Recording:
    """Wraps the model port and records the no-re-sample invariant at call time."""

    def __init__(self, world: World) -> None:
        self.world = world
        self.inner = ScriptedModelPort(
            ScriptedModel(
                ModelScript(
                    id="k12", turns=[Turn(from_request=_reply_for) for _ in range(200)]
                )
            )
        )
        self.resampled: list[tuple[int, int]] = []

    async def sample(
        self, call: ModelCall, on_delta: Callable[[DeltaKind, str], Awaitable[None]]
    ) -> SampledTurn:
        journaled = len(self.world.events("model.response", RUN))
        turn = 1 + sum(1 for m in call.messages if isinstance(m, AssistantMessage))
        if turn <= journaled:
            self.resampled.append((turn, journaled))
        return await self.inner.sample(call, on_delta)

    def restore(
        self, raw: bytes, *, provider: str, transport: str
    ) -> AssistantMessage | None:
        return self.inner.restore(raw, provider=provider, transport=transport)


def _drive(
    world: World, port: _Recording, loop: asyncio.AbstractEventLoop
) -> Disposition | None:
    if world.journal.status(RUN) != "queued":
        return None
    # A high attempts cap: this test is about crash windows, not the quarantine (K-9).
    kernel = world.kernel(
        port, approval=_ask_to_post, journal_retry_s=0, max_attempts=1000
    )  # type: ignore[arg-type]
    return loop.run_until_complete(world.run(kernel))


def _drive_crashing(
    world: World,
    port: _Recording,
    loop: asyncio.AbstractEventLoop,
    point: str | None,
    nth: int,
) -> Disposition | None:
    if point is None:
        return _drive(world, port, loop)
    with armed({point: Arm(Raise(Crash), Nth(nth))}):
        try:
            return _drive(world, port, loop)
        except Crash:
            world.expire()
            return None


def _resolve(world: World) -> None:
    for ix in world.journal.open_interactions(RUN):
        # Approvals go through; an unknown effect is confirmed by the human as done.
        world.journal.resolve_interaction(
            RUN, ix, "approved" if ix.startswith("ix.approve.") else "denied"
        )


def _finish(
    world: World, port: _Recording, loop: asyncio.AbstractEventLoop
) -> Disposition | None:
    last: Disposition | None = None
    for _ in range(20):
        status = world.journal.status(RUN)
        if status in ("completed", "failed", "cancelled", "quarantined"):
            return last
        if status == "suspended":
            _resolve(world)
            continue
        last = _drive(world, port, loop) or last
    raise AssertionError("run did not finish")


def _golden() -> list[tuple[str, str]]:
    world = World()
    world.admit()
    loop = asyncio.new_event_loop()
    try:
        done = _finish(world, _Recording(world), loop)
    finally:
        loop.close()
    assert done is not None and done.status == "completed"
    return render_pairs(list(done.transcript))


GOLDEN = _golden()


def _check_final(
    world: World,
    port: _Recording,
    loop: asyncio.AbstractEventLoop,
    last: Disposition | None,
) -> None:
    last = _finish(world, port, loop) or last
    assert world.journal.status(RUN) == "completed"
    if last is not None and last.status == "completed":
        transcript: list[Message] = list(last.transcript)
    else:  # the worker that completed the run crashed after the commit
        session = fold_session(world.journal.events("ses1"), stream_id="ses1")
        transcript = loop.run_until_complete(
            TranscriptRenderer(world.content, port).render(session)
        )
    assert_paired(transcript)
    rendered = render_pairs(transcript)
    assert rendered == loop.run_until_complete(project(world))
    if not world.events("tool.unknown"):
        assert rendered == GOLDEN
    _assert_no_duplicate_effect(world)
    assert port.resampled == []


def _assert_no_duplicate_effect(world: World) -> None:
    keys = Counter(e.idempotency_key for e in world.saas.effects if e.idempotency_key)
    assert all(n == 1 for n in keys.values()), keys
    posts = Counter(
        str(e.payload["invocation"]) for e in world.saas.effects_for("post_message")
    )
    assert all(n == 1 for n in posts.values()), posts


@pytest.mark.parametrize("drive", [0, 1, 2])
@pytest.mark.parametrize("nth", [1, 2, 3])
@pytest.mark.parametrize("point", CRASH_POINTS)
def test_every_single_crash_window(point: str, nth: int, drive: int) -> None:
    """Deterministic sweep: one crash at each point, hit, and worker drive."""
    world = World()
    world.admit()
    port = _Recording(world)
    loop = asyncio.new_event_loop()
    try:
        last: Disposition | None = None
        for index in range(drive + 1):
            if world.journal.status(RUN) == "suspended":
                _resolve(world)
            last = (
                _drive_crashing(
                    world, port, loop, point if index == drive else None, nth
                )
                or last
            )
        _check_final(world, port, loop, last)
    finally:
        loop.close()


class CrashSchedule(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.world = World()
        self.world.admit()
        self.port = _Recording(self.world)
        self.loop = asyncio.new_event_loop()
        self.last: Disposition | None = None

    @precondition(lambda self: self.world.journal.status(RUN) == "queued")
    @rule(point=st.sampled_from([*CRASH_POINTS, None]), nth=st.integers(1, 3))
    def drive(self, point: str | None, nth: int) -> None:
        self.last = (
            _drive_crashing(self.world, self.port, self.loop, point, nth) or self.last
        )

    @precondition(lambda self: self.world.journal.status(RUN) == "suspended")
    @rule()
    def resolve(self) -> None:
        _resolve(self.world)

    @precondition(lambda self: self.world.journal.status(RUN) == "queued")
    @rule(n=st.integers(1, 2))
    def journal_hiccup(self, n: int) -> None:
        self.world.journal.unavailable_calls = n

    @rule()
    def observe(self) -> None:
        """Always applicable, so a finished run still lets the schedule end."""

    @invariant()
    def no_duplicate_external_effect(self) -> None:
        _assert_no_duplicate_effect(self.world)

    @invariant()
    def never_resamples_a_journaled_response(self) -> None:
        assert self.port.resampled == []

    def teardown(self) -> None:
        try:
            self.world.journal.unavailable_calls = 0
            _check_final(self.world, self.port, self.loop, self.last)
        finally:
            self.loop.close()


CrashSchedule.TestCase.settings = settings(
    max_examples=200,
    stateful_step_count=20,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
TestCrashSchedules = CrashSchedule.TestCase
