"""Tests for fair-scheduling instrumentation.

The point of these is that the numbers an operator would page on are
actually emitted, and that instrumentation can never take the consume loop
down.
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiokafka import TopicPartition

from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer.indexing_consumer import (
    IndexingKafkaConsumer,
)
from app.services.messaging.scheduling.interface import FairSchedulerConfig
from app.telemetry.backend import METRICS_BACKEND
from app.telemetry.modules import scheduling_metrics as metrics


@pytest.fixture
def logger():
    return logging.getLogger("test_scheduling_metrics")


@pytest.fixture
def kafka_config():
    return KafkaConsumerConfig(
        topics=["record-events"],
        client_id="c",
        group_id="g",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        bootstrap_servers=["b:9092"],
    )


def _series() -> str:
    return METRICS_BACKEND.serialize()


def _consumer(logger, kafka_config, **overrides):
    config = {
        "enabled": True,
        "key_fields": ("orgId", "connectorId"),
        "default_quantum": 1,
        "max_buffered_messages": 100,
        "max_per_entity_messages": 50,
        "max_dwell_seconds": 900.0,
    }
    config.update(overrides)
    consumer = IndexingKafkaConsumer(
        logger, kafka_config, fair_scheduler_config=FairSchedulerConfig(**config)
    )
    consumer.consumer = MagicMock()
    consumer.consumer.commit = AsyncMock()
    consumer.running = True
    return consumer


class TestWatermarkLagIsExported:
    """The metric you cannot operate without: an offset that never resolves
    stalls every later commit on its partition until a restart, and that is
    invisible until the restart replays everything."""

    def test_lag_is_published_per_partition(self, logger, kafka_config):
        consumer = _consumer(logger, kafka_config)
        tp = TopicPartition("record-events", 0)
        for offset in range(5):
            consumer._offset_tracker.track(tp, offset)

        consumer._IndexingKafkaConsumer__publish_scheduler_metrics()

        assert (
            'pipeshub_indexing_watermark_lag{partition="0",topic="record-events"} 5.0'
            in _series()
        )

    def test_lag_falls_back_to_zero_once_everything_resolves(
        self, logger, kafka_config
    ):
        consumer = _consumer(logger, kafka_config)
        tp = TopicPartition("record-events", 1)
        consumer._offset_tracker.track(tp, 0)
        consumer._offset_tracker.mark_done(tp, 0)

        consumer._IndexingKafkaConsumer__publish_scheduler_metrics()

        assert (
            'pipeshub_indexing_watermark_lag{partition="1",topic="record-events"} 0.0'
            in _series()
        )


class TestSchedulerGauges:
    def test_depth_and_active_key_counts_are_published(
        self, logger, kafka_config
    ):
        consumer = _consumer(logger, kafka_config)
        consumer._scheduler.enqueue(("org-a", "c1"), "1")
        consumer._scheduler.enqueue(("org-a", "c2"), "2")
        consumer._scheduler.enqueue(("org-b", "c1"), "3")

        consumer._IndexingKafkaConsumer__publish_scheduler_metrics()

        series = _series()
        assert 'pipeshub_indexing_scheduler_buffer_depth{broker="kafka"} 3.0' in series
        assert (
            'pipeshub_indexing_scheduler_active_keys{broker="kafka",level="org"} 2.0'
            in series
        )
        assert (
            'pipeshub_indexing_scheduler_active_keys{broker="kafka",level="connector"} 3.0'
            in series
        )

    def test_paused_lane_count_is_published(self, logger, kafka_config):
        consumer = _consumer(logger, kafka_config)
        consumer._lane_paused[TopicPartition("record-events", 0)] = ("org-a", "c1")

        consumer._IndexingKafkaConsumer__publish_scheduler_metrics()

        assert 'pipeshub_indexing_lanes_paused{broker="kafka"} 1.0' in _series()


class TestInstrumentationNeverBreaksTheLoop:
    def test_a_failing_gauge_does_not_propagate(self, logger, kafka_config):
        """A metrics failure must never be the reason indexing stops."""
        consumer = _consumer(logger, kafka_config)
        consumer._scheduler = MagicMock()
        type(consumer._scheduler).pending_count = property(
            lambda _self: (_ for _ in ()).throw(RuntimeError("boom"))
        )

        consumer._IndexingKafkaConsumer__publish_scheduler_metrics()

    def test_no_scheduler_publishes_nothing(self, logger, kafka_config):
        consumer = IndexingKafkaConsumer(logger, kafka_config)
        consumer._IndexingKafkaConsumer__publish_scheduler_metrics()


class TestCardinality:
    def test_dispatch_is_labelled_by_org_not_by_connector(self):
        """Fairness is per (org, connector), but a per-connector label would
        be an unbounded series count on a busy install."""
        metrics.record_dispatch("kafka", "org-1")
        line = next(
            line
            for line in _series().splitlines()
            if line.startswith("pipeshub_indexing_scheduler_dispatched_total")
        )
        assert "org=" in line
        assert "connector" not in line


class TestMissingKeyIsVisible:
    async def test_absent_fairness_field_is_counted(self, logger, kafka_config):
        """Records grouped under the default key share one fair slice, which
        is correct but worth knowing about -- it usually means a payload
        regression upstream."""
        import json

        consumer = _consumer(logger, kafka_config)
        envelope = {
            "eventType": "newRecord",
            "payload": {"recordId": "r1", "orgId": "org-1"},
        }
        message = MagicMock()
        message.topic = "record-events"
        message.partition = 0
        message.offset = 0
        message.value = json.dumps(envelope).encode()

        await consumer._IndexingKafkaConsumer__enqueue_message(
            TopicPartition("record-events", 0), message
        )

        assert (
            'pipeshub_indexing_scheduler_missing_key_total{broker="kafka",field="connectorId"}'
            in _series()
        )
