"""A day of the stranded-record sweep against a consumer that is hours behind.

Reproduces a report from a community user: with a backlog longer than
STRANDED_RECORD_REPUBLISH_AFTER_SECONDS (one hour), the sweep re-queued healthy
records over and over -- about 185,000 duplicate events for about 43,000 GitLab
records in a day -- and every duplicate made the backlog it was waiting behind
longer.

The sweep under test is the real one. What is simulated is the clock, a graph
that holds the records, and one broker lane consumed in order at a fixed rate.
The lane reports its backlog the way both real brokers do: the publish time of
the oldest event the consumer has not finished with.
"""
from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from app.indexing_main import _republish_stranded_records
from app.services.messaging.lanes.backlog import LaneBacklog
from tests.support.fake_record_graph import FakeRecordGraph

if TYPE_CHECKING:
    from collections.abc import Iterator

MINUTE_MS = 60 * 1000
HOUR_MS = 60 * MINUTE_MS
START_MS = 1_800_000_000_000
RECORDS = 120
# Four events an hour: thirty hours of work for the records above, so the
# consumer is still behind when the simulated day ends.
MINUTES_PER_EVENT = 15
CONNECTOR = "gitlab-1"
LANE = "record-events.3"


class _Clock:
    def __init__(self) -> None:
        self.now_ms = START_MS

    def __call__(self) -> int:
        return self.now_ms


class _Lane:
    """One broker lane: events in publish order, finished from the front."""

    def __init__(self, clock: _Clock, graph: FakeRecordGraph) -> None:
        self.clock = clock
        self.graph = graph
        self.events: list[tuple[int, str]] = []
        self.finished = 0
        self.resent: Counter[str] = Counter()
        self.readable = True

    def publish(self, record_id: str) -> None:
        self.events.append((self.clock.now_ms, record_id))

    async def send_event(self, topic, event_type, payload, key=None) -> bool:
        """The producer the sweep re-publishes through."""
        self.resent[payload["recordId"]] += 1
        self.publish(payload["recordId"])
        return True

    def consume_one(self) -> None:
        if self.finished == len(self.events):
            return
        _published_ms, record_id = self.events[self.finished]
        self.finished += 1
        # A second event for a record that is already indexed is skipped by the
        # handler, but it still took the consumer's turn.
        self.graph.mark_indexed(record_id)

    async def backlog(self) -> LaneBacklog:
        if not self.readable:
            raise ConnectionError("broker unreachable")
        waiting = self.events[self.finished:]
        return LaneBacklog("record-events", {LANE: float(waiting[0][0])} if waiting else {})


class _Day:
    def __init__(self) -> None:
        self.clock = _Clock()
        self.graph = FakeRecordGraph()
        self.lane = _Lane(self.clock, self.graph)

    def sync(self, lose: frozenset[int] = frozenset()) -> None:
        """A connector sync: one record a second, each with its event unless lost."""
        for i in range(RECORDS):
            key = f"rec-{i:04d}"
            self.graph.add_queued(
                key,
                CONNECTOR,
                self.clock.now_ms,
                # Source-system time, as GitLab and Jira report it.
                updatedAtTimestamp=START_MS - 400 * 24 * HOUR_MS,
            )
            if i not in lose:
                self.lane.publish(key)
            self.clock.now_ms += 1000

    async def run(self, hours: int) -> None:
        """The sweep every minute, as in production; the consumer at its own pace."""

        async def run_coordination(coro):  # noqa: ANN202
            return await coro

        for minute in range(1, hours * 60 + 1):
            self.clock.now_ms += MINUTE_MS
            if minute % MINUTES_PER_EVENT == 0:
                self.lane.consume_one()
            await _republish_stranded_records(
                graph_provider=self.graph,
                logger=MagicMock(),
                producer=self.lane,
                run_coordination=run_coordination,
                concurrency_manager=None,
                page_size=50,
                read_backlog=self.lane.backlog,
            )


@pytest.fixture
def day(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Day]:
    monkeypatch.setenv("STRANDED_RECORD_REPUBLISH_AFTER_SECONDS", "3600")
    simulated = _Day()
    with patch("app.indexing_main.get_epoch_timestamp_in_ms", simulated.clock):
        yield simulated


async def test_a_backlog_longer_than_the_threshold_is_not_requeued(day: _Day) -> None:
    day.sync()

    await day.run(hours=24)

    duplicates = sum(day.lane.resent.values())
    print(f"duplicate events over the simulated day: {duplicates} for {RECORDS} records")
    assert duplicates == 0
    # Nothing the sweep did held the consumer up: one record indexed per turn.
    assert len(day.graph.still_queued()) == RECORDS - 24 * 60 // MINUTES_PER_EVENT


async def test_a_record_whose_event_was_lost_is_still_resent_once_the_queue_passes_it(day: _Day) -> None:
    lost = frozenset({7, 60, 113})
    day.sync(lose=lost)

    # The backlog drains in a little over 29 hours; nothing can be told apart before then.
    await day.run(hours=28)
    assert sum(day.lane.resent.values()) == 0, "indistinguishable from the backlog so far"

    await day.run(hours=8)

    assert dict(day.lane.resent) == {f"rec-{i:04d}": 1 for i in sorted(lost)}
    assert day.graph.still_queued() == []


async def test_with_the_broker_unreadable_the_resends_thin_out_instead_of_repeating(day: _Day) -> None:
    day.sync()
    day.lane.readable = False

    await day.run(hours=24)

    per_record = max(day.lane.resent.values())
    total = sum(day.lane.resent.values())
    print(f"broker unreadable: {total} duplicate events, at most {per_record} for one record")
    # At 1h, then 2h, 4h and 8h after the one before; the next would be at 31h.
    assert per_record == 4
