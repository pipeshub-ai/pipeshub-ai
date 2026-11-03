"""INDEXING_CONSUME_LANES: an indexing replica that reads only some lanes."""

import pytest

from app.services.messaging.config import (
    MessageBrokerType,
    messaging_env,
    parse_lane_set,
)
from app.services.messaging.messaging_factory import lane_topics_for


@pytest.fixture(autouse=True)
def _eight_redis_lanes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MESSAGE_BROKER", "redis")
    monkeypatch.setenv("FAIR_SCHEDULING_LANE_COUNT", "8")


def test_parses_lanes_and_ranges() -> None:
    assert parse_lane_set("0-2, 5,7") == frozenset({0, 1, 2, 5, 7})
    assert parse_lane_set("4") == frozenset({4})


@pytest.mark.parametrize("raw", ["a", "3-1", "-1", "1-", "2,,3"])
def test_refuses_anything_else(raw: str) -> None:
    with pytest.raises(ValueError, match="lane"):
        parse_lane_set(raw)


def test_refuses_a_huge_range_before_expanding_it() -> None:
    with pytest.raises(ValueError, match="outside"):
        parse_lane_set("0-1000000000")


def test_unset_means_every_lane(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INDEXING_CONSUME_LANES", raising=False)
    assert messaging_env.indexing_consume_lanes is None
    monkeypatch.setenv("INDEXING_CONSUME_LANES", "6-7")
    assert messaging_env.indexing_consume_lanes == frozenset({6, 7})


def test_a_subset_reads_the_base_stream_and_its_lanes() -> None:
    assert lane_topics_for("record-events", MessageBrokerType.REDIS, frozenset({7, 6})) == [
        "record-events",
        "record-events.6",
        "record-events.7",
    ]
    assert lane_topics_for("record-events", MessageBrokerType.REDIS) == [
        "record-events",
        *(f"record-events.{lane}" for lane in range(8)),
    ]


def test_a_subset_must_name_configured_lanes_on_redis() -> None:
    with pytest.raises(ValueError, match="outside"):
        lane_topics_for("record-events", MessageBrokerType.REDIS, frozenset({7, 8}))
    with pytest.raises(ValueError, match="Redis Streams"):
        lane_topics_for("record-events", MessageBrokerType.KAFKA, frozenset({1}))
    with pytest.raises(ValueError, match="not laned"):
        lane_topics_for("sync-events", MessageBrokerType.REDIS, frozenset({1}))
