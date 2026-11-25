"""What the indexing consumers and the PostgreSQL queue announce: ``indexing.stage.completed`` per stage of a
record's delivery, ``message.consumed`` for the delivery itself and ``message.published`` from the queue's producer."""

import json
import logging
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core import org_context
from app.platform_events import catalog
from app.services.messaging import broker_events
from app.services.messaging.config import RedisStreamsConfig
from app.services.messaging.postgres.consumer import PgMessagingConsumer
from app.services.messaging.postgres.producer import PgMessagingProducer
from app.services.messaging.postgres.queue import Delivery
from app.utils import indexing_timing as timing
from tests.support.platform_event_capture import captured_events, facts
from tests.unit.services.messaging.test_indexing_end import (
    _handler,
    _pg,
    _PgQueue,
    _run,
    _Tracker,
)
from tests.unit.services.messaging.test_indexing_message_binding import LOGGER, _value

SECRET = "SEEDED-payload-text"
STAGE_FIELDS = set(catalog.EVENTS["indexing.stage.completed"].fields)
BROKERS = pytest.mark.parametrize("broker", ["redis", "kafka"])


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(broker_events, "_types", set())
    monkeypatch.setattr(timing, "_connectors", set())
    monkeypatch.setenv("MAX_DELIVERY_ATTEMPTS", "3")


