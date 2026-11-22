"""The admission counters are wired into both consumers' message wrappers, per stage, with the message handed on.

Each test drives the real wrapper of the Redis Streams and the Kafka consumer and looks at the counters while the record
waits. A call site that is dropped, a counter that is never decremented or a stage that loses its message fails here.
"""
from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.messaging import consumer_concurrency as cc
from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    PipelineEventData,
)
from app.services.resource_governor.models import ParseTier, Pool
from tests.unit.services.messaging.test_download_ahead_cycles import build

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

H = ParseTier.HEAVY
KINDS = ["redis", "kafka"]


class Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, ParseTier, str, Any]] = []

    def entered(self, tier: ParseTier, phase: str, message: Any) -> None:  # noqa: ANN401
        self.events.append(("in", tier, phase, message))

    def left(self, tier: ParseTier, phase: str, message: Any) -> None:  # noqa: ANN401
        self.events.append(("out", tier, phase, message))


def message_of(rid: str, ext: str = "pdf") -> Any:  # noqa: ANN401
    from app.services.messaging.config import StreamMessage

    return StreamMessage(eventType="newRecord", payload={"recordId": rid, "extension": ext}, requestId=rid)


def start(kind: str, consumer: Any, rid: str, token: Any, ext: str = "pdf") -> asyncio.Task:  # noqa: ANN401
    """Run the consumer's real wrapper for one record, with the waiter token the dispatcher would have made."""
    parsed = message_of(rid, ext)
    env = {"eventType": "newRecord", "payload": parsed.payload}
    if kind == "redis":
        coro = consumer._process_message_wrapper("t", f"{rid}-0", {"value": json.dumps(env)}, token, parsed)
    else:
        raw = MagicMock()
        raw.topic, raw.partition, raw.offset, raw.value = "t", 0, 1, json.dumps(env).encode()
        coro = consumer._IndexingKafkaConsumer__process_message_wrapper(raw, token, parsed, None)
    return asyncio.create_task(coro)


async def settle() -> None:
    await asyncio.sleep(0.15)


async def stop(task: asyncio.Task) -> None:
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def quick_handler(message: Any) -> AsyncGenerator[PipelineEvent, None]:  # noqa: ANN401
    yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=message.payload["recordId"]))


@pytest.mark.parametrize("kind", KINDS)
async def test_a_record_waiting_for_an_index_permit_is_counted_at_the_gate_until_it_is_admitted(kind: str) -> None:
    consumer = build(kind)
    try:
        consumer.message_handler = quick_handler
        gate = consumer.governor.gate(Pool.INDEX_HEAVY)
        for _ in range(consumer.governor.limit(Pool.INDEX_HEAVY)):
            assert await gate.acquire()
        token = cc.GateWaiterToken(consumer, H, message_of("r1"))
        task = start(kind, consumer, "r1", token)
        await settle()
        assert consumer.admission_explain()["backlog"]["tiers"]["heavy"]["atGate"] == 1
        gate.release()
        await asyncio.wait_for(task, 5)
        assert consumer.admission_explain()["backlog"]["tiers"]["heavy"]["atGate"] == 0
        assert consumer.gate_waiters.count() == 0
    finally:
        consumer.governor.close()


