"""G-45 / GA-24: a full worker gives a background run's slot to a person's turn, at a step boundary.

Before, a worker whose slots all held scheduled runs left a person's turn queued until one of them finished, however
long that took. Now the claimer, full but holding a background or batch run, asks Node for a run of a strictly higher
class, starts it at once in one of its overcommit slots, and asks the background run to give its slot back: the run
yields ``preempted`` at its next step boundary (never inside a model call or a tool effect) and is claimed again
later, resuming from its journal.

The saturation case (GA-24's acceptance) runs on virtual time with a fake model and no network: a worker saturated
with scheduled runs, then a stream of interactive turns. Interactive session-ready (admission to the run's first
model call) holds p95 within the warm-pool SLO (design-plan PF-01: p95 <= 5 s) while every scheduled run still
completes; the same load without preemption misses it, which is what the test is measuring.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest

from app.harness.provider_gate import RedisProviderGate
from app.harness.worker.claimer import (
    DEFAULT_MIN_SLICE_S,
    DEFAULT_OVERCOMMIT,
    MIN_SLICE_ENV,
    OVERCOMMIT_ENV,
    RunClaimer,
    preempt_min_slice_s,
    preempt_overcommit,
)
from app.services.rate_budget import RatePriority, rate_priority_for_class
from harness_core import bindings
from harness_core.access.types import ENGINE_MODEL_ID
from harness_core.contracts import dump_contract, validate_contract
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.driver import AssembledRuntime, RunDriver
from harness_core.effects.types import ToolExecution
from harness_core.gateway.model_gateway import ChatCompletion
from harness_core.gateway.priority_gate import PriorityGate
from harness_core.journal import InMemoryContentStore, InMemoryJournal
from harness_core.kernel.core.messages import (
    AssistantMessage,
    TextPart,
    ToolCall,
    UserMessage,
)
from harness_core.kernel.core.responses import ModelResponse, StopReason
from harness_core.kernel.durable import SampledTurn
from harness_core.models.transports.admitted import AdmittedTransport
from harness_core.priority import PreemptRequests, class_for_run_kind
from harness_core.testing import ManualClock
from harness_core.testing.durable import FunctionToolPort
from harness_core.testing.model_gateway import (
    GatewayRig,
    RecordedTransport,
    body_of,
    reply_events,
)
from harness_core.testing.run_spec import minimal_run_spec
from tests.support.fake_redis_connection_provider import FakeRedisConnectionProvider

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from harness_core.effects.types import ToolContext
    from harness_core.gateway.priority_gate import ProviderAdmission
    from harness_core.journal import Claim
    from harness_core.kernel.durable import DeltaKind, ModelCall
    from harness_core.models.transports.base import LLMTransport
    from harness_core.priority import PriorityClass

SLO_SESSION_READY_P95_S = 5.0  # design-plan.md "Session ready, warm pool": p95 <= 5 s (PF-01)
TURN_S = 1.0
TICK_S = 0.1
IDLE_POLL_S = 0.1
BACKGROUND_RUNS = 16
BACKGROUND_STEPS = 24


def _spec(run_kind: str, steps: int) -> RunSpec:
    raw = dump_contract(minimal_run_spec(max_turns=steps + 4))
    raw["runKind"] = run_kind
    return validate_contract(RunSpec, raw)


def _steps_of(text: str) -> int:
    return int(text.rsplit(":", 1)[1])


@dataclass
class _Model:
    """Each run asks ``steps`` tool calls, one per turn, then answers; every turn takes ``TURN_S`` of virtual time."""

    clock: ManualClock
    first_call: dict[str, float] = field(default_factory=dict[str, float])
    turns: dict[str, int] = field(default_factory=dict[str, int])

    async def sample(self, call: ModelCall, on_delta: Callable[[DeltaKind, str], Awaitable[None]]) -> SampledTurn:
        text = _input_of(call)
        run = text.split(" ", 1)[0]
        _ = self.first_call.setdefault(run, self.clock.monotonic())
        self.turns[run] = self.turns.get(run, 0) + 1
        await self.clock.sleep(TURN_S)
        done = sum(1 for m in call.messages if isinstance(m, AssistantMessage))
        if done < _steps_of(text):
            step = ToolCall(id=f"{run}-{done}", name="step", arguments={"n": done})
            return SampledTurn(message=AssistantMessage(content=[], tool_calls=[step]))
        return SampledTurn(message=AssistantMessage(content=[TextPart(text="done")]))

    def restore(self, raw: bytes, *, provider: str, transport: str) -> AssistantMessage | None:
        return None


def _input_of(call: ModelCall) -> str:
    for message in call.messages:
        if isinstance(message, UserMessage) and "steps:" in str(message.content):
            content = message.content
            return content if isinstance(content, str) else "".join(getattr(part, "text", "") for part in content)
    raise AssertionError("no user input in the call")


class _Assembler:
    def __init__(self, model: _Model, tools: FunctionToolPort) -> None:
        self.model, self.tools = model, tools
        self.content = InMemoryContentStore()

    async def build(self, claim: Claim) -> AssembledRuntime:
        return AssembledRuntime(model=self.model, tools=self.tools, content=self.content, bindings=bindings.NEUTRAL)


@dataclass
class _World:
    clock: ManualClock
    journal: InMemoryJournal
    model: _Model
    tools: FunctionToolPort
    claimer: RunClaimer
    admitted: dict[str, float] = field(default_factory=dict[str, float])

    def admit(self, run_id: str, run_kind: str, steps: int) -> None:
        self.journal.admit(
            run_id, org_id="org-1", session_id=f"s-{run_id}", spec=_spec(run_kind, steps), input_text=f"{run_id} steps:{steps}"
        )
        self.admitted[run_id] = self.clock.monotonic()

    async def tick(self, seconds: float = TICK_S) -> None:
        _ = self.clock.advance(seconds)
        for _ in range(60):
            await asyncio.sleep(0)

    async def stop(self) -> None:
        """Drains the worker; its drain deadline waits on this clock, so time moves until it is done."""
        draining = self.claimer.begin_drain()
        for _ in range(int(120 / TICK_S)):
            if draining.done():
                break
            await self.tick()
        await draining

    def done(self, run_id: str) -> bool:
        return self.journal.status(run_id) == "completed"

    def ready_s(self, run_id: str) -> float:
        return self.model.first_call[run_id] - self.admitted[run_id]


async def _step(args: Mapping[str, Any], ctx: ToolContext) -> ToolExecution:
    return ToolExecution(status="ok", content={"n": args["n"]})


def _world(*, slots: int, preempt: bool, min_slice_s: float = DEFAULT_MIN_SLICE_S, accepts: frozenset[str] | None = None) -> _World:
    clock = ManualClock()
    journal = InMemoryJournal(clock=clock, lease_ms=3_600_000, accepts=accepts)
    model = _Model(clock)
    tools = FunctionToolPort()
    tools.add("step", _step, tags=[("category", "read")])
    asked = PreemptRequests() if preempt else None
    driver = RunDriver(journal=journal, assembler=_Assembler(model, tools), clock=clock, sleep=clock.sleep, preempt=asked)
    claimer = RunClaimer(
        journal=journal,
        driver=driver,
        worker_id="w1",
        slots=slots,
        clock=clock,
        sleep=clock.sleep,
        idle_poll_s=IDLE_POLL_S,
        max_idle_poll_s=2 * IDLE_POLL_S,
        preempt=asked,
        min_slice_s=min_slice_s,
    )
    return _World(clock, journal, model, tools, claimer)


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


async def _saturated(*, preempt: bool) -> tuple[_World, list[str], list[str]]:
    """Four slots, 16 scheduled runs of 24 steps admitted first; once the slots are all held, one interactive turn of
    two steps every two seconds, twelve in all. Runs until every run completed."""
    w = _world(slots=4, preempt=preempt)
    background = [f"firing-{i}" for i in range(BACKGROUND_RUNS)]
    for run_id in background:
        w.admit(run_id, "scheduled", BACKGROUND_STEPS)
    w.claimer.start()
    for _ in range(int(10 / TICK_S)):
        await w.tick()
    assert sorted(w.claimer.running) == sorted(background[:4]), "the scheduled runs hold every slot"
    interactive = [f"turn-{i}" for i in range(12)]
    for run_id in interactive:
        w.admit(run_id, "interactive", 2)
        for _ in range(int(2 / TICK_S)):
            await w.tick()
    for _ in range(int(400 / TICK_S)):
        if all(w.done(r) for r in background + interactive):
            break
        await w.tick()
    await w.stop()
    return w, background, interactive


async def test_ga24_under_saturation_interactive_session_ready_p95_holds_the_slo_and_scheduled_runs_finish() -> None:
    w, background, interactive = await _saturated(preempt=True)
    ready = [w.ready_s(r) for r in interactive]
    assert _p95(ready) <= SLO_SESSION_READY_P95_S, sorted(ready)
    assert all(w.done(r) for r in interactive)
    # Lower classes were queued and preempted, never dropped: every scheduled run completed, each step once.
    assert all(w.done(r) for r in background), [r for r in background if not w.done(r)]
    yields = [
        e for r in background for e in w.journal.events(f"s-{r}") if e.type == "run.yielded" and e.data["reason"] == "preempted"
    ]
    assert yields, "the turns were made room for by preemption"
    steps = [name for name, _ in w.tools.executed]
    assert len(steps) == BACKGROUND_RUNS * BACKGROUND_STEPS + 12 * 2, "a preempted run resumed from its journal: no step ran twice"


async def test_ga24_control_without_preemption_the_same_load_misses_the_slo() -> None:
    """What the saturation case measures: the queue order alone leaves a turn waiting for a scheduled run to end."""
    w, background, interactive = await _saturated(preempt=False)
    ready = [w.ready_s(r) for r in interactive]
    assert _p95(ready) > SLO_SESSION_READY_P95_S, sorted(ready)
    assert all(w.done(r) for r in background + interactive)


async def test_ga24_a_preempted_run_yields_after_its_step_never_inside_a_tool_effect() -> None:
    w = _world(slots=1, preempt=True, min_slice_s=0)
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow(args: Mapping[str, Any], ctx: ToolContext) -> ToolExecution:
        entered.set()
        await release.wait()
        return ToolExecution(status="ok", content={"n": args["n"]})

    w.tools.add("step", slow, tags=[("category", "read")])
    w.admit("firing", "scheduled", 3)
    w.claimer.start()
    for _ in range(int(10 / TICK_S)):
        if entered.is_set():
            break
        await w.tick()
    assert entered.is_set(), [(e.type, e.data) for e in w.journal.events("s-firing")]
    w.admit("turn", "interactive", 1)
    for _ in range(int(1 / TICK_S)):
        await w.tick()
    assert "turn" in w.claimer.running, "the turn started at once in an overcommit slot"
    assert "firing" in w.claimer.running, "the scheduled run is inside its tool call: it is not interrupted"
    release.set()
    for _ in range(int(30 / TICK_S)):
        if w.done("firing") and w.done("turn"):
            break
        await w.tick()
    await w.stop()
    events = [e.type if e.type != "run.yielded" else f"run.yielded:{e.data['reason']}" for e in w.journal.events("s-firing")]
    yielded = events.index("run.yielded:preempted")
    assert "tool.outcome" in events[:yielded], "the step's outcome was journaled before the run gave its slot back"
    assert events.count("run.claimed") == 2
    assert [name for name, _ in w.tools.executed].count("step") == 3 + 1
    assert w.done("firing") and w.done("turn")


async def test_ga24_an_interactive_run_is_never_preempted() -> None:
    w = _world(slots=1, preempt=True, min_slice_s=0)
    w.admit("turn-a", "interactive", 6)
    w.claimer.start()
    for _ in range(int(2 / TICK_S)):
        await w.tick()
    w.admit("turn-b", "interactive", 1)
    for _ in range(int(3 / TICK_S)):
        await w.tick()
    assert w.claimer.running == ("turn-a",), "a full worker holding only interactive runs waits for a slot"
    for _ in range(int(30 / TICK_S)):
        if w.done("turn-a") and w.done("turn-b"):
            break
        await w.tick()
    await w.stop()
    assert not [e for e in w.journal.events("s-turn-a") if e.type == "run.yielded"]


async def test_ga24_a_run_holds_its_slot_for_the_minimum_slice_before_it_is_asked() -> None:
    w = _world(slots=1, preempt=True, min_slice_s=5)
    w.admit("firing", "scheduled", 20)
    w.claimer.start()
    await w.tick()
    w.admit("turn", "interactive", 1)
    for _ in range(int(4 / TICK_S)):
        await w.tick()
    assert w.claimer.running == ("firing",)
    for _ in range(int(2 / TICK_S)):
        await w.tick()
    assert "turn" in w.claimer.running
    await w.stop()


async def test_ga24_under_a_node_that_does_not_name_priority_preemption_nothing_is_preempted() -> None:
    w = _world(slots=1, preempt=True, min_slice_s=0, accepts=frozenset())
    w.admit("firing", "scheduled", 6)
    w.claimer.start()
    for _ in range(int(2 / TICK_S)):
        await w.tick()
    w.admit("turn", "interactive", 1)
    for _ in range(int(3 / TICK_S)):
        await w.tick()
    assert w.claimer.running == ("firing",), "an older Node would read aboveClass as any run: the worker never asks"
    await w.stop()


@pytest.mark.parametrize(
    ("raw", "overcommit", "slice_s"),
    [(None, DEFAULT_OVERCOMMIT, DEFAULT_MIN_SLICE_S), ("0", 0, 0.0), ("3", 3, 2.5), ("x", DEFAULT_OVERCOMMIT, DEFAULT_MIN_SLICE_S)],
)
def test_preemption_settings_parse_and_fall_back(
    monkeypatch: pytest.MonkeyPatch, raw: str | None, overcommit: int, slice_s: float
) -> None:
    for name, value in ((OVERCOMMIT_ENV, raw), (MIN_SLICE_ENV, {"0": "0", "3": "2.5", "x": "-1"}.get(raw or ""))):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    assert (preempt_overcommit(), preempt_min_slice_s()) == (overcommit, slice_s)


@pytest.mark.parametrize(
    ("cls", "expected"),
    [
        ("interactive", RatePriority.INTERACTIVE),
        ("approval_resume", RatePriority.INTERACTIVE),
        ("background", RatePriority.BACKGROUND),
        ("batch", RatePriority.BACKGROUND),
    ],
)
def test_ga24_each_class_draws_the_vendor_rate_budget_at_the_vectors_priority(cls: PriorityClass, expected: RatePriority) -> None:
    import json
    from pathlib import Path

    vector = json.loads(
        (Path(__file__).resolve().parents[5] / "contracts" / "harness" / "vectors" / "priority-classes.json").read_text()
    )
    assert rate_priority_for_class(cls) is expected
    assert vector["ratePriority"][cls] == expected.value


# ---- GA-24, mixed load: loop runs and engine runs over one provider limit (WP-48) ----

PROVIDER_LIMIT = 4
MIXED_STEPS = 12
LOOP_BACKGROUND = 8
ENGINE_BACKGROUND = 8
ENGINE_TURNS = 4


@dataclass
class _Provider:
    """The provider both processes call: every call takes ``TURN_S``; it counts the calls it serves at once."""

    clock: ManualClock
    serving: int = 0
    peak: int = 0
    first_call: dict[str, float] = field(default_factory=dict[str, float])

    async def serve(self, run: str) -> None:
        _ = self.first_call.setdefault(run, self.clock.monotonic())
        self.serving += 1
        self.peak = max(self.peak, self.serving)
        try:
            await self.clock.sleep(TURN_S)
        finally:
            self.serving -= 1


class _LoopProvider(RecordedTransport):
    """A loop run's model, called from the worker: ``steps`` tool calls, one per turn, then the answer."""

    def __init__(self, provider: _Provider) -> None:
        super().__init__()
        self.provider_side = provider

    async def complete(self, messages: list[Any], *args: Any, **kwargs: Any) -> ModelResponse:  # noqa: ANN401  # pyright: ignore[reportIncompatibleMethodOverride]
        text = _input_of_messages(messages)
        run = text.split(" ", 1)[0]
        await self.provider_side.serve(run)
        done = sum(1 for m in messages if isinstance(m, AssistantMessage))
        if done < _steps_of(text):
            step = ToolCall(id=f"{run}-{done}", name="step", arguments={"n": done})
            return ModelResponse(message=AssistantMessage(content=[], tool_calls=[step]), stop_reason=StopReason.TOOL_USE)
        return ModelResponse(message=AssistantMessage(content=[TextPart(text="done")]), stop_reason=StopReason.END_TURN)


