"""Topic names are declared twice -- once in Python's `Topic` enum and once in
the Node service's `BrokerTopic` -- and the two must not drift.

The split is not cosmetic. Python has no Kafka admin client, so the *only*
thing that creates topics on a Kafka cluster is the Node service's startup
bootstrap, which derives its list from `BrokerTopic`. A topic that Python
publishes to but Node never declares does not exist on any cluster with
`auto.create.topics.enable=false` (the default on MSK and most managed
Kafka), and the publish fails while the matching consumer subscribes to
nothing. On Redis Streams the same code works, because `XADD` creates the
stream implicitly -- so this drift is invisible until someone switches
brokers.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services.messaging.config import REQUIRED_TOPICS, Topic

_NODE_MESSAGING_TYPES = (
    Path(__file__).resolve().parents[5]
    / "nodejs/apps/src/libs/types/messaging.types.ts"
)


def _node_broker_topics() -> set[str]:
    source = _NODE_MESSAGING_TYPES.read_text(encoding="utf-8")
    body = re.search(r"export enum BrokerTopic \{(.*?)\}", source, re.DOTALL)
    assert body is not None, f"BrokerTopic enum not found in {_NODE_MESSAGING_TYPES}"
    return set(re.findall(r"=\s*'([^']+)'", body.group(1)))


def test_the_node_messaging_types_file_is_where_we_think_it_is() -> None:
    assert _NODE_MESSAGING_TYPES.is_file(), (
        f"{_NODE_MESSAGING_TYPES} has moved; this suite's drift guard is now vacuous"
    )


@pytest.mark.parametrize("topic", list(Topic), ids=lambda t: t.value)
def test_every_python_topic_is_declared_on_the_node_side(topic: Topic) -> None:
    assert topic.value in _node_broker_topics(), (
        f"{topic.value!r} is published or consumed by a Python service but missing "
        f"from BrokerTopic, so nothing creates it on a Kafka cluster"
    )


def test_the_task_engine_topics_are_registered_for_both_brokers() -> None:
    """`REQUIRED_TOPICS` is derived from the whole enum rather than a
    hand-maintained list, so this fails only if someone reintroduces one."""
    assert Topic.TASK_EVENTS.value in REQUIRED_TOPICS
    assert Topic.APP_EVENTS.value in REQUIRED_TOPICS
