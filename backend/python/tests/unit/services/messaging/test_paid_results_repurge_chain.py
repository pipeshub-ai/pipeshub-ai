"""The delayed re-purge is a chain of short hops that covers the longest paid stage (D-160)."""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.config.constants.arangodb import EventTypes
from app.core import envelope
from app.core.org_context import OrgContext, bind, bind_optional
from app.services.messaging.config import IndexingEvent, StreamMessage
from app.services.paid_cache import access, repurge
from app.services.paid_cache.cache import PaidResultCache
from app.services.paid_cache.keys import CacheKey
from app.services.paid_cache.locks import (
    LOCK_TTL_MS,
    REPURGE_HOP_S,
    STAGE_DEADLINE_S,
    InProcessLocks,
    repurge_horizon_s,
)
from app.services.paid_cache.repurge import (
    next_hop_payload,
    repurge_event_payload,
    schedule_next_hop,
)
from app.services.paid_cache.run import RunScope, clear_run
from tests.support.paid_cache import MemoryResultStore
from tests.support.record_handler import make_record_handler

ORG, REC = "org-1", "rec-1"
DELETE = {"recordId": REC, "orgId": ORG, "virtualRecordId": "vr"}


def _page(i: int) -> CacheKey:
    return CacheKey(ORG, REC, "ocr_page", 1, "m", "p", "q", f"p{i}")


async def _paid() -> str:
    return "text"


def _handler(producer: Any) -> Any:  # noqa: ANN401
    handler = make_record_handler(producer=producer)
    handler.event_processor.graph_provider.get_document = AsyncMock(return_value={"_key": REC, "orgId": ORG, "virtualRecordId": "vr"})
    handler.event_processor.processor.indexing_pipeline.bulk_delete_embeddings = AsyncMock()
    handler._request_code_edge_build_for_deleted = AsyncMock()
    handler._hand_over_shared_copy = AsyncMock()
    return handler


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    now = [1_000_000.0]
    monkeypatch.setattr(repurge, "time", SimpleNamespace(time=lambda: now[0]))
    return now


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> Any:  # noqa: ANN401
    store, locks = MemoryResultStore(), InProcessLocks()
    cache = PaidResultCache(store, locks)
    monkeypatch.setattr(access, "paid_cache_for", lambda *_a, **_k: cache)
    yield store, locks, cache
    clear_run()


class _Wire:
    """Publishes the way the messaging producer does (envelope injected, then signed) and remembers when."""

    def __init__(self, clock: list[float]) -> None:
        self.clock = clock
        self.sent: list[tuple[str, dict, float, float]] = []   # topic, wire, fake time published, real time signed

    async def send_event(self, topic: str, event_type: str, payload: dict, key: str | None = None) -> bool:
        from app.utils.request_context import inject_envelope

        message = {"eventType": event_type, "payload": payload, "timestamp": int(time.time() * 1000)}
        self.sent.append((topic, envelope.sign_message(topic, inject_envelope(message)), self.clock[0], time.time()))
        return True


async def _consume(handler: Any, topic: str, wire: dict, *, age_s: float, signed_at: float) -> list:  # noqa: ANN401
    keys = await envelope._keys() if envelope._keys else ()
    opened = await envelope.open_envelope(topic, wire, keys=keys, now=lambda: signed_at + age_s)
    msg = StreamMessage(**opened.message, org_ctx=opened.ctx)
    with bind_optional(envelope.ctx_of(msg)):
        return [e.event async for e in handler.process_event(msg.eventType, msg.payload) if e.event != IndexingEvent.START_DOWNLOAD]


def test_the_chain_covers_the_longest_paid_stage() -> None:
    assert REPURGE_HOP_S == LOCK_TTL_MS // 1000 + 120 + 60 == 300
    assert repurge_horizon_s() >= STAGE_DEADLINE_S + LOCK_TTL_MS // 1000
    assert STAGE_DEADLINE_S >= 2400                       # a parse may run that long (api/routes/parsing.py)


def test_the_first_hop_carries_when_the_record_was_deleted() -> None:
    payload = repurge_event_payload(ORG, REC, now=5000.0)
    assert (payload["deletedAt"], payload["hop"], payload["_retry_not_before"]) == (5000.0, 1, 5000.0 + REPURGE_HOP_S)


