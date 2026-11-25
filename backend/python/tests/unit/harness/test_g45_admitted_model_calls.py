"""G-45 / GA-24: a loop run's own model calls take their provider slot, at the run's class, as engine calls do.

Before, only an engine's calls through the model gateway waited for a provider slot; a loop run called its model
from the worker directly, so a worker full of background loop runs held provider calls a person's turn could not
pass. ``AdmittedTransport`` takes the slot next to the provider, under the ledger's metering, for every call the
run makes (the turn, a stream, a structured call) and gives it back when the answer settles or its reader leaves.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest

from app.harness.assembly.model import ResolvedModel, admitted, metered
from harness_core.budget.metered import MeteredTransport
from harness_core.gateway.priority_gate import PriorityGate, provider_key
from harness_core.kernel.core.messages import UserMessage
from harness_core.models.transports.admitted import AdmittedTransport
from harness_core.testing import ManualClock
from harness_core.testing.model_gateway import RecordedTransport, reply_events

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from harness_core.kernel.core.streaming import StreamEvent
    from harness_core.priority import PriorityClass


class _Held(RecordedTransport):
    """Answers once ``go`` is set; records the calls it is serving at once."""

    def __init__(self, go: asyncio.Event) -> None:
        super().__init__([reply_events("ok")])
        self.go = go
        self.serving = 0
        self.peak = 0

    async def complete(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401  # pyright: ignore[reportIncompatibleMethodOverride]
        self.serving += 1
        self.peak = max(self.peak, self.serving)
        try:
            await self.go.wait()
            return await super().complete(*args, **kwargs)
        finally:
            self.serving -= 1

    async def stream(self, *args: Any, **kwargs: Any) -> AsyncIterator[StreamEvent]:  # noqa: ANN401  # pyright: ignore[reportIncompatibleMethodOverride]
        await self.go.wait()
        async for event in super().stream(*args, **kwargs):
            yield event


def _gate(limit: int) -> PriorityGate:
    return PriorityGate(limit, floor_every=0, monotonic=ManualClock().monotonic)


def _messages() -> list[Any]:
    return [UserMessage(content="hi")]


async def _turns(n: int = 30) -> None:
    for _ in range(n):
        await asyncio.sleep(0)


async def test_ga24_a_loop_call_holds_its_provider_slot_until_the_answer_and_a_person_goes_first() -> None:
    go = asyncio.Event()
    inner = _Held(go)
    gate = _gate(1)
    order: list[str] = []

    async def call(name: str, cls: PriorityClass) -> None:
        _ = await AdmittedTransport(inner, admission=gate, priority_class=cls).complete(_messages())
        order.append(name)

    first = asyncio.create_task(call("firing-1", "background"))
    await _turns()
    assert gate.in_flight("openai") == 1
    queued = [asyncio.create_task(call("firing-2", "background"))]
    await _turns()
    queued.append(asyncio.create_task(call("turn", "interactive")))
    await _turns()
    assert gate.waiting("openai") == 2 and inner.peak == 1
    go.set()
    await asyncio.gather(first, *queued)
    assert order == ["firing-1", "turn", "firing-2"]
    assert gate.in_flight("openai") == 0


async def test_ga24_a_streamed_call_holds_its_slot_until_the_stream_ends_or_its_reader_leaves() -> None:
    go = asyncio.Event()
    go.set()
    gate = _gate(2)
    transport = AdmittedTransport(_Held(go), admission=gate, priority_class="background")
    stream = transport.stream(_messages())
    assert gate.in_flight("openai") == 0, "nothing is taken before the stream is read"
    _ = await anext(aiter(stream))
    assert gate.in_flight("openai") == 1
    await stream.aclose()  # pyright: ignore[reportAttributeAccessIssue]
    assert gate.in_flight("openai") == 0
    async for _ in transport.stream(_messages()):
        pass
    assert gate.in_flight("openai") == 0


async def test_a_provider_failure_gives_the_slot_back() -> None:
    gate = _gate(1)
    failing = RecordedTransport(fail=RuntimeError("provider down"))
    transport = AdmittedTransport(failing, admission=gate, priority_class="interactive")
    with pytest.raises(RuntimeError):
        _ = await transport.complete(_messages())
    with pytest.raises(RuntimeError):
        async for _ in transport.stream(_messages()):
            pass
    assert gate.in_flight("openai") == 0


def test_ga24_a_loop_run_and_the_gateway_count_one_provider_under_one_name() -> None:
    """The loop's configured model says ``azureOpenAI``; the gateway's route says ``azureopenai``."""
    transport = AdmittedTransport(RecordedTransport(family="azure_openai"), admission=_gate(1), priority_class=None,
                                  provider="azureOpenAI")
    assert transport.key == provider_key("azureopenai") == "azureopenai"


def _model(transport: RecordedTransport) -> ResolvedModel:
    return ResolvedModel(
        raw_llm=None, provider="openAI", is_multimodal=False, context_length=None, is_reasoning=False,
        port=None, transport=transport,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize(
    ("claimed", "run_kind", "expected"),
    [("approval_resume", "scheduled", "approval_resume"), (None, "scheduled", "background"), (None, None, "interactive")],
)
def test_ga24_the_run_model_is_admitted_at_its_class_under_the_ledger(
    monkeypatch: pytest.MonkeyPatch, claimed: PriorityClass | None, run_kind: str | None, expected: PriorityClass
) -> None:
    """Admitted next to the provider, then metered: the slot is held only while the provider answers."""
    from harness_core.testing.ids import SeededIds

    monkeypatch.setattr("app.harness.assembly.model.ledger_enabled", lambda: True)
    inner = RecordedTransport()
    run = admitted(_model(inner), _gate(1), claimed, run_kind)
    assert isinstance(run.transport, AdmittedTransport)
    assert run.transport.priority_class == expected and run.transport.key == "openai"
    charged, _ = metered(run, accounts=object(), account=object(), pricing_model="gpt-x", ids=SeededIds(0))  # type: ignore[arg-type]
    assert isinstance(charged.transport, MeteredTransport)
    assert charged.transport._inner is run.transport  # pyright: ignore[reportPrivateUsage]


def test_without_an_admission_the_model_is_unchanged() -> None:
    model = _model(RecordedTransport())
    assert admitted(model, None, "background", "scheduled") is model


async def test_ga24_the_assembler_admits_the_loop_runs_model_at_the_claims_class() -> None:
    """The worker's assembler: the run's model, as ``_model`` hands it to the loop, takes its slots at the class
    the claim names."""
    import dataclasses

    from app.harness.assembly.assembler import PipesHubRuntimeAssembler
    from app.harness.classification import PassthroughGuard
    from harness_core.logs import HarnessLog
    from tests.unit.harness.assembly import world as w

    gate = _gate(4)
    inner = RecordedTransport()

    async def factory(run: object, services: object) -> ResolvedModel:
        return _model(inner)

    assembler = PipesHubRuntimeAssembler(w.Services(), model_factory=factory, admission=gate)  # type: ignore[arg-type]
    claim = dataclasses.replace(w.claim(w.pinned_spec(tool_ids=[])), priority_class="background")
    run = assembler.container(claim).run_context()
    log = HarnessLog.named("test")
    model, *_ = await assembler._model(run, claim, log, None, PassthroughGuard())  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]
    # With no classification the guard wraps nothing: the model's transport is the admitted one.
    admitted = model.transport
    assert isinstance(admitted, AdmittedTransport)
    assert admitted.priority_class == "background"
    assert admitted._admission is gate  # pyright: ignore[reportPrivateUsage]
