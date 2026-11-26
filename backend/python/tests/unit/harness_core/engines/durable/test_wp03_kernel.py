"""WP-03 through the durable engine kernel and its controller, on the in-memory journal (Node as the tests play it).

- SEC-DOS-02 (C2.31, C6.39): a scripted engine sending a million events is throttled, then its attempt fails with
  ``event_flood``, a code the chat words; memory and time stay bounded. A flood that ends the engine's stream (the
  channel's own cap) fails the same way, never as a crash the next attempt repeats.
- EV-13 (C2.40): unknown native events are journaled sealed, at most 20 a turn; the run goes on.
- UP-07 (C2.39): a turn's native transcript is journaled in sealed parts whose bytes are the tape's.
- EV-06 (C2.49): a run the worker ends (a cancel, a crash-loop quarantine) closes every call left open; one Node
  ends (a cancel of a run no worker holds) is closed by Node. The autouse history check (H8) judges every journal.
"""

from __future__ import annotations

import dataclasses
import tracemalloc
from typing import TYPE_CHECKING

from harness_core.effects import args_digest
from harness_core.engines.errors import EngineCrashed
from harness_core.engines.events import (
    EngineEventMeta,
    NativeUnknown,
    TextDelta,
    TurnCompleted,
)
from harness_core.engines.native import MAX_UNKNOWN_EVENTS, NativeTape
from harness_core.engines.rate import EngineEventFlood, EventRate
from harness_core.journal.types import CommitRequest, NewEvent
from harness_core.testing.engines.durable import (
    CREATE,
    NATIVE_TRANSCRIPT,
    NATIVE_UNKNOWN,
    SESSION,
    EngineWorld,
    ScriptedEngineAdapter,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    import pytest

    from harness_core.engines.events import EngineEvent
    from harness_core.engines.types import EngineTask, FollowUp

RUN = "run-wp03"
FLOOD = 1_000_000


class _Wrapped:
    def __init__(self, inner: object) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


class _Flooding(_Wrapped):
    """An engine that sends a million text deltas and never ends its turn."""

    sent = 0

    async def run_turn(self, task: EngineTask | FollowUp) -> AsyncIterator[EngineEvent]:
        for seq in range(FLOOD):
            self.sent = seq + 1
            yield TextDelta(EngineEventMeta(task.turn_id, seq), "m1", "x")


class _ChannelFlood(_Wrapped):
    """An engine whose own stream the channel's cap ended: the session reports a lost stream, caused by the flood."""

    async def run_turn(self, task: EngineTask | FollowUp) -> AsyncIterator[EngineEvent]:
        yield TextDelta(EngineEventMeta(task.turn_id, 0), "m1", "x")
        flood = EngineEventFlood("the engine sent events faster than its channel allows")
        raise EngineCrashed("the engine event stream ended before the turn completed") from flood


class _Native(_Wrapped):
    """A newer engine: unknown events among its own, and a session that tapes what it maps."""

    unknown = MAX_UNKNOWN_EVENTS + 5

    def tap_native(self, tape: NativeTape) -> None:
        self.tape = tape

    async def run_turn(self, task: EngineTask | FollowUp) -> AsyncIterator[EngineEvent]:
        seq = 0
        async for event in self._inner.run_turn(task):  # type: ignore[attr-defined]
            if isinstance(event, TurnCompleted):
                for n in range(self.unknown):
                    self.tape.op("feed", ({"method": "item/hologram", "n": n},))
                    yield NativeUnknown(EngineEventMeta(task.turn_id, seq), "item/hologram", b'{"n":%d}' % n, 9)
                    seq += 1
                self.tape.op("feed", ({"method": "turn/completed"},))
                yield TurnCompleted(EngineEventMeta(task.turn_id, seq), event.reason, event.final_text)
                return
            seq = event.meta.seq + 1
            yield event


class _Adapter(ScriptedEngineAdapter):
    def __init__(self, wrap: Callable[[object], object]) -> None:
        super().__init__()
        self._wrap = wrap
        self.opened: list[object] = []

    async def open(self, sandbox, launch, restore, *, correlation=None):  # type: ignore[no-untyped-def] # noqa: ANN202
        session = self._wrap(await super().open(sandbox, launch, restore, correlation=correlation))
        self.opened.append(session)
        return session


def _world(wrap: Callable[[object], object], adapter: _Adapter | None = None) -> EngineWorld:
    return EngineWorld(approvals={"create": "allow"}, adapter=adapter or _Adapter(wrap))


async def test_sec_dos_02_a_million_events_are_throttled_then_fail_the_attempt_bounded_in_memory_and_time() -> None:
    adapter = _Adapter(_Flooding)
    world = _world(_Flooding, adapter)
    # A tenth of the production rate and burst, the same throttle budget: the flood is cut by the same rule after a
    # tenth of the events, which keeps the test's CPU (every event crosses the controller) off a loaded runner's
    # timeout. The production values are EventRate's defaults, checked against the limiter on its own clock.
    production = EventRate()
    rate = dataclasses.replace(production, per_second=production.per_second / 10, burst=production.burst // 10)
    world.config = dataclasses.replace(world.config, event_rate=rate)
    world.admit(RUN, "say done")
    tracemalloc.start()
    try:
        disposition = await world.attempt(RUN)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert disposition.status == "failed" and disposition.reason == "event_flood"
    [failed] = [e for e in world.events(RUN) if e.type == "run.failed"]
    assert failed.data["errorType"] == "event_flood"
    # Bounded: the flood is cut long before its million events, in memory and in time (the events read are the
    # work: the burst and the throttle budget's worth at the sustained rate, not the million).
    sent = sum(session.sent for session in adapter.opened if isinstance(session, _Flooding))
    assert 0 < sent <= rate.burst + rate.per_second * rate.max_throttled_s + rate.burst, sent
    assert peak < 64 * 1024 * 1024, peak


async def test_sec_dos_02_a_flood_that_ended_the_engines_stream_fails_the_run_and_is_never_retried() -> None:
    world = _world(_ChannelFlood)
    world.admit(RUN, "say done")
    disposition = await world.attempt(RUN)
    assert disposition.status == "failed" and disposition.reason == "event_flood"
    assert world.journal.status(RUN) == "failed"


async def _content(world: EngineWorld, ref: dict[str, object]) -> bytes:
    from harness_core.contracts.generated.common import ContentRef

    return await world.content.get(ContentRef.model_validate(ref))


async def test_ev13_unknown_native_events_are_journaled_sealed_at_most_twenty_a_turn_and_the_run_completes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    world = _world(_Native)
    world.admit(RUN, "say done")
    with caplog.at_level("WARNING"):
        disposition = await world.attempt(RUN)
    assert disposition.status == "completed"
    kept = [e for e in world.events(RUN) if e.type == NATIVE_UNKNOWN]
    assert len(kept) == MAX_UNKNOWN_EVENTS
    # Node decides who else sees a worker's event (owner, as every worker writes); the chat never renders this type.
    assert {e.visibility for e in kept} == {"owner"} and not any(e.critical for e in kept)
    assert kept[0].data["kind"] == "item/hologram"
    # Sealed: a ref into the run's content store, never the bytes inline in the journal.
    assert not str(kept[0].data["content"]["ref"]).startswith("inline:")  # type: ignore[index]
    assert await _content(world, kept[0].data["content"]) == b'{"n":0}'  # type: ignore[arg-type]
    # The counter: every unknown event, past the journal's bound too.
    counted = [r for r in caplog.records if r.getMessage().startswith("engine.native.unknown")]
    assert counted and f"count={_Native.unknown}" in counted[-1].getMessage()
    # Never a progress line: nothing the chat or the activity renders.
    assert not [c for c in world.presenter.calls if "Native" in c[0]]


async def test_up07_the_turns_native_transcript_is_journaled_sealed_as_the_tape_wrote_it() -> None:
    world = _world(_Native)
    world.admit(RUN, "say done")
    assert (await world.attempt(RUN)).status == "completed"
    parts = [e for e in world.events(RUN) if e.type == NATIVE_TRANSCRIPT]
    assert parts and parts[-1].data["final"] is True and parts[-1].data["truncated"] is False
    data = b"".join([await _content(world, p.data["content"]) for p in parts])  # type: ignore[arg-type]
    lines = data.splitlines()
    assert len(lines) == sum(int(p.data["lines"]) for p in parts) == _Native.unknown + 1  # type: ignore[arg-type]
    assert lines[-1] == b'["feed",{"method":"turn/completed"}]'


async def test_c239_a_journal_that_does_not_accept_native_events_is_never_sent_one() -> None:
    world = EngineWorld(approvals={"create": "allow"}, adapter=_Adapter(_Native), journal_accepts=frozenset())
    world.admit(RUN, "say done")
    assert (await world.attempt(RUN)).status == "completed"
    assert not {NATIVE_UNKNOWN, NATIVE_TRANSCRIPT} & set(world.types(RUN))


def _dead_intent(invocation: str) -> NewEvent:
    return NewEvent(
        "tool.intent",
        {
            "invocationId": invocation,
            "toolId": CREATE.effect.tool_id,
            "argsDigest": args_digest({"name": "a"}),
            "effect": "write",
            "sink": {"egress": "external", "argsLeaveBoundary": True},
        },
    )


async def _crashed_with_an_open_write(world: EngineWorld) -> None:
    claim = await world.journal.claim(RUN, worker_id="dead")
    assert claim is not None
    _ = await world.journal.commit(CommitRequest("dead:intent:1", claim.token, (_dead_intent("inv_dead_1"),)))


def _outcomes(world: EngineWorld) -> list[tuple[str, str, object]]:
    return [(e.writer, str(e.data["invocationId"]), e.data["status"]) for e in world.events(RUN) if e.type == "tool.outcome"]


async def test_ev06_a_cancel_the_next_attempt_takes_closes_the_crashed_attempts_write_as_effect_unknown() -> None:
    world = _world(lambda s: s)
    world.admit(RUN, 'tool create {"name": "a"}\nsay done')
    await _crashed_with_an_open_write(world)
    world.expire()
    claim = await world.journal.claim(RUN, worker_id="w-e1")
    assert claim is not None
    world.journal.request_cancel(RUN)
    disposition = await world.driver().run_claimed(claim, claim_sent_at=world.clock.suspend_aware())
    assert disposition.status == "cancelled"
    # A write that may have happened is never called cancelled: its outcome says the effect is unknown.
    assert _outcomes(world) == [("worker", "inv_dead_1", "error")]
    types = world.types(RUN)
    assert types.index("tool.outcome") < types.index("run.cancelled")


async def test_ev06_a_cancel_whose_attempt_died_is_ended_by_the_sweep_with_its_open_call_closed() -> None:
    """A cancel while the attempt that opened the call is gone: whichever of Node's paths ends the run (the sweep of a
    run being cancelled, SL-05; or the cancel of the run it re-queued), the open call is closed before the end."""
    world = _world(lambda s: s)
    world.admit(RUN, 'tool create {"name": "a"}\nsay done')
    await _crashed_with_an_open_write(world)
    world.journal.request_cancel(RUN)
    world.expire()
    world.journal.request_cancel(RUN)
    assert world.journal.status(RUN) == "cancelled"
    assert _outcomes(world) == [("node", "inv_dead_1", "cancelled")]
    assert world.types(RUN)[-2:] == ["tool.outcome", "run.cancelled"]


async def test_ev06_a_crash_loop_quarantine_closes_the_calls_its_attempts_left_open() -> None:
    world = _world(lambda s: s)
    world.admit(RUN, 'tool create {"name": "a"}\nsay done')
    await _crashed_with_an_open_write(world)
    for _ in range(3):
        world.expire()
        claim = await world.journal.claim(RUN, worker_id="dead")
        assert claim is not None
    world.expire()
    disposition = await world.attempt(RUN)
    assert disposition.status == "quarantined"
    assert _outcomes(world) == [("worker", "inv_dead_1", "error")]


async def test_ev06_a_cancel_node_ends_itself_closes_the_open_call_as_cancelled() -> None:
    world = _world(lambda s: s)
    world.admit(RUN, 'tool create {"name": "a"}\nsay done')
    await _crashed_with_an_open_write(world)
    world.expire()
    world.journal.request_cancel(RUN)
    assert world.journal.status(RUN) == "cancelled"
    assert _outcomes(world) == [("node", "inv_dead_1", "cancelled")]
    events = world.journal.events(SESSION)
    assert [e.type for e in events][-2:] == ["tool.outcome", "run.cancelled"]