class _EngineProvider(RecordedTransport):
    """What the model gateway forwards an engine run's call to: the same provider."""

    def __init__(self, provider: _Provider, run: str) -> None:
        super().__init__([reply_events("ok")])
        self.provider_side = provider
        self.run = run

    async def complete(self, *args: Any, **kwargs: Any) -> ModelResponse:  # noqa: ANN401  # pyright: ignore[reportIncompatibleMethodOverride]
        await self.provider_side.serve(self.run)
        return await super().complete(*args, **kwargs)


def _input_of_messages(messages: list[Any]) -> str:
    for message in messages:
        if isinstance(message, UserMessage) and "steps:" in str(message.content):
            content = message.content
            return content if isinstance(content, str) else "".join(getattr(part, "text", "") for part in content)
    raise AssertionError("no user input in the call")


@dataclass
class _LoopPort:
    """The loop's model port over its transport, sampled as ``TransportModelPort`` samples one."""

    transport: LLMTransport

    async def sample(self, call: ModelCall, on_delta: Callable[[DeltaKind, str], Awaitable[None]]) -> SampledTurn:
        response = await self.transport.complete(list(call.messages), tools=list(call.tools or []))
        return SampledTurn(message=response.message)

    def restore(self, raw: bytes, *, provider: str, transport: str) -> AssistantMessage | None:
        return None


