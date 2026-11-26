"""A Docling 429 holds a record the dispatch could not judge once its resolved parse tier names Docling's pool (F23g).

The dispatch hold (``held_downstream``) reads the event's own labels, so a stored PDF sent with no mime and a light
extension is passed there. Its START_PARSING tier is raised to heavy once the handler has resolved the stored mime,
and ``wait_out_held_service`` holds it then: inside START_PARSING's own parse wait (the budget paused once, the same
bound), and handed back when the pause outlasts that bound, since a gate with room admits whatever the timeout.

A real ``BackpressureCoordinator`` on a fake clock; a patched ``asyncio.sleep`` advances that clock, so loop time is
never asserted.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest

from app.services.messaging import consumer_concurrency as concurrency
from app.services.messaging.backpressure import BackpressureCoordinator
from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    PipelineEventData,
    StreamMessage,
)
from app.services.resource_governor import DownstreamFeedback
from app.services.resource_governor.feedback import set_default_downstream_feedback
from app.services.resource_governor.models import ParseTier
from tests.unit.services.messaging.governor_test_helpers import make_test_governor
from tests.unit.services.messaging.test_indexing_end import _Tracker
from tests.unit.services.messaging.test_indexing_message_binding import (
    _kafka,
    _kafka_message,
    _redis,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

DOCLING = "DoclingService"
PARSING = "ParsingService"
PAUSE = 30.0
LOG = logging.getLogger("test_docling_hold_recheck")
# A stored PDF sent with no mime and a light extension: the dispatch reads it as text, the handler as a PDF.
UNJUDGED = {"recordId": "r1", "orgId": "org-1", "connectorId": "c1", "extension": "txt"}
JUDGED = {**UNJUDGED, "extension": "pdf", "mimeType": "application/pdf"}  # the dispatch already held such a record
TEXT = {**UNJUDGED, "mimeType": "text/plain"}


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def _fresh_feedback() -> Iterator[None]:
    set_default_downstream_feedback(DownstreamFeedback())
    yield
    set_default_downstream_feedback(None)


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


@pytest.fixture
def coordinator(clock: _Clock) -> BackpressureCoordinator:
    return BackpressureCoordinator(clock=clock)


@pytest.fixture
def slept(monkeypatch: pytest.MonkeyPatch, clock: _Clock) -> list[tuple[float, object]]:
    """Each sleep with the record budget's deadline at that moment (None: paused); the coordinator clock advances."""
    seen: list[tuple[float, object]] = []
    real_sleep = asyncio.sleep

    async def fake(seconds: float, *_a: object, **_k: object) -> None:
        seen.append((seconds, _BUDGET[0].when() if _BUDGET else "no budget"))
        clock.now += seconds
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", fake)
    return seen


_BUDGET: list[asyncio.Timeout] = []


def _host(coordinator: BackpressureCoordinator) -> SimpleNamespace:
    governor = SimpleNamespace(ceilings=SimpleNamespace(index_light=64))
    return SimpleNamespace(backpressure_coordinator=coordinator, governor=governor, stage_admission=None, logger=LOG)


async def _hold(host: SimpleNamespace, payload: dict[str, str], tier: ParseTier, *, scoped: bool = True) -> None:
    """``wait_out_held_service`` as the consumers call it: inside START_PARSING's parse wait, under a record budget."""
    async with asyncio.timeout(3600) as budget:
        _BUDGET[:] = [budget]
        try:
            async with concurrency.parse_admission_wait(host, budget, "m-1") as wait:
                await concurrency.wait_out_held_service(
                    host, tier, StreamMessage(eventType="newRecord", payload=payload), wait, scoped=scoped
                )
        finally:
            _BUDGET.clear()


