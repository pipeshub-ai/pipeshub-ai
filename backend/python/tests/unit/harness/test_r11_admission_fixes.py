"""Round 11 item E: how WP-44/WP-48 admission met the rest of the harness, and what is fixed.

R11-ADM-1 a streamed Model Gateway call whose client leaves before its first chunk gives its provider slot back and
          releases its ledger reservation at once, not after the slot's 15-minute hold.
R11-ADM-2 a loop run's streamed call gives its slot back when the turn has its answer, not when the garbage collector
          finalizes the stream; a slot the gate takes back after its hold serves the calls waiting for it.
R11-ADM-3 free slots in the shared gate reach every eligible waiter at their next ask, not one per poll interval
          behind the head of the queue; the reserve still holds background calls back.
R11-ADM-4 a process that started while Redis was unreachable joins the shared limit once Redis answers.
R11-ADM-5 the inline profile (the query service's own claimer) admits its runs' model calls as the worker does.
R11-ADM-6 a metered call takes its provider slot before its ledger reservation, as the gateway does, so a queued call
          holds no budget while it waits; and the wait for a slot is not the model's stall (the watchdog's first-event
          bound starts once the slot is granted).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest

from harness_core.access.types import ENGINE_MODEL_ID
from harness_core.gateway.model_gateway import ChatStream
from harness_core.gateway.priority_gate import PriorityGate
from harness_core.kernel.core.messages import UserMessage
from harness_core.models.transports.admitted import AdmittedTransport
from harness_core.testing import ManualClock
from harness_core.testing.model_gateway import (
    GatewayRig,
    RecordedTransport,
    body_of,
    reply_events,
)
from tests.support.settle import settle

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from harness_core.kernel.core.streaming import StreamEvent


def _stream_body() -> dict[str, Any]:
    return {"model": ENGINE_MODEL_ID, "messages": [{"role": "user", "content": "hi"}], "stream": True}


def _open(ledger: Any) -> int:  # noqa: ANN401
    """Calls' reservations still held: neither settled nor released. A cut call's hold (WP-66) is the ledger's own,
    kept on purpose to its time, not a reservation the call failed to give back."""
    return sum(1 for held in ledger._held.values() if held.settlement is None and not held.hold)  # pyright: ignore[reportPrivateUsage]


async def _turns(n: int = 50) -> None:
    for _ in range(n):
        await asyncio.sleep(0)


async def test_r11_adm_1_a_stream_closed_before_its_first_chunk_gives_its_slot_and_reservation_back() -> None:
    gate = PriorityGate(1, monotonic=ManualClock().monotonic)
    rig = GatewayRig(gate=gate)
    token = rig.token(rig.pin("run-1", priority_class="background"))
    outcome = await rig.gateway.chat(token, {}, body_of(_stream_body()))
    assert isinstance(outcome, ChatStream)
    assert gate.in_flight("openai") == 1
    # What the server does when the client is gone before ``prepare`` (a Stop, a lease lost, a preempted sandbox).
    await outcome.aclose()
    assert gate.in_flight("openai") == 0
    assert _open(rig.ledger) == 0
    await outcome.aclose()  # idempotent
    assert gate.in_flight("openai") == 0


async def test_r11_adm_1_a_stream_read_to_its_end_then_closed_settles_once() -> None:
    gate = PriorityGate(1, monotonic=ManualClock().monotonic)
    rig = GatewayRig(gate=gate)
    token = rig.token(rig.pin("run-1", priority_class="interactive"))
    outcome = await rig.gateway.chat(token, {}, body_of(_stream_body()))
    assert isinstance(outcome, ChatStream)
    chunks = [chunk async for chunk in outcome.chunks]
    assert chunks
    await outcome.aclose()
    assert gate.in_flight("openai") == 0
    assert _open(rig.ledger) == 0


async def test_r11_adm_1_the_server_closes_the_stream_when_the_client_is_gone_before_prepare() -> None:
    from harness_core.gateway.model_server import (
        _sse,  # pyright: ignore[reportPrivateUsage]
    )

    gate = PriorityGate(1, monotonic=ManualClock().monotonic)
    rig = GatewayRig(gate=gate)
    token = rig.token(rig.pin("run-1", priority_class="background"))
    outcome = await rig.gateway.chat(token, {}, body_of(_stream_body()))
    assert isinstance(outcome, ChatStream)

    class _Gone:
        """A request whose transport closed: ``prepare`` raises as aiohttp's does."""

    with pytest.raises(Exception):  # noqa: B017 - whatever prepare raises for a request with no transport
        _ = await _sse(_Gone(), outcome)  # type: ignore[arg-type]
    assert gate.in_flight("openai") == 0