class _AdmittedAssembler(_Assembler):
    """The worker's assembly as production's (``admitted``): the run's model admitted at the claim's class."""

    def __init__(self, transport: LLMTransport, tools: FunctionToolPort, admission: ProviderAdmission | None) -> None:
        super().__init__(None, tools)  # type: ignore[arg-type]
        self.transport, self.admission = transport, admission

    async def build(self, claim: Claim) -> AssembledRuntime:
        transport = self.transport
        if self.admission is not None:
            transport = AdmittedTransport(
                transport,
                admission=self.admission,
                priority_class=claim.priority_class or class_for_run_kind(claim.spec.run_kind),
                provider="openAI",
            )
        return AssembledRuntime(model=_LoopPort(transport), tools=self.tools, content=self.content, bindings=bindings.NEUTRAL)


@dataclass
class _Mixed:
    world: _World
    provider: _Provider
    rig: GatewayRig
    gates: list[RedisProviderGate]
    engine_started: dict[str, float] = field(default_factory=dict[str, float])
    engine_done: set[str] = field(default_factory=set[str])

    async def engine_run(self, run_id: str, cls: PriorityClass, calls: int) -> None:
        binding = self.rig.pin(run_id, priority_class=cls, transport=_EngineProvider(self.provider, run_id))
        self.engine_started[run_id] = self.world.clock.monotonic()
        body = {"model": ENGINE_MODEL_ID, "messages": [{"role": "user", "content": "go"}], "stream": False}
        for _ in range(calls):
            # A token for each call: on virtual time a run outlives one, as a long engine run renews its own.
            outcome = await self.rig.gateway.chat(self.rig.token(binding), {}, body_of(body))
            assert isinstance(outcome, ChatCompletion)
            await self.world.clock.sleep(TICK_S)  # the engine's own turn between its calls
        self.engine_done.add(run_id)

    def engine_ready_s(self, run_id: str) -> float:
        return self.provider.first_call[run_id] - self.engine_started[run_id]