class TestWaitOutHeldService:
    async def test_an_unjudged_record_waits_the_docling_pause_out_with_its_budget_paused(self, coordinator, clock, slept) -> None:
        coordinator.signal(DOCLING, PAUSE)
        await _hold(_host(coordinator), UNJUDGED, ParseTier.HEAVY)
        assert sum(seconds for seconds, _ in slept) == PAUSE
        assert all(deadline is None for _, deadline in slept), "the budget is paused while it waits"
        assert coordinator.pause_remaining() == 0.0

    async def test_a_record_the_dispatch_judged_is_not_held_again(self, coordinator, slept) -> None:
        coordinator.signal(DOCLING, PAUSE)
        await _hold(_host(coordinator), JUDGED, ParseTier.HEAVY)
        assert slept == []

    async def test_a_light_record_never_waits(self, coordinator, slept) -> None:
        coordinator.signal(DOCLING, PAUSE)
        await _hold(_host(coordinator), TEXT, ParseTier.LIGHT)
        assert slept == []

    @pytest.mark.parametrize("paused", [None, PARSING], ids=["no-pause", "shared-service"])
    async def test_only_a_tier_scoped_pause_holds_here(self, coordinator, slept, paused: str | None) -> None:
        if paused:
            coordinator.signal(paused, PAUSE)  # the read stop already covers a service every record calls
        await _hold(_host(coordinator), UNJUDGED, ParseTier.HEAVY)
        assert slept == []

    async def test_a_longer_shared_service_pause_is_not_waited_out_here(self, coordinator, slept) -> None:
        coordinator.signal(DOCLING, PAUSE)
        coordinator.signal(PARSING, 3 * PAUSE)  # the read stop's job; held here it would keep its permit and memory
        await _hold(_host(coordinator), UNJUDGED, ParseTier.HEAVY)
        assert sum(seconds for seconds, _ in slept) == PAUSE

    async def test_a_broker_order_consumer_does_not_wait_here(self, coordinator, slept) -> None:
        coordinator.signal(DOCLING, PAUSE)  # it stops reading for every pause instead
        await _hold(_host(coordinator), UNJUDGED, ParseTier.HEAVY, scoped=False)
        assert slept == []

    async def test_a_pause_that_outlasts_the_wait_hands_the_record_back(
        self, coordinator, slept, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(concurrency, "parse_admission_wait_seconds", lambda: 0.0)
        coordinator.signal(DOCLING, PAUSE)
        with pytest.raises(concurrency.ParseAdmissionTimeout) as raised:
            await _hold(_host(coordinator), UNJUDGED, ParseTier.HEAVY)
        assert raised.value.pool == "docling"
        assert slept == []

    async def test_a_pause_that_clears_before_the_bound_lets_the_record_on(
        self, coordinator, slept, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(concurrency, "parse_admission_wait_seconds", lambda: PAUSE + 10)
        coordinator.signal(DOCLING, PAUSE)
        await _hold(_host(coordinator), UNJUDGED, ParseTier.HEAVY)
        assert sum(seconds for seconds, _ in slept) == PAUSE


# The consumers: one START_PARSING for the unjudged record, a free parse slot, and a Docling pause.
def _handler() -> object:
    async def handler(_message: object) -> AsyncIterator[PipelineEvent]:
        yield PipelineEvent(event=IndexingEvent.START_PARSING, data=PipelineEventData(record_id="r1", tier=ParseTier.HEAVY, size_bytes=10))
        for event in (IndexingEvent.PARSING_COMPLETE, IndexingEvent.INDEXING_COMPLETE):
            yield PipelineEvent(event=event, data=PipelineEventData(record_id="r1"))

    return handler


async def _deliver(broker: str, coordinator: BackpressureCoordinator) -> dict[str, Any]:
    value = json.dumps({"eventType": "newRecord", "payload": UNJUDGED, "requestId": "req-hold"})
    out: dict[str, Any] = {"acquired": 0, "waits": 0}
    real_acquire, real_wait = concurrency.acquire_parsing_slot, concurrency.parse_admission_wait

    async def acquire(*a: object, **k: object) -> object:
        out["acquired"] += 1
        return await real_acquire(*a, **k)

    @contextlib.asynccontextmanager
    async def counted_wait(*a: object, **k: object) -> AsyncIterator[object]:
        out["waits"] += 1
        async with real_wait(*a, **k) as wait:
            yield wait

    c = _redis(retry_manager=_Tracker()) if broker == "redis" else _kafka()
    if broker == "kafka":
        c.retry_manager = _Tracker()
    c._requeue_message = AsyncMock()
    c.message_handler = _handler()
    c.backpressure_coordinator = coordinator
    c.governor = make_test_governor(logger_name="test-docling-hold")  # real gates: one with room admits whatever the timeout
    c._scheduler = object()  # a fair consumer: it holds Docling's records rather than stopping its reads
    if broker == "kafka":
        c._IndexingKafkaConsumer__rebuffer_after_failed_handback = AsyncMock()
    with patch.object(concurrency, "acquire_parsing_slot", side_effect=acquire), \
         patch.object(concurrency, "parse_admission_wait", counted_wait):
        if broker == "redis":
            out["ok"] = await c._process_message_wrapper("record-events", "5-0", {"value": value})
        else:
            out["ok"] = await c._IndexingKafkaConsumer__process_message_wrapper(_kafka_message(value.encode()))
    out["requeued"] = c._requeue_message.await_count
    return out


BROKERS = pytest.mark.parametrize("broker", ["redis", "kafka"])


@BROKERS
async def test_the_consumer_waits_the_pause_out_before_the_parse_permit(broker: str, coordinator, slept) -> None:
    coordinator.signal(DOCLING, PAUSE)
    out = await _deliver(broker, coordinator)
    assert out["ok"] and out["acquired"] == 1, out
    assert sum(seconds for seconds, _ in slept) == PAUSE
    assert out["waits"] == 1, "the Docling wait shares START_PARSING's parse wait: no third paused window"


@BROKERS
async def test_a_free_slot_does_not_let_a_held_record_through_once_the_bound_is_spent(
    broker: str, coordinator, slept, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(concurrency, "parse_admission_wait_seconds", lambda: 0.0)
    coordinator.signal(DOCLING, PAUSE)
    out = await _deliver(broker, coordinator)
    assert out["acquired"] == 0, f"admitted into Docling's pause through a free slot: {out}"  # a free slot admits at 0 s
    assert out["requeued"] == 1 and not out["ok"], out
    assert out["waits"] == 1


@BROKERS
async def test_without_a_pause_the_record_goes_straight_on(broker: str, coordinator, slept) -> None:
    out = await _deliver(broker, coordinator)
    assert out["ok"] and out["acquired"] == 1 and slept == [] and out["waits"] == 1, out