async def test_r11_adm_2_a_loop_stream_gives_its_slot_back_when_the_turn_has_its_answer() -> None:
    from harness_core.kernel.durable.adapters import StreamingModelPort
    from harness_core.models.transport_model import TransportModel

    gate = PriorityGate(1, monotonic=ManualClock().monotonic)
    transport = AdmittedTransport(RecordedTransport([reply_events("ok")]), admission=gate,
                                  priority_class="background")
    port = StreamingModelPort(TransportModel(transport), provider="openai", transport="direct")
    from harness_core.kernel.durable.ports import ModelCall

    async def on_delta(kind: str, text: str) -> None:
        return None

    _ = await port._streamed(ModelCall(messages=[UserMessage(content="hi")]), on_delta)  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]
    assert gate.in_flight("openai") == 0, "given back with the answer, before any other task runs"


async def test_r11_adm_2_a_slot_taken_back_after_its_hold_serves_the_waiting_call() -> None:
    clock = ManualClock()
    gate = PriorityGate(1, monotonic=clock.monotonic, sleep=clock.sleep, max_hold_s=60)
    _leaked = await gate.acquire("openai", "background")
    waiting = asyncio.ensure_future(gate.acquire("openai", "interactive"))
    await _turns()
    assert not waiting.done()
    _ = clock.advance(61)
    await _turns()
    # No other call arrives: the leaked slot's hold running out is what serves the waiting one.
    assert waiting.done()
    waiting.result().release()
    assert gate.in_flight("openai") == 0


class _Shared:
    def __init__(self, limit: int) -> None:
        from tests.support.fake_redis_connection_provider import (
            FakeRedisConnectionProvider,
        )

        self.clock = ManualClock()
        self.redis = FakeRedisConnectionProvider(is_cluster=True, key_namespace="ns")
        self.limit = limit

    def process(self) -> Any:  # noqa: ANN401
        from app.harness.provider_gate import RedisProviderGate

        return RedisProviderGate(self.redis, self.limit, monotonic=self.clock.monotonic, sleep=self.clock.sleep,
                                 now_ms=lambda: int(self.clock.monotonic() * 1000), floor_every=0)

    async def tick(self, seconds: float) -> None:
        _ = self.clock.advance(seconds)
        await _turns(50)


async def test_r11_adm_3_free_shared_slots_reach_every_eligible_waiter_at_their_next_ask() -> None:
    from app.harness.provider_gate import POLL_MAX_S

    rig = _Shared(8)
    a, b = rig.process(), rig.process()
    held = [await a.acquire("openai", "interactive") for _ in range(8)]
    tasks = []
    for cls in ["batch", "background", "approval_resume", "interactive"]:
        tasks.append(asyncio.ensure_future(b.acquire("openai", cls)))
        await _turns()
    for _ in range(40):  # every waiter at its longest poll interval
        await rig.tick(0.05)
    for slot in held[:6]:
        slot.release()
    await _turns()
    started = rig.clock.monotonic()
    while not all(t.done() for t in tasks) and rig.clock.monotonic() - started < 5:
        await rig.tick(0.01)
    assert all(t.done() for t in tasks)
    assert rig.clock.monotonic() - started <= POLL_MAX_S + 0.02, "one poll interval, not one per waiter"


async def test_r11_adm_3_the_reserve_still_holds_background_back_past_the_head() -> None:
    rig = _Shared(8)
    a, b = rig.process(), rig.process()
    held = [await a.acquire("openai", "interactive") for _ in range(8)]
    interactive = asyncio.ensure_future(b.acquire("openai", "interactive"))
    background = [asyncio.ensure_future(b.acquire("openai", "background")) for _ in range(3)]
    await _turns()
    for slot in held[:3]:  # room 3, reserve 2: the person's call and no background one
        slot.release()
    for _ in range(20):
        await rig.tick(0.05)
    assert interactive.done()
    assert not any(t.done() for t in background)
    for t in background:
        _ = t.cancel()
    await _turns()


async def test_r11_adm_4_a_process_started_without_redis_joins_the_shared_limit_once_redis_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.harness import provider_gate
    from tests.support.fake_redis_connection_provider import FakeRedisConnectionProvider

    redis = FakeRedisConnectionProvider(is_cluster=True, key_namespace="ns")
    down = {"now": True}
    real_load = redis.load_script

    async def load_script(body: str) -> str:
        if down["now"]:
            raise ConnectionError("redis down")
        return await real_load(body)

    redis.load_script = load_script  # type: ignore[method-assign]

    async def ping() -> bool:
        if down["now"]:
            raise ConnectionError("redis down")
        return True

    class _Config:
        async def get_redis_config(self) -> dict[str, Any]:
            return {}

    monkeypatch.setattr("app.services.redis.connection_provider_factory.get_redis_provider", lambda _cfg: redis)
    monkeypatch.setattr("app.services.redis.config.RedisConnectionConfig.from_redis_config", lambda _raw: object())
    monkeypatch.setattr(redis, "prepare", _noop, raising=False)
    monkeypatch.setattr(redis.get_client(), "ping", ping, raising=False)
    clock = ManualClock()
    admission = await provider_gate.provider_admission(_Config(), clock)  # type: ignore[arg-type]
    assert isinstance(admission, provider_gate.RedisProviderGate), "always the shared gate, over its own fallback"
    local = await admission.acquire("openai", "interactive")
    assert admission.held_here("openai") == 0, "Redis down: the process's own gate"
    local.release()
    down["now"] = False
    monkeypatch.setattr(provider_gate, "OUTAGE_SKIP_S", 0.0)
    admission._skip_until = float("-inf")  # pyright: ignore[reportPrivateUsage]  # the outage window has passed
    shared = await admission.acquire("openai", "interactive")
    assert admission.held_here("openai") == 1, "Redis back: the deployment's limit"
    shared.release()
    await admission.aclose()


