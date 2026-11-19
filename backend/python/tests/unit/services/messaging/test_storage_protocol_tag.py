"""Every indexing consumer carries the storage-protocol tag, so a member of an indexing consumer
group without it can be told from a pod that writes the stored record by compare-and-set."""

from __future__ import annotations

import ast
import inspect
import logging

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


def _calls_tagged(node: ast.AST) -> bool:
    return any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "tagged" for n in ast.walk(node)
    )


def test_every_client_id_the_indexing_consumers_set_is_tagged() -> None:
    """The backlog probe and both fetch-back readers derive their ids from the tagged one.

    The value assigned to each ``client_id`` is checked, not the text around it.
    """
    tree = ast.parse(inspect.getsource(kafka_indexing))
    assigned = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Subscript)
        and isinstance(target.slice, ast.Constant)
        and target.slice.value == "client_id"
    ]
    in_dicts = [
        value
        for node in ast.walk(tree)
        if isinstance(node, ast.Dict)
        for key, value in zip(node.keys, node.values, strict=True)
        if isinstance(key, ast.Constant) and key.value == "client_id"
    ]
    assert len(assigned) == 3
    assert len(in_dicts) == 1
    assert all(_calls_tagged(value) for value in assigned + in_dicts)


def test_the_fetch_back_readers_connect_tagged() -> None:
    consumer = IndexingKafkaConsumer(
        logging.getLogger("test_storage_protocol_tag"),
        KafkaConsumerConfig(
            bootstrap_servers=["b:9092"], group_id="g", client_id="indexing-1", topics=["t"],
            auto_offset_reset="earliest", enable_auto_commit=False,
        ),
    )

    for reader in (consumer._IndexingKafkaConsumer__offset_fetcher(), consumer._IndexingKafkaConsumer__waiting_fetcher()):
        assert is_tagged(reader._client_config["client_id"])


def test_the_redis_indexing_consumer_is_named_tagged() -> None:
    assert "tagged(config.client_id)" in inspect.getsource(redis_indexing.IndexingRedisStreamsConsumer.__init__)
