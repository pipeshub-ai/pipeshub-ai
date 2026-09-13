"""Pipeline-stage consumers admit through their own permits and lease pool, serialise on the
job, and hand paused work back without counting an attempt."""

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services.messaging import consumer_concurrency as concurrency
from app.services.messaging.config import RedisStreamsConfig
from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer.indexing_consumer import (
    IndexingKafkaConsumer,
)
from app.services.messaging.redis_streams.indexing_consumer import (
    IndexingRedisStreamsConsumer,
)
from app.services.resource_governor.models import ParseTier
from tests.unit.services.messaging.governor_test_helpers import make_test_governor

CLASSIFY = concurrency.StageAdmission(stage="classify", limit=5)


def _host(stage_admission: concurrency.StageAdmission | None) -> SimpleNamespace:
    return SimpleNamespace(stage_admission=stage_admission, governor=None)


class TestWorkKey:
    def test_a_record_event_serialises_on_its_record(self) -> None:
        assert concurrency.work_key({"recordId": "rec-1", "jobId": "j"}, "msg-1") == "rec-1"

    def test_a_stage_job_serialises_on_its_job(self) -> None:
        assert concurrency.work_key({"jobId": "vr:rev:classify@1"}, "msg-1") == "job:vr:rev:classify@1"

    def test_anything_else_serialises_on_its_message(self) -> None:
        assert concurrency.work_key({"eventType": "bulkDeleteRecords"}, "msg-1") == "msg-1"


class TestStageAdmission:
    def test_has_its_own_lease_pool(self) -> None:
        assert concurrency.admission_lease_pool(_host(CLASSIFY), ParseTier.HEAVY) == "stage:classify"

    def test_a_record_consumer_keeps_the_indexing_pool(self) -> None:
        assert concurrency.admission_lease_pool(_host(None), ParseTier.HEAVY) == "indexing"

    def test_its_limit_is_the_cluster_ceiling(self) -> None:
        assert concurrency.index_ceiling(_host(CLASSIFY), ParseTier.HEAVY) == 5  # type: ignore[arg-type]

    def test_rejects_a_zero_limit(self) -> None:
        with pytest.raises(ValueError, match=">= 1"):
            concurrency.StageAdmission(stage="classify", limit=0)


def test_a_parse_wait_timeout_is_handed_back_without_an_attempt() -> None:
    assert issubclass(concurrency.ParseAdmissionTimeout, concurrency.RequeueWithoutAttempt)


def _redis(**kwargs: object) -> IndexingRedisStreamsConsumer:
    config = RedisStreamsConfig(host="h", port=6379, group_id="g", topics=["pipeline.classify"], batch_size=10)
    return IndexingRedisStreamsConsumer(logging.getLogger("t"), config, provider=MagicMock(), **kwargs)  # type: ignore[arg-type]


def _kafka(**kwargs: object) -> IndexingKafkaConsumer:
    config = KafkaConsumerConfig(
        topics=["pipeline.classify"], client_id="c", group_id="g", auto_offset_reset="earliest",
        enable_auto_commit=False, bootstrap_servers=["b:9092"],
    )
    return IndexingKafkaConsumer(logging.getLogger("t"), config, **kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize("build", [_redis, _kafka])
def test_a_stage_consumer_carries_its_admission(build) -> None:
    consumer = build(stage_admission=CLASSIFY)
    assert consumer.stage_admission is CLASSIFY
    assert consumer.governor is None


@pytest.mark.parametrize("build", [_redis, _kafka])
def test_a_stage_consumer_refuses_a_governor(build) -> None:
    with pytest.raises(ValueError, match="not a governor"):
        build(stage_admission=CLASSIFY, governor=make_test_governor())


@pytest.mark.parametrize("build", [_redis, _kafka])
def test_a_record_consumer_has_no_stage_admission(build) -> None:
    assert build(governor=make_test_governor()).stage_admission is None


@pytest.mark.parametrize("broker", ["redis", "kafka"])
def test_the_factory_gives_a_stage_consumer_parallel_dispatch(broker: str) -> None:
    from app.services.messaging.config import (
        ConsumerType,
        MessageBrokerType,
    )
    from app.services.messaging.messaging_factory import (
        MessagingFactory,
    )

    config = (
        RedisStreamsConfig(host="h", port=6379, group_id="g", topics=["pipeline.classify"], batch_size=10)
        if broker == "redis"
        else KafkaConsumerConfig(
            topics=["pipeline.classify"], client_id="c", group_id="g", auto_offset_reset="earliest",
            enable_auto_commit=False, bootstrap_servers=["b:9092"],
        )
    )
    consumer = MessagingFactory.create_consumer(
        logging.getLogger("t"),
        config=config,
        broker_type=MessageBrokerType(broker),
        consumer_type=ConsumerType.INDEXING,
        stage_admission=CLASSIFY,
    )
    fair = consumer.fair_scheduler_config  # type: ignore[attr-defined]
    assert fair.enabled and fair.parallel_partitions