@pytest.mark.parametrize("kind", KINDS)
async def test_a_suspect_record_waiting_its_turn_is_counted_and_the_count_returns_to_zero(
    kind: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    consumer = build(kind)
    try:
        consumer.message_handler = quick_handler
        monkeypatch.setattr(cc, "unfinished_attempts", AsyncMock(return_value=1))
        consumer.retry_manager = MagicMock()
        consumer._suspects = asyncio.Lock()
        await consumer._suspects.acquire()
        task = start(kind, consumer, "r1", cc.GateWaiterToken(consumer, H))
        await settle()
        assert consumer.admission_explain()["suspectsWaiting"] == 1
        consumer._suspects.release()
        await asyncio.wait_for(task, 5)
        assert consumer.admission_explain()["suspectsWaiting"] == 0
    finally:
        consumer.governor.close()


@pytest.mark.parametrize("kind", KINDS)
async def test_the_lease_wait_is_counted_per_pool_and_the_observer_gets_the_message(kind: str) -> None:
    consumer = build(kind)
    try:
        recorder = Recorder()
        consumer.admission_backlog = cc.AdmissionBacklog(observer=recorder)
        consumer.message_handler = quick_handler
        consumer.concurrency_manager = MagicMock()
        consumer.concurrency_manager.try_acquire = AsyncMock(return_value=False)
        task = start(kind, consumer, "r1", cc.GateWaiterToken(consumer, H))
        await settle()
        leases = consumer.admission_explain()["leases"]
        waiting = {pool: v["waiting"] for pool, v in leases.items()}
        assert waiting["indexing"] == 1 and sum(waiting.values()) == 1, waiting
        entered = [e for e in recorder.events if e[0] == "in" and e[2] == cc.PHASE_LEASE]
        assert entered and entered[0][3] is not None and entered[0][3].payload["recordId"] == "r1"
        await stop(task)
        assert consumer.admission_explain()["backlog"]["tiers"]["heavy"][cc.PHASE_LEASE] == 0
        assert {pool: v["waiting"] for pool, v in consumer.admission_explain()["leases"].items()}["indexing"] == 0
    finally:
        consumer.governor.close()


@pytest.mark.parametrize("kind", KINDS)
async def test_download_and_parse_waits_are_counted_and_carry_the_message(kind: str) -> None:
    consumer = build(kind, parse=1, ahead=1)
    try:
        recorder = Recorder()
        consumer.admission_backlog = cc.AdmissionBacklog(observer=recorder)
        assert await consumer.governor.download_ahead.acquire()  # the only place is taken
        consumer.message_handler = _download_then_parse
        task = start(kind, consumer, "r1", cc.GateWaiterToken(consumer, H))
        await settle()
        assert consumer.admission_explain()["backlog"]["tiers"]["heavy"][cc.PHASE_DOWNLOAD] == 1
        consumer.governor.download_ahead.release()
        await settle()
        # the record now holds its place and waits for a parse permit that is out
        parse_gate = consumer.governor.gate(Pool.HEAVY_PARSE)
        for _ in range(8):
            if consumer.admission_explain()["backlog"]["tiers"]["heavy"][cc.PHASE_PARSE]:
                break
            await asyncio.sleep(0.05)
        await stop(task)
        phases = {e[2]: e[3] for e in recorder.events if e[0] == "in"}
        assert cc.PHASE_DOWNLOAD in phases and phases[cc.PHASE_DOWNLOAD].payload["recordId"] == "r1"
        assert parse_gate is not None
    finally:
        consumer.governor.close()


async def _download_then_parse(message: Any) -> AsyncGenerator[PipelineEvent, None]:  # noqa: ANN401
    rid = message.payload["recordId"]
    yield PipelineEvent(event=IndexingEvent.START_DOWNLOAD, data=PipelineEventData(record_id=rid))
    yield PipelineEvent(event=IndexingEvent.START_PARSING, data=PipelineEventData(record_id=rid, tier=ParseTier.HEAVY, size_bytes=1))
    yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=rid))


@pytest.mark.parametrize("kind", KINDS)
async def test_a_parse_wait_is_counted_and_carries_the_message(kind: str) -> None:
    consumer = build(kind, parse=1, ahead=0)
    try:
        recorder = Recorder()
        consumer.admission_backlog = cc.AdmissionBacklog(observer=recorder)
        parse_gate = consumer.governor.gate(Pool.HEAVY_PARSE)
        assert await parse_gate.acquire(cost=consumer.governor.limit(Pool.HEAVY_PARSE))
        consumer.message_handler = _download_then_parse
        task = start(kind, consumer, "r1", cc.GateWaiterToken(consumer, H))
        await settle()
        assert consumer.admission_explain()["backlog"]["tiers"]["heavy"][cc.PHASE_PARSE] == 1
        parsed = [e[3] for e in recorder.events if e[0] == "in" and e[2] == cc.PHASE_PARSE]
        assert parsed and parsed[0].payload["recordId"] == "r1"
        await stop(task)
        assert consumer.admission_explain()["backlog"]["tiers"]["heavy"][cc.PHASE_PARSE] == 0
    finally:
        consumer.governor.close()


async def test_a_caller_polling_for_the_shared_slots_is_counted_while_it_waits(tmp_path: Any) -> None:  # noqa: ANN401
    from tests.unit.services.messaging.governor_test_helpers import make_test_governor

    governor = make_test_governor(worker_count=2, heavy_parse_slots_dir=tmp_path)
    try:
        held = governor.try_acquire_heavy_parse_slots(governor.admission_explain()["sharedSlots"]["limit"])
        assert held is not None
        waiter = asyncio.create_task(cc._wait_for_heavy_parse_slots(governor, 1, timeout=0.6))
        await asyncio.sleep(0.2)
        assert governor.admission_explain()["sharedSlots"]["waiting"] == 1
        assert await waiter is None
        assert governor.admission_explain()["sharedSlots"]["waiting"] == 0
    finally:
        governor.close()