async def _noop() -> None:
    return None


async def test_r11_adm_5_the_inline_profile_admits_its_runs_model_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.harness import inline

    admission = PriorityGate(4, monotonic=ManualClock().monotonic)
    built: dict[str, Any] = {}

    async def provider_admission(config_service: object, clock: object) -> PriorityGate:
        return admission

    class _Assembler:
        def __init__(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401
            built.update(kwargs)

    class _Claimer:
        def __init__(self, **kwargs: Any) -> None:  # noqa: ANN401
            pass

        def start(self) -> None:
            pass

    class _Bus:
        @staticmethod
        async def create(config_service: object) -> None:
            return None

    class _Container:
        def config_service(self) -> object:
            return object()

    monkeypatch.setattr(inline, "provider_admission", provider_admission)
    monkeypatch.setattr(inline, "PipesHubRuntimeAssembler", _Assembler)
    monkeypatch.setattr(inline, "RunClaimer", _Claimer)
    monkeypatch.setattr(inline, "RedisLiveBus", _Bus)
    monkeypatch.setattr(inline, "NodeJournal", lambda **_k: object())
    monkeypatch.setattr(inline, "RunDriver", lambda **_k: object())
    monkeypatch.setattr(inline, "RunPolicies", lambda *_a, **_k: object())
    monkeypatch.setattr(inline, "worker_live_layer", lambda *_a, **_k: object())
    monkeypatch.setattr(inline, "log_capabilities", lambda *_a, **_k: None)
    _ = await inline.InlineClaimer(_Container())._build()
    assert built["admission"] is admission, "the inline runs take provider slots as the worker's do"


async def test_r11_adm_6_a_metered_call_takes_its_slot_before_its_reservation() -> None:
    from harness_core.budget.memory import InMemoryBudgetLedger
    from harness_core.budget.metered import MeteredTransport
    from harness_core.budget.types import Cap, RunAccountRef
    from harness_core.testing.ids import SeededIds

    clock = ManualClock()
    ledger = InMemoryBudgetLedger(clock)
    account = RunAccountRef("org-1", "run-1")
    _ = await ledger.open_root(account, Cap(5_000_000, 1_000_000))
    reserved: list[str] = []
    real_reserve = ledger.reserve

    async def reserve(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        reserved.append(args[1])
        return await real_reserve(*args, **kwargs)

    ledger.reserve = reserve  # type: ignore[method-assign]
    gate = PriorityGate(1, monotonic=clock.monotonic)
    blocker = await gate.acquire("openai", "interactive")
    inner = AdmittedTransport(RecordedTransport([reply_events("ok"), reply_events("ok")]), admission=gate,
                              priority_class="background")
    charged = MeteredTransport(inner, accounts=ledger, account=account, pricing_model="gpt-4o", ids=SeededIds(0))
    waiting = asyncio.ensure_future(charged.complete([UserMessage(content="hi")]))
    await _turns()
    assert gate.waiting("openai") == 1
    assert reserved == [], "nothing reserved while the call waits for its provider slot"
    blocker.release()
    _ = await waiting
    assert len(reserved) == 1 and gate.in_flight("openai") == 0

    blocker = await gate.acquire("openai", "interactive")
    reading = asyncio.ensure_future(_drain(charged.stream([UserMessage(content="hi")])))
    await _turns()
    assert len(reserved) == 1, "the stream waits for its slot before it reserves"
    blocker.release()
    await reading
    assert len(reserved) == 2 and gate.in_flight("openai") == 0
    assert _open(ledger) == 0


async def _drain(stream: AsyncIterator[StreamEvent]) -> None:
    async for _ in stream:
        pass


async def test_r11_adm_6_the_wait_for_a_slot_is_not_the_models_stall() -> None:
    from harness_core.models.stream_watch import StreamTimeouts, watched

    gate = PriorityGate(1, monotonic=ManualClock().monotonic)
    blocker = await gate.acquire("openai", "interactive")
    transport = AdmittedTransport(RecordedTransport([reply_events("ok")]), admission=gate, priority_class="background")
    stream = watched(transport.stream([UserMessage(content="hi")]), StreamTimeouts(first_event_s=0.05, idle_s=0.05))
    reading = asyncio.ensure_future(_drain(stream))
    await asyncio.sleep(0.2)  # four first-event bounds spent waiting for the slot
    assert not reading.done(), "still waiting for its slot, not failed as a stalled model"
    blocker.release()
    await settle(reading)