def test_the_chain_stops_once_the_horizon_has_passed_and_never_trusts_a_future_delete_time() -> None:
    first = repurge_event_payload(ORG, REC, now=0.0)
    assert next_hop_payload(ORG, first, now=repurge_horizon_s()) is not None
    assert next_hop_payload(ORG, first, now=repurge_horizon_s() + 1) is None
    assert next_hop_payload(ORG, {"orgId": ORG, "recordId": REC}, now=10.0) is None            # an older single-hop event
    assert next_hop_payload(ORG, {**first, "deletedAt": "soon"}, now=10.0) is None
    assert next_hop_payload(ORG, {**first, "deletedAt": float("nan")}, now=10.0) is None
    future = next_hop_payload(ORG, {**first, "deletedAt": 10.0**12}, now=100.0)
    assert future is not None and future["deletedAt"] == 100.0                                  # clamped to now


def test_a_hop_takes_its_org_from_the_caller_not_the_body() -> None:
    body = {**repurge_event_payload("org-evil", REC, now=0.0)}
    assert next_hop_payload(ORG, body, now=10.0)["orgId"] == ORG  # type: ignore[index]


async def test_a_hop_whose_next_cannot_be_published_raises_so_it_is_redelivered(world: Any, clock: list[float]) -> None:  # noqa: ANN401
    producer = AsyncMock()
    producer.send_event.side_effect = ConnectionError("broker down")
    with pytest.raises(ConnectionError):
        await schedule_next_hop(producer, ORG, repurge_event_payload(ORG, REC, now=clock[0]))


async def test_a_hop_with_no_producer_fails_while_the_chain_goes_on(clock: list[float]) -> None:
    with pytest.raises(RuntimeError):
        await schedule_next_hop(None, ORG, repurge_event_payload(ORG, REC, now=clock[0]))
    assert await schedule_next_hop(None, ORG, {"orgId": ORG, "recordId": REC}) is False        # an event of the old shape


async def test_an_orphaned_parse_paying_for_2400_s_after_a_delete_with_redis_flushed_leaves_nothing(world: Any, clock: list[float]) -> None:  # noqa: ANN401
    store, locks, cache = world
    producer = AsyncMock()
    handler = _handler(producer)
    delete_at = clock[0]
    with bind(OrgContext(ORG, None, "service", "envelope", "req-1")):
        [e async for e in handler.process_event(EventTypes.DELETE_RECORD.value, DELETE)]
        locks._held.clear()                                           # Redis lost the fence

        scope = RunScope(ORG, REC, "rev1", "parse")
        pending = [producer.send_event.await_args.kwargs["payload"]]
        hops, page = 0, 0
        while clock[0] - delete_at < 2400:
            clock[0] += 50
            await scope.note(cache.store, _page(page).relative_path(), opens=True)
            await cache.get_or_compute(_page(page), _paid)           # the orphan pays for one more page
            page += 1
            while pending and pending[0]["_retry_not_before"] <= clock[0]:
                due = pending.pop(0)
                producer.send_event.reset_mock()
                [e async for e in handler.process_event("purgePaidResults", due)]
                hops += 1
                if producer.send_event.await_args is not None:
                    pending.append(producer.send_event.await_args.kwargs["payload"])
        await scope.finish(cache.store)                              # the parse ends with its manifest
        assert store.paths(ORG, REC) != []
        while pending:                                                # the hops that remain
            clock[0] = max(clock[0], pending[0]["_retry_not_before"])
            due = pending.pop(0)
            producer.send_event.reset_mock()
            [e async for e in handler.process_event("purgePaidResults", due)]
            hops += 1
            if producer.send_event.await_args is not None:
                pending.append(producer.send_event.await_args.kwargs["payload"])

    assert store.paths(ORG, REC) == [], f"left: {store.paths(ORG, REC)}"
    assert repurge_horizon_s() // REPURGE_HOP_S <= hops <= repurge_horizon_s() // REPURGE_HOP_S + 2   # then the chain ends
    assert clock[0] - delete_at <= repurge_horizon_s() + REPURGE_HOP_S