class TestStages:
    def _timeline(self, **begin: object) -> tuple[timing.RecordTimeline, object]:
        return timing.begin_record("rec-1", "newRecord", "light", started=time.perf_counter() - 10, **begin)

    def test_each_stage_a_record_spent_time_in_is_announced_when_it_ends(self) -> None:
        with captured_events("indexing.stage.completed") as events, org_context.for_org("org_a", job="test"):
            timeline, token = self._timeline(connector="DRIVE")
            now = time.perf_counter()
            timeline.mark(timing.ADMITTED, now - 8)
            timeline.mark(timing.PARSE_REQUESTED, now - 7)
            timeline.mark(timing.PARSE_ADMITTED, now - 6)
            timeline.mark(timing.PARSED, now - 4)
            for step, seconds in (("download", 0.5), ("extract", 1.0), ("entities.extract", 0.25), ("embed.sparse", 0.5),
                                  ("vector.upsert", 0.75), ("vector.upsert", 0.25), ("entities.persist", 0.5), ("lease.record", 0.125)):
                timing.record_stage(step, seconds)
            timing.end_record(timeline, token, outcome="ok")

        stages = facts(events, "indexing.stage.completed")
        assert all(set(stage) == STAGE_FIELDS for stage in stages)
        assert [(s["stage"], round(s["duration_seconds"], 3), s["count"], s["outcome"]) for s in stages] == [
            ("queued", 3.0, 1, "success"),  # 2 s for an indexing slot, 1 s for a parse slot
            ("fetch", 0.5, 1, "success"),
            ("parse", 2.0, 1, "success"),
            ("extract", 1.25, 2, "success"),
            ("embed", 0.5, 1, "success"),
            ("store", 1.5, 3, "success"),
            ("other", 0.125, 1, "success"),
        ]
        assert {(s["connector"], s["org_id"], s["record_id"]) for s in stages} == {("DRIVE", "org_a", "rec-1")}

    def test_a_step_that_raised_fails_its_stage_only(self) -> None:
        with captured_events("indexing.stage.completed") as events:
            timeline, token = self._timeline()
            timeline.mark(timing.ADMITTED)
            with timing.stage("embed.sparse"):
                pass
            with pytest.raises(RuntimeError), timing.stage("vector.upsert"):
                raise RuntimeError(SECRET)
            timing.end_record(timeline, token, outcome="failed")

        outcomes = {s["stage"]: s["outcome"] for s in facts(events, "indexing.stage.completed")}
        assert outcomes == {"queued": "success", "embed": "success", "store": "failure"}
        assert SECRET not in repr(events)

    def test_a_record_that_never_got_a_slot_or_never_finished_parsing_says_so(self) -> None:
        with captured_events("indexing.stage.completed") as events:
            timeline, token = self._timeline()
            timing.end_record(timeline, token, outcome="stopped")
            timeline, token = self._timeline()
            timeline.mark(timing.ADMITTED)
            timeline.mark(timing.PARSE_REQUESTED)
            timeline.mark(timing.PARSE_ADMITTED)
            timing.end_record(timeline, token, outcome="failed")

        assert [(s["stage"], s["outcome"]) for s in facts(events, "indexing.stage.completed")] == [
            ("queued", "failure"), ("queued", "success"), ("parse", "failure"),
        ]

    @pytest.mark.parametrize(
        ("step", "stage"),
        [("download", "fetch"), ("chunk", "extract"), ("images.describe", "extract"), ("entities.resolve", "extract"),
         ("entities.upsert", "store"), ("embed.image", "embed"), ("blob.write", "store"), ("graph.enrich", "store"),
         ("swap.finish", "store"), ("reconcile", "store"), ("seeds", "other"), ("a.step.added.later", "other")],
    )
    def test_every_step_belongs_to_one_of_a_closed_set_of_stages(self, step: str, stage: str) -> None:
        assert timing.stage_of(step) == stage and stage in timing.STAGES

    @pytest.mark.parametrize(
        ("value", "expected"),
        [("DRIVE", "DRIVE"), ("SHAREPOINT ONLINE", "SHAREPOINT ONLINE"), (None, "unknown"), ("", "unknown"), (7, "unknown"),
         ("x" * 65, "unknown"), ("a@b.co", "unknown")],
    )
    def test_a_connector_type_is_a_short_name_or_unknown(self, value: object, expected: str) -> None:
        assert timing.connector_type(value) == expected

    def test_distinct_connector_types_are_capped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(timing, "_MAX_CONNECTORS", 1)

        assert [timing.connector_type(v) for v in ("A", "B", "A")] == ["A", "unknown", "A"]

    def test_nothing_is_built_when_nobody_listens(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(timing.RecordTimeline, "stage_facts", lambda *_a: pytest.fail("built facts nobody hears"))
        timeline, token = self._timeline(broker="kafka", topic="record-events")

        timing.end_record(timeline, token, outcome="ok")


class TestIndexingConsumers:
    @BROKERS
    async def test_a_delivery_is_announced_with_its_stages_and_as_consumed(self, broker: str) -> None:
        with captured_events("indexing.stage.completed", "message.consumed") as events:
            assert await _run(broker, _handler()) is True

        (consumed,) = facts(events, "message.consumed")
        assert set(consumed) == set(catalog.EVENTS["message.consumed"].fields)
        assert (consumed["broker"], consumed["topic"], consumed["message_type"], consumed["outcome"]) == (
            broker, "record-events", "newRecord", "success")
        stages = facts(events, "indexing.stage.completed")
        assert stages and stages[0]["stage"] == "queued"
        assert {(s["record_id"], s["org_id"]) for s in stages} == {("rec-1", "org_a")}

    @BROKERS
    async def test_a_failure_that_will_be_retried_is_consumed_as_a_retry(self, broker: str) -> None:
        with captured_events("message.consumed") as events:
            await _run(broker, _handler(ConnectionError("down")), retry_manager=_Tracker())

        assert [c["outcome"] for c in facts(events, "message.consumed")] == ["retry"]

    async def test_redis_lag_is_the_age_of_the_stream_entry(self) -> None:
        from tests.unit.services.messaging.test_indexing_message_binding import _redis

        c = _redis()
        c.message_handler = _handler()
        entry = f"{int(time.time() * 1000) - 3000}-0"
        with captured_events("message.consumed") as events:
            await c._process_message_wrapper("record-events", entry, {"value": _value()})

        (consumed,) = facts(events, "message.consumed")
        assert 3000 <= consumed["lag_ms"] < 60_000


class TestPostgresQueue:
    async def _settle(self, consumer: PgMessagingConsumer, value: str, *, topic: str = "record-events") -> list[dict]:
        with captured_events("message.consumed") as events:
            await consumer._settle(Delivery(topic=topic, message_id=7, attempts=1, value=value, key=None))
        return facts(events, "message.consumed")

    async def test_an_indexing_delivery_is_consumed_with_the_lag_its_producer_stamped(self) -> None:
        body = json.loads(_value()) | {"timestamp": int(time.time() * 1000) - 4000, "text": SECRET}

        (consumed,) = await self._settle(_pg(_handler()), json.dumps(body))

        assert set(consumed) == set(catalog.EVENTS["message.consumed"].fields)
        assert (consumed["broker"], consumed["topic"], consumed["message_type"], consumed["outcome"]) == (
            "postgres", "record-events", "newRecord", "success")
        assert 4000 <= consumed["lag_ms"] < 60_000
        assert SECRET not in repr(consumed) and "rec-1" not in repr(consumed)

    async def test_a_failure_with_attempts_left_is_a_retry_and_a_body_that_is_not_json_a_dead_letter(self) -> None:
        (retried,) = await self._settle(_pg(_handler(ConnectionError("down"))), _value())
        (dead,) = await self._settle(_pg(_handler()), "{not json")

        assert (retried["outcome"], retried["lag_ms"]) == ("retry", None)
        assert (dead["outcome"], dead["message_type"]) == ("dead_letter", "unknown")

    async def test_a_delivery_of_another_topic_is_announced_too(self) -> None:
        consumer = PgMessagingConsumer(
            logging.getLogger(LOGGER), RedisStreamsConfig(topics=["entity-events"], group_id="g"), queue=_PgQueue()
        )
        consumer.message_handler = AsyncMock(return_value=True)
        opened = MagicMock(message={"eventType": "userAdded", "payload": {}}, ctx=None)
        with patch("app.services.messaging.postgres.consumer.open_configured", AsyncMock(return_value=opened)):
            (consumed,) = await self._settle(consumer, json.dumps({"eventType": "userAdded", "payload": {}}), topic="entity-events")

        assert (consumed["topic"], consumed["message_type"], consumed["outcome"]) == ("entity-events", "userAdded", "success")

    async def test_a_cancelled_delivery_is_left_to_be_read_again(self) -> None:
        import asyncio

        async def cancelled(_message):  # noqa: ANN202
            raise asyncio.CancelledError
            yield

        with captured_events("message.consumed") as events, pytest.raises(asyncio.CancelledError):
            await _pg(cancelled)._settle(Delivery(topic="record-events", message_id=7, attempts=1, value=_value(), key=None))

        assert [c["outcome"] for c in facts(events, "message.consumed")] == ["retry"]

    async def test_the_body_is_not_read_when_nobody_listens(self) -> None:
        with patch("app.services.messaging.postgres.consumer.broker_events.consumed") as consumed:
            await _pg(_handler())._settle(Delivery(topic="record-events", message_id=7, attempts=1, value=_value(), key=None))

        consumed.assert_not_called()

    async def test_the_producer_announces_each_message_published(self) -> None:
        queue = MagicMock(publish=AsyncMock())
        producer = PgMessagingProducer(logging.getLogger(LOGGER), queue=queue)
        message = {"eventType": "newRecord", "payload": {"text": SECRET}}

        with captured_events("message.published") as events:
            assert await producer.send_message("record-events", message, key="rec-1") is True
            assert await producer.send_messages("record-events", [("k1", dict(message)), ("k2", {"payload": {}})]) == [True, True]

        published = facts(events, "message.published")
        assert all(set(p) == set(catalog.EVENTS["message.published"].fields) for p in published)
        assert [(p["broker"], p["topic"], p["message_type"], p["outcome"]) for p in published] == [
            ("postgres", "record-events", "newRecord", "success"),
            ("postgres", "record-events", "newRecord", "success"),
            ("postgres", "record-events", "unknown", "success"),
        ]
        assert SECRET not in repr(published) and "rec-1" not in repr(published)

    async def test_a_publish_the_queue_refused_is_announced_as_a_failure(self) -> None:
        queue = MagicMock(publish=AsyncMock(side_effect=ConnectionError("down")))
        producer = PgMessagingProducer(logging.getLogger(LOGGER), queue=queue)

        with captured_events("message.published") as events:
            with pytest.raises(ConnectionError):
                await producer.send_message("record-events", {"eventType": "newRecord"})
            assert await producer.send_messages("record-events", [(None, {"eventType": "newRecord"})]) == [False]

        assert [p["outcome"] for p in facts(events, "message.published")] == ["failure", "failure"]