async def _mixed_saturated(*, shared: bool) -> tuple[_Mixed, list[str], list[str], list[str]]:
    """Four worker slots and a provider limit of four. ``shared``: the worker's loop runs and the gateway's engine
    runs take their calls from one limit over Redis (two gate instances, as two processes); otherwise as WP-44 built
    it, the gateway's own gate and the loop's calls unadmitted. Eight scheduled loop runs and eight background
    engine runs of twelve calls start first; then a loop turn every two seconds, twelve in all, and four
    interactive engine runs among them."""
    clock = ManualClock()
    provider = _Provider(clock)
    redis = FakeRedisConnectionProvider()

    def shared_gate() -> RedisProviderGate:
        return RedisProviderGate(
            redis, PROVIDER_LIMIT, monotonic=clock.monotonic, sleep=clock.sleep, now_ms=lambda: int(clock.monotonic() * 1000)
        )

    gates = [shared_gate(), shared_gate()] if shared else []
    worker_gate: ProviderAdmission | None = gates[0] if shared else None
    gateway_gate: ProviderAdmission = gates[1] if shared else PriorityGate(PROVIDER_LIMIT, monotonic=clock.monotonic)
    journal = InMemoryJournal(clock=clock, lease_ms=3_600_000)
    tools = FunctionToolPort()
    tools.add("step", _step, tags=[("category", "read")])
    asked = PreemptRequests()
    assembler = _AdmittedAssembler(_LoopProvider(provider), tools, worker_gate)
    driver = RunDriver(journal=journal, assembler=assembler, clock=clock, sleep=clock.sleep, preempt=asked)
    claimer = RunClaimer(
        journal=journal, driver=driver, worker_id="w1", slots=4, clock=clock, sleep=clock.sleep,
        idle_poll_s=IDLE_POLL_S, max_idle_poll_s=2 * IDLE_POLL_S, preempt=asked,
    )
    world = _World(clock, journal, provider, tools, claimer)  # type: ignore[arg-type]
    mixed = _Mixed(world, provider, GatewayRig(clock=clock, gate=gateway_gate), gates)
    loop_background = [f"firing-{i}" for i in range(LOOP_BACKGROUND)]
    engine_background = [f"engine-bg-{i}" for i in range(ENGINE_BACKGROUND)]
    for run_id in loop_background:
        world.admit(run_id, "scheduled", MIXED_STEPS)
    engines = [asyncio.ensure_future(mixed.engine_run(r, "background", MIXED_STEPS)) for r in engine_background]
    claimer.start()
    for _ in range(int(5 / TICK_S)):
        await world.tick()
    interactive = [f"turn-{i}" for i in range(12)]
    engine_turns = [f"engine-turn-{i}" for i in range(ENGINE_TURNS)]
    for i, run_id in enumerate(interactive):
        world.admit(run_id, "interactive", 2)
        if i % 3 == 0:
            engines.append(asyncio.ensure_future(mixed.engine_run(engine_turns[i // 3], "interactive", 2)))
        for _ in range(int(2 / TICK_S)):
            await world.tick()
    for _ in range(int(600 / TICK_S)):
        if all(world.done(r) for r in loop_background + interactive) and all(e.done() for e in engines):
            break
        await world.tick()
    await world.stop()
    for engine in engines:
        await engine
    for gate in gates:
        await gate.aclose()
    return mixed, loop_background + engine_background, interactive, engine_turns


async def test_ga24_mixed_loop_and_engine_load_one_provider_limit_holds_and_people_go_first() -> None:
    mixed, background, interactive, engine_turns = await _mixed_saturated(shared=True)
    w = mixed.world
    assert mixed.provider.peak <= PROVIDER_LIMIT, "loop and engine calls together never exceed the provider's limit"
    loop_ready = [w.ready_s(r) for r in interactive]
    engine_ready = [mixed.engine_ready_s(r) for r in engine_turns]
    assert _p95(loop_ready) <= SLO_SESSION_READY_P95_S, sorted(loop_ready)
    assert max(engine_ready) <= SLO_SESSION_READY_P95_S, sorted(engine_ready)
    assert all(w.done(r) for r in interactive)
    assert all(w.done(r) for r in background if r.startswith("firing-")), "every scheduled loop run completed"
    assert {r for r in background if r.startswith("engine-")} <= mixed.engine_done, "every background engine run completed"
    steps = [name for name, _ in w.tools.executed]
    assert len(steps) == LOOP_BACKGROUND * MIXED_STEPS + 12 * 2, "no loop step ran twice"


async def test_ga24_control_with_only_the_gateways_own_gate_the_loop_runs_exceed_the_provider_limit() -> None:
    """What the mixed case measures: as WP-44 built it, the loop's calls never took a provider slot."""
    mixed, _, _, _ = await _mixed_saturated(shared=False)
    assert mixed.provider.peak > PROVIDER_LIMIT, mixed.provider.peak