@pytest.mark.parametrize("lag_s", [0, 300, 590])
async def test_every_hop_verifies_within_the_signature_window(
    world: Any, clock: list[float], monkeypatch: pytest.MonkeyPatch, lag_s: int,  # noqa: ANN401
) -> None:
    monkeypatch.setenv("ENVELOPE_SIGN", "true")
    monkeypatch.setenv("ORG_CONTEXT_MODE", "enforce")
    monkeypatch.setattr(envelope, "_sign_key", b"k" * 32)
    monkeypatch.setattr(envelope, "_keys", AsyncMock(return_value=[b"k" * 32]))
    wire = _Wire(clock)
    handler = _handler(wire)
    with bind(OrgContext(ORG, None, "service", "envelope", "req-1")):
        [e async for e in handler.process_event(EventTypes.DELETE_RECORD.value, DELETE)]
    delete_at, consumed = clock[0], 0
    while wire.sent and consumed < 60:
        topic, signed, published_at, signed_at = wire.sent[consumed]
        due = published_at + REPURGE_HOP_S                            # the consumer holds it until then, plus its lag
        clock[0] = due + lag_s
        age = clock[0] - published_at
        assert age < 900 and age == REPURGE_HOP_S + lag_s             # never the window's worth of holding
        events = await _consume(handler, topic, signed, age_s=age, signed_at=signed_at)
        assert events == [IndexingEvent.PARSING_COMPLETE, IndexingEvent.INDEXING_COMPLETE]
        consumed += 1
        if consumed == len(wire.sent):
            break
    # A lagging consumer reaches the horizon in fewer hops; the last hop always runs after it (it is the one
    # that finds nothing left to schedule).
    assert (repurge_horizon_s() // REPURGE_HOP_S <= consumed <= repurge_horizon_s() // REPURGE_HOP_S + 2) if lag_s == 0 else consumed >= 3
    assert clock[0] - delete_at > repurge_horizon_s()


async def test_a_hop_older_than_the_window_is_still_rejected(world: Any, clock: list[float], monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN401
    monkeypatch.setenv("ENVELOPE_SIGN", "true")
    monkeypatch.setenv("ORG_CONTEXT_MODE", "enforce")
    monkeypatch.setattr(envelope, "_sign_key", b"k" * 32)
    monkeypatch.setattr(envelope, "_keys", AsyncMock(return_value=[b"k" * 32]))
    wire = _Wire(clock)
    with bind(OrgContext(ORG, None, "service", "envelope", "req-1")):
        [e async for e in _handler(wire).process_event(EventTypes.DELETE_RECORD.value, DELETE)]
    topic, signed, _published, signed_at = wire.sent[0]
    with pytest.raises(envelope.EnvelopeRejected):
        await _consume(_handler(AsyncMock()), topic, signed, age_s=901, signed_at=signed_at)


def test_a_chain_keeps_the_horizon_it_started_with_when_the_budget_is_lowered_later(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.paid_cache.locks import repurge_horizon_s

    monkeypatch.setenv("PDF_RECORD_PROCESSING_TIMEOUT", "21600")
    long_horizon = repurge_horizon_s()
    first = repurge_event_payload(ORG, REC, now=0.0)
    assert first["horizonS"] == long_horizon

    monkeypatch.setenv("PDF_RECORD_PROCESSING_TIMEOUT", "900")           # lowered while the chain runs
    assert repurge_horizon_s() < long_horizon
    assert next_hop_payload(ORG, first, now=long_horizon - 1) is not None
    carried = next_hop_payload(ORG, first, now=1000.0)
    assert carried is not None and carried["horizonS"] == long_horizon  # carried by every later hop
    assert next_hop_payload(ORG, first, now=long_horizon + 1) is None


def test_a_carried_horizon_that_is_not_a_sane_duration_is_ignored_or_capped() -> None:
    from app.services.paid_cache.locks import repurge_horizon_s
    from app.services.paid_cache.repurge import MAX_CARRIED_HORIZON_S

    past = repurge_horizon_s() + 1
    for bad in (float("inf"), float("nan"), -5, "9999999", True):
        payload = {**repurge_event_payload(ORG, REC, now=0.0), "horizonS": bad}
        assert next_hop_payload(ORG, payload, now=past) is None, bad          # the live horizon applies
    huge = {**repurge_event_payload(ORG, REC, now=0.0), "horizonS": 10**12}
    assert next_hop_payload(ORG, huge, now=MAX_CARRIED_HORIZON_S - 1) is not None
    assert next_hop_payload(ORG, huge, now=MAX_CARRIED_HORIZON_S + 1) is None   # capped


def test_a_hostile_figure_in_a_hop_ends_the_chain_instead_of_raising() -> None:
    """An integer too large for a float must not raise on every delivery."""
    base = repurge_event_payload(ORG, REC, now=0.0)
    assert next_hop_payload(ORG, {**base, "deletedAt": 10**400}, now=10.0) is None
    assert next_hop_payload(ORG, {**base, "deletedAt": -(10**400)}, now=10.0) is None
    assert next_hop_payload(ORG, {**base, "deletedAt": float("nan")}, now=10.0) is None
    carried = next_hop_payload(ORG, {**base, "horizonS": 10**400}, now=10.0)   # ignored, the live horizon applies
    assert carried is not None and carried["horizonS"] == repurge_horizon_s()


def test_the_first_hop_of_an_org_carries_the_horizon_too() -> None:
    from app.services.paid_cache.repurge import org_repurge_event_payload

    first = org_repurge_event_payload(ORG, now=0.0)
    assert first["horizonS"] == repurge_horizon_s()
    following = next_hop_payload(ORG, first, now=100.0)
    assert following is not None and following["horizonS"] == repurge_horizon_s() and following["scope"] == "org"
