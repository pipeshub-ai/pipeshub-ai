"""On the PostgreSQL broker a message's key is its ordering key: a lane key (the connector) would run a whole repository one record at a time."""
import pytest

from app.services.messaging.config import messaging_env


@pytest.mark.parametrize("broker", ["kafka", "redis"])
def test_other_brokers_keep_their_lanes(monkeypatch, broker) -> None:
    monkeypatch.setenv("MESSAGE_BROKER", broker)
    monkeypatch.delenv("FAIR_SCHEDULING_LANE_COUNT", raising=False)
    assert messaging_env.fair_scheduling_lane_count == 8


def test_postgres_has_no_lanes_so_each_record_is_its_own_ordering_key(monkeypatch) -> None:
    monkeypatch.setenv("MESSAGE_BROKER", "postgres")
    monkeypatch.delenv("FAIR_SCHEDULING_LANE_COUNT", raising=False)
    assert messaging_env.fair_scheduling_lane_count == 1
    monkeypatch.setenv("FAIR_SCHEDULING_LANE_COUNT", "8")      # not even by setting it: producers and consumers must agree
    assert messaging_env.fair_scheduling_lane_count == 1
