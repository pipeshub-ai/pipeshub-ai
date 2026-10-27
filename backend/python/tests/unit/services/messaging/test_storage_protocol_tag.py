"""Every indexing consumer carries the storage-protocol tag, so a member of an indexing consumer
group without it can be told from a pod that writes the stored record by compare-and-set."""

from __future__ import annotations

import ast
import inspect

from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer import indexing_consumer as kafka_indexing
from app.services.messaging.kafka.consumer.indexing_consumer import (
    IndexingKafkaConsumer,
)
from app.services.messaging.redis_streams import indexing_consumer as redis_indexing
from app.services.messaging.storage_protocol import (
    STORAGE_PROTOCOL_TAG,
    is_tagged,
    tagged,
)


def test_a_name_is_tagged_once() -> None:
    assert tagged("pipeshub") == f"pipeshub{STORAGE_PROTOCOL_TAG}"
    assert tagged(tagged("pipeshub")) == tagged("pipeshub")
    assert is_tagged(tagged("x")) and not is_tagged("x")
    # A substring test: the consumers append their own suffixes.
    assert is_tagged(f"{tagged('pipeshub')}-backlog") and is_tagged(f"{tagged('pipeshub')}-abc123")


def test_the_kafka_indexing_consumer_connects_tagged() -> None:
    config = KafkaConsumerConfig(
        bootstrap_servers=["b:9092"], group_id="g", client_id="indexing-1", topics=["t"],
        auto_offset_reset="earliest", enable_auto_commit=False,
    )

    assert is_tagged(IndexingKafkaConsumer.kafka_config_to_dict(config)["client_id"])


def test_every_client_id_the_indexing_consumers_set_is_tagged() -> None:
    """The backlog probe and the fetch-back consumer derive their ids from the tagged one."""
    source = inspect.getsource(kafka_indexing)
    ids = [
        node for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and node.slice.value == "client_id"
    ]
    assert len(ids) == 2
    assert source.count("tagged(self.kafka_config.client_id)") == 2
    assert "'client_id': tagged(kafka_config.client_id)" in source


def test_the_redis_indexing_consumer_is_named_tagged() -> None:
    assert "tagged(config.client_id)" in inspect.getsource(redis_indexing.IndexingRedisStreamsConsumer.__init__)
